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

from nova import exception
from nova.i18n import _
import nova.network
from nova.network import model
from nova.network import os_vif_util
import os_vif
from os_win import constants as os_win_const
from os_win import utilsfactory
from oslo_log import log as logging

import compute_hyperv.nova.conf

LOG = logging.getLogger(__name__)
CONF = compute_hyperv.nova.conf.CONF


class HyperVBaseVIFPlugin(object):
    @abc.abstractmethod
    def plug(self, instance, vif):
        pass

    @abc.abstractmethod
    def unplug(self, instance, vif):
        pass


class HyperVNeutronVIFPlugin(HyperVBaseVIFPlugin):
    """Neutron VIF plugin."""

    def plug(self, instance, vif):
        # Neutron takes care of plugging the port
        pass

    def unplug(self, instance, vif):
        # Neutron takes care of unplugging the port
        pass


class HyperVNovaNetworkVIFPlugin(HyperVBaseVIFPlugin):
    """Nova network VIF plugin."""

    def __init__(self):
        self._netutils = utilsfactory.get_networkutils()

    def plug(self, instance, vif):
        self._netutils.connect_vnic_to_vswitch(CONF.hyperv.vswitch_name,
                                               vif['id'])

    def unplug(self, instance, vif):
        # TODO(alepilotti) Not implemented
        pass


class HyperVVIFDriver(object):
    def __init__(self):
        self._metricsutils = utilsfactory.get_metricsutils()
        self._netutils = utilsfactory.get_networkutils()
        self._vmutils = utilsfactory.get_vmutils()
        if nova.network.is_neutron():
            self._vif_plugin = HyperVNeutronVIFPlugin()
        else:
            self._vif_plugin = HyperVNovaNetworkVIFPlugin()

    def plug(self, instance, vif):
        vif_type = vif['type']
        if vif_type == model.VIF_TYPE_HYPERV:
            self._vif_plugin.plug(instance, vif)
        elif vif_type == model.VIF_TYPE_OVS:
            vif = os_vif_util.nova_to_osvif_vif(vif)
            instance = os_vif_util.nova_to_osvif_instance(instance)

            # NOTE(claudiub): the vNIC has to be connected to a vSwitch
            # before the ovs port is created.
            self._netutils.connect_vnic_to_vswitch(CONF.hyperv.vswitch_name,
                                                   vif.id)
            if CONF.hyperv.enable_instance_metrics_collection:
                self._netutils.add_metrics_collection_acls(vif.id)
                self.enable_metrics(instance.name, vif.id)

            os_vif.plug(vif, instance)
        else:
            reason = _("Failed to plug virtual interface: "
                       "unexpected vif_type=%s") % vif_type
            raise exception.VirtualInterfacePlugException(reason)

    def unplug(self, instance, vif):
        vif_type = vif['type']
        if vif_type == model.VIF_TYPE_HYPERV:
            self._vif_plugin.unplug(instance, vif)
        elif vif_type == model.VIF_TYPE_OVS:
            vif = os_vif_util.nova_to_osvif_vif(vif)
            instance = os_vif_util.nova_to_osvif_instance(instance)
            os_vif.unplug(vif, instance)
        else:
            reason = _("unexpected vif_type=%s") % vif_type
            raise exception.VirtualInterfaceUnplugException(reason=reason)

    def enable_metrics(self, instance_name, vif_id):
        # Hyper-V's metric collection API is extremely inconsistent.
        # As opposed to other metrics, network metrics have to be enabled
        # whenever the vm starts. Attempting to do so while the vm is shut off
        # will fail. Also, this option gets reset when the vm is rebooted.
        #
        # Note that meter ACLs must already be set on the specified port.
        # For "hyperv" ports, this is handled by networking-hyperv, while
        # for OVS ports, we're doing it on the Nova side.
        vm_state = self._vmutils.get_vm_state(instance_name)
        if vm_state in [os_win_const.HYPERV_VM_STATE_ENABLED,
                        os_win_const.HYPERV_VM_STATE_PAUSED]:
            LOG.debug("Enabling instance port metrics. "
                      "Instance name: %(instance_name)s. "
                      "Port name: %(port_name)s.",
                      dict(instance_name=instance_name,
                           port_name=vif_id))
            self._metricsutils.enable_port_metrics_collection(vif_id)
        else:
            LOG.debug("Instance %s is not running. Port metrics will "
                      "be enabled when the instance starts.",
                      instance_name)
