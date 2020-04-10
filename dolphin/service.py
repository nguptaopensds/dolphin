# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# Copyright 2011 Justin Santa Barbara
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Generic Node base class for all workers that run on hosts."""

import inspect
import os
import random
import socket

from oslo_config import cfg
from oslo_log import log
from oslo_service import service
from oslo_service import wsgi
from oslo_service import loopingcall
from oslo_utils import importutils
import oslo_messaging as messaging

from dolphin import context
from dolphin import exception
from dolphin import rpc

LOG = log.getLogger(__name__)

service_opts = [
    cfg.IntOpt('report_interval',
               default=10,
               help='Seconds between nodes reporting state to datastore.'),
    cfg.IntOpt('periodic_interval',
               default=60,
               help='Seconds between running periodic tasks.'),
    cfg.IntOpt('periodic_fuzzy_delay',
               default=60,
               help='Range of seconds to randomly delay when starting the '
                    'periodic task scheduler to reduce stampeding. '
                    '(Disable by setting to 0)'),
    cfg.HostAddressOpt('dolphin_listen',
                       default="::",
                       help='IP address for Dolphin API to listen '
                            'on.'),
    cfg.PortOpt('dolphin_listen_port',
                default=8188,
                help='Port for Dolphin API to listen on.'),
    cfg.IntOpt('dolphin_workers',
               default=1,
               help='Number of workers for Dolphin API service.'),
    cfg.BoolOpt('dolphin_use_ssl',
                default=False,
                help='Wraps the socket in a SSL context if True is set. '
                     'A certificate file and key file must be specified.'),
]

CONF = cfg.CONF
CONF.register_opts(service_opts)


class Service(service.Service):
    """Service object for binaries running on hosts.

    A service takes a manager and enables rpc by listening to queues based
    on topic. It also periodically runs tasks on the manager and reports
    it state to the database services table.
    """

    def __init__(self, host, binary, topic, manager, report_interval=None,
                 periodic_interval=None, periodic_fuzzy_delay=None,
                 service_name=None, coordination=False, *args, **kwargs):
        super(Service, self).__init__()
        if not rpc.initialized():
            rpc.init(CONF)
        self.host = host
        self.binary = binary
        self.topic = topic
        self.manager_class_name = manager
        manager_class = importutils.import_class(self.manager_class_name)
        self.manager = manager_class(host=self.host,
                                     service_name=service_name,
                                     *args, **kwargs)
        self.report_interval = report_interval
        self.periodic_interval = periodic_interval
        self.periodic_fuzzy_delay = periodic_fuzzy_delay
        self.saved_args, self.saved_kwargs = args, kwargs
        self.timers = []
        self.coordinator = coordination

    def start(self):
        # version_string = version.version_string()
        LOG.info('Starting %(topic)s node.', {'topic': self.topic})
        # self.model_disconnected = False
        # ctxt = context.get_admin_context()

        # if self.coordinator:
        #     coordination.LOCK_COORDINATOR.start()
        #
        # try:
        #     service_ref = db.service_get_by_args(ctxt,
        #                                          self.host,
        #                                          self.binary)
        #     self.service_id = service_ref['id']
        # except exception.NotFound:
        #     self._create_service_ref(ctxt)

        LOG.debug("Creating RPC server for service %s.", self.topic)

        target = messaging.Target(topic=self.topic, server=self.host)
        endpoints = [self.manager]
        endpoints.extend(self.manager.additional_endpoints)
        self.rpcserver = rpc.get_server(target, endpoints)
        self.rpcserver.start()

        self.manager.init_host()
        # if self.report_interval:
        #     pulse = loopingcall.FixedIntervalLoopingCall(self.report_state)
        #     pulse.start(interval=self.report_interval,
        #                 initial_delay=self.report_interval)
        #     self.timers.append(pulse)

        if self.periodic_interval:
            if self.periodic_fuzzy_delay:
                initial_delay = random.randint(0, self.periodic_fuzzy_delay)
            else:
                initial_delay = None

            periodic = loopingcall.FixedIntervalLoopingCall(
                self.periodic_tasks)
            periodic.start(interval=self.periodic_interval,
                           initial_delay=initial_delay)
            self.timers.append(periodic)

    # def _create_service_ref(self, context):
    #     service_args = {
    #         'host': self.host,
    #         'binary': self.binary,
    #         'topic': self.topic,
    #         'report_count': 0,
    #         'availability_zone': self.availability_zone
    #     }
    #     service_ref = db.service_create(context, service_args)
    #     self.service_id = service_ref['id']

    def __getattr__(self, key):
        manager = self.__dict__.get('manager', None)
        return getattr(manager, key)

    @classmethod
    def create(cls, host=None, binary=None, topic=None, manager=None,
               report_interval=None, periodic_interval=None,
               periodic_fuzzy_delay=None, service_name=None,
               coordination=False):
        """Instantiates class and passes back application object.

        :param host: defaults to CONF.host
        :param binary: defaults to basename of executable
        :param topic: defaults to bin_name - 'dolphin-' part
        :param manager: defaults to CONF.<topic>_manager
        :param report_interval: defaults to CONF.report_interval
        :param periodic_interval: defaults to CONF.periodic_interval
        :param periodic_fuzzy_delay: defaults to CONF.periodic_fuzzy_delay

        """
        if not host:
            host = CONF.host
        if not binary:
            binary = os.path.basename(inspect.stack()[-1][1])
        if not topic:
            topic = binary
        if not manager:
            subtopic = topic.rpartition('dolphin-')[2]
            manager = CONF.get('%s_manager' % subtopic, None)
        if report_interval is None:
            report_interval = CONF.report_interval
        if periodic_interval is None:
            periodic_interval = CONF.periodic_interval
        if periodic_fuzzy_delay is None:
            periodic_fuzzy_delay = CONF.periodic_fuzzy_delay
        service_obj = cls(host, binary, topic, manager,
                          report_interval=report_interval,
                          periodic_interval=periodic_interval,
                          periodic_fuzzy_delay=periodic_fuzzy_delay,
                          service_name=service_name,
                          coordination=coordination)

        return service_obj

    def kill(self):
        """Destroy the service object in the datastore."""
        self.stop()
        # try:
        #     db.service_destroy(context.get_admin_context(), self.service_id)
        # except exception.NotFound:
        #     LOG.warning('Service killed that has no database entry.')

    def stop(self):
        # Try to shut the connection down, but if we get any sort of
        # errors, go ahead and ignore them.. as we're shutting down anyway
        try:
            self.rpcserver.stop()
        except Exception:
            pass
        for x in self.timers:
            try:
                x.stop()
            except Exception:
                pass
        # if self.coordinator:
        #     try:
        #         coordination.LOCK_COORDINATOR.stop()
        #     except Exception:
        #         LOG.exception("Unable to stop the Tooz Locking "
        #                       "Coordinator.")

        self.timers = []

        super(Service, self).stop()

    def wait(self):
        for x in self.timers:
            try:
                x.wait()
            except Exception:
                pass

    def periodic_tasks(self, raise_on_error=False):
        """Tasks to be run at a periodic interval."""
        ctxt = context.get_admin_context()
        self.manager.periodic_tasks(ctxt, raise_on_error=raise_on_error)


class WSGIService(service.ServiceBase):
    """Provides ability to launch API from a 'paste' configuration."""

    def __init__(self, name, loader=None):
        """Initialize, but do not start the WSGI server.

        :param name: The name of the WSGI server given to the loader.
        :param loader: Loads the WSGI application using the given name.
        :returns: None

        """
        self.name = name
        self.manager = self._get_manager()
        self.loader = loader or wsgi.Loader(CONF)
        # if not rpc.initialized():
        #     rpc.init(CONF)
        self.app = self.loader.load_app(name)
        self.host = getattr(CONF, '%s_listen' % name, "0.0.0.0")
        self.port = getattr(CONF, '%s_listen_port' % name, 0)
        self.workers = getattr(CONF, '%s_workers' % name, None)
        self.use_ssl = getattr(CONF, '%s_use_ssl' % name, False)
        if self.workers is not None and self.workers < 1:
            LOG.warning(
                "Value of config option %(name)s_workers must be integer "
                "greater than 1.  Input value ignored.", {'name': name})
            # Reset workers to default
            self.workers = None
        self.server = wsgi.Server(
            CONF,
            name,
            self.app,
            host=self.host,
            port=self.port,
            use_ssl=self.use_ssl
        )

    def _get_manager(self):
        """Initialize a Manager object appropriate for this service.

        Use the service name to look up a Manager subclass from the
        configuration and initialize an instance. If no class name
        is configured, just return None.

        :returns: a Manager instance, or None.

        """
        fl = '%s_manager' % self.name
        if fl not in CONF:
            return None

        manager_class_name = CONF.get(fl, None)
        if not manager_class_name:
            return None

        manager_class = importutils.import_class(manager_class_name)
        return manager_class()

    def start(self):
        """Start serving this service using loaded configuration.

        Also, retrieve updated port number in case '0' was passed in, which
        indicates a random port should be used.

        :returns: None

        """
        if self.manager:
            self.manager.init_host()
        self.server.start()
        self.port = self.server.port

    def stop(self):
        """Stop serving this API.

        :returns: None

        """
        self.server.stop()

    def wait(self):
        """Wait for the service to stop serving this API.

        :returns: None

        """
        self.server.wait()

    def reset(self):
        """Reset server greenpool size to default.

        :returns: None
        """
        self.server.reset()


def process_launcher():
    # return service.ProcessLauncher(CONF, restart_method='mutate')
    return service.ServiceLauncher(CONF, restart_method='mutate')


# NOTE(vish): the global launcher is to maintain the existing
#             functionality of calling service.serve +
#             service.wait
_launcher = None


def serve(server, workers=None):
    global _launcher
    if _launcher:
        raise RuntimeError('serve() can only be called once')
    _launcher = service.launch(CONF, server, workers=workers,
                               restart_method='mutate')


def wait():
    CONF.log_opt_values(LOG, log.DEBUG)
    try:
        _launcher.wait()
    except KeyboardInterrupt:
        _launcher.stop()
    rpc.cleanup()