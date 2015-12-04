# Copyright 2013 Cloudbase Solutions Srl
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

from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import importutils
import six

from hyperv.i18n import _
from hyperv.nova import hostutils
from hyperv.nova import vmutils

CONF = cfg.CONF

LOG = logging.getLogger(__name__)
CONF.import_group('hyperv', 'os_win.utilsfactory')

utils = hostutils.HostUtils()

class_utils = {
    'hostutils': {'HostUtilsV2': {'min_version': 6.2, 'max_version': None}},
    'vmutils': {'VMUtilsV2': {'min_version': 6.2, 'max_version': 10},
                'VMUtils10': {'min_version': 10, 'max_version': None}},
}


def _get_class(utils_class_type):
    if utils_class_type not in class_utils:
        raise vmutils.HyperVException(_("Class %(class)s does not exist")
                                      % utils_class_type)

    windows_version = utils.get_windows_version()
    build = list(map(int, windows_version.split('.')))
    windows_version = float("%i.%i" % (build[0], build[1]))

    existing_classes = class_utils.get(utils_class_type)
    for class_variant in six.iterkeys(existing_classes):
        version = existing_classes.get(class_variant)
        if (version['min_version'] <= windows_version and
                (version['max_version'] is None or
                 windows_version < version['max_version'])):
            module_name = class_variant.lower()
            path = 'hyperv.nova.%(module)s.%(class)s' % {
                   'module': module_name, 'class': class_variant}
            return importutils.import_object(path)

    raise vmutils.HyperVException(_('Class %(class)s is not found '
        'for windows version: %(win_version)s')
        % {'class': utils_class_type, 'win_version': windows_version})


def get_vmutils(host='.'):
    return _get_class(utils_class_type='vmutils')


def get_hostutils():
    return _get_class(utils_class_type='hostutils')
