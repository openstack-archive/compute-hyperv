# Copyright 2014 Cloudbase Solutions Srl
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

import copy
import os

import mock
from oslo_config import cfg

from nova import exception
from nova.tests.unit import fake_block_device
from oslo_utils import units

from hyperv.nova import constants
from hyperv.nova import pathutils
from hyperv.nova import vmutils
from hyperv.nova import volumeops
from hyperv.tests.unit import test_base

CONF = cfg.CONF

connection_data = {'target_lun': mock.sentinel.fake_lun,
                   'target_iqn': mock.sentinel.fake_iqn,
                   'target_portal': mock.sentinel.fake_portal,
                   'auth_method': 'chap',
                   'auth_username': mock.sentinel.fake_user,
                   'auth_password': mock.sentinel.fake_pass}


def get_fake_block_dev_info():
    return {'block_device_mapping': [
        fake_block_device.AnonFakeDbBlockDeviceDict({'source_type': 'volume'})]
    }


def get_fake_connection_info(**kwargs):
    return {'data': dict(connection_data, **kwargs)}


class VolumeOpsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for VolumeOps class."""

    def setUp(self):
        super(VolumeOpsTestCase, self).setUp()
        self._volumeops = volumeops.VolumeOps()

    def test_get_volume_driver(self):
        fake_conn_info = {'driver_volume_type': mock.sentinel.fake_driver_type}
        self._volumeops.volume_drivers[mock.sentinel.fake_driver_type] = (
            mock.sentinel.fake_driver)

        result = self._volumeops._get_volume_driver(
            connection_info=fake_conn_info)
        self.assertEqual(mock.sentinel.fake_driver, result)

    def test_get_volume_driver_exception(self):
        fake_conn_info = {'driver_volume_type': 'fake_driver'}
        self.assertRaises(exception.VolumeDriverNotFound,
                          self._volumeops._get_volume_driver,
                          connection_info=fake_conn_info)

    @mock.patch.object(volumeops.VolumeOps, 'attach_volume')
    def test_attach_volumes(self, mock_attach_volume):
        block_device_info = get_fake_block_dev_info()

        self._volumeops.attach_volumes(
            block_device_info['block_device_mapping'],
            mock.sentinel.instance_name)

        mock_attach_volume.assert_called_once_with(
            block_device_info['block_device_mapping'][0]['connection_info'],
            mock.sentinel.instance_name)

    def test_fix_instance_volume_disk_paths(self):
        block_device_info = get_fake_block_dev_info()
        fake_vol_conn_info = (
            block_device_info['block_device_mapping'][0]['connection_info'])

        mock_get_volume_driver = mock.MagicMock()
        mock_ebs_in_block_devices = mock.MagicMock()
        with mock.patch.multiple(self._volumeops,
                _get_volume_driver=mock_get_volume_driver,
                ebs_root_in_block_devices=mock_ebs_in_block_devices):

            fake_vol_driver = mock_get_volume_driver.return_value
            mock_ebs_in_block_devices.return_value = False

            self._volumeops.fix_instance_volume_disk_paths(
                mock.sentinel.instance_name,
                block_device_info)

            func = fake_vol_driver.fix_instance_volume_disk_path
            func.assert_called_once_with(
                mock.sentinel.instance_name,
                fake_vol_conn_info, 0)

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    @mock.patch.object(volumeops.VolumeOps, 'parse_disk_qos_specs')
    def test_attach_volume(self, mock_parse_qos_specs,
                           mock_get_volume_driver):
        fake_conn_info = {
            'data': {'qos_specs': mock.sentinel.qos_specs}
        }

        mock_volume_driver = mock_get_volume_driver.return_value
        mock_parse_qos_specs.return_value = [
            mock.sentinel.min_iops,
            mock.sentinel.max_iops
        ]

        self._volumeops.attach_volume(fake_conn_info,
                                      mock.sentinel.instance_name,
                                      mock.sentinel.fake_disk_bus)

        mock_volume_driver.attach_volume.assert_called_once_with(
            fake_conn_info,
            mock.sentinel.instance_name,
            disk_bus=mock.sentinel.fake_disk_bus)
        mock_parse_qos_specs.assert_called_once_with(mock.sentinel.qos_specs)
        mock_volume_driver.set_disk_qos_specs.assert_called_once_with(
            fake_conn_info, mock.sentinel.instance_name,
            mock.sentinel.min_iops, mock.sentinel.max_iops)

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    def test_disconnect_volumes(self, mock_get_volume_driver):
        block_device_info = get_fake_block_dev_info()
        block_device_mapping = block_device_info['block_device_mapping']
        block_device_mapping[0]['connection_info'] = {
            'driver_volume_type': mock.sentinel.fake_vol_type}
        fake_volume_driver = mock_get_volume_driver.return_value
        self._volumeops.disconnect_volumes(block_device_info)
        fake_volume_driver.disconnect_volumes.assert_called_once_with(
            block_device_mapping)

    def test_ebs_root_in_block_devices(self):
        block_device_info = get_fake_block_dev_info()

        response = self._volumeops.ebs_root_in_block_devices(block_device_info)

        self._volumeops._volutils.volume_in_mapping.assert_called_once_with(
            self._volumeops._default_root_device, block_device_info)
        self.assertEqual(
            self._volumeops._volutils.volume_in_mapping.return_value,
            response)

    def test_get_volume_connector(self):
        mock_instance = mock.DEFAULT
        initiator = self._volumeops._volutils.get_iscsi_initiator.return_value
        expected = {'ip': CONF.my_ip,
                    'host': CONF.host,
                    'initiator': initiator}

        response = self._volumeops.get_volume_connector(instance=mock_instance)

        self._volumeops._volutils.get_iscsi_initiator.assert_called_once_with()
        self.assertEqual(expected, response)

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    def test_initialize_volumes_connection(self, mock_get_volume_driver):
        block_device_info = get_fake_block_dev_info()

        self._volumeops.initialize_volumes_connection(block_device_info)

        init_vol_conn = (
            mock_get_volume_driver.return_value.initialize_volume_connection)
        init_vol_conn.assert_called_once_with(
            block_device_info['block_device_mapping'][0]['connection_info'])

    def test_group_block_devices_by_type(self):
        block_device_map = get_fake_block_dev_info()['block_device_mapping']
        block_device_map[0]['connection_info'] = {
            'driver_volume_type': 'iscsi'}
        result = self._volumeops._group_block_devices_by_type(
            block_device_map)

        expected = {'iscsi': [block_device_map[0]]}
        self.assertEqual(expected, result)

    def test_parse_disk_qos_specs_using_iops(self):
        fake_qos_specs = {
            'total_iops_sec': 10,
            'min_iops_sec': 1,
        }

        ret_val = self._volumeops.parse_disk_qos_specs(fake_qos_specs)

        expected_qos_specs = (fake_qos_specs['min_iops_sec'],
                              fake_qos_specs['total_iops_sec'])
        self.assertEqual(expected_qos_specs, ret_val)

    def test_parse_disk_qos_specs_using_bytes_per_sec(self):
        fake_qos_specs = {
            'total_bytes_sec': units.Ki * 15,
            'min_bytes_sec': 0,
        }

        ret_val = self._volumeops.parse_disk_qos_specs(fake_qos_specs)

        expected_qos_specs = (0, 2)  # Normalized IOPS
        self.assertEqual(expected_qos_specs, ret_val)

    def test_parse_disk_qos_specs_exception(self):
        fake_qos_specs = {
            'total_iops_sec': 1,
            'min_iops_sec': 2
        }

        self.assertRaises(vmutils.HyperVException,
                          self._volumeops.parse_disk_qos_specs,
                          fake_qos_specs)

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    def test_get_mounted_disk_path_from_volume(self, mock_get_volume_driver):
        fake_volume_driver = mock_get_volume_driver.return_value

        self._volumeops.get_mounted_disk_path_from_volume(
            mock.sentinel.CONN_INFO)
        get_mounted_disk = fake_volume_driver.get_mounted_disk_path_from_volume
        get_mounted_disk.assert_called_once_with(mock.sentinel.CONN_INFO)


class ISCSIVolumeDriverTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for Hyper-V ISCSIVolumeDriver class."""

    def setUp(self):
        super(ISCSIVolumeDriverTestCase, self).setUp()
        self._volume_driver = volumeops.ISCSIVolumeDriver()
        self._volume_driver._vmutils = mock.MagicMock()
        self._volume_driver._volutils = mock.MagicMock()

    def test_login_storage_target_auth_exception(self):
        connection_info = get_fake_connection_info(
            auth_method='fake_auth_method')

        self.assertRaises(vmutils.HyperVException,
                          self._volume_driver.login_storage_target,
                          connection_info)

    @mock.patch.object(volumeops.ISCSIVolumeDriver,
                       '_get_mounted_disk_from_lun')
    def _check_login_storage_target(self, mock_get_mounted_disk_from_lun,
                                    dev_number):
        connection_info = get_fake_connection_info()
        login_target = self._volume_driver._volutils.login_storage_target
        get_number = self._volume_driver._volutils.get_device_number_for_target
        get_number.return_value = dev_number

        self._volume_driver.login_storage_target(connection_info)

        get_number.assert_called_once_with(mock.sentinel.fake_iqn,
                                           mock.sentinel.fake_lun)
        if not dev_number:
            login_target.assert_called_once_with(
                mock.sentinel.fake_lun, mock.sentinel.fake_iqn,
                mock.sentinel.fake_portal, mock.sentinel.fake_user,
                mock.sentinel.fake_pass)
            mock_get_mounted_disk_from_lun.assert_called_once_with(
                mock.sentinel.fake_iqn, mock.sentinel.fake_lun, True)
        else:
            self.assertFalse(login_target.called)

    def test_login_storage_target_already_logged(self):
        self._check_login_storage_target(dev_number=1)

    def test_login_storage_target(self):
        self._check_login_storage_target(dev_number=0)

    def _check_logout_storage_target(self, disconnected_luns_count=0):
        self._volume_driver._volutils.get_target_lun_count.return_value = 1

        self._volume_driver.logout_storage_target(
            target_iqn=mock.sentinel.fake_iqn,
            disconnected_luns_count=disconnected_luns_count)

        logout_storage = self._volume_driver._volutils.logout_storage_target

        if disconnected_luns_count:
            logout_storage.assert_called_once_with(mock.sentinel.fake_iqn)
        else:
            self.assertFalse(logout_storage.called)

    def test_logout_storage_target_skip(self):
        self._check_logout_storage_target()

    def test_logout_storage_target(self):
        self._check_logout_storage_target(disconnected_luns_count=1)

    @mock.patch.object(volumeops.ISCSIVolumeDriver,
                       '_get_mounted_disk_from_lun')
    def test_get_mounted_disk_path_from_volume(self, mock_get_disk_from_lun):
        fake_conn_info = {'data': {'target_lun': mock.sentinel.fake_lun,
                                   'target_iqn': mock.sentinel.fake_iqn}}
        self._volume_driver.get_mounted_disk_path_from_volume(fake_conn_info)
        mock_get_disk_from_lun.assert_called_once_with(
            mock.sentinel.fake_iqn, mock.sentinel.fake_lun,
            wait_for_device=True)

    @mock.patch.object(volumeops.ISCSIVolumeDriver,
                       'get_mounted_disk_path_from_volume')
    @mock.patch.object(volumeops.ISCSIVolumeDriver, 'logout_storage_target')
    @mock.patch.object(volumeops.ISCSIVolumeDriver, 'login_storage_target')
    def test_attach_volume_exception(self, mock_login_storage_target,
                                     mock_logout_storage_target,
                                     mock_get_mounted_disk):
        connection_info = get_fake_connection_info()
        mock_get_mounted_disk.side_effect = vmutils.HyperVException

        self.assertRaises(vmutils.HyperVException,
                          self._volume_driver.attach_volume, connection_info,
                          mock.sentinel.instance_name)
        mock_logout_storage_target.assert_called_with(mock.sentinel.fake_iqn)

    @mock.patch.object(volumeops.ISCSIVolumeDriver,
                       'get_mounted_disk_path_from_volume')
    @mock.patch.object(volumeops.ISCSIVolumeDriver, 'login_storage_target')
    def _check_attach_volume(self, mock_login_storage_target,
                             mock_get_mounted_disk_path, disk_bus):
        connection_info = get_fake_connection_info()

        get_ide_path = self._volume_driver._vmutils.get_vm_ide_controller
        get_scsi_path = self._volume_driver._vmutils.get_vm_scsi_controller
        fake_ide_path = get_ide_path.return_value
        fake_scsi_path = get_scsi_path.return_value
        fake_mounted_disk_path = mock_get_mounted_disk_path.return_value
        attach_vol = self._volume_driver._vmutils.attach_volume_to_controller

        get_free_slot = self._volume_driver._vmutils.get_free_controller_slot
        get_free_slot.return_value = 1

        self._volume_driver.attach_volume(
            connection_info=connection_info,
            instance_name=mock.sentinel.instance_name,
            disk_bus=disk_bus)

        mock_login_storage_target.assert_called_once_with(connection_info)
        mock_get_mounted_disk_path.assert_called_once_with(
            connection_info)
        if disk_bus == constants.CTRL_TYPE_IDE:
            get_ide_path.assert_called_once_with(
                mock.sentinel.instance_name, 0)
            attach_vol.assert_called_once_with(mock.sentinel.instance_name,
                                               fake_ide_path, 0,
                                               fake_mounted_disk_path)
        else:
            get_scsi_path.assert_called_once_with(mock.sentinel.instance_name)
            get_free_slot.assert_called_once_with(fake_scsi_path)
            attach_vol.assert_called_once_with(mock.sentinel.instance_name,
                                               fake_scsi_path, 1,
                                               fake_mounted_disk_path)

    def test_attach_volume_root_device(self):
        self._check_attach_volume(disk_bus=constants.CTRL_TYPE_IDE)

    def test_attach_volume(self):
        self._check_attach_volume(disk_bus=constants.CTRL_TYPE_SCSI)

    @mock.patch.object(volumeops.ISCSIVolumeDriver,
                       'get_mounted_disk_path_from_volume')
    @mock.patch.object(volumeops.ISCSIVolumeDriver, 'logout_storage_target')
    def test_detach_volume(self, mock_logout_storage_target,
                           mock_get_mounted_disk_path_from_vol):
        connection_info = get_fake_connection_info()

        self._volume_driver.detach_volume(connection_info,
                                          mock.sentinel.instance_name)

        mock_get_mounted_disk_path_from_vol.assert_called_once_with(
            connection_info)
        self._volume_driver._vmutils.detach_vm_disk.assert_called_once_with(
            mock.sentinel.instance_name,
            mock_get_mounted_disk_path_from_vol.return_value)
        mock_logout_storage_target.assert_called_once_with(
            mock.sentinel.fake_iqn)

    def test_get_mounted_disk_from_lun(self):
        mock_get_device_number_for_target = (
            self._volume_driver._volutils.get_device_number_for_target)
        mock_get_device_number_for_target.return_value = 0

        mock_get_mounted_disk = (
            self._volume_driver._vmutils.get_mounted_disk_by_drive_number)
        mock_get_mounted_disk.return_value = mock.sentinel.disk_path

        disk = self._volume_driver._get_mounted_disk_from_lun(
            mock.sentinel.target_iqn,
            mock.sentinel.target_lun)
        self.assertEqual(mock.sentinel.disk_path, disk)

    def test_get_target_from_disk_path(self):
        result = self._volume_driver.get_target_from_disk_path(
            mock.sentinel.physical_drive_path)

        mock_get_target = (
            self._volume_driver._volutils.get_target_from_disk_path)
        mock_get_target.assert_called_once_with(
            mock.sentinel.physical_drive_path)
        self.assertEqual(mock_get_target.return_value, result)

    @mock.patch.object(volumeops.ISCSIVolumeDriver,
                       '_get_mounted_disk_from_lun')
    def test_fix_instance_volume_disk_path(self, mock_get_disk_from_lun):
        connection_info = get_fake_connection_info()

        set_disk_host_res = self._volume_driver._vmutils.set_disk_host_resource
        get_scsi_ctrl = self._volume_driver._vmutils.get_vm_scsi_controller
        get_scsi_ctrl.return_value = mock.sentinel.controller_path
        mock_get_disk_from_lun.return_value = mock.sentinel.mounted_path

        self._volume_driver.fix_instance_volume_disk_path(
            mock.sentinel.instance_name,
            connection_info,
            mock.sentinel.disk_address)

        mock_get_disk_from_lun.assert_called_once_with(
            mock.sentinel.fake_iqn, mock.sentinel.fake_lun, True)
        get_scsi_ctrl.assert_called_once_with(mock.sentinel.instance_name)
        set_disk_host_res.assert_called_once_with(
            mock.sentinel.instance_name, mock.sentinel.controller_path,
            mock.sentinel.disk_address, mock.sentinel.mounted_path)

    @mock.patch('time.sleep')
    def test_get_mounted_disk_from_lun_failure(self, fake_sleep):
        self.flags(mounted_disk_query_retry_count=1, group='hyperv')

        with mock.patch.object(self._volume_driver._volutils,
                               'get_device_number_for_target') as m_device_num:
            m_device_num.side_effect = [None, -1]

            self.assertRaises(exception.NotFound,
                              self._volume_driver._get_mounted_disk_from_lun,
                              mock.sentinel.target_iqn,
                              mock.sentinel.target_lun)

    @mock.patch.object(volumeops.ISCSIVolumeDriver, 'logout_storage_target')
    def test_disconnect_volumes(self, mock_logout_storage_target):
        block_device_info = get_fake_block_dev_info()
        connection_info = get_fake_connection_info()
        block_device_mapping = block_device_info['block_device_mapping']
        block_device_mapping[0]['connection_info'] = connection_info

        self._volume_driver.disconnect_volumes(block_device_mapping)

        mock_logout_storage_target.assert_called_once_with(
            mock.sentinel.fake_iqn, 1)

    def test_get_target_lun_count(self):
        result = self._volume_driver.get_target_lun_count(
            mock.sentinel.target_iqn)

        mock_get_lun_count = self._volume_driver._volutils.get_target_lun_count
        mock_get_lun_count.assert_called_once_with(mock.sentinel.target_iqn)
        self.assertEqual(mock_get_lun_count.return_value, result)

    @mock.patch.object(volumeops.ISCSIVolumeDriver, 'login_storage_target')
    def test_initialize_volume_connection(self, mock_login_storage_target):
        self._volume_driver.initialize_volume_connection(
            mock.sentinel.connection_info)
        mock_login_storage_target.assert_called_once_with(
            mock.sentinel.connection_info)


class SMBFSVolumeDriverTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V SMBFSVolumeDriver class."""

    _FAKE_SHARE = '//1.2.3.4/fake_share'
    _FAKE_SHARE_NORMALIZED = _FAKE_SHARE.replace('/', '\\')
    _FAKE_DISK_NAME = 'fake_volume_name.vhdx'
    _FAKE_USERNAME = 'fake_username'
    _FAKE_PASSWORD = 'fake_password'
    _FAKE_SMB_OPTIONS = '-o username=%s,password=%s' % (_FAKE_USERNAME,
                                                        _FAKE_PASSWORD)
    _FAKE_CONNECTION_INFO = {'data': {'export': _FAKE_SHARE,
                                      'name': _FAKE_DISK_NAME,
                                      'options': _FAKE_SMB_OPTIONS}}

    def setUp(self):
        super(SMBFSVolumeDriverTestCase, self).setUp()
        self._volume_driver = volumeops.SMBFSVolumeDriver()
        self._volume_driver._vmutils = mock.MagicMock()
        self._volume_driver._pathutils = mock.MagicMock()

    @mock.patch.object(volumeops.SMBFSVolumeDriver, 'ensure_share_mounted')
    @mock.patch.object(volumeops.SMBFSVolumeDriver, '_get_disk_path')
    def _check_attach_volume(self, mock_get_disk_path,
                             mock_ensure_share_mounted,
                             disk_bus=constants.CTRL_TYPE_SCSI):
        mock_get_disk_path.return_value = mock.sentinel.disk_path

        self._volume_driver.attach_volume(
            self._FAKE_CONNECTION_INFO,
            mock.sentinel.instance_name,
            disk_bus)

        if disk_bus == constants.CTRL_TYPE_IDE:
            get_vm_ide_controller = (
                self._volume_driver._vmutils.get_vm_ide_controller)
            get_vm_ide_controller.assert_called_once_with(
                mock.sentinel.instance_name, 0)
            ctrller_path = get_vm_ide_controller.return_value
            slot = 0
        else:
            get_vm_scsi_controller = (
                self._volume_driver._vmutils.get_vm_scsi_controller)
            get_vm_scsi_controller.assert_called_once_with(
                mock.sentinel.instance_name)
            get_free_controller_slot = (
                self._volume_driver._vmutils.get_free_controller_slot)
            get_free_controller_slot.assert_called_once_with(
                get_vm_scsi_controller.return_value)

            ctrller_path = get_vm_scsi_controller.return_value
            slot = get_free_controller_slot.return_value

        mock_ensure_share_mounted.assert_called_once_with(
            self._FAKE_CONNECTION_INFO)
        mock_get_disk_path.assert_called_once_with(self._FAKE_CONNECTION_INFO)
        self._volume_driver._vmutils.attach_drive.assert_called_once_with(
            mock.sentinel.instance_name, mock.sentinel.disk_path,
            ctrller_path, slot)

    def test_attach_volume_ide(self):
        self._check_attach_volume(disk_bus=constants.CTRL_TYPE_IDE)

    def test_attach_volume_scsi(self):
        self._check_attach_volume()

    @mock.patch.object(volumeops.SMBFSVolumeDriver, 'ensure_share_mounted')
    @mock.patch.object(volumeops.SMBFSVolumeDriver, '_get_disk_path')
    def test_attach_non_existing_image(self, mock_get_disk_path,
                                       mock_ensure_share_mounted):
        self._volume_driver._vmutils.attach_drive.side_effect = (
            vmutils.HyperVException())
        self.assertRaises(vmutils.HyperVException,
                          self._volume_driver.attach_volume,
                          self._FAKE_CONNECTION_INFO,
                          mock.sentinel.instance_name)

    @mock.patch.object(volumeops.SMBFSVolumeDriver, '_get_disk_path')
    @mock.patch.object(pathutils.PathUtils, 'unmount_smb_share')
    def test_detach_volume(self, mock_unmount_smb_share, mock_get_disk_path):
        mock_get_disk_path.return_value = (
            mock.sentinel.disk_path)

        self._volume_driver.detach_volume(self._FAKE_CONNECTION_INFO,
                                          mock.sentinel.instance_name)

        self._volume_driver._vmutils.detach_vm_disk.assert_called_once_with(
            mock.sentinel.instance_name, mock.sentinel.disk_path,
            is_physical=False)

    def test_parse_credentials(self):
        username, password = self._volume_driver._parse_credentials(
            self._FAKE_SMB_OPTIONS)
        self.assertEqual(self._FAKE_USERNAME, username)
        self.assertEqual(self._FAKE_PASSWORD, password)

    def test_get_export_path(self):
        result = self._volume_driver._get_export_path(
            self._FAKE_CONNECTION_INFO)

        expected = self._FAKE_SHARE.replace('/', '\\')
        self.assertEqual(expected, result)

    def test_get_disk_path(self):
        expected = os.path.join(self._FAKE_SHARE_NORMALIZED,
                                self._FAKE_DISK_NAME)

        disk_path = self._volume_driver._get_disk_path(
            self._FAKE_CONNECTION_INFO)

        self.assertEqual(expected, disk_path)

    @mock.patch.object(volumeops.SMBFSVolumeDriver, '_parse_credentials')
    def _test_ensure_mounted(self, mock_parse_credentials, is_mounted=False):
        mock_mount_smb_share = self._volume_driver._pathutils.mount_smb_share
        mock_check_smb_mapping = (
            self._volume_driver._pathutils.check_smb_mapping)
        mock_check_smb_mapping.return_value = is_mounted
        mock_parse_credentials.return_value = (
            self._FAKE_USERNAME, self._FAKE_PASSWORD)

        self._volume_driver.ensure_share_mounted(
            self._FAKE_CONNECTION_INFO)

        if is_mounted:
            self.assertFalse(
                mock_mount_smb_share.called)
        else:
            mock_parse_credentials.assert_called_once_with(
                self._FAKE_SMB_OPTIONS)
            mock_mount_smb_share.assert_called_once_with(
                self._FAKE_SHARE_NORMALIZED,
                username=self._FAKE_USERNAME,
                password=self._FAKE_PASSWORD)

    def test_ensure_mounted_new_share(self):
        self._test_ensure_mounted()

    def test_ensure_already_mounted(self):
        self._test_ensure_mounted(is_mounted=True)

    def test_disconnect_volumes(self):
        mock_unmount_smb_share = (
            self._volume_driver._pathutils.unmount_smb_share)
        block_device_mapping = [
            {'connection_info': self._FAKE_CONNECTION_INFO}]
        self._volume_driver.disconnect_volumes(block_device_mapping)
        mock_unmount_smb_share.assert_called_once_with(
            self._FAKE_SHARE_NORMALIZED)

    @mock.patch.object(volumeops.SMBFSVolumeDriver, '_parse_credentials')
    def test_ensure_mounted_missing_opts(self, mock_parse_credentials):
        mock_mount_smb_share = self._volume_driver._pathutils.mount_smb_share
        mock_check_smb_mapping = (
            self._volume_driver._pathutils.check_smb_mapping)
        mock_check_smb_mapping.return_value = False
        mock_parse_credentials.return_value = (None, None)

        fake_conn_info = copy.deepcopy(self._FAKE_CONNECTION_INFO)
        fake_conn_info['data']['options'] = None

        self._volume_driver.ensure_share_mounted(fake_conn_info)

        mock_parse_credentials.assert_called_once_with('')
        mock_mount_smb_share.assert_called_once_with(
            self._FAKE_SHARE_NORMALIZED,
            username=None,
            password=None)

    @mock.patch.object(volumeops.SMBFSVolumeDriver, '_get_disk_path')
    def test_set_disk_qos_specs(self, mock_get_disk_path):
        self._volume_driver.set_disk_qos_specs(mock.sentinel.connection_info,
                                               mock.sentinel.instance_name,
                                               mock.sentinel.min_iops,
                                               mock.sentinel.max_iops)

        mock_disk_path = mock_get_disk_path.return_value
        mock_get_disk_path.assert_called_once_with(
            mock.sentinel.connection_info)
        mock_set_qos_specs = self._volume_driver._vmutils.set_disk_qos_specs
        mock_set_qos_specs.assert_called_once_with(
            mock.sentinel.instance_name,
            mock_disk_path,
            mock.sentinel.min_iops,
            mock.sentinel.max_iops)
