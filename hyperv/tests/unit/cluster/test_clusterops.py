# Copyright 2016 Cloudbase Solutions Srl
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

import time

import mock
from nova.compute import power_state
from nova.compute import task_states
from nova.compute import vm_states
from nova import objects
from os_win import exceptions as os_win_exc

from hyperv.nova.cluster import clusterops
from hyperv.tests import fake_instance
from hyperv.tests.unit import test_base


class ClusterOpsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V ClusterOps class."""

    _FAKE_INSTANCE_NAME = 'fake_instance_name'

    @mock.patch('nova.network.API')
    def setUp(self, mock_network_api):
        super(ClusterOpsTestCase, self).setUp()
        self.context = 'fake_context'

        self.clusterops = clusterops.ClusterOps()
        self.clusterops._context = self.context
        self.clusterops._clustutils = mock.MagicMock()
        self.clusterops._vmutils = mock.MagicMock()
        self.clusterops._network_api = mock.MagicMock()
        self.clusterops._vmops = mock.MagicMock()
        self.clusterops._serial_console_ops = mock.MagicMock()

    def test_get_instance_host(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self.clusterops.get_instance_host(mock_instance)

        self.clusterops._clustutils.get_vm_host.assert_called_once_with(
            mock_instance.name)

    def test_add_to_cluster(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self.clusterops.add_to_cluster(mock_instance)

        mock_add_vm = self.clusterops._clustutils.add_vm_to_cluster
        mock_add_vm.assert_called_once_with(mock_instance.name)
        self.assertEqual(mock_instance.uuid,
                         self.clusterops._instance_map[mock_instance.name])

    @mock.patch.object(clusterops, 'LOG')
    def test_add_to_cluster_exception(self, mock_LOG):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_add_vm = self.clusterops._clustutils.add_vm_to_cluster
        mock_add_vm.side_effect = os_win_exc.HyperVClusterException

        self.clusterops.add_to_cluster(mock_instance)
        self.assertTrue(mock_LOG.exception.called)

    def test_remove_from_cluster(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self.clusterops.remove_from_cluster(mock_instance)

        self.clusterops._clustutils.vm_exists.assert_called_once_with(
            mock_instance.name)
        self.clusterops._clustutils.delete.assert_called_once_with(
            mock_instance.name)
        self.assertIsNone(self.clusterops._instance_map.get(
            mock_instance.name))

    @mock.patch.object(clusterops, 'LOG')
    def test_remove_from_cluster_exception(self, mock_LOG):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_delete = self.clusterops._clustutils.delete
        mock_delete.side_effect = os_win_exc.HyperVClusterException

        self.clusterops.remove_from_cluster(mock_instance)
        self.assertTrue(mock_LOG.exception.called)

    def test_post_migration(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self.clusterops.post_migration(mock_instance)

        self.assertEqual(
            self.clusterops._instance_map[mock_instance.name],
            mock_instance.uuid)

    def test_start_failover_listener_daemon(self):
        self.flags(cluster_event_check_interval=0, group='hyperv')
        self.clusterops._clustutils.monitor_vm_failover.side_effect = (
            os_win_exc.HyperVClusterException)
        self.clusterops.start_failover_listener_daemon()

        # wait for the daemon to do something.
        time.sleep(0.5)
        self.clusterops._clustutils.monitor_vm_failover.assert_called_with(
            self.clusterops._failover_migrate)

    @mock.patch.object(clusterops, 'LOG')
    @mock.patch.object(clusterops.ClusterOps, '_get_instance_by_name')
    def test_failover_migrate_no_instance(self, mock_get_instance_by_name,
                                          mock_LOG):
        mock_get_instance_by_name.return_value = None

        self.clusterops._failover_migrate(mock.sentinel.instance_name,
                                          mock.sentinel.old_host,
                                          mock.sentinel.new_host)

        mock_LOG.debug.assert_called_once_with(
            'Instance %s does not exist in nova. Skipping.',
            mock.sentinel.instance_name)
        self.assertFalse(
            self.clusterops._network_api.get_instance_nw_info.called)

    @mock.patch.object(clusterops, 'LOG')
    @mock.patch.object(clusterops.ClusterOps, '_get_instance_by_name')
    def test_failover_migrate_migrating(self, mock_get_instance_by_name,
                                        mock_LOG):
        instance = mock_get_instance_by_name.return_value
        instance.task_state = task_states.MIGRATING

        self.clusterops._failover_migrate(mock.sentinel.instance_name,
                                          mock.sentinel.old_host,
                                          mock.sentinel.new_host)

        mock_LOG.debug.assert_called_once_with(
            'Instance %s is live migrating.', mock.sentinel.instance_name)

    @mock.patch.object(clusterops.ClusterOps, '_get_instance_by_name')
    def test_failover_migrate_same_host(self, mock_get_instance_by_name):
        instance = mock_get_instance_by_name.return_value
        instance.host = self.clusterops._this_node

        self.clusterops._failover_migrate(mock.sentinel.instance_name,
                                          mock.sentinel.old_host,
                                          mock.sentinel.new_host)

        instance.host.upper.assert_called_with()

    @mock.patch.object(clusterops.ClusterOps, '_get_instance_by_name')
    def test_failover_migrate_at_source_node(self, mock_get_instance_by_name):
        instance = mock_get_instance_by_name.return_value
        old_host = 'old_host'
        self.clusterops._this_node = old_host

        self.clusterops._failover_migrate(mock.sentinel.instance_name,
                                          old_host, mock.sentinel.new_host)

        self.clusterops._vmops.unplug_vifs.assert_called_once_with(instance,
            self.clusterops._network_api.get_instance_nw_info.return_value)

    @mock.patch.object(clusterops, 'LOG')
    @mock.patch.object(clusterops.ClusterOps, '_get_instance_by_name')
    def test_failover_migrate_not_this_node(self, mock_get_instance_by_name,
                                            mock_LOG):
        self.clusterops._this_node = 'new_host'

        self.clusterops._failover_migrate(mock.sentinel.instance_name,
                                          None, 'host')

        mock_LOG.debug.assert_called_once_with(
            'Instance %s did not failover to this node.',
            mock.sentinel.instance_name)

    @mock.patch.object(clusterops.ClusterOps, '_failover_migrate_networks')
    @mock.patch.object(clusterops.ClusterOps, '_nova_failover_server')
    @mock.patch.object(clusterops.ClusterOps, '_get_instance_by_name')
    def test_failover_migrate_this_node(self, mock_get_instance_by_name,
                                        mock_nova_failover_server,
                                        mock_failover_migrate_networks):
        instance = mock_get_instance_by_name.return_value
        old_host = 'old_host'
        new_host = 'new_host'
        self.clusterops._this_node = new_host

        self.clusterops._failover_migrate(mock.sentinel.instance_name,
                                          old_host, new_host)

        mock_get_instance_by_name.assert_called_once_with(
            mock.sentinel.instance_name)
        get_inst_nw_info = self.clusterops._network_api.get_instance_nw_info
        get_inst_nw_info.assert_called_once_with(self.clusterops._context,
                                                 instance)
        mock_nova_failover_server.assert_called_once_with(instance, new_host)
        mock_failover_migrate_networks.assert_called_once_with(
            instance, old_host)
        self.clusterops._vmops.post_start_vifs.assert_called_once_with(
            instance, get_inst_nw_info.return_value)
        c_handler = self.clusterops._serial_console_ops.start_console_handler
        c_handler.assert_called_once_with(mock.sentinel.instance_name)

    def test_failover_migrate_networks(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        fake_source = mock.MagicMock()
        fake_migration = {'source_compute': fake_source,
                          'dest_compute': self.clusterops._this_node}

        self.clusterops._failover_migrate_networks(mock_instance,
                                                    fake_source)

        mock_network_api = self.clusterops._network_api
        calls = [mock.call(self.clusterops._context, mock_instance,
                           self.clusterops._this_node),
                 mock.call(self.clusterops._context, mock_instance,
                           self.clusterops._this_node),
                 mock.call(self.clusterops._context, mock_instance,
                           self.clusterops._this_node),
                 mock.call(self.clusterops._context, mock_instance,
                           fake_source, teardown=True)]
        mock_network_api.setup_networks_on_host.assert_has_calls(calls)
        mock_network_api.migrate_instance_start.assert_called_once_with(
            self.clusterops._context, mock_instance, fake_migration)
        mock_network_api.migrate_instance_finish.assert_called_once_with(
            self.clusterops._context, mock_instance, fake_migration)

    @mock.patch.object(objects.Instance, 'get_by_uuid')
    def test_get_instance_by_name(self, mock_get_by_uuid):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_get_by_uuid.return_value = mock_instance
        self.clusterops._instance_map[mock_instance.name] = mock_instance.uuid

        ret = self.clusterops._get_instance_by_name(mock_instance.name)
        self.assertEqual(ret, mock_instance)

    @mock.patch.object(objects.Instance, 'get_by_uuid')
    def test_get_instance_by_name_not_in_cache(self, mock_get_by_uuid):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self.clusterops._vmutils.get_instance_uuid.return_value = (
            mock_instance.uuid)
        mock_get_by_uuid.return_value = mock_instance

        ret = self.clusterops._get_instance_by_name(mock_instance.name)
        self.assertEqual(ret, mock_instance)
        self.assertEqual(mock_instance.uuid,
                         self.clusterops._instance_map[mock_instance.name])

    @mock.patch.object(objects.Instance, 'get_by_uuid')
    def test_get_instance_by_name_not_update_map(self, mock_get_by_uuid):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self.clusterops._vmutils.get_instance_uuid.side_effect = (
            os_win_exc.HyperVVMNotFoundException(vm_name=mock_instance.name))
        self.clusterops._update_instance_map = mock.MagicMock()
        self.clusterops._instance_map = mock.MagicMock()
        self.clusterops._instance_map.get.side_effect = [None,
                                                          mock_instance.uuid]
        mock_get_by_uuid.return_value = mock_instance

        ret = self.clusterops._get_instance_by_name(mock_instance.name)
        self.assertEqual(ret, mock_instance)
        self.clusterops._update_instance_map.assert_called_with()

    @mock.patch.object(clusterops.objects.InstanceList, 'get_by_filters')
    def test_update_instance_map(self, mock_get_by_filters):
        mock_instance = mock.MagicMock(uuid=mock.sentinel.uuid)
        mock_instance.configure_mock(name=mock.sentinel.name)
        mock_get_by_filters.return_value = [mock_instance]

        self.clusterops._update_instance_map()

        expected_attrs = ['id', 'uuid', 'name']
        mock_get_by_filters.assert_called_once_with(
            self.clusterops._context, {'deleted': False},
            expected_attrs=expected_attrs)
        self.assertEqual(mock.sentinel.uuid,
                         self.clusterops._instance_map[mock.sentinel.name])

    @mock.patch.object(clusterops.block_device, 'DriverVolumeBlockDevice')
    @mock.patch.object(clusterops.objects.BlockDeviceMappingList,
                       'get_by_instance_uuid')
    def test_get_instance_block_device_mappings(self, mock_get_by_uuid,
                                                mock_DriverVBD):
        mock_get_by_uuid.return_value = [mock.sentinel.bdm]
        mock_instance = mock.MagicMock()

        bdms = self.clusterops._get_instance_block_device_mappings(
            mock_instance)

        self.assertEqual([mock_DriverVBD.return_value], bdms)
        mock_get_by_uuid.assert_called_once_with(self.clusterops._context,
                                                 mock_instance.uuid)
        mock_DriverVBD.assert_called_once_with(mock.sentinel.bdm)

    def test_nova_failover_server(self):
        mock_instance = mock.MagicMock(vm_state=vm_states.ERROR,
                                       power_state=power_state.NOSTATE)

        self.clusterops._nova_failover_server(mock_instance,
                                               mock.sentinel.host)

        self.assertEqual(vm_states.ACTIVE, mock_instance.vm_state)
        self.assertEqual(power_state.RUNNING, mock_instance.power_state)
        self.assertEqual(mock.sentinel.host, mock_instance.host)
        self.assertEqual(mock.sentinel.host, mock_instance.node)
        mock_instance.save.assert_called_once_with(expected_task_state=[None])
