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

import ddt
import mock
from nova import exception
from nova.objects import migrate_data as migrate_data_obj
from os_win import exceptions as os_win_exc

import compute_hyperv.nova.conf
from compute_hyperv.nova import livemigrationops
from compute_hyperv.tests import fake_instance
from compute_hyperv.tests.unit import test_base

CONF = compute_hyperv.nova.conf.CONF


@ddt.ddt
class LiveMigrationOpsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V LiveMigrationOps class."""

    _autospec_classes = [
        livemigrationops.pathutils.PathUtils,
        livemigrationops.vmops.VMOps,
        livemigrationops.volumeops.VolumeOps,
        livemigrationops.serialconsoleops.SerialConsoleOps,
        livemigrationops.imagecache.ImageCache,
        livemigrationops.block_device_manager.BlockDeviceInfoManager,
    ]

    def setUp(self):
        super(LiveMigrationOpsTestCase, self).setUp()
        self.context = 'fake_context'
        self._livemigrops = livemigrationops.LiveMigrationOps()
        self._pathutils = self._livemigrops._pathutils
        self._vmops = self._livemigrops._vmops

    def _test_live_migration(self, side_effect=None,
                             shared_storage=False,
                             migrate_data_received=True,
                             migrate_data_version='1.1'):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_post = mock.MagicMock()
        mock_recover = mock.MagicMock()

        mock_copy_dvd_disks = self._livemigrops._vmops.copy_vm_dvd_disks
        mock_stop_console_handler = (
            self._livemigrops._serial_console_ops.stop_console_handler)
        mock_copy_logs = self._livemigrops._pathutils.copy_vm_console_logs
        fake_dest = mock.sentinel.DESTINATION
        mock_check_shared_inst_dir = (
            self._pathutils.check_remote_instances_dir_shared)
        mock_check_shared_inst_dir.return_value = shared_storage
        self._livemigrops._livemigrutils.live_migrate_vm.side_effect = [
            side_effect]

        if migrate_data_received:
            migrate_data = migrate_data_obj.HyperVLiveMigrateData()
            if migrate_data_version != '1.0':
                migrate_data.is_shared_instance_path = shared_storage
        else:
            migrate_data = None

        if side_effect is os_win_exc.HyperVException:
            self.assertRaises(os_win_exc.HyperVException,
                              self._livemigrops.live_migration,
                              self.context, mock_instance, fake_dest,
                              mock_post, mock_recover,
                              mock.sentinel.block_migr,
                              migrate_data)
            mock_recover.assert_called_once_with(self.context, mock_instance,
                                                 fake_dest,
                                                 migrate_data)
        else:
            self._livemigrops.live_migration(context=self.context,
                                             instance_ref=mock_instance,
                                             dest=fake_dest,
                                             post_method=mock_post,
                                             recover_method=mock_recover,
                                             block_migration=(
                                                mock.sentinel.block_migr),
                                             migrate_data=migrate_data)
            post_call_args = mock_post.call_args_list
            self.assertEqual(1, len(post_call_args))

            post_call_args_list = post_call_args[0][0]
            self.assertEqual((self.context, mock_instance,
                              fake_dest, mock.sentinel.block_migr),
                             post_call_args_list[:-1])
            # The last argument, the migrate_data object, should be created
            # by the callee if not received.
            migrate_data_arg = post_call_args_list[-1]
            self.assertIsInstance(
                migrate_data_arg,
                migrate_data_obj.HyperVLiveMigrateData)
            self.assertEqual(shared_storage,
                             migrate_data_arg.is_shared_instance_path)

        if not migrate_data_received or migrate_data_version == '1.0':
            mock_check_shared_inst_dir.assert_called_once_with(fake_dest)
        else:
            self.assertFalse(mock_check_shared_inst_dir.called)

        mock_stop_console_handler.assert_called_once_with(mock_instance.name)

        if not shared_storage:
            mock_copy_logs.assert_called_once_with(mock_instance.name,
                                                   fake_dest)
            mock_copy_dvd_disks.assert_called_once_with(mock_instance.name,
                                                        fake_dest)
        else:
            self.assertFalse(mock_copy_logs.called)
            self.assertFalse(mock_copy_dvd_disks.called)

        mock_live_migr = self._livemigrops._livemigrutils.live_migrate_vm
        mock_live_migr.assert_called_once_with(
            mock_instance.name,
            fake_dest,
            migrate_disks=not shared_storage)

    def test_live_migration(self):
        self._test_live_migration(migrate_data_received=False)

    def test_live_migration_old_migrate_data_version(self):
        self._test_live_migration(migrate_data_version='1.0')

    def test_live_migration_exception(self):
        self._test_live_migration(side_effect=os_win_exc.HyperVException)

    def test_live_migration_shared_storage(self):
        self._test_live_migration(shared_storage=True)

    def _test_pre_live_migration(self, phys_disks_attached=True):
        mock_get_disk_path_mapping = (
            self._livemigrops._volumeops.get_disk_path_mapping)
        mock_get_cached_image = self._livemigrops._imagecache.get_cached_image

        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.image_ref = "fake_image_ref"
        mock_get_disk_path_mapping.return_value = (
            mock.sentinel.disk_path_mapping if phys_disks_attached
            else None)
        bdman = self._livemigrops._block_dev_man
        mock_is_boot_from_vol = bdman.is_boot_from_volume
        mock_is_boot_from_vol.return_value = None
        CONF.set_override('use_cow_images', True)
        self._livemigrops.pre_live_migration(
            self.context, mock_instance,
            block_device_info=mock.sentinel.BLOCK_INFO,
            network_info=mock.sentinel.NET_INFO)

        check_config = (
            self._livemigrops._livemigrutils.check_live_migration_config)
        check_config.assert_called_once_with()
        mock_is_boot_from_vol.assert_called_once_with(
            mock.sentinel.BLOCK_INFO)
        mock_get_cached_image.assert_called_once_with(self.context,
                                                      mock_instance)
        self._livemigrops._volumeops.connect_volumes.assert_called_once_with(
            mock.sentinel.BLOCK_INFO)
        mock_get_disk_path_mapping.assert_called_once_with(
            mock.sentinel.BLOCK_INFO, block_dev_only=True)
        if phys_disks_attached:
            livemigrutils = self._livemigrops._livemigrutils
            livemigrutils.create_planned_vm.assert_called_once_with(
                mock_instance.name,
                mock_instance.host,
                mock.sentinel.disk_path_mapping)

    def test_pre_live_migration(self):
        self._test_pre_live_migration()

    def test_pre_live_migration_invalid_disk_mapping(self):
        self._test_pre_live_migration(phys_disks_attached=False)

    def _test_post_live_migration(self, shared_storage=False):
        migrate_data = migrate_data_obj.HyperVLiveMigrateData(
            is_shared_instance_path=shared_storage)

        self._livemigrops.post_live_migration(
            self.context, mock.sentinel.instance,
            mock.sentinel.block_device_info,
            migrate_data)
        mock_disconnect_volumes = (
            self._livemigrops._volumeops.disconnect_volumes)
        mock_disconnect_volumes.assert_called_once_with(
            mock.sentinel.block_device_info)
        mock_get_inst_dir = self._pathutils.get_instance_dir

        if not shared_storage:
            mock_get_inst_dir.assert_called_once_with(
                mock.sentinel.instance.name,
                create_dir=False, remove_dir=True)
        else:
            self.assertFalse(mock_get_inst_dir.called)

    def test_post_block_migration(self):
        self._test_post_live_migration()

    def test_post_live_migration_shared_storage(self):
        self._test_post_live_migration(shared_storage=True)

    @mock.patch.object(migrate_data_obj, 'HyperVLiveMigrateData')
    def test_check_can_live_migrate_destination(self, mock_migr_data_cls):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        migr_data = self._livemigrops.check_can_live_migrate_destination(
            mock.sentinel.context, mock_instance, mock.sentinel.src_comp_info,
            mock.sentinel.dest_comp_info)

        mock_check_shared_inst_dir = (
            self._pathutils.check_remote_instances_dir_shared)
        mock_check_shared_inst_dir.assert_called_once_with(mock_instance.host)

        self.assertEqual(mock_migr_data_cls.return_value, migr_data)
        self.assertEqual(mock_check_shared_inst_dir.return_value,
                         migr_data.is_shared_instance_path)

    def test_check_can_live_migrate_destination_exception(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_check_shared_inst_dir = (
            self._pathutils.check_remote_instances_dir_shared)
        mock_check_shared_inst_dir.side_effect = OSError

        self.assertRaises(
            exception.MigrationPreCheckError,
            self._livemigrops.check_can_live_migrate_destination,
            mock.sentinel.context, mock_instance, mock.sentinel.src_comp_info,
            mock.sentinel.dest_comp_info)

    def test_post_live_migration_at_destination(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        self._livemigrops.post_live_migration_at_destination(
            self.context, mock_instance,
            network_info=mock.sentinel.NET_INFO,
            block_migration=mock.sentinel.BLOCK_INFO)
        self._livemigrops._vmops.plug_vifs.assert_called_once_with(
            mock_instance, mock.sentinel.NET_INFO)
        self._vmops.configure_instance_metrics.assert_called_once_with(
            mock_instance.name)
