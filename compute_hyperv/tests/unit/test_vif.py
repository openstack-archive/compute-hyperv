# Copyright 2015 Cloudbase Solutions Srl
#
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

import ddt
import mock
from nova import exception
from nova.network import model
from os_win import constants as os_win_const

import compute_hyperv.nova.conf
from compute_hyperv.nova import vif
from compute_hyperv.tests.unit import test_base


CONF = compute_hyperv.nova.conf.CONF


class HyperVNovaNetworkVIFPluginTestCase(test_base.HyperVBaseTestCase):
    def setUp(self):
        super(HyperVNovaNetworkVIFPluginTestCase, self).setUp()
        self.vif_driver = vif.HyperVNovaNetworkVIFPlugin()

    def test_plug(self):
        self.flags(vswitch_name='fake_vswitch_name', group='hyperv')
        fake_vif = {'id': mock.sentinel.fake_id}

        self.vif_driver.plug(mock.sentinel.instance, fake_vif)
        netutils = self.vif_driver._netutils
        netutils.connect_vnic_to_vswitch.assert_called_once_with(
            'fake_vswitch_name', mock.sentinel.fake_id)


@ddt.ddt
class HyperVVIFDriverTestCase(test_base.HyperVBaseTestCase):
    def setUp(self):
        super(HyperVVIFDriverTestCase, self).setUp()
        self.vif_driver = vif.HyperVVIFDriver()
        self.vif_driver._vif_plugin = mock.MagicMock()

        self._netutils = self.vif_driver._netutils
        self._vmutils = self.vif_driver._vmutils
        self._metricsutils = self.vif_driver._metricsutils

    @mock.patch.object(vif.nova.network, 'is_neutron')
    def test_init_neutron(self, mock_is_neutron):
        mock_is_neutron.return_value = True

        driver = vif.HyperVVIFDriver()
        self.assertIsInstance(driver._vif_plugin, vif.HyperVNeutronVIFPlugin)

    @mock.patch.object(vif.nova.network, 'is_neutron')
    def test_init_nova(self, mock_is_neutron):
        mock_is_neutron.return_value = False

        driver = vif.HyperVVIFDriver()
        self.assertIsInstance(driver._vif_plugin,
                              vif.HyperVNovaNetworkVIFPlugin)

    def test_plug(self):
        vif = {'type': model.VIF_TYPE_HYPERV}
        self.vif_driver.plug(mock.sentinel.instance, vif)

        self.vif_driver._vif_plugin.plug.assert_called_once_with(
            mock.sentinel.instance, vif)

    @mock.patch.object(vif, 'os_vif')
    @mock.patch.object(vif.HyperVVIFDriver, 'enable_metrics')
    @mock.patch.object(vif.os_vif_util, 'nova_to_osvif_instance')
    @mock.patch.object(vif.os_vif_util, 'nova_to_osvif_vif')
    def test_plug_ovs(self, mock_nova_to_osvif_vif,
                      mock_nova_to_osvif_instance,
                      mock_enable_metrics, mock_os_vif):
        self.flags(enable_instance_metrics_collection=True,
                   group='hyperv')

        vif = {'type': model.VIF_TYPE_OVS}
        osvif_instance = mock_nova_to_osvif_instance.return_value
        vif_obj = mock_nova_to_osvif_vif.return_value

        self.vif_driver.plug(mock.sentinel.instance, vif)

        mock_nova_to_osvif_vif.assert_called_once_with(vif)
        mock_nova_to_osvif_instance.assert_called_once_with(
            mock.sentinel.instance)
        connect_vnic = self.vif_driver._netutils.connect_vnic_to_vswitch
        connect_vnic.assert_called_once_with(
            CONF.hyperv.vswitch_name, vif_obj.id)
        mock_os_vif.plug.assert_called_once_with(
            vif_obj, osvif_instance)

        self._netutils.add_metrics_collection_acls.assert_called_once_with(
            vif_obj.id)
        mock_enable_metrics.assert_called_once_with(
            osvif_instance.name, vif_obj.id)

    @ddt.data(True, False)
    def test_enable_metrics(self, vm_running):
        state = (os_win_const.HYPERV_VM_STATE_ENABLED if vm_running
                 else os_win_const.HYPERV_VM_STATE_DISABLED)
        self._vmutils.get_vm_state.return_value = state

        enable_metrics = self._metricsutils.enable_port_metrics_collection

        self.vif_driver.enable_metrics(mock.sentinel.instance_name,
                                       mock.sentinel.vif_id)

        self._vmutils.get_vm_state.assert_called_once_with(
            mock.sentinel.instance_name)
        if vm_running:
            enable_metrics.assert_called_once_with(mock.sentinel.vif_id)
        else:
            enable_metrics.assert_not_called()

    def test_plug_type_unknown(self):
        vif = {'type': mock.sentinel.vif_type}
        self.assertRaises(exception.VirtualInterfacePlugException,
                          self.vif_driver.plug,
                          mock.sentinel.instance, vif)

    def test_unplug(self):
        vif = {'type': model.VIF_TYPE_HYPERV}
        self.vif_driver.unplug(mock.sentinel.instance, vif)

        self.vif_driver._vif_plugin.unplug.assert_called_once_with(
            mock.sentinel.instance, vif)

    @mock.patch.object(vif, 'os_vif')
    @mock.patch.object(vif.os_vif_util, 'nova_to_osvif_instance')
    @mock.patch.object(vif.os_vif_util, 'nova_to_osvif_vif')
    def test_unplug_ovs(self, mock_nova_to_osvif_vif,
                        mock_nova_to_osvif_instance, mock_os_vif):
        vif = {'type': model.VIF_TYPE_OVS}
        self.vif_driver.unplug(mock.sentinel.instance, vif)

        mock_nova_to_osvif_vif.assert_called_once_with(vif)
        mock_nova_to_osvif_instance.assert_called_once_with(
            mock.sentinel.instance)
        mock_os_vif.unplug.assert_called_once_with(
            mock_nova_to_osvif_vif.return_value,
            mock_nova_to_osvif_instance.return_value)

    def test_unplug_type_unknown(self):
        vif = {'type': mock.sentinel.vif_type}
        self.assertRaises(exception.VirtualInterfaceUnplugException,
                          self.vif_driver.unplug,
                          mock.sentinel.instance, vif)
