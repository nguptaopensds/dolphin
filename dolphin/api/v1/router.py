# Copyright 2011 OpenStack LLC.
# Copyright 2011 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
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

"""
WSGI middleware for OpenStack Share API v1.
"""
from oslo_config import cfg

from dolphin.api import extensions
from dolphin.api import common
from dolphin.registration import storages
from dolphin.api.common import wsgi
from oslo_log import log
import webob
from webob import exc

CONF = cfg.CONF
LOG = log.getLogger(__name__)


class APIRouter(common.APIRouter):
    """Route API requests.

    Routes requests on the OpenStack API to the appropriate controller
    and method.
    """
    ExtensionManager = extensions.ExtensionManager

    def _setup_routes(self, mapper):
        self.resources['storages'] = storages.create_resource()
        mapper.resource("storage", "storages",
                        controller=self.resources['storages'],
                        collection={'detail': 'POST'},
                        member={'action': 'POST'})

        mapper.resource("storage", "storages",
                        controller=self.resources['storages'],
                        collection={'detail': 'GET'},
                        member={'action': 'GET'})