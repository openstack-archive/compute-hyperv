# Copyright (c) 2016 Cloudbase Solutions Srl
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
from nova import objects
from nova.virt import block_device as driver_block_device
from os_win import constants as os_win_const
from os_win import exceptions as os_win_exc
from oslo_serialization import jsonutils

from compute_hyperv.nova import block_device_manager
from compute_hyperv.nova import constants
from compute_hyperv.tests.unit import test_base


@ddt.ddt
class BlockDeviceManagerTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V BlockDeviceInfoManager class."""

    _autospec_classes = [
        block_device_manager.volumeops.VolumeOps,
        block_device_manager.pathutils.PathUtils,
    ]

    _FAKE_CONN_INFO = {
        'serial': 'fake_volume_id'
    }

    _FAKE_ATTACH_INFO = {
        'controller_type': constants.CTRL_TYPE_SCSI,
        'controller_addr': 0,
        'controller_slot': 1
    }

    def setUp(self):
        super(BlockDeviceManagerTestCase, self).setUp()
        self._bdman = block_device_manager.BlockDeviceInfoManager()

        self._volops = self._bdman._volops
        self._pathutils = self._bdman._pathutils

    @ddt.data(constants.CTRL_TYPE_SCSI, constants.CTRL_TYPE_IDE)
    def test_get_device_bus(self, controller_type):
        fake_ctrl_addr = self._FAKE_ATTACH_INFO['controller_addr']
        fake_ctrl_slot = self._FAKE_ATTACH_INFO['controller_slot']

        bus = self._bdman._get_device_bus(
            controller_type, fake_ctrl_addr, fake_ctrl_slot)

        if controller_type == constants.CTRL_TYPE_SCSI:
            exp_addr = '0:0:%s:%s' % (fake_ctrl_addr, fake_ctrl_slot)
            exp_cls = objects.SCSIDeviceBus
        else:
            exp_addr = '%s:%s' % (fake_ctrl_addr, fake_ctrl_slot)
            exp_cls = objects.IDEDeviceBus

        self.assertIsInstance(bus, exp_cls)
        self.assertEqual(exp_addr, bus.address)

    @ddt.data({},
              {'bdm_is_vol': False},
              {'conn_info_set': False})
    @ddt.unpack
    @mock.patch.object(driver_block_device, 'convert_volume')
    def test_get_vol_bdm_att_info(self, mock_convert_vol,
                                  bdm_is_vol=True,
                                  conn_info_set=True):
        mock_drv_bdm = (dict(connection_info=self._FAKE_CONN_INFO)
                        if conn_info_set else {})
        mock_convert_vol.return_value = (mock_drv_bdm
                                         if bdm_is_vol
                                         else None)

        self._volops.get_disk_attachment_info.return_value = (
            self._FAKE_ATTACH_INFO.copy())

        attach_info = self._bdman._get_vol_bdm_attachment_info(
            mock.sentinel.bdm)

        mock_convert_vol.assert_called_once_with(
            mock.sentinel.bdm)

        if bdm_is_vol and conn_info_set:
            exp_attach_info = self._FAKE_ATTACH_INFO.copy()
            exp_attach_info['serial'] = self._FAKE_CONN_INFO['serial']

            self._volops.get_disk_attachment_info.assert_called_once_with(
                self._FAKE_CONN_INFO)
        else:
            exp_attach_info = None

            self._volops.get_disk_attachment_info.assert_not_called()

        self.assertEqual(exp_attach_info, attach_info)

    @ddt.data({},
              {'eph_name_set': False},
              {'eph_disk_exists': False})
    @ddt.unpack
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       'get_bdm_connection_info')
    @mock.patch('os.path.exists')
    def test_get_eph_bdm_attachment_info(self, mock_exists,
                                         mock_get_bdm_conn_info,
                                         eph_name_set=True,
                                         eph_disk_exists=True):
        fake_instance_dir = 'fake_instance_dir'
        fake_eph_name = 'eph0.vhdx'
        mock_instance = mock.Mock()

        fake_conn_info = self._FAKE_CONN_INFO.copy()
        if eph_name_set:
            fake_conn_info['eph_filename'] = fake_eph_name

        mock_get_bdm_conn_info.return_value = fake_conn_info
        mock_exists.return_value = eph_disk_exists
        mock_get_attach_info = self._bdman._vmutils.get_disk_attachment_info

        self._pathutils.get_instance_dir.return_value = fake_instance_dir

        attach_info = self._bdman._get_eph_bdm_attachment_info(
            mock_instance, mock.sentinel.bdm)

        if eph_name_set and eph_disk_exists:
            exp_attach_info = mock_get_attach_info.return_value
            exp_eph_path = os.path.join(fake_instance_dir, fake_eph_name)

            mock_exists.assert_called_once_with(exp_eph_path)
            mock_get_attach_info.assert_called_once_with(
                exp_eph_path,
                is_physical=False)
        else:
            exp_attach_info = None

            mock_get_attach_info.assert_not_called()

        self.assertEqual(exp_attach_info, attach_info)

        mock_get_bdm_conn_info.assert_called_once_with(
            mock.sentinel.bdm)

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_get_vol_bdm_attachment_info')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_get_eph_bdm_attachment_info')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_get_device_bus')
    @mock.patch.object(block_device, 'new_format_is_ephemeral')
    @mock.patch.object(objects, 'DiskMetadata')
    def test_get_disk_metadata(self, mock_diskmetadata_cls,
                               mock_is_eph,
                               mock_get_device_bus,
                               mock_get_vol_attach_info,
                               mock_get_eph_attach_info,
                               bdm_is_eph=False,
                               bdm_is_vol=False,
                               attach_info_retrieved=True):
        mock_instance = mock.Mock()
        mock_bdm = mock.Mock()
        mock_bdm.is_volume = bdm_is_vol

        if attach_info_retrieved:
            attach_info = self._FAKE_ATTACH_INFO.copy()
            attach_info['serial'] = mock.sentinel.serial
        else:
            attach_info = None

        mock_get_eph_attach_info.return_value = attach_info
        mock_get_vol_attach_info.return_value = attach_info
        mock_is_eph.return_value = bdm_is_eph

        disk_metadata = self._bdman._get_disk_metadata(
            mock_instance, mock_bdm)

        if (bdm_is_vol or bdm_is_eph) and attach_info_retrieved:
            exp_disk_meta = mock_diskmetadata_cls.return_value

            mock_get_device_bus.assert_called_once_with(
                self._FAKE_ATTACH_INFO['controller_type'],
                self._FAKE_ATTACH_INFO['controller_addr'],
                self._FAKE_ATTACH_INFO['controller_slot'])
            mock_diskmetadata_cls.assert_called_once_with(
                bus=mock_get_device_bus.return_value,
                tags=[mock_bdm.tag],
                serial=mock.sentinel.serial)
        else:
            exp_disk_meta = None

            mock_get_device_bus.assert_not_called()

        self.assertEqual(exp_disk_meta, disk_metadata)

        if bdm_is_vol:
            mock_get_vol_attach_info.assert_called_once_with(mock_bdm)
        elif bdm_is_eph:
            mock_get_eph_attach_info.assert_called_once_with(mock_instance,
                                                             mock_bdm)

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_get_disk_metadata')
    @mock.patch.object(objects.BlockDeviceMappingList,
                       'get_by_instance_uuid')
    def test_get_bdm_metadata(self, mock_get_bdm_list,
                              mock_get_disk_meta):
        bdms = [mock.Mock()] * 4
        disk_meta = mock.Mock()
        mock_instance = mock.Mock()

        mock_get_bdm_list.return_value = bdms
        mock_get_disk_meta.side_effect = [
            None,
            exception.DiskNotFound(message='fake_err'),
            os_win_exc.DiskNotFound(message='fake_err'),
            disk_meta]

        bdm_meta = self._bdman.get_bdm_metadata(mock.sentinel.context,
                                                mock_instance)

        self.assertEqual([disk_meta], bdm_meta)

        mock_get_bdm_list.assert_called_once_with(mock.sentinel.context,
                                                  mock_instance.uuid)
        mock_get_disk_meta.assert_has_calls(
            [mock.call(mock_instance, bdm) for bdm in bdms])

    @mock.patch.object(objects.BlockDeviceMapping,
                       'get_by_volume_and_instance')
    def test_set_vol_bdm_conn_info(self, mock_get_bdm):
        mock_instance = mock.Mock()
        mock_bdm = mock_get_bdm.return_value

        self._bdman.set_volume_bdm_connection_info(
            mock.sentinel.context, mock_instance, self._FAKE_CONN_INFO)

        mock_get_bdm.assert_called_once_with(
            mock.sentinel.context,
            self._FAKE_CONN_INFO['serial'],
            mock_instance.uuid)

        self.assertEqual(self._FAKE_CONN_INFO,
                         jsonutils.loads(mock_bdm.connection_info))
        mock_bdm.save.assert_called_once_with()

    def test_get_bdm_connection_info(self):
        bdm = mock.Mock(connection_info=None)
        self.assertEqual({}, self._bdman.get_bdm_connection_info(bdm))

        bdm = mock.Mock()
        bdm.connection_info = jsonutils.dumps(self._FAKE_CONN_INFO)
        self.assertEqual(self._FAKE_CONN_INFO,
                         self._bdman.get_bdm_connection_info(bdm))

    def test_update_bdm_conn_info(self):
        connection_info = self._FAKE_CONN_INFO.copy()

        mock_bdm = mock.Mock()
        mock_bdm.connection_info = jsonutils.dumps(connection_info)

        updates = dict(some_key='some_val',
                       some_other_key='some_other_val')

        self._bdman.update_bdm_connection_info(
            mock_bdm, **updates)

        exp_connection_info = connection_info.copy()
        exp_connection_info.update(**updates)

        self.assertEqual(exp_connection_info,
                         jsonutils.loads(mock_bdm.connection_info))
        mock_bdm.save.assert_called_once_with()

    @mock.patch('nova.virt.configdrive.required_by')
    def test_init_controller_slot_counter_gen1_no_configdrive(
            self, mock_cfg_drive_req):
        mock_cfg_drive_req.return_value = False
        slot_map = self._bdman._initialize_controller_slot_counter(
            mock.sentinel.FAKE_INSTANCE, constants.VM_GEN_1)

        self.assertEqual(slot_map[constants.CTRL_TYPE_IDE][0],
                             os_win_const.IDE_CONTROLLER_SLOTS_NUMBER)
        self.assertEqual(slot_map[constants.CTRL_TYPE_IDE][1],
                         os_win_const.IDE_CONTROLLER_SLOTS_NUMBER)
        self.assertEqual(slot_map[constants.CTRL_TYPE_SCSI][0],
                         os_win_const.SCSI_CONTROLLER_SLOTS_NUMBER)

    @mock.patch('nova.virt.configdrive.required_by')
    def test_init_controller_slot_counter_gen1(self, mock_cfg_drive_req):
        slot_map = self._bdman._initialize_controller_slot_counter(
            mock.sentinel.FAKE_INSTANCE, constants.VM_GEN_1)

        self.assertEqual(slot_map[constants.CTRL_TYPE_IDE][1],
                         os_win_const.IDE_CONTROLLER_SLOTS_NUMBER - 1)

    @mock.patch.object(block_device_manager.configdrive, 'required_by')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_initialize_controller_slot_counter')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_check_and_update_root_device')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_check_and_update_ephemerals')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_check_and_update_volumes')
    def _check_validate_and_update_bdi(self, mock_check_and_update_vol,
                                       mock_check_and_update_eph,
                                       mock_check_and_update_root,
                                       mock_init_ctrl_cntr,
                                       mock_required_by, available_slots=1):
        mock_required_by.return_value = True
        slot_map = {constants.CTRL_TYPE_SCSI: [available_slots]}
        mock_init_ctrl_cntr.return_value = slot_map

        if available_slots:
            self._bdman.validate_and_update_bdi(mock.sentinel.FAKE_INSTANCE,
                                                mock.sentinel.IMAGE_META,
                                                constants.VM_GEN_2,
                                                mock.sentinel.BLOCK_DEV_INFO)
        else:
            self.assertRaises(exception.InvalidBDMFormat,
                              self._bdman.validate_and_update_bdi,
                              mock.sentinel.FAKE_INSTANCE,
                              mock.sentinel.IMAGE_META,
                              constants.VM_GEN_2,
                              mock.sentinel.BLOCK_DEV_INFO)

        mock_init_ctrl_cntr.assert_called_once_with(
            mock.sentinel.FAKE_INSTANCE, constants.VM_GEN_2)
        mock_check_and_update_root.assert_called_once_with(
            constants.VM_GEN_2, mock.sentinel.IMAGE_META,
            mock.sentinel.BLOCK_DEV_INFO, slot_map)
        mock_check_and_update_eph.assert_called_once_with(
            constants.VM_GEN_2, mock.sentinel.BLOCK_DEV_INFO, slot_map)
        mock_check_and_update_vol.assert_called_once_with(
            constants.VM_GEN_2, mock.sentinel.BLOCK_DEV_INFO, slot_map)
        mock_required_by.assert_called_once_with(mock.sentinel.FAKE_INSTANCE)

    def test_validate_and_update_bdi(self):
        self._check_validate_and_update_bdi()

    def test_validate_and_update_bdi_insufficient_slots(self):
        self._check_validate_and_update_bdi(available_slots=0)

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_get_available_controller_slot')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       'is_boot_from_volume')
    def _test_check_and_update_root_device(self, mock_is_boot_from_vol,
                                           mock_get_avail_ctrl_slot,
                                           disk_format,
                                           vm_gen=constants.VM_GEN_1,
                                           boot_from_volume=False):
        image_meta = {'disk_format': disk_format}
        bdi = {'root_device': '/dev/sda',
               'block_device_mapping': [
                    {'mount_device': '/dev/sda',
                     'connection_info': mock.sentinel.FAKE_CONN_INFO}]}

        mock_is_boot_from_vol.return_value = boot_from_volume
        mock_get_avail_ctrl_slot.return_value = (0, 0)

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

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       'is_boot_from_volume', return_value=False)
    def test_check_and_update_root_device_exception(self, mock_is_boot_vol):
        bdi = {}
        image_meta = mock.MagicMock(disk_format=mock.sentinel.fake_format)

        self.assertRaises(exception.InvalidImageFormat,
                          self._bdman._check_and_update_root_device,
                          constants.VM_GEN_1, image_meta, bdi,
                          mock.sentinel.SLOT_MAP)

    def test_check_and_update_root_device_gen1(self):
        self._test_check_and_update_root_device(disk_format='vhd')

    def test_check_and_update_root_device_gen1_vhdx(self):
        self._test_check_and_update_root_device(disk_format='vhdx')

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
            self.assertRaises(exception.InvalidBDMFormat,
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

    def test_get_available_controller_slot_scsi_ctrl(self):
        self._test_get_available_controller_slot(bus=constants.CTRL_TYPE_SCSI)

    def test_get_available_controller_slot_exception(self):
        self._test_get_available_controller_slot(fail=True)

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

        self.assertEqual(bdm2, ret)

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
    def test_check_and_update_bdm_with_defaults(self, mock_get_ctrl_slot):
        mock_get_ctrl_slot.return_value = ((mock.sentinel.DRIVE_ADDR,
                                            mock.sentinel.CTRL_DISK_ADDR))
        bdm = {'device_type': None,
               'disk_bus': None,
               'boot_index': None}

        self._bdman._check_and_update_bdm(mock.sentinel.FAKE_SLOT_MAP,
                                          constants.VM_GEN_1, bdm)

        mock_get_ctrl_slot.assert_called_once_with(
          bdm['disk_bus'], mock.sentinel.FAKE_SLOT_MAP)
        self.assertEqual(mock.sentinel.DRIVE_ADDR, bdm['drive_addr'])
        self.assertEqual(mock.sentinel.CTRL_DISK_ADDR, bdm['ctrl_disk_addr'])
        self.assertEqual('disk', bdm['device_type'])
        self.assertEqual(self._bdman._DEFAULT_BUS, bdm['disk_bus'])
        self.assertIsNone(bdm['boot_index'])

    def test_check_and_update_bdm_exception_device_type(self):
        bdm = {'device_type': 'cdrom',
               'disk_bus': 'IDE'}

        self.assertRaises(exception.InvalidDiskInfo,
                          self._bdman._check_and_update_bdm,
                          mock.sentinel.FAKE_SLOT_MAP, constants.VM_GEN_1, bdm)

    def test_check_and_update_bdm_exception_disk_bus(self):
        bdm = {'device_type': 'disk',
               'disk_bus': 'fake_bus'}

        self.assertRaises(exception.InvalidDiskInfo,
                          self._bdman._check_and_update_bdm,
                          mock.sentinel.FAKE_SLOT_MAP, constants.VM_GEN_1, bdm)

    def test_sort_by_boot_order(self):
        original = [{'boot_index': 2}, {'boot_index': None}, {'boot_index': 1}]
        expected = [original[2], original[0], original[1]]

        self._bdman._sort_by_boot_order(original)
        self.assertEqual(expected, original)

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_get_boot_order_gen1')
    def test_get_boot_order_gen1_vm(self, mock_get_boot_order):
        self._bdman.get_boot_order(constants.VM_GEN_1,
                                   mock.sentinel.BLOCK_DEV_INFO)
        mock_get_boot_order.assert_called_once_with(
            mock.sentinel.BLOCK_DEV_INFO)

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       '_get_boot_order_gen2')
    def test_get_boot_order_gen2_vm(self, mock_get_boot_order):
        self._bdman.get_boot_order(constants.VM_GEN_2,
                                   mock.sentinel.BLOCK_DEV_INFO)
        mock_get_boot_order.assert_called_once_with(
            mock.sentinel.BLOCK_DEV_INFO)

    def test_get_boot_order_gen1_iso(self):
        fake_bdi = {'root_disk': {'type': 'iso'}}
        expected = [os_win_const.BOOT_DEVICE_CDROM,
                    os_win_const.BOOT_DEVICE_HARDDISK,
                    os_win_const.BOOT_DEVICE_NETWORK,
                    os_win_const.BOOT_DEVICE_FLOPPY]

        res = self._bdman._get_boot_order_gen1(fake_bdi)
        self.assertEqual(expected, res)

    def test_get_boot_order_gen1_vhd(self):
        fake_bdi = {'root_disk': {'type': 'vhd'}}
        expected = [os_win_const.BOOT_DEVICE_HARDDISK,
                    os_win_const.BOOT_DEVICE_CDROM,
                    os_win_const.BOOT_DEVICE_NETWORK,
                    os_win_const.BOOT_DEVICE_FLOPPY]

        res = self._bdman._get_boot_order_gen1(fake_bdi)
        self.assertEqual(expected, res)

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

        self._bdman._volops.get_disk_resource_path = (
            mock.MagicMock(return_value=fake_bdm['connection_info']))

        expected_res = [mock.sentinel.FAKE_ROOT_PATH,
                        mock.sentinel.FAKE_CONN_INFO,
                        mock.sentinel.FAKE_EPH_PATH1,
                        mock.sentinel.FAKE_EPH_PATH2]

        res = self._bdman._get_boot_order_gen2(fake_bdi)

        self.assertEqual(expected_res, res)
