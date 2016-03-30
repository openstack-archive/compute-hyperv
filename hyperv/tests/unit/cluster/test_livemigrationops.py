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

import mock
from os_win import exceptions as os_win_exc

from hyperv.nova.cluster import livemigrationops
from hyperv.nova import livemigrationops as base_livemigrationops
from hyperv.tests import fake_instance
from hyperv.tests.unit import test_base


class ClusterLiveMigrationOpsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V Cluster LivemigrationOps class."""

    def setUp(self):
        super(ClusterLiveMigrationOpsTestCase, self).setUp()
        self._fake_context = 'fake_context'
        self.livemigrops = livemigrationops.ClusterLiveMigrationOps()
        self.livemigrops._clustutils = mock.MagicMock()
        self.livemigrops._volumeops = mock.MagicMock()

    def test_is_instance_clustered(self):
        ret = self.livemigrops._is_instance_clustered(
            mock.sentinel.instance)

        self.assertEqual(
            self.livemigrops._clustutils.vm_exists.return_value, ret)

    def test_live_migration_in_cluster(self):
        mock_instance = fake_instance.fake_instance_obj(self._fake_context)
        self.livemigrops._clustutils.vm_exists.return_value = True
        post_method = mock.MagicMock()
        dest = 'fake_dest'
        node_names = [dest, 'fake_node2']
        get_nodes = self.livemigrops._clustutils.get_cluster_node_names
        get_nodes.return_value = node_names

        self.livemigrops.live_migration(
            self._fake_context, mock_instance, dest, post_method,
            mock.sentinel.recover_method, block_migration=False,
            migrate_data=None)

        clustutils = self.livemigrops._clustutils
        clustutils.live_migrate_vm.assert_called_once_with(
            mock_instance.name, dest)
        post_method.assert_called_once_with(
            self._fake_context, mock_instance, dest, False)

    def test_live_migration_in_cluster_exception(self):
        mock_instance = fake_instance.fake_instance_obj(self._fake_context)
        self.livemigrops._clustutils.vm_exists.return_value = True
        recover_method = mock.MagicMock()
        dest = 'fake_dest'
        node_names = [dest, 'fake_node2']
        get_nodes = self.livemigrops._clustutils.get_cluster_node_names
        get_nodes.return_value = node_names
        clustutils = self.livemigrops._clustutils
        clustutils.live_migrate_vm.side_effect = [
            os_win_exc.HyperVVMNotFoundException(mock_instance.name)]

        self.assertRaises(
            os_win_exc.HyperVVMNotFoundException,
            self.livemigrops.live_migration,
            self._fake_context, mock_instance, dest, mock.sentinel.post_method,
            recover_method, block_migration=False, migrate_data=None)

        recover_method.assert_called_once_with(
            self._fake_context, mock_instance, dest, False)

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
                                             mock.sentinel.bdi)

        self.assertFalse(mock_post_live_migration.called)

    @mock.patch.object(base_livemigrationops.LiveMigrationOps,
                       'post_live_migration')
    def test_post_live_migration_not_clustered(self, mock_post_live_migration):
        self.livemigrops._clustutils.vm_exists.return_value = False
        self.livemigrops.post_live_migration(self._fake_context,
                                             mock.sentinel.fake_instance,
                                             mock.sentinel.bdi)

        mock_post_live_migration.assert_called_once_with(
            self._fake_context, mock.sentinel.fake_instance,
            mock.sentinel.bdi)
