# Copyright 2013 Cloudbase Solutions Srl
# Copyright 2013 Pedro Navarro Perez
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

import abc

from nova.i18n import _
from nova.network import model as network_model
from oslo_config import cfg
from oslo_log import log as logging

from hyperv.nova import ovsutils
from hyperv.nova import utilsfactory


hyperv_opts = [
    cfg.StrOpt('vswitch_name',
               help='External virtual switch Name, '
                    'if not provided, the first external virtual '
                    'switch is used'),
]

CONF = cfg.CONF
CONF.register_opts(hyperv_opts, 'hyperv')
CONF.import_opt('network_api_class', 'nova.network')

LOG = logging.getLogger(__name__)


class HyperVBaseVIFDriver(object):
    @abc.abstractmethod
    def plug(self, instance, vif):
        pass

    @abc.abstractmethod
    def post_start(self, instance, vif):
        pass

    @abc.abstractmethod
    def unplug(self, instance, vif):
        pass


class HyperVNeutronVIFDriver(HyperVBaseVIFDriver):
    """Neutron VIF driver."""
    pass


class HyperVNovaNetworkVIFDriver(HyperVBaseVIFDriver):
    """Nova network VIF driver."""

    def __init__(self):
        self._vmutils = utilsfactory.get_vmutils()
        self._netutils = utilsfactory.get_networkutils()

    def plug(self, instance, vif):
        vswitch_path = self._netutils.get_external_vswitch(
            CONF.hyperv.vswitch_name)

        vm_name = instance.name
        LOG.debug('Creating vswitch port for instance: %s', vm_name)
        if self._netutils.vswitch_port_needed():
            vswitch_data = self._netutils.create_vswitch_port(vswitch_path,
                                                              vm_name)
        else:
            vswitch_data = vswitch_path

        self._vmutils.set_nic_connection(vm_name, vif['id'], vswitch_data)


class HyperVOVSVIFDriver(HyperVNovaNetworkVIFDriver):

    def _get_bridge_name(self, vif):
        return vif['network']['bridge']

    def _get_ovs_interfaceid(self, vif):
        return vif.get('ovs_interfaceid') or vif['id']

    def post_start(self, instance, vif):
        nic_name = vif['id']
        bridge = self._get_bridge_name(vif)
        if ovsutils.check_bridge_has_dev(bridge, nic_name,
                                          run_as_root=False):
            return

        ovsutils.create_ovs_vif_port(
            self._get_bridge_name(vif),
            nic_name,
            self._get_ovs_interfaceid(vif),
            vif['address'],
            instance.uuid)

    def unplug(self, instance, vif):
        ovsutils.delete_ovs_vif_port(
            self._get_bridge_name(vif),
            vif['id'])


_vif_driver_class_map = {
    'nova.network.neutronv2.api.API': HyperVNeutronVIFDriver,
    'nova.network.api.API': HyperVNovaNetworkVIFDriver,
}
_ovs_vif_driver = HyperVOVSVIFDriver


def get_vif_driver(vif_type):
    # results should be cached. Creating a global driver map
    # with instantiated classes will cause tests to fail on
    # non windows platforms
    if vif_type == network_model.VIF_TYPE_OVS:
        return _ovs_vif_driver()

    try:
        return _vif_driver_class_map[CONF.network_api_class]()
    except KeyError:
        raise TypeError(_("VIF driver not found for "
                          "network_api_class: %(api_class)s, %(vif_type)s") %
                        {"api_class": CONF.network_api_class,
                         "vif_type": vif_type})
