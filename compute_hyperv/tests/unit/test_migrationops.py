#  Copyright 2014 IBM Corp.
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

import os

import ddt
import mock
from nova import block_device
from nova import exception
from nova.virt import driver
from os_win import exceptions as os_win_exc
from oslo_utils import units

from compute_hyperv.nova import constants
from compute_hyperv.nova import migrationops
from compute_hyperv.tests import fake_instance
from compute_hyperv.tests.unit import test_base


@ddt.ddt
class MigrationOpsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V MigrationOps class."""

    _autospec_classes = [
        migrationops.pathutils.PathUtils,
        migrationops.volumeops.VolumeOps,
        migrationops.vmops.VMOps,
        migrationops.imagecache.ImageCache,
        migrationops.block_device_manager.BlockDeviceInfoManager,
    ]

    _FAKE_DISK = 'fake_disk'
    _FAKE_TIMEOUT = 10
    _FAKE_RETRY_INTERVAL = 5

    def setUp(self):
        super(MigrationOpsTestCase, self).setUp()
        self.context = 'fake-context'

        self._migrationops = migrationops.MigrationOps()
        self._vmops = self._migrationops._vmops
        self._vmutils = self._migrationops._vmutils
        self._pathutils = self._migrationops._pathutils
        self._vhdutils = self._migrationops._vhdutils
        self._volumeops = self._migrationops._volumeops
        self._imagecache = self._migrationops._imagecache
        self._block_dev_man = self._migrationops._block_dev_man

    def test_move_vm_files(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        vm_files_path = self._migrationops._move_vm_files(mock_instance)

        mock_get_inst_dir = self._migrationops._pathutils.get_instance_dir
        mock_get_inst_dir.assert_called_once_with(mock_instance.name)
        mock_get_revert_dir = (
            self._migrationops._pathutils.get_instance_migr_revert_dir)
        mock_get_revert_dir.assert_called_once_with(
            mock_get_inst_dir.return_value, remove_dir=True, create_dir=True)
        mock_get_export_dir = self._migrationops._pathutils.get_export_dir
        mock_get_export_dir.assert_called_once_with(
            instance_dir=mock_get_revert_dir.return_value, create_dir=True)

        mock_move = self._migrationops._pathutils.move_folder_files
        mock_move.assert_called_once_with(mock_get_inst_dir.return_value,
                                          mock_get_revert_dir.return_value)
        copy_config_files = self._migrationops._pathutils.copy_vm_config_files
        copy_config_files.assert_called_once_with(
            mock_instance.name, mock_get_export_dir.return_value)
        self.assertEqual(mock_get_revert_dir.return_value, vm_files_path)

    @ddt.data({},
              {'ephemerals_size': 2},
              {'ephemerals_size': 3, 'flavor_eph_size': 0},
              {'ephemerals_size': 3, 'expect_invalid_flavor': True},
              {'current_root_gb': 3, 'expect_invalid_flavor': True},
              {'current_root_gb': 3, 'boot_from_vol': True})
    @ddt.unpack
    @mock.patch.object(driver, 'block_device_info_get_ephemerals')
    @mock.patch.object(block_device, 'get_bdm_ephemeral_disk_size')
    def test_check_target_flavor(self, mock_get_eph_size, mock_get_eph,
                                 ephemerals_size=0,
                                 flavor_eph_size=2,
                                 flavor_root_gb=2,
                                 current_root_gb=1,
                                 boot_from_vol=False,
                                 expect_invalid_flavor=False):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.flavor.root_gb = current_root_gb
        mock_flavor = mock.MagicMock(root_gb=flavor_root_gb,
                                     ephemeral_gb=flavor_eph_size)

        mock_get_eph_size.return_value = ephemerals_size
        self._block_dev_man.is_boot_from_volume.return_value = boot_from_vol

        if expect_invalid_flavor:
            self.assertRaises(exception.InstanceFaultRollback,
                              self._migrationops._check_target_flavor,
                              mock_instance, mock_flavor,
                              mock.sentinel.block_device_info)
        else:
            self._migrationops._check_target_flavor(
                mock_instance, mock_flavor, mock.sentinel.block_device_info)

        mock_get_eph.assert_called_once_with(mock.sentinel.block_device_info)
        mock_get_eph_size.assert_called_once_with(mock_get_eph.return_value)
        self._block_dev_man.is_boot_from_volume.assert_called_once_with(
            mock.sentinel.block_device_info)

    def test_check_and_attach_config_drive(self):
        mock_instance = fake_instance.fake_instance_obj(
            self.context, expected_attrs=['system_metadata'])
        mock_instance.config_drive = 'True'

        self._migrationops._check_and_attach_config_drive(
            mock_instance, mock.sentinel.vm_gen)

        self._migrationops._vmops.attach_config_drive.assert_called_once_with(
            mock_instance,
            self._migrationops._pathutils.lookup_configdrive_path.return_value,
            mock.sentinel.vm_gen)

    def test_check_and_attach_config_drive_unknown_path(self):
        instance = fake_instance.fake_instance_obj(
            self.context, expected_attrs=['system_metadata'])
        instance.config_drive = 'True'
        self._migrationops._pathutils.lookup_configdrive_path.return_value = (
            None)
        self.assertRaises(exception.ConfigDriveNotFound,
                          self._migrationops._check_and_attach_config_drive,
                          instance,
                          mock.sentinel.FAKE_VM_GEN)

    @mock.patch.object(migrationops.MigrationOps, '_move_vm_files')
    @mock.patch.object(migrationops.MigrationOps, '_check_target_flavor')
    def test_migrate_disk_and_power_off(self, mock_check_flavor,
                                        mock_move_vm_files):
        instance = mock.MagicMock()
        instance.system_metadata = {}
        flavor = mock.MagicMock()
        network_info = mock.MagicMock()

        disk_info = self._migrationops.migrate_disk_and_power_off(
            self.context, instance, mock.sentinel.FAKE_DEST, flavor,
            network_info, mock.sentinel.bdi,
            self._FAKE_TIMEOUT, self._FAKE_RETRY_INTERVAL)

        self.assertEqual(mock_move_vm_files.return_value, disk_info)
        mock_check_flavor.assert_called_once_with(
            instance, flavor, mock.sentinel.bdi)
        self._migrationops._vmops.power_off.assert_called_once_with(
            instance, self._FAKE_TIMEOUT, self._FAKE_RETRY_INTERVAL)
        mock_move_vm_files.assert_called_once_with(instance)
        self.assertEqual(mock_move_vm_files.return_value,
                         instance.system_metadata['backup_location'])
        instance.save.assert_called_once_with()
        self._migrationops._vmops.destroy.assert_called_once_with(
            instance, network_info, mock.sentinel.bdi, destroy_disks=True,
            cleanup_migration_files=False)

    def test_confirm_migration(self):
        mock_instance = fake_instance.fake_instance_obj(
            self.context, expected_attrs=['system_metadata'])
        fake_path_revert = 'fake_path_revert'
        mock_instance.system_metadata['backup_location'] = fake_path_revert

        self._migrationops.confirm_migration(
            context=self.context,
            migration=mock.sentinel.migration, instance=mock_instance,
            network_info=mock.sentinel.network_info)

        get_export_dir = self._migrationops._pathutils.get_export_dir
        get_export_dir.assert_called_once_with(instance_dir=fake_path_revert)
        self._migrationops._pathutils.check_dir.assert_has_calls([
            mock.call(get_export_dir.return_value, remove_dir=True),
            mock.call(fake_path_revert, remove_dir=True)])

    def test_revert_migration_files(self):
        mock_instance = fake_instance.fake_instance_obj(
            self.context, expected_attrs=['system_metadata'])
        fake_path_revert = 'fake_path_revert'
        mock_instance.system_metadata['backup_location'] = fake_path_revert

        instance_path = self._migrationops._revert_migration_files(
            mock_instance)

        expected_instance_path = fake_path_revert.rstrip('_revert')
        self.assertEqual(expected_instance_path, instance_path)
        self._migrationops._pathutils.rename.assert_called_once_with(
            fake_path_revert, expected_instance_path)

    @mock.patch.object(migrationops.MigrationOps, '_import_and_setup_vm')
    @mock.patch.object(migrationops.MigrationOps, '_revert_migration_files')
    def test_finish_revert_migration(self, mock_revert_migration_files,
                                     mock_import_and_setup_vm):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        self._migrationops.finish_revert_migration(
            context=self.context, instance=mock_instance,
            network_info=mock.sentinel.network_info,
            block_device_info=mock.sentinel.block_device_info,
            power_on=True)

        mock_revert_migration_files.assert_called_once_with(
            mock_instance)
        image_meta = self._imagecache.get_image_details.return_value
        mock_import_and_setup_vm.assert_called_once_with(
            self.context, mock_instance,
            mock_revert_migration_files.return_value,
            image_meta, mock.sentinel.block_device_info)
        self._migrationops._vmops.power_on.assert_called_once_with(
            mock_instance, network_info=mock.sentinel.network_info)

    def test_merge_base_vhd(self):
        fake_diff_vhd_path = 'fake/diff/path'
        fake_base_vhd_path = 'fake/base/path'
        base_vhd_copy_path = os.path.join(
            os.path.dirname(fake_diff_vhd_path),
            os.path.basename(fake_base_vhd_path))

        self._migrationops._merge_base_vhd(diff_vhd_path=fake_diff_vhd_path,
                                           base_vhd_path=fake_base_vhd_path)

        self._migrationops._pathutils.copyfile.assert_called_once_with(
            fake_base_vhd_path, base_vhd_copy_path)
        recon_parent_vhd = self._migrationops._vhdutils.reconnect_parent_vhd
        recon_parent_vhd.assert_called_once_with(fake_diff_vhd_path,
                                                 base_vhd_copy_path)
        self._migrationops._vhdutils.merge_vhd.assert_called_once_with(
            fake_diff_vhd_path)
        self._migrationops._pathutils.rename.assert_called_once_with(
            base_vhd_copy_path, fake_diff_vhd_path)

    def test_merge_base_vhd_exception(self):
        fake_diff_vhd_path = 'fake/diff/path'
        fake_base_vhd_path = 'fake/base/path'
        base_vhd_copy_path = os.path.join(
            os.path.dirname(fake_diff_vhd_path),
            os.path.basename(fake_base_vhd_path))

        self._migrationops._vhdutils.reconnect_parent_vhd.side_effect = (
            os_win_exc.HyperVException)
        self._migrationops._pathutils.exists.return_value = True

        self.assertRaises(os_win_exc.HyperVException,
                          self._migrationops._merge_base_vhd,
                          fake_diff_vhd_path, fake_base_vhd_path)
        self._migrationops._pathutils.exists.assert_called_once_with(
            base_vhd_copy_path)
        self._migrationops._pathutils.remove.assert_called_once_with(
            base_vhd_copy_path)

    @mock.patch.object(migrationops.MigrationOps, '_resize_vhd')
    def test_check_resize_vhd(self, mock_resize_vhd):
        self._migrationops._check_resize_vhd(
            vhd_path=mock.sentinel.vhd_path, vhd_info={'VirtualSize': 1},
            new_size=2)
        mock_resize_vhd.assert_called_once_with(mock.sentinel.vhd_path, 2)

    def test_check_resize_vhd_exception(self):
        self.assertRaises(exception.CannotResizeDisk,
                          self._migrationops._check_resize_vhd,
                          mock.sentinel.vhd_path,
                          {'VirtualSize': 1}, 0)

    @mock.patch.object(migrationops.MigrationOps, '_merge_base_vhd')
    def test_resize_vhd(self, mock_merge_base_vhd):
        fake_vhd_path = 'fake/path.vhd'
        new_vhd_size = 2
        self._migrationops._resize_vhd(vhd_path=fake_vhd_path,
                                       new_size=new_vhd_size)

        get_vhd_parent_path = self._migrationops._vhdutils.get_vhd_parent_path
        get_vhd_parent_path.assert_called_once_with(fake_vhd_path)
        mock_merge_base_vhd.assert_called_once_with(
            fake_vhd_path,
            self._migrationops._vhdutils.get_vhd_parent_path.return_value)
        self._migrationops._vhdutils.resize_vhd.assert_called_once_with(
            fake_vhd_path, new_vhd_size)

    def test_check_base_disk(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        fake_src_vhd_path = 'fake/src/path'
        fake_base_vhd = 'fake/vhd'
        get_cached_image = self._migrationops._imagecache.get_cached_image
        get_cached_image.return_value = fake_base_vhd

        self._migrationops._check_base_disk(
            context=self.context, instance=mock_instance,
            diff_vhd_path=mock.sentinel.diff_vhd_path,
            src_base_disk_path=fake_src_vhd_path)

        get_cached_image.assert_called_once_with(self.context, mock_instance)
        recon_parent_vhd = self._migrationops._vhdutils.reconnect_parent_vhd
        recon_parent_vhd.assert_called_once_with(
            mock.sentinel.diff_vhd_path, fake_base_vhd)

    @ddt.data((False, '\\\\fake-srv\\C$\\inst_dir_0000000e_revert', True),
              (False, '\\\\fake-srv\\share_path\\inst_dir_0000000e_revert'),
              (True, 'C:\\fake_inst_dir_0000000e_revert'))
    @ddt.unpack
    def test_migrate_disks_from_source(self, move_disks_on_migration,
                                       source_inst_dir, is_remote_path=False):
        self.flags(move_disks_on_cold_migration=move_disks_on_migration,
                   group='hyperv')
        mock_migration = mock.MagicMock()
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_get_remote_path = self._migrationops._pathutils.get_remote_path
        mock_get_remote_path.return_value = source_inst_dir

        mock_get_export_dir = self._migrationops._pathutils.get_export_dir
        mock_get_export_dir.side_effect = [mock.sentinel.source_export_dir,
                                           mock.sentinel.dest_export_dir]

        instance_dir = self._migrationops._migrate_disks_from_source(
            mock_migration, mock_instance, mock.sentinel.source_dir)

        mock_get_remote_path.assert_called_once_with(
            mock_migration.source_compute, mock.sentinel.source_dir)

        if move_disks_on_migration or is_remote_path:
            mock_get_inst_dir = self._migrationops._pathutils.get_instance_dir
            mock_get_inst_dir.assert_called_once_with(
                mock_instance.name, create_dir=True, remove_dir=True)
            expected_inst_dir = mock_get_inst_dir.return_value
        else:
            expected_inst_dir = source_inst_dir[0: - len('_revert')]
            self._migrationops._pathutils.check_dir.assert_called_once_with(
                expected_inst_dir, create_dir=True)

        mock_get_export_dir.assert_has_calls([
            mock.call(instance_dir=mock_get_remote_path.return_value),
            mock.call(instance_dir=expected_inst_dir)])

        mock_copy = self._migrationops._pathutils.copy_folder_files
        mock_copy.assert_called_once_with(mock_get_remote_path.return_value,
                                          expected_inst_dir)
        self._migrationops._pathutils.copy_dir.assert_called_once_with(
            mock.sentinel.source_export_dir, mock.sentinel.dest_export_dir)
        self.assertEqual(expected_inst_dir, instance_dir)

    @mock.patch.object(migrationops.MigrationOps, '_import_and_setup_vm')
    @mock.patch.object(migrationops.MigrationOps, '_migrate_disks_from_source')
    def test_finish_migration(self, mock_migrate_disks_from_source,
                              mock_import_and_setup_vm):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_migration = mock.MagicMock()

        self._migrationops.finish_migration(
            context=self.context, migration=mock_migration,
            instance=mock_instance, disk_info=mock.sentinel.disk_info,
            network_info=mock.sentinel.network_info,
            image_meta=mock.sentinel.image_meta, resize_instance=False,
            block_device_info=mock.sentinel.block_device_info)

        mock_migrate_disks_from_source.assert_called_once_with(
            mock_migration, mock_instance, mock.sentinel.disk_info)
        mock_import_and_setup_vm.assert_called_once_with(
            self.context, mock_instance,
            mock_migrate_disks_from_source.return_value,
            mock.sentinel.image_meta, mock.sentinel.block_device_info, True)
        self._vmops.power_on.assert_called_once_with(
            mock_instance, network_info=mock.sentinel.network_info)

    @mock.patch.object(migrationops.MigrationOps, '_check_ephemeral_disks')
    @mock.patch.object(migrationops.MigrationOps, '_check_and_update_disks')
    @mock.patch.object(migrationops.MigrationOps, '_update_disk_image_paths')
    @mock.patch.object(migrationops.MigrationOps, '_import_vm')
    def test_import_and_setup_vm(self, mock_import_vm,
                                 mock_update_disk_image_paths,
                                 mock_check_and_update_disks,
                                 mock_check_eph_disks):
        block_device_info = {'ephemerals': mock.sentinel.ephemerals}
        mock_instance = fake_instance.fake_instance_obj(self.context)

        self._migrationops._import_and_setup_vm(
            self.context, mock_instance, mock.sentinel.instance_dir,
            mock.sentinel.image_meta, block_device_info,
            resize_instance=mock.sentinel.resize_instance)

        get_image_vm_gen = self._vmops.get_image_vm_generation
        get_image_vm_gen.assert_called_once_with(mock_instance.uuid,
                                                 mock.sentinel.image_meta)
        mock_import_vm.assert_called_once_with(mock.sentinel.instance_dir)
        self._migrationops._vmops.update_vm_resources.assert_called_once_with(
            mock_instance, get_image_vm_gen.return_value,
            mock.sentinel.image_meta, mock.sentinel.instance_dir,
            mock.sentinel.resize_instance)
        self._migrationops._volumeops.connect_volumes.assert_called_once_with(
            block_device_info)
        mock_update_disk_image_paths.assert_called_once_with(
            mock_instance, mock.sentinel.instance_dir)
        mock_check_and_update_disks.assert_called_once_with(
            self.context, mock_instance, get_image_vm_gen.return_value,
            mock.sentinel.image_meta, block_device_info,
            resize_instance=mock.sentinel.resize_instance)
        self._volumeops.fix_instance_volume_disk_paths.assert_called_once_with(
            mock_instance.name, block_device_info)
        self._migrationops._migrationutils.realize_vm.assert_called_once_with(
            mock_instance.name)
        mock_check_eph_disks.assert_called_once_with(
            mock_instance, mock.sentinel.ephemerals,
            mock.sentinel.resize_instance)
        self._migrationops._vmops.configure_remotefx.assert_called_once_with(
            mock_instance, get_image_vm_gen.return_value,
            mock.sentinel.resize_instance)
        self._vmops.configure_instance_metrics.assert_called_once_with(
            mock_instance.name)

    def test_import_vm(self):
        self._migrationops._import_vm(mock.sentinel.instance_dir)

        self._pathutils.get_instance_snapshot_dir.assert_called_once_with(
            instance_dir=mock.sentinel.instance_dir)
        self._pathutils.get_vm_config_file.assert_called_once_with(
            self._migrationops._pathutils.get_export_dir.return_value)
        mock_import_vm_definition = (
            self._migrationops._migrationutils.import_vm_definition)
        mock_import_vm_definition.assert_called_once_with(
            self._pathutils.get_vm_config_file.return_value,
            self._pathutils.get_instance_snapshot_dir.return_value)
        self._migrationops._pathutils.get_export_dir.assert_has_calls([
            mock.call(instance_dir=mock.sentinel.instance_dir),
            mock.call(instance_dir=mock.sentinel.instance_dir,
                      remove_dir=True)])

    @mock.patch('os.path.exists')
    def test_update_disk_image_paths(self, mock_exists):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        inst_dir = "instances"
        expected_inst_dir = "expected_instances"
        config_drive_iso = os.path.join(inst_dir, 'configdrive.iso')
        expected_config_drive_iso = os.path.join(expected_inst_dir,
                                                 'configdrive.iso')
        ephemeral_disk = os.path.join(inst_dir, 'eph1.vhdx')
        expected_ephemeral_disk = os.path.join(expected_inst_dir, 'eph1.vhdx')
        other_disk = '//some/path/to/vol-UUID.vhdx'
        disk_files = [config_drive_iso, ephemeral_disk, other_disk]

        self._vmutils.get_vm_storage_paths.return_value = (
            disk_files, mock.sentinel.volume_drives)
        mock_exists.return_value = True

        self._migrationops._update_disk_image_paths(mock_instance,
                                                    expected_inst_dir)

        self._vmutils.get_vm_storage_paths.assert_called_once_with(
            mock_instance.name)
        expected_calls = [
            mock.call(config_drive_iso, expected_config_drive_iso,
                      is_physical=False),
            mock.call(ephemeral_disk, expected_ephemeral_disk,
                      is_physical=False)]
        self._vmutils.update_vm_disk_path.assert_has_calls(expected_calls)

    @mock.patch('os.path.exists')
    def test_update_disk_image_paths_exception(self, mock_exists):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        inst_dir = "instances"
        disk_files = [os.path.join(inst_dir, "root.vhdx")]

        self._vmutils.get_vm_storage_paths.return_value = (
            disk_files, mock.sentinel.volume_drives)
        self._pathutils.get_instance_dir.return_value = inst_dir
        mock_exists.return_value = False

        self.assertRaises(exception.DiskNotFound,
                          self._migrationops._update_disk_image_paths,
                          mock_instance, inst_dir)

        self._vmutils.get_vm_storage_paths.assert_called_once_with(
            mock_instance.name)
        self.assertFalse(self._vmutils.update_vm_disk_path.called)

    @ddt.data(constants.DISK, mock.sentinel.root_type)
    @mock.patch.object(migrationops.MigrationOps, '_check_base_disk')
    @mock.patch.object(migrationops.MigrationOps, '_check_resize_vhd')
    def test_check_and_update_disks(self, root_type,
                                    mock_check_resize_vhd,
                                    mock_check_base_disk):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.flavor.root_gb = 1
        root_device = {'type': root_type}
        block_device_info = {'root_disk': root_device,
                             'ephemerals': mock.sentinel.ephemerals}
        expected_check_resize = []
        expected_get_info = []

        self._migrationops._check_and_update_disks(
            self.context, mock_instance, mock.sentinel.vm_gen,
            mock.sentinel.image_meta, block_device_info, resize_instance=True)

        mock_bdi = self._block_dev_man.validate_and_update_bdi
        mock_bdi.assert_called_once_with(
            mock_instance, mock.sentinel.image_meta, mock.sentinel.vm_gen,
            block_device_info)

        if root_device['type'] == constants.DISK:
            root_device_path = (
                self._pathutils.lookup_root_vhd_path.return_value)
            self._pathutils.lookup_root_vhd_path.assert_called_once_with(
                mock_instance.name)
            expected_get_info = [mock.call(root_device_path)]

            mock_vhd_info = self._vhdutils.get_vhd_info.return_value
            mock_vhd_info.get.assert_called_once_with("ParentPath")
            mock_check_base_disk.assert_called_once_with(
                self.context, mock_instance, root_device_path,
                mock_vhd_info.get.return_value)
            expected_check_resize.append(
                mock.call(root_device_path, mock_vhd_info,
                          mock_instance.flavor.root_gb * units.Gi))
        else:
            self.assertFalse(self._pathutils.lookup_root_vhd_path.called)

        mock_check_resize_vhd.assert_has_calls(expected_check_resize)
        self._vhdutils.get_vhd_info.assert_has_calls(
            expected_get_info)

    def test_check_and_update_disks_not_found(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        root_device = {'type': constants.DISK}
        block_device_info = {'root_disk': root_device}

        self._pathutils.lookup_root_vhd_path.return_value = None

        self.assertRaises(exception.DiskNotFound,
                          self._migrationops._check_and_update_disks,
                          self.context, mock_instance, mock.sentinel.vm_gen,
                          mock.sentinel.image_meta, block_device_info,
                          resize_instance=True)

        self._pathutils.get_instance_dir.assert_called_once_with(
            mock_instance.name)

    @mock.patch.object(migrationops.MigrationOps, '_check_resize_vhd')
    @mock.patch.object(migrationops.LOG, 'warning')
    def test_check_ephemeral_disks_multiple_eph_warn(self, mock_warn,
                                                     mock_check_resize_vhd):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.ephemeral_gb = 3
        mock_ephemerals = [{'size': 1}, {'size': 1}]

        self._migrationops._check_ephemeral_disks(mock_instance,
                                                  mock_ephemerals,
                                                  True)

        mock_warn.assert_called_once_with(
            "Cannot resize multiple ephemeral disks for instance.",
            instance=mock_instance)

    def test_check_ephemeral_disks_exception(self):
        mock_instance = fake_instance.fake_instance_obj(self.context,
                                                        ephemeral_gb=1)
        mock_ephemerals = [dict(size=1)]

        lookup_eph_path = (
            self._migrationops._pathutils.lookup_ephemeral_vhd_path)
        lookup_eph_path.return_value = None

        self.assertRaises(exception.DiskNotFound,
                          self._migrationops._check_ephemeral_disks,
                          mock_instance, mock_ephemerals)

    @ddt.data({},
              {'existing_eph_path': mock.sentinel.eph_path},
              {'existing_eph_path': mock.sentinel.eph_path,
               'new_eph_size': 0},
              {'use_default_eph': True})
    @ddt.unpack
    @mock.patch.object(migrationops.MigrationOps, '_check_resize_vhd')
    def test_check_ephemeral_disks(self, mock_check_resize_vhd,
                                   existing_eph_path=None, new_eph_size=42,
                                   use_default_eph=False):
        mock_vmops = self._migrationops._vmops

        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.ephemeral_gb = new_eph_size
        eph = {}
        mock_ephemerals = [eph] if not use_default_eph else []

        mock_pathutils = self._migrationops._pathutils
        lookup_eph_path = mock_pathutils.lookup_ephemeral_vhd_path
        lookup_eph_path.return_value = existing_eph_path
        mock_get_eph_vhd_path = mock_pathutils.get_ephemeral_vhd_path
        mock_get_eph_vhd_path.return_value = mock.sentinel.get_path

        mock_vhdutils = self._migrationops._vhdutils
        mock_get_vhd_format = mock_vhdutils.get_best_supported_vhd_format
        mock_get_vhd_format.return_value = mock.sentinel.vhd_format

        self._vmutils.get_free_controller_slot.return_value = (
            mock.sentinel.ctrl_slot)

        attached_eph_paths = [mock.sentinel.eph_path,
                              mock.sentinel.default_eph_path]
        mock_vmops.get_attached_ephemeral_disks.return_value = (
            attached_eph_paths)

        self._migrationops._check_ephemeral_disks(mock_instance,
                                                  mock_ephemerals,
                                                  True)

        if not use_default_eph:
            self.assertEqual(mock_instance.ephemeral_gb, eph['size'])
        if not existing_eph_path:
            mock_vmops.create_ephemeral_disk.assert_called_once_with(
                mock_instance.name, mock.ANY)
            self._vmutils.get_vm_scsi_controller.assert_called_once_with(
                mock_instance.name)
            self._vmutils.get_free_controller_slot.assert_called_once_with(
                self._vmutils.get_vm_scsi_controller.return_value)

            create_eph_args = mock_vmops.create_ephemeral_disk.call_args_list
            created_eph = create_eph_args[0][0][1]
            self.assertEqual(mock.sentinel.vhd_format, created_eph['format'])
            self.assertEqual(mock.sentinel.get_path, created_eph['path'])
            self.assertEqual(constants.CTRL_TYPE_SCSI,
                             created_eph['disk_bus'])
            self.assertEqual(mock.sentinel.ctrl_slot,
                             created_eph['ctrl_disk_addr'])
        elif new_eph_size:
            mock_check_resize_vhd.assert_called_once_with(
                existing_eph_path,
                self._migrationops._vhdutils.get_vhd_info.return_value,
                mock_instance.ephemeral_gb * units.Gi)
            self.assertEqual(existing_eph_path, eph['path'])
        else:
            self._vmutils.detach_vm_disk.assert_has_calls(
                [mock.call(mock_instance.name, eph_path,
                          is_physical=False)
                 for eph_path in attached_eph_paths],
                any_order=True)
            self._migrationops._pathutils.remove.assert_has_calls(
                [mock.call(eph_path) for eph_path in attached_eph_paths],
                any_order=True)
