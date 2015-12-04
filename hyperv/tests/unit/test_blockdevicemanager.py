# Copyright (c) 2015 Cloudbase Solutions Srl
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
from nova import exception

from hyperv.nova import block_device_manager
from hyperv.nova import constants
from hyperv.tests.unit import test_base


class BlockDeviceManagerTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V BlockDeviceInfoManager class."""

    def setUp(self):
        super(BlockDeviceManagerTestCase, self).setUp()
        self._bdman = block_device_manager.BlockDeviceInfoManager()

    @mock.patch('nova.virt.configdrive.required_by')
    def _test_init_controller_slot_counter(self, mock_cfg_drive_req,
                                           vm_gen, configdrive=True):
        mock_cfg_drive_req.return_value = configdrive
        slot_map = self._bdman._initialize_controller_slot_counter(
            mock.sentinel.FAKE_INSTANCE, vm_gen)
        if vm_gen == constants.VM_GEN_1:
            self.assertEqual(slot_map[constants.CTRL_TYPE_IDE][0],
                             constants.IDE_CONTROLLER_SLOTS_NUMBER)
            self.assertEqual(slot_map[constants.CTRL_TYPE_SCSI][0],
                             constants.SCSI_CONTROLLER_SLOTS_NUMBER)
            if configdrive:
                self.assertEqual(slot_map[constants.CTRL_TYPE_IDE][1],
                                 constants.IDE_CONTROLLER_SLOTS_NUMBER - 1)
            else:
                self.assertEqual(slot_map[constants.CTRL_TYPE_IDE][1],
                                 constants.IDE_CONTROLLER_SLOTS_NUMBER)
        else:
            if configdrive:
                self.assertEqual(slot_map[constants.CTRL_TYPE_SCSI][0],
                                 constants.SCSI_CONTROLLER_SLOTS_NUMBER - 1)
            else:
                self.assertEqual(slot_map[constants.CTRL_TYPE_SCSI][0],
                                 constants.SCSI_CONTROLLER_SLOTS_NUMBER)

    def test_init_controller_slot_counter_gen1(self):
        self._test_init_controller_slot_counter(vm_gen=constants.VM_GEN_1)

    def test_init_controller_slot_counter_gen1_no_configdrive(self):
        self._test_init_controller_slot_counter(vm_gen=constants.VM_GEN_1,
                                                configdrive=False)

    def test_init_controller_slot_counter_gen2(self):
        self._test_init_controller_slot_counter(vm_gen=constants.VM_GEN_2)

    def test_init_controller_slot_counter_gen2_no_configdrive(self):
        self._test_init_controller_slot_counter(vm_gen=constants.VM_GEN_2,
                                                configdrive=False)

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_initialize_controller_slot_counter')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_check_and_update_root_device')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_check_and_update_ephemerals')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_check_and_update_volumes')
    def test_validate_and_update_bdi(self, mock_check_and_update_vol,
                                     mock_check_and_update_eph,
                                     mock_check_and_update_root,
                                     mock_init_ctrl_cntr):
        mock_init_ctrl_cntr.return_value = mock.sentinel.FAKE_SLOT_MAP

        self._bdman.validate_and_update_bdi(mock.sentinel.FAKE_INSTANCE,
                                            mock.sentinel.IMAGE_META,
                                            mock.sentinel.VM_GEN,
                                            mock.sentinel.BLOCK_DEV_INFO)

        mock_init_ctrl_cntr.assert_called_once_with(
            mock.sentinel.FAKE_INSTANCE, mock.sentinel.VM_GEN)
        mock_check_and_update_root.assert_called_once_with(
            mock.sentinel.VM_GEN, mock.sentinel.IMAGE_META,
            mock.sentinel.BLOCK_DEV_INFO, mock.sentinel.FAKE_SLOT_MAP)
        mock_check_and_update_eph.assert_called_once_with(
            mock.sentinel.VM_GEN, mock.sentinel.BLOCK_DEV_INFO,
            mock.sentinel.FAKE_SLOT_MAP)
        mock_check_and_update_vol.assert_called_once_with(
            mock.sentinel.VM_GEN, mock.sentinel.BLOCK_DEV_INFO,
            mock.sentinel.FAKE_SLOT_MAP)

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_get_available_controller_slot')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       'is_boot_from_volume')
    def _test_check_and_update_root_device(self, mock_is_boot_from_vol,
                                           mock_get_avail_ctrl_slot,
                                           disk_format,
                                           vm_gen=constants.VM_GEN_1,
                                           fail=False,
                                           boot_from_volume=False):
        image_meta = {'disk_format': disk_format}
        bdi = {'root_device': '/dev/sda',
               'block_device_mapping': [
                    {'mount_device': '/dev/sda',
                     'connection_info': mock.sentinel.FAKE_CONN_INFO}]}

        mock_is_boot_from_vol.return_value = boot_from_volume
        mock_get_avail_ctrl_slot.return_value = (0, 0)
        if fail:
            self.assertRaises(exception.InvalidDiskFormat,
                              self._bdman._check_and_update_root_device,
                              vm_gen, image_meta, bdi,
                              mock.sentinel.SLOT_MAP)
        else:
            self._bdman._check_and_update_root_device(vm_gen, image_meta, bdi,
                                                      mock.sentinel.SLOT_MAP)
            root_disk = bdi['root_disk']
            if boot_from_volume:
                self.assertEqual(root_disk['type'], constants.VOLUME)
                self.assertIsNone(root_disk['path'])
                self.assertEqual(root_disk['connection_info'],
                                 mock.sentinel.FAKE_CONN_INFO)
            else:
                image_type = self._bdman._TYPE_FOR_DISK_FORMAT.get(
                    image_meta['disk_format'])
                self.assertEqual(root_disk['type'], image_type)
                self.assertIsNone(root_disk['path'])
                self.assertIsNone(root_disk['connection_info'])
            disk_bus = (constants.CTRL_TYPE_IDE if
                vm_gen == constants.VM_GEN_1 else constants.CTRL_TYPE_SCSI)
            self.assertEqual(root_disk['disk_bus'], disk_bus)
            self.assertEqual(root_disk['drive_addr'], 0)
            self.assertEqual(root_disk['ctrl_disk_addr'], 0)
            self.assertEqual(root_disk['boot_index'], 0)
            self.assertEqual(root_disk['mount_device'], bdi['root_device'])
            mock_get_avail_ctrl_slot.assert_called_once_with(
                root_disk['disk_bus'], mock.sentinel.SLOT_MAP)

    def test_check_and_update_root_device_exception(self):
        self._test_check_and_update_root_device(disk_format='fake_format',
                                                fail=True)

    def test_check_and_update_root_device_gen1(self):
        self._test_check_and_update_root_device(disk_format='vhd')

    def test_check_and_update_root_device_gen1_iso(self):
        self._test_check_and_update_root_device(disk_format='iso')

    def test_check_and_update_root_device_gen2(self):
        self._test_check_and_update_root_device(disk_format='vhd',
                                                vm_gen=constants.VM_GEN_2)

    def test_check_and_update_root_device_boot_from_vol_gen1(self):
        self._test_check_and_update_root_device(disk_format='vhd',
                                                boot_from_volume=True)

    def test_check_and_update_root_device_boot_from_vol_gen2(self):
        self._test_check_and_update_root_device(disk_format='vhd',
                                                vm_gen=constants.VM_GEN_2,
                                                boot_from_volume=True)

    @mock.patch('nova.virt.configdrive.required_by', return_value=True)
    def _test_get_available_controller_slot(self, mock_config_drive_req,
                                            bus=constants.CTRL_TYPE_IDE,
                                            fail=False):

        slot_map = self._bdman._initialize_controller_slot_counter(
            mock.sentinel.FAKE_VM, constants.VM_GEN_1)

        if fail:
            slot_map[constants.CTRL_TYPE_IDE][0] = 0
            slot_map[constants.CTRL_TYPE_IDE][1] = 0
            self.assertRaises(exception.Invalid,
                              self._bdman._get_available_controller_slot,
                              constants.CTRL_TYPE_IDE,
                              slot_map)
        else:
            (disk_addr,
             ctrl_disk_addr) = self._bdman._get_available_controller_slot(
                bus, slot_map)

            self.assertEqual(0, disk_addr)
            self.assertEqual(0, ctrl_disk_addr)

    def test_get_available_controller_slot(self):
        self._test_get_available_controller_slot()

    def test_get_available_controller_slot_exception(self):
        self._test_get_available_controller_slot(fail=True)

    def test_get_available_controller_slot_scsi_ctrl(self):
        self._test_get_available_controller_slot(bus=constants.CTRL_TYPE_SCSI)

    def test_is_boot_from_volume_true(self):
        vol = {'mount_device': self._bdman._DEFAULT_ROOT_DEVICE}
        block_device_info = {'block_device_mapping': [vol]}
        ret = self._bdman.is_boot_from_volume(block_device_info)

        self.assertTrue(ret)

    def test_is_boot_from_volume_false(self):
        block_device_info = {'block_device_mapping': []}
        ret = self._bdman.is_boot_from_volume(block_device_info)

        self.assertFalse(ret)

    def test_get_root_device_bdm(self):
        mount_device = '/dev/sda'
        bdm1 = {'mount_device': None}
        bdm2 = {'mount_device': mount_device}
        bdi = {'block_device_mapping': [bdm1, bdm2]}

        ret = self._bdman._get_root_device_bdm(bdi, mount_device)

        self.assertEqual(ret, bdm2)

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_check_and_update_bdm')
    def test_check_and_update_ephemerals(self, mock_check_and_update_bdm):
        fake_ephemerals = [mock.sentinel.eph1, mock.sentinel.eph2,
                           mock.sentinel.eph3]
        fake_bdi = {'ephemerals': fake_ephemerals}
        expected_calls = []
        for eph in fake_ephemerals:
            expected_calls.append(mock.call(mock.sentinel.fake_slot_map,
                                            mock.sentinel.fake_vm_gen,
                                            eph))
        self._bdman._check_and_update_ephemerals(mock.sentinel.fake_vm_gen,
                                                 fake_bdi,
                                                 mock.sentinel.fake_slot_map)
        mock_check_and_update_bdm.assert_has_calls(expected_calls)

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_check_and_update_bdm')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_get_root_device_bdm')
    def test_check_and_update_volumes(self, mock_get_root_dev_bdm,
                                      mock_check_and_update_bdm):
        fake_vol1 = {'mount_device': '/dev/sda'}
        fake_vol2 = {'mount_device': '/dev/sdb'}
        fake_volumes = [fake_vol1, fake_vol2]
        fake_bdi = {'block_device_mapping': fake_volumes,
                    'root_disk': {'mount_device': '/dev/sda'}}
        mock_get_root_dev_bdm.return_value = fake_vol1

        self._bdman._check_and_update_volumes(mock.sentinel.fake_vm_gen,
                                              fake_bdi,
                                              mock.sentinel.fake_slot_map)

        mock_get_root_dev_bdm.assert_called_once_with(fake_bdi, '/dev/sda')
        mock_check_and_update_bdm.assert_called_once_with(
            mock.sentinel.fake_slot_map, mock.sentinel.fake_vm_gen, fake_vol2)
        self.assertNotIn(fake_vol1, fake_bdi)

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_get_available_controller_slot')
    def _test_check_and_update_bdm(self, mock_get_ctrl_slot,
                                   bdm, fail=False,
                                   vm_gen=constants.VM_GEN_1,
                                   slot_map=None):
        mock_get_ctrl_slot.return_value = ((mock.sentinel.DRIVE_ADDR,
                                            mock.sentinel.CTRL_DISK_ADDR))
        if fail:
            self.assertRaises(exception.InvalidDiskInfo,
                              self._bdman._check_and_update_bdm,
                              slot_map, vm_gen, bdm)
        else:
            self._bdman._check_and_update_bdm(slot_map, vm_gen, bdm)
            mock_get_ctrl_slot.assert_called_once_with(bdm['disk_bus'],
                                                       slot_map)
            self.assertEqual(bdm['drive_addr'], mock.sentinel.DRIVE_ADDR)
            self.assertEqual(bdm['ctrl_disk_addr'],
                             mock.sentinel.CTRL_DISK_ADDR)

    def test_check_and_update_bdm_with_defaults(self):
        bdm = {'device_type': None,
               'disk_bus': None,
               'boot_index': None}

        self._test_check_and_update_bdm(bdm=bdm,
                                        slot_map=mock.sentinel.FAKE_SLOT_MAP)
        self.assertEqual('disk', bdm['device_type'])
        self.assertEqual(self._bdman._DEFAULT_BUS, bdm['disk_bus'])
        self.assertIsNone(bdm['boot_index'])

    def test_check_and_update_bdm_exception_device_type(self):
        bdm = {'device_type': 'cdrom',
               'disk_bus': 'IDE'}

        self._test_check_and_update_bdm(bdm=bdm, fail=True,
                                        slot_map=mock.sentinel.FAKE_SLOT_MAP)

    def test_check_and_update_bdm_exception_disk_bus(self):
        bdm = {'device_type': 'disk',
               'disk_bus': 'fake_bus'}

        self._test_check_and_update_bdm(bdm=bdm, fail=True,
                                        slot_map=mock.sentinel.FAKE_SLOT_MAP)

    def test_sort_by_boot_order(self):
        original = [{'boot_index': 2}, {'boot_index': None}, {'boot_index': 1}]
        expected = [original[2], original[0], original[1]]

        self._bdman._sort_by_boot_order(original)

        self.assertEqual(expected, original)

    def _test_get_boot_order(self, mock_get_boot_order, vm_gen, bdi):
        self._bdman.get_boot_order(vm_gen, bdi)
        mock_get_boot_order.assert_called_once_with(bdi)

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_get_boot_order_gen1')
    def test_get_boot_order_gen1_vm(self, mock_get_boot_order):
        self._test_get_boot_order(mock_get_boot_order, constants.VM_GEN_1,
                                  mock.sentinel.BLOCK_DEV_INFO)

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_get_boot_order_gen2')
    def test_get_boot_order_gen2_vm(self, mock_get_boot_order):
        self._test_get_boot_order(mock_get_boot_order, constants.VM_GEN_2,
                                  mock.sentinel.BLOCK_DEV_INFO)

    def _test_get_boot_order_gen1(self, bdi, expected):
        res = self._bdman._get_boot_order_gen1(bdi)

        self.assertEqual(expected, res)

    def test_get_boot_order_gen1_iso(self):
        fake_bdi = {'root_disk': {'type': 'iso'}}

        expected = [constants.BOOT_DEVICE_CDROM,
                    constants.BOOT_DEVICE_HARDDISK,
                    constants.BOOT_DEVICE_NETWORK,
                    constants.BOOT_DEVICE_FLOPPY]

        self._test_get_boot_order_gen1(fake_bdi, expected)

    def test_get_boot_order_gen1_vhd(self):
        fake_bdi = {'root_disk': {'type': 'vhd'}}

        expected = [constants.BOOT_DEVICE_HARDDISK,
                    constants.BOOT_DEVICE_CDROM,
                    constants.BOOT_DEVICE_NETWORK,
                    constants.BOOT_DEVICE_FLOPPY]

        self._test_get_boot_order_gen1(fake_bdi, expected)

    def test_get_boot_order_gen2(self):
        fake_root_disk = {'boot_index': 0,
                          'path': mock.sentinel.FAKE_ROOT_PATH}
        fake_eph1 = {'boot_index': 2,
                     'path': mock.sentinel.FAKE_EPH_PATH1}
        fake_eph2 = {'boot_index': 3,
                     'path': mock.sentinel.FAKE_EPH_PATH2}
        fake_bdm = {'boot_index': 1,
                    'connection_info': mock.sentinel.FAKE_CONN_INFO}
        fake_bdi = {'root_disk': fake_root_disk,
                    'ephemerals': [fake_eph1,
                                   fake_eph2],
                    'block_device_mapping': [fake_bdm]}

        self._bdman._volops.get_mounted_disk_path_from_volume = (
            mock.MagicMock(return_value=fake_bdm['connection_info']))

        expected_res = [mock.sentinel.FAKE_ROOT_PATH,
                        mock.sentinel.FAKE_CONN_INFO,
                        mock.sentinel.FAKE_EPH_PATH1,
                        mock.sentinel.FAKE_EPH_PATH2]

        res = self._bdman._get_boot_order_gen2(fake_bdi)

        self.assertEqual(expected_res, res)
