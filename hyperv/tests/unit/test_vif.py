# Copyright 2014 Cloudbase Solutions SRL
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
Unit tests for the Hyper-V vif module.
"""

import mock

from nova.network import model as network_model
from nova.tests.unit.objects import test_virtual_interface
from oslo_config import cfg

from hyperv.nova import vif
from hyperv.tests import fake_instance
from hyperv.tests.unit import test_base

CONF = cfg.CONF


class GetVIFDriverTestCase(test_base.HyperVBaseTestCase):

    def _test_get_vif_driver(self, expected_driver, vif_type,
                             network_class='nova.network.api.API',
                             expected_exception=None):
        self.flags(network_api_class=network_class)
        if expected_exception:
            self.assertRaises(expected_exception,
                              vif.get_vif_driver,
                              vif_type)
        else:
            actual_class = type(vif.get_vif_driver(vif_type))
            self.assertEqual(expected_driver, actual_class)

    def test_get_vif_driver_neutron(self):
        self._test_get_vif_driver(
            expected_driver=vif.HyperVNeutronVIFDriver,
            vif_type=network_model.VIF_TYPE_OTHER,
            network_class='nova.network.neutronv2.api.API')

    def test_get_vif_driver_nova(self):
        self._test_get_vif_driver(
            expected_driver=vif.HyperVNovaNetworkVIFDriver,
            vif_type=network_model.VIF_TYPE_OTHER,
            network_class='nova.network.api.API')

    def test_get_vif_driver_ovs(self):
        self._test_get_vif_driver(expected_driver=vif.HyperVOVSVIFDriver,
                                  vif_type=network_model.VIF_TYPE_OVS)

    def test_get_vif_driver_invalid_class(self):
        self._test_get_vif_driver(
            expected_driver=None,
            vif_type=network_model.VIF_TYPE_OTHER,
            network_class='fake.driver',
            expected_exception=TypeError)


class HyperVOVSVIFDriverTestCase(test_base.HyperVBaseTestCase):

    def setUp(self):
        super(HyperVOVSVIFDriverTestCase, self).setUp()

        self.context = 'fake-context'
        self.instance = fake_instance.fake_instance_obj(self.context)
        self._vif = vif.HyperVOVSVIFDriver()
        self._vif._vmutils = mock.MagicMock()
        self._vif._netutils = mock.MagicMock()

        self._fake_vif = dict(test_virtual_interface.fake_vif,
                              network={'bridge': 'fake_bridge'})

    def test_plug(self):
        mock_get_external_vswitch = self._vif._netutils.get_external_vswitch
        mock_set_nic_connection = self._vif._vmutils.set_nic_connection
        self._vif._netutils.vswitch_port_needed.return_value = False

        self._vif.plug(self.instance, self._fake_vif)

        mock_set_nic_connection.assert_called_once_with(
            self.instance.name,
            self._fake_vif['id'],
            mock_get_external_vswitch())

    @mock.patch('nova.utils.execute')
    @mock.patch('hyperv.nova.ovsutils.check_bridge_has_dev')
    def _test_post_start(self, mock_check_bridge_has_dev, mock_execute, calls,
                         bridge_has_dev=True):
        mock_check_bridge_has_dev.return_value = bridge_has_dev
        self._vif.post_start(self.instance, self._fake_vif)
        mock_execute.assert_has_calls(calls)

    def test_post_start_no_dev(self, bridge_has_dev=False):
        calls = [
              mock.call('ovs-vsctl', '--timeout=120', '--', '--if-exists',
                        'del-port', self._fake_vif['id'],
                        '--', 'add-port',
                        self._fake_vif['network']['bridge'],
                        self._fake_vif['id'], '--', 'set', 'Interface',
                        self._fake_vif['id'],
                        'external-ids:iface-id=%s' % self._fake_vif['id'],
                        'external-ids:iface-status=active',
                        'external-ids:attached-mac=%s' %
                        self._fake_vif['address'],
                        'external-ids:vm-uuid=%s' % self.instance.uuid)
          ]
        self._test_post_start(calls=calls, bridge_has_dev=bridge_has_dev)

    def test_post_start(self):
        self._test_post_start(calls=[])

    @mock.patch('nova.utils.execute')
    def test_unplug(self, mock_execute):
        calls = [
            mock.call(
                'ovs-vsctl', '--timeout=120', '--',
                '--if-exists', 'del-port',
                self._fake_vif['network']['bridge'],
                self._fake_vif['id'])
        ]

        self._vif.unplug(self.instance, self._fake_vif)
        mock_execute.assert_has_calls(calls)
