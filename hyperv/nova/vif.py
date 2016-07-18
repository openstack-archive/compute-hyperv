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

import nova.conf
from nova.network import model as network_model
from os_win import utilsfactory

from hyperv.nova import ovsutils

CONF = nova.conf.CONF


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
        self._netutils = utilsfactory.get_networkutils()

    def plug(self, instance, vif):
        self._netutils.connect_vnic_to_vswitch(CONF.hyperv.vswitch_name,
                                               vif['id'])


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


def get_vif_driver(vif_type):
    # results should be cached. Creating a global driver map
    # with instantiated classes will cause tests to fail on
    # non windows platforms
    if vif_type == network_model.VIF_TYPE_OVS:
        return HyperVOVSVIFDriver()

    if nova.network.is_neutron():
        return HyperVNeutronVIFDriver()
    else:
        return HyperVNovaNetworkVIFDriver()
