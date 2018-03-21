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

import ddt
import mock
from nova.compute import vm_states
from nova import exception
from nova import test as nova_test
from os_win import constants as os_win_const

from compute_hyperv.nova.cluster import livemigrationops
from compute_hyperv.nova import livemigrationops as base_livemigrationops
from compute_hyperv.tests import fake_instance
from compute_hyperv.tests.unit import test_base


@ddt.ddt
class ClusterLiveMigrationOpsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V Cluster LivemigrationOps class."""

    _autospec_classes = [
        base_livemigrationops.volumeops.VolumeOps,
    ]

    def setUp(self):
        super(ClusterLiveMigrationOpsTestCase, self).setUp()
        self._fake_context = 'fake_context'
        self.livemigrops = livemigrationops.ClusterLiveMigrationOps()
        self._clustutils = self.livemigrops._clustutils

    def test_is_instance_clustered(self):
        ret = self.livemigrops.is_instance_clustered(
            mock.sentinel.instance)

        self.assertEqual(
            self.livemigrops._clustutils.vm_exists.return_value, ret)

    def test_live_migration_in_cluster(self):
        migr_timeout = 10
        self.flags(instance_live_migration_timeout=migr_timeout,
                   group='hyperv')

        mock_instance = fake_instance.fake_instance_obj(self._fake_context)
        self.livemigrops._clustutils.vm_exists.return_value = True
        post_method = mock.MagicMock()
        dest = 'fake_dest'
        node_names = [dest, 'fake_node2']
        get_nodes = self.livemigrops._clustutils.get_cluster_node_names
        get_nodes.return_value = node_names

        self.livemigrops.live_migration(
            self._fake_context, mock_instance, dest, post_method,
            mock.sentinel.recover_method,
            block_migration=mock.sentinel.block_migration,
            migrate_data=mock.sentinel.migrate_data)

        clustutils = self.livemigrops._clustutils
        clustutils.live_migrate_vm.assert_called_once_with(
            mock_instance.name, dest, migr_timeout)
        post_method.assert_called_once_with(
            self._fake_context, mock_instance, dest,
            mock.sentinel.block_migration, mock.sentinel.migrate_data)

    @mock.patch.object(livemigrationops.ClusterLiveMigrationOps,
                       '_check_failed_instance_migration')
    def test_live_migration_in_cluster_exception(self, mock_check_migr):
        mock_instance = fake_instance.fake_instance_obj(self._fake_context)
        self.livemigrops._clustutils.vm_exists.return_value = True
        recover_method = mock.MagicMock()
        dest = 'fake_dest'
        node_names = [dest, 'fake_node2']
        get_nodes = self.livemigrops._clustutils.get_cluster_node_names
        get_nodes.return_value = node_names
        clustutils = self.livemigrops._clustutils
        clustutils.live_migrate_vm.side_effect = nova_test.TestingException

        self.assertRaises(
            nova_test.TestingException,
            self.livemigrops.live_migration,
            self._fake_context, mock_instance, dest, mock.sentinel.post_method,
            recover_method,
            block_migration=mock.sentinel.block_migration,
            migrate_data=mock.sentinel.migrate_data)

        mock_check_migr.assert_called_once_with(
            mock_instance,
            expected_state=os_win_const.CLUSTER_GROUP_ONLINE)

        recover_method.assert_called_once_with(
            self._fake_context, mock_instance, dest,
            mock.sentinel.block_migration,
            mock.sentinel.migrate_data)

    @mock.patch.object(base_livemigrationops.LiveMigrationOps,
                       'live_migration')
    def test_live_migration_outside_cluster(self, mock_super_live_migration):
        mock_instance = fake_instance.fake_instance_obj(self._fake_context)
        self.livemigrops._clustutils.vm_exists.return_value = True
        dest = 'fake_dest'
        node_names = ['fake_node1', 'fake_node2']
        get_nodes = self.livemigrops._clustutils.get_cluster_node_names
        get_nodes.return_value = node_names

        self.livemigrops.live_migration(
            self._fake_context, mock_instance, dest, mock.sentinel.post_method,
            mock.sentinel.recover_method, block_migration=False,
            migrate_data=None)

        mock_super_live_migration.assert_called_once_with(
            self._fake_context, mock_instance, dest, mock.sentinel.post_method,
            mock.sentinel.recover_method, False, None)

    @ddt.data({},
              {'state': os_win_const.CLUSTER_GROUP_PENDING,
               'expected_invalid_state': True},
              {'migration_queued': True,
               'expected_invalid_state': True},
              {'owner_node': 'some_other_node',
               'expected_invalid_state': True})
    @ddt.unpack
    def test_check_failed_instance_migration(
            self, state=os_win_const.CLUSTER_GROUP_ONLINE,
            owner_node='source_node', migration_queued=False,
            expected_invalid_state=False):
        state_info = dict(owner_node=owner_node.upper(),
                          state=state,
                          migration_queued=migration_queued)
        self._clustutils.get_cluster_group_state_info.return_value = (
            state_info)
        self._clustutils.get_node_name.return_value = 'source_node'

        mock_instance = mock.Mock()

        if expected_invalid_state:
            self.assertRaises(
                exception.InstanceInvalidState,
                self.livemigrops._check_failed_instance_migration,
                mock_instance,
                os_win_const.CLUSTER_GROUP_ONLINE)
            self.assertEqual(vm_states.ERROR, mock_instance.vm_state)
        else:
            self.livemigrops._check_failed_instance_migration(
                mock_instance, os_win_const.CLUSTER_GROUP_ONLINE)

        self._clustutils.get_cluster_group_state_info.assert_called_once_with(
            mock_instance.name)
        self._clustutils.get_node_name.assert_called_once_with()

    def test_pre_live_migration_clustered(self):
        self.livemigrops.pre_live_migration(self._fake_context,
                                            mock.sentinel.fake_instance,
                                            mock.sentinel.bdi,
                                            mock.sentinel.network_info)

        fake_conn_vol = self.livemigrops._volumeops.connect_volumes
        fake_conn_vol.assert_called_once_with(mock.sentinel.bdi)

    @mock.patch.object(base_livemigrationops.LiveMigrationOps,
                       'pre_live_migration')
    def test_pre_live_migration_not_clustered(self, mock_pre_live_migration):
        self.livemigrops._clustutils.vm_exists.return_value = False
        self.livemigrops.pre_live_migration(self._fake_context,
                                            mock.sentinel.fake_instance,
                                            mock.sentinel.bdi,
                                            mock.sentinel.network_info)

        mock_pre_live_migration.assert_called_once_with(
            self._fake_context, mock.sentinel.fake_instance,
            mock.sentinel.bdi, mock.sentinel.network_info)

    @mock.patch.object(base_livemigrationops.LiveMigrationOps,
                       'post_live_migration')
    def test_post_live_migration_clustered(self, mock_post_live_migration):
        self.livemigrops.post_live_migration(self._fake_context,
                                             mock.sentinel.fake_instance,
                                             mock.sentinel.bdi,
                                             mock.sentinel.migrate_data)

        self.assertFalse(mock_post_live_migration.called)

    @mock.patch.object(base_livemigrationops.LiveMigrationOps,
                       'post_live_migration')
    def test_post_live_migration_not_clustered(self, mock_post_live_migration):
        self.livemigrops._clustutils.vm_exists.return_value = False
        self.livemigrops.post_live_migration(self._fake_context,
                                             mock.sentinel.fake_instance,
                                             mock.sentinel.bdi,
                                             mock.sentinel.migrate_data)

        mock_post_live_migration.assert_called_once_with(
            self._fake_context, mock.sentinel.fake_instance,
            mock.sentinel.bdi,
            mock.sentinel.migrate_data)
