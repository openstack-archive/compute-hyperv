# Copyright 2014 Cloudbase Solutions Srl
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
Implements necessary OVS helper functions. These functions are similar
to the linux_net ones but have linux specific options, such as run_as_root
and delete_net_dev, removed.
This also allows us the flexibility to adapt to any future platform
specific differences.
"""

import os

from nova import exception
from nova import utils
from oslo_config import cfg
from oslo_log import log as logging

from hyperv.i18n import _LE

LOG = logging.getLogger(__name__)
net_cfg = [
    cfg.IntOpt('ovs_vsctl_timeout',
               default=120,
               help='Amount of time, in seconds, that ovs_vsctl should wait '
                    'for a response from the database. 0 is to wait forever.'),
]
CONF = cfg.CONF
CONF.register_opts(net_cfg)


def _ovs_vsctl(args):
    full_args = ['ovs-vsctl', '--timeout=%s' % CONF.ovs_vsctl_timeout] + args
    try:
        return utils.execute(*full_args)
    except Exception as e:
        LOG.error(_LE("Unable to execute %(cmd)s. Exception: %(exception)s"),
                  {'cmd': full_args, 'exception': e})
        raise exception.AgentError(method=full_args)


def create_ovs_vif_port(bridge, dev, iface_id, mac, instance_id):
    _ovs_vsctl(['--', '--if-exists', 'del-port', dev, '--',
                'add-port', bridge, dev,
                '--', 'set', 'Interface', dev,
                'external-ids:iface-id=%s' % iface_id,
                'external-ids:iface-status=active',
                'external-ids:attached-mac=%s' % mac,
                'external-ids:vm-uuid=%s' % instance_id])


def delete_ovs_vif_port(bridge, dev):
    _ovs_vsctl(['--', '--if-exists', 'del-port', bridge, dev])


def check_bridge_has_dev(bridge, dev, run_as_root=True):
    ports = _ovs_vsctl(['--', 'list-ports', bridge])[0]
    return dev in ports.split(os.linesep)
