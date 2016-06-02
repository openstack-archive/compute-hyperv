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
import platform
import sys

import ddt
import mock
from oslo_config import cfg

from nova import exception
from nova.tests.unit import fake_block_device
from os_win import exceptions as os_win_exc
from oslo_utils import units

from hyperv.nova import constants
from hyperv.nova import volumeops
from hyperv.tests.unit import test_base

CONF = cfg.CONF

connection_data = {'volume_id': 'fake_vol_id',
                   'target_lun': mock.sentinel.target_lun,
                   'target_iqn': mock.sentinel.target_iqn,
                   'target_portal': mock.sentinel.target_portal,
                   'auth_method': 'chap',
                   'auth_username': mock.sentinel.auth_username,
                   'auth_password': mock.sentinel.auth_password}


def get_fake_block_dev_info(dev_count=1):
    return {'block_device_mapping': [
        fake_block_device.AnonFakeDbBlockDeviceDict({'source_type': 'volume'})
        for dev in range(dev_count)]
    }


def get_fake_connection_info(**kwargs):
    return {'data': dict(connection_data, **kwargs),
            'serial': mock.sentinel.serial}


class VolumeOpsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for VolumeOps class."""

    def setUp(self):
        super(VolumeOpsTestCase, self).setUp()
        self._volumeops = volumeops.VolumeOps()
        self._volumeops._vmutils = mock.Mock()
        self._volumeops._hostutils = mock.Mock()

    def test_verify_setup(self):
        self.flags(use_multipath_io=True, group='hyperv')
        hostutils = self._volumeops._hostutils
        hostutils.check_server_feature.return_value = False

        self.assertRaises(exception.ServiceUnavailable,
                          self._volumeops._verify_setup)
        hostutils.check_server_feature.assert_called_once_with(
            hostutils.FEATURE_MPIO)

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

    def test_fix_instance_volume_disk_paths_empty_bdm(self):
        self._volumeops.fix_instance_volume_disk_paths(
            mock.sentinel.instance_name,
            block_device_info={})
        self.assertFalse(
            self._volumeops._vmutils.get_vm_physical_disk_mapping.called)

    @mock.patch.object(volumeops.VolumeOps, 'get_disk_path_mapping')
    def test_fix_instance_volume_disk_paths(self, mock_get_disk_path_mapping):
        block_device_info = get_fake_block_dev_info()

        mock_disk1 = {
            'mounted_disk_path': mock.sentinel.mounted_disk1_path,
            'resource_path': mock.sentinel.resource1_path
        }
        mock_disk2 = {
            'mounted_disk_path': mock.sentinel.mounted_disk2_path,
            'resource_path': mock.sentinel.resource2_path
        }

        mock_vm_disk_mapping = {
            mock.sentinel.disk1_serial: mock_disk1,
            mock.sentinel.disk2_serial: mock_disk2
        }
        # In this case, only the first disk needs to be updated.
        mock_phys_disk_path_mapping = {
            mock.sentinel.disk1_serial: mock.sentinel.actual_disk1_path,
            mock.sentinel.disk2_serial: mock.sentinel.mounted_disk2_path
        }

        vmutils = self._volumeops._vmutils
        vmutils.get_vm_physical_disk_mapping.return_value = (
            mock_vm_disk_mapping)

        mock_get_disk_path_mapping.return_value = mock_phys_disk_path_mapping

        self._volumeops.fix_instance_volume_disk_paths(
            mock.sentinel.instance_name,
            block_device_info)

        vmutils.get_vm_physical_disk_mapping.assert_called_once_with(
            mock.sentinel.instance_name)
        mock_get_disk_path_mapping.assert_called_once_with(
            block_device_info)
        vmutils.set_disk_host_res.assert_called_once_with(
            mock.sentinel.resource1_path,
            mock.sentinel.actual_disk1_path)

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    @mock.patch.object(volumeops.VolumeOps, 'parse_disk_qos_specs')
    def test_attach_volume_exc(self, mock_parse_qos_specs,
                               mock_get_volume_driver):
        fake_conn_info = {
            'data': {'qos_specs': mock.sentinel.qos_specs}
        }

        mock_volume_driver = mock_get_volume_driver.return_value
        mock_volume_driver.set_disk_qos_specs.side_effect = (
            exception.NovaException)
        mock_parse_qos_specs.return_value = [
            mock.sentinel.min_iops,
            mock.sentinel.max_iops
        ]

        self.assertRaises(exception.NovaException,
                          self._volumeops.attach_volume,
                          fake_conn_info,
                          mock.sentinel.instance_name,
                          mock.sentinel.fake_disk_bus)

        mock_get_volume_driver.assert_called_once_with(fake_conn_info)
        mock_volume_driver.attach_volume.assert_called_once_with(
            fake_conn_info,
            mock.sentinel.instance_name,
            disk_bus=mock.sentinel.fake_disk_bus)
        mock_parse_qos_specs.assert_called_once_with(mock.sentinel.qos_specs)
        mock_volume_driver.set_disk_qos_specs.assert_called_once_with(
            fake_conn_info, mock.sentinel.min_iops, mock.sentinel.max_iops)

        # We check that the volume was detached and disconnected
        # after a failed attach attempt.
        mock_volume_driver.detach_volume.assert_called_once_with(
            fake_conn_info, mock.sentinel.instance_name)
        mock_volume_driver.disconnect_volume.assert_called_once_with(
            fake_conn_info)

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    def test_detach_volume(self, mock_get_volume_driver):
        self._volumeops.detach_volume(mock.sentinel.conn_info,
                                      mock.sentinel.instance_name)

        mock_get_volume_driver.assert_called_once_with(
            mock.sentinel.conn_info)
        mock_volume_driver = mock_get_volume_driver.return_value
        mock_volume_driver.detach_volume.assert_called_once_with(
            mock.sentinel.conn_info, mock.sentinel.instance_name)
        mock_volume_driver.disconnect_volume.assert_called_once_with(
            mock.sentinel.conn_info)

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    def test_disconnect_volumes(self, mock_get_volume_driver):
        block_device_info = get_fake_block_dev_info()
        conn_info = block_device_info[
            'block_device_mapping'][0]['connection_info']

        self._volumeops.disconnect_volumes(block_device_info)

        mock_get_volume_driver.assert_called_once_with(conn_info)
        disconnect_volume = (
            mock_get_volume_driver.return_value.disconnect_volume)
        disconnect_volume.assert_called_once_with(
            conn_info)

    @mock.patch('nova.block_device.volume_in_mapping')
    def test_ebs_root_in_block_devices(self, mock_vol_in_mapping):
        block_device_info = get_fake_block_dev_info()

        response = self._volumeops.ebs_root_in_block_devices(block_device_info)

        mock_vol_in_mapping.assert_called_once_with(
            self._volumeops._default_root_device, block_device_info)
        self.assertEqual(mock_vol_in_mapping.return_value, response)

    def test_get_volume_connector(self):
        fake_connector_props = {
            'fake_vol_driver_specific_prop':
                mock.sentinel.vol_driver_specific_val
        }
        mock_vol_driver = mock.Mock()
        mock_vol_driver.get_volume_connector_props.return_value = (
            fake_connector_props)
        mock_vol_drivers = {
            mock.sentinel.vol_driver_type: mock_vol_driver
        }
        self._volumeops.volume_drivers = mock_vol_drivers

        connector = self._volumeops.get_volume_connector()

        expected_connector = dict(ip=CONF.my_ip,
                                  host=CONF.host,
                                  multipath=CONF.hyperv.use_multipath_io,
                                  os_type=sys.platform,
                                  platform=platform.machine(),
                                  **fake_connector_props)
        self.assertEqual(expected_connector, connector)

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    def test_connect_volumes(self, mock_get_volume_driver):
        block_device_info = get_fake_block_dev_info()
        conn_info = block_device_info[
            'block_device_mapping'][0]['connection_info']

        self._volumeops.connect_volumes(block_device_info)

        mock_get_volume_driver.assert_called_once_with(conn_info)
        connect_volume = (
            mock_get_volume_driver.return_value.connect_volume)
        connect_volume.assert_called_once_with(
            conn_info)

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    def test_get_disk_path_mapping(self, mock_get_vol_drv):
        block_device_info = get_fake_block_dev_info(dev_count=2)
        block_device_mapping = block_device_info['block_device_mapping']

        block_dev_conn_info = get_fake_connection_info()
        block_dev_conn_info['serial'] = mock.sentinel.block_dev_serial

        # We expect this to be filtered out if only block devices are
        # requested.
        disk_file_conn_info = get_fake_connection_info()
        disk_file_conn_info['serial'] = mock.sentinel.disk_file_serial

        block_device_mapping[0]['connection_info'] = block_dev_conn_info
        block_device_mapping[1]['connection_info'] = disk_file_conn_info

        block_dev_drv = mock.Mock(_is_block_dev=True)
        mock_get_vol_drv.side_effect = [block_dev_drv,
                                        mock.Mock(_is_block_dev=False)]

        block_dev_drv.get_disk_resource_path.return_value = (
            mock.sentinel.disk_path)

        resulted_disk_path_mapping = self._volumeops.get_disk_path_mapping(
            block_device_info, block_dev_only=True)

        block_dev_drv.get_disk_resource_path.assert_called_once_with(
            block_dev_conn_info)
        expected_disk_path_mapping = {
            mock.sentinel.block_dev_serial: mock.sentinel.disk_path
        }
        self.assertEqual(expected_disk_path_mapping,
                         resulted_disk_path_mapping)

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

        self.assertRaises(exception.Invalid,
                          self._volumeops.parse_disk_qos_specs,
                          fake_qos_specs)

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    def test_get_disk_resource_path(self, mock_get_volume_driver):
        fake_conn_info = get_fake_connection_info()
        fake_volume_driver = mock_get_volume_driver.return_value

        resulted_disk_path = self._volumeops.get_disk_resource_path(
            fake_conn_info)

        mock_get_volume_driver.assert_called_once_with(fake_conn_info)
        fake_volume_driver.get_disk_resource_path.assert_called_once_with(
            fake_conn_info)
        self.assertEqual(
            fake_volume_driver.get_disk_resource_path.return_value,
            resulted_disk_path)


class BaseVolumeDriverTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for Hyper-V BaseVolumeDriver class."""

    @mock.patch.object(volumeops.BaseVolumeDriver,
                       '__abstractmethods__', set())
    def setUp(self):
        super(BaseVolumeDriverTestCase, self).setUp()
        self._base_vol_driver = volumeops.BaseVolumeDriver()
        self._base_vol_driver._vmutils = mock.MagicMock()

    @mock.patch.object(volumeops.BaseVolumeDriver,
                       'get_disk_resource_path')
    @mock.patch.object(volumeops.BaseVolumeDriver, '_get_disk_ctrl_and_slot')
    def _test_attach_volume(self, mock_get_disk_ctrl_and_slot,
                            mock_get_disk_resource_path,
                            is_block_dev=True):
        connection_info = get_fake_connection_info()
        self._base_vol_driver._is_block_dev = is_block_dev
        vmutils = self._base_vol_driver._vmutils

        mock_get_disk_resource_path.return_value = (
            mock.sentinel.disk_path)
        mock_get_disk_ctrl_and_slot.return_value = (
            mock.sentinel.ctrller_path,
            mock.sentinel.slot)

        self._base_vol_driver.attach_volume(
            connection_info=connection_info,
            instance_name=mock.sentinel.instance_name,
            disk_bus=mock.sentinel.disk_bus)

        if is_block_dev:
            vmutils.attach_volume_to_controller.assert_called_once_with(
                mock.sentinel.instance_name,
                mock.sentinel.ctrller_path,
                mock.sentinel.slot,
                mock.sentinel.disk_path,
                serial=connection_info['serial'])
        else:
            vmutils.attach_drive.assert_called_once_with(
                mock.sentinel.instance_name,
                mock.sentinel.disk_path,
                mock.sentinel.ctrller_path,
                mock.sentinel.slot)

        mock_get_disk_resource_path.assert_called_once_with(
            connection_info)
        mock_get_disk_ctrl_and_slot.assert_called_once_with(
            mock.sentinel.instance_name, mock.sentinel.disk_bus)

    def test_attach_volume_image_file(self):
        self._test_attach_volume(is_block_dev=True)

    def test_attach_volume_block_dev(self):
        self._test_attach_volume()

    @mock.patch.object(volumeops.BaseVolumeDriver,
                       'get_disk_resource_path')
    def test_detach_volume(self, mock_get_disk_resource_path):
        connection_info = get_fake_connection_info()

        self._base_vol_driver.detach_volume(connection_info,
                                            mock.sentinel.instance_name)

        mock_get_disk_resource_path.assert_called_once_with(
            connection_info)
        self._base_vol_driver._vmutils.detach_vm_disk.assert_called_once_with(
            mock.sentinel.instance_name,
            mock_get_disk_resource_path.return_value,
            is_physical=self._base_vol_driver._is_block_dev)

    def _test_get_disk_ctrl_and_slot(self, disk_bus=constants.CTRL_TYPE_IDE):
        ctrl, slot = self._base_vol_driver._get_disk_ctrl_and_slot(
            mock.sentinel.instance_name,
            disk_bus)

        vmutils = self._base_vol_driver._vmutils
        if disk_bus == constants.CTRL_TYPE_IDE:
            expected_ctrl = vmutils.get_vm_ide_controller.return_value
            expected_slot = 0

            vmutils.get_vm_ide_controller.assert_called_once_with(
                mock.sentinel.instance_name, 0)
        else:
            expected_ctrl = vmutils.get_vm_scsi_controller.return_value
            expected_slot = vmutils.get_free_controller_slot.return_value

            vmutils.get_vm_scsi_controller.assert_called_once_with(
                mock.sentinel.instance_name)
            vmutils.get_free_controller_slot(
                vmutils.get_vm_scsi_controller.return_value)

        self.assertEqual(expected_ctrl, ctrl)
        self.assertEqual(expected_slot, slot)

    def test_get_disk_ctrl_and_slot_ide(self):
        self._test_get_disk_ctrl_and_slot()

    def test_get_disk_ctrl_and_slot_scsi(self):
        self._test_get_disk_ctrl_and_slot(
            disk_bus=constants.CTRL_TYPE_SCSI)

    def test_get_disk_ctrl_and_slot_unknown(self):
        self.assertRaises(exception.Invalid,
                          self._base_vol_driver._get_disk_ctrl_and_slot,
                          mock.sentinel.instance_name,
                          'fake bus')

    def test_check_device_paths_multiple_found(self):
        device_paths = [mock.sentinel.dev_path_0, mock.sentinel.dev_path_1]
        self.assertRaises(exception.InvalidDevicePath,
                          self._base_vol_driver._check_device_paths,
                          device_paths)

    def test_check_device_paths_none_found(self):
        self.assertRaises(exception.DiskNotFound,
                          self._base_vol_driver._check_device_paths,
                          [])

    def test_check_device_paths_one_device_found(self):
        self._base_vol_driver._check_device_paths([mock.sentinel.dev_path])

    def test_get_mounted_disk_by_dev_name(self):
        vmutils = self._base_vol_driver._vmutils
        diskutils = self._base_vol_driver._diskutils
        mock_get_dev_number = diskutils.get_device_number_from_device_name

        mock_get_dev_number.return_value = mock.sentinel.dev_number
        vmutils.get_mounted_disk_by_drive_number.return_value = (
            mock.sentinel.disk_path)

        disk_path = self._base_vol_driver._get_mounted_disk_path_by_dev_name(
            mock.sentinel.dev_name)

        mock_get_dev_number.assert_called_once_with(mock.sentinel.dev_name)
        vmutils.get_mounted_disk_by_drive_number.assert_called_once_with(
            mock.sentinel.dev_number)

        self.assertEqual(mock.sentinel.disk_path, disk_path)


@ddt.ddt
class ISCSIVolumeDriverTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for Hyper-V ISCSIVolumeDriver class."""

    def setUp(self):
        super(ISCSIVolumeDriverTestCase, self).setUp()
        self._volume_driver = volumeops.ISCSIVolumeDriver()
        self._iscsi_utils = self._volume_driver._iscsi_utils
        self._diskutils = self._volume_driver._diskutils

    def _test_get_volume_connector_props(self, initiator_present=True):
        expected_initiator = self._volume_driver._initiator_node_name
        expected_props = dict(initiator=expected_initiator)
        resulted_props = self._volume_driver.get_volume_connector_props()
        self.assertEqual(expected_props, resulted_props)

    def test_get_vol_connector_props(self):
        self._test_get_volume_connector_props()

    def test_get_vol_connector_props_without_initiator(self):
        self._test_get_volume_connector_props(initiator_present=False)

    @ddt.data({'requested_initiators': [mock.sentinel.initiator_0],
               'available_initiators': [mock.sentinel.initiator_0,
                                        mock.sentinel.initiator_1]},
              {'requested_initiators': [mock.sentinel.initiator_0],
               'available_initiators': [mock.sentinel.initiator_1]})
    @ddt.unpack
    def test_validate_initiators(self, requested_initiators,
                                 available_initiators):
        self.flags(iscsi_initiator_list=requested_initiators, group='hyperv')
        self._iscsi_utils.get_iscsi_initiators.return_value = (
            available_initiators)

        expected_valid_initiator = not (
            set(requested_initiators).difference(set(available_initiators)))
        valid_initiator = self._volume_driver.validate_initiators()

        self.assertEqual(expected_valid_initiator, valid_initiator)

    def test_get_all_targets_multipath(self):
        conn_props = {'target_portals': [mock.sentinel.portal0,
                                         mock.sentinel.portal1],
                      'target_iqns': [mock.sentinel.target0,
                                      mock.sentinel.target1],
                      'target_luns': [mock.sentinel.lun0,
                                      mock.sentinel.lun1]}
        expected_targets = zip(conn_props['target_portals'],
                               conn_props['target_iqns'],
                               conn_props['target_luns'])

        resulted_targets = self._volume_driver._get_all_targets(conn_props)
        self.assertEqual(list(expected_targets), list(resulted_targets))

    def test_get_all_targets_single_path(self):
        conn_props = dict(target_portal=mock.sentinel.portal,
                          target_iqn=mock.sentinel.target,
                          target_lun=mock.sentinel.lun)
        expected_targets = [
            (mock.sentinel.portal, mock.sentinel.target, mock.sentinel.lun)]
        resulted_targets = self._volume_driver._get_all_targets(conn_props)
        self.assertEqual(expected_targets, resulted_targets)

    @ddt.data([mock.sentinel.initiator_1, mock.sentinel.initiator_2], [])
    @mock.patch.object(volumeops.ISCSIVolumeDriver, '_get_all_targets')
    def test_get_all_paths(self, requested_initiators, mock_get_all_targets):
        self.flags(iscsi_initiator_list=requested_initiators, group='hyperv')

        target = (mock.sentinel.portal, mock.sentinel.target,
                  mock.sentinel.lun)
        mock_get_all_targets.return_value = [target]

        paths = self._volume_driver._get_all_paths(mock.sentinel.conn_props)

        expected_initiators = requested_initiators or [None]
        expected_paths = [(initiator, ) + target
                          for initiator in expected_initiators]

        self.assertEqual(expected_paths, paths)
        mock_get_all_targets.assert_called_once_with(mock.sentinel.conn_props)

    @ddt.data(True, False)
    @mock.patch.object(volumeops.ISCSIVolumeDriver, '_get_all_paths')
    def test_connect_volume(self, use_multipath,
                            mock_get_all_paths):
        self.flags(use_multipath_io=use_multipath, group='hyperv')
        fake_paths = [(mock.sentinel.initiator_name,
                       mock.sentinel.target_portal,
                       mock.sentinel.target_iqn,
                       mock.sentinel.target_lun)] * 3

        mock_get_all_paths.return_value = fake_paths
        self._iscsi_utils.login_storage_target.side_effect = [
            os_win_exc.OSWinException, None, None]

        conn_info = get_fake_connection_info()
        conn_props = conn_info['data']

        self._volume_driver.connect_volume(conn_info)

        mock_get_all_paths.assert_called_once_with(conn_props)
        expected_login_attempts = 3 if use_multipath else 2
        self._iscsi_utils.login_storage_target.assert_has_calls(
            [mock.call(target_lun=mock.sentinel.target_lun,
                       target_iqn=mock.sentinel.target_iqn,
                       target_portal=mock.sentinel.target_portal,
                       auth_username=conn_props['auth_username'],
                       auth_password=conn_props['auth_password'],
                       mpio_enabled=use_multipath,
                       initiator_name=mock.sentinel.initiator_name)] *
            expected_login_attempts)

    @mock.patch.object(volumeops.ISCSIVolumeDriver, '_get_all_paths')
    def test_connect_volume_failed(self, mock_get_all_paths):
        self.flags(use_multipath_io=True, group='hyperv')
        fake_paths = [(mock.sentinel.initiator_name,
                       mock.sentinel.target_portal,
                       mock.sentinel.target_iqn,
                       mock.sentinel.target_lun)] * 3

        mock_get_all_paths.return_value = fake_paths
        self._iscsi_utils.login_storage_target.side_effect = (
            os_win_exc.OSWinException)

        self.assertRaises(exception.VolumeAttachFailed,
                          self._volume_driver.connect_volume,
                          get_fake_connection_info())

    def test_connect_volume_invalid_auth_method(self):
        conn_info = get_fake_connection_info(auth_method='fake_auth')
        self.assertRaises(exception.UnsupportedBDMVolumeAuthMethod,
                          self._volume_driver.connect_volume,
                          conn_info)

    @mock.patch.object(volumeops.ISCSIVolumeDriver, '_get_all_targets')
    def test_disconnect_volume(self, mock_get_all_targets):
        targets = [
            (mock.sentinel.portal_0, mock.sentinel.tg_0, mock.sentinel.lun_0),
            (mock.sentinel.portal_1, mock.sentinel.tg_1, mock.sentinel.lun_1)]

        mock_get_all_targets.return_value = targets
        self._iscsi_utils.get_target_luns.return_value = [mock.sentinel.lun_0]

        conn_info = get_fake_connection_info()
        self._volume_driver.disconnect_volume(conn_info)

        self._diskutils.rescan_disks.assert_called_once_with()
        mock_get_all_targets.assert_called_once_with(conn_info['data'])
        self._iscsi_utils.logout_storage_target.assert_called_once_with(
            mock.sentinel.tg_0)
        self._iscsi_utils.get_target_luns.assert_has_calls(
            [mock.call(mock.sentinel.tg_0), mock.call(mock.sentinel.tg_1)])

    @mock.patch.object(volumeops.ISCSIVolumeDriver, '_get_all_targets')
    @mock.patch.object(volumeops.ISCSIVolumeDriver, '_check_device_paths')
    @mock.patch.object(volumeops.ISCSIVolumeDriver,
                       '_get_mounted_disk_path_by_dev_name')
    def test_get_disk_resource_path(self, mock_get_mounted_disk,
                                    mock_check_dev_paths,
                                    mock_get_all_targets):
        targets = [
            (mock.sentinel.portal_0, mock.sentinel.tg_0, mock.sentinel.lun_0),
            (mock.sentinel.portal_1, mock.sentinel.tg_1, mock.sentinel.lun_1)]

        mock_get_all_targets.return_value = targets
        self._iscsi_utils.get_device_number_and_path.return_value = [
            mock.sentinel.dev_num, mock.sentinel.dev_path]

        conn_info = get_fake_connection_info()
        volume_paths = self._volume_driver.get_disk_resource_path(conn_info)
        self.assertEqual(mock_get_mounted_disk.return_value, volume_paths)

        mock_get_all_targets.assert_called_once_with(conn_info['data'])
        self._iscsi_utils.get_device_number_and_path.assert_has_calls(
            [mock.call(mock.sentinel.tg_0, mock.sentinel.lun_0),
             mock.call(mock.sentinel.tg_1, mock.sentinel.lun_1)])
        mock_check_dev_paths.assert_called_once_with(
            set([mock.sentinel.dev_path]))
        mock_get_mounted_disk.assert_called_once_with(mock.sentinel.dev_path)


@ddt.ddt
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
                                      'options': _FAKE_SMB_OPTIONS,
                                      'volume_id': 'fake_vol_id'}}

    def setUp(self):
        super(SMBFSVolumeDriverTestCase, self).setUp()
        self._volume_driver = volumeops.SMBFSVolumeDriver()
        self._volume_driver._vmutils = mock.MagicMock()
        self._volume_driver._smbutils = mock.MagicMock()
        self._volume_driver._pathutils = mock.MagicMock()
        self._smbutils = self._volume_driver._smbutils
        self._pathutils = self._volume_driver._pathutils

    @mock.patch.object(volumeops.SMBFSVolumeDriver, '_get_disk_path')
    def test_get_disk_resource_path(self, mock_get_disk_path):
        disk_path = self._volume_driver.get_disk_resource_path(
            mock.sentinel.conn_info)

        self.assertEqual(mock_get_disk_path.return_value, disk_path)
        mock_get_disk_path.assert_called_once_with(mock.sentinel.conn_info)

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

    @ddt.data(True, False)
    def test_get_disk_path(self, is_local):
        fake_local_share_path = 'fake_local_share_path'
        self._smbutils.is_local_share.return_value = is_local
        self._smbutils.get_smb_share_path.return_value = (
            fake_local_share_path)

        expected_dir = (fake_local_share_path if is_local
                        else self._FAKE_SHARE_NORMALIZED)
        expected_path = os.path.join(expected_dir,
                                     self._FAKE_DISK_NAME)

        disk_path = self._volume_driver._get_disk_path(
            self._FAKE_CONNECTION_INFO)

        self._smbutils.is_local_share.assert_called_once_with(
            self._FAKE_SHARE_NORMALIZED)
        if is_local:
            self._smbutils.get_smb_share_path.assert_called_once_with(
                'fake_share')
        self.assertEqual(expected_path, disk_path)

    def test_get_disk_path_not_found(self):
        self._smbutils.is_local_share.return_value = True
        self._smbutils.get_smb_share_path.return_value = False

        self.assertRaises(exception.DiskNotFound,
                          self._volume_driver._get_disk_path,
                          self._FAKE_CONNECTION_INFO)

    @mock.patch.object(volumeops.SMBFSVolumeDriver, '_parse_credentials')
    @ddt.data({},
              {'is_mounted': True},
              {'is_local': True})
    @ddt.unpack
    def test_ensure_mounted(self, mock_parse_credentials,
                            is_mounted=False, is_local=False):
        self._smbutils.is_local_share.return_value = is_local
        mock_mount_smb_share = self._volume_driver._smbutils.mount_smb_share
        mock_check_smb_mapping = (
            self._volume_driver._smbutils.check_smb_mapping)
        mock_check_smb_mapping.return_value = is_mounted
        mock_parse_credentials.return_value = (
            self._FAKE_USERNAME, self._FAKE_PASSWORD)

        self._volume_driver.ensure_share_mounted(
            self._FAKE_CONNECTION_INFO)

        self._smbutils.is_local_share.assert_called_once_with(
            self._FAKE_SHARE_NORMALIZED)

        if is_local or is_mounted:
            self.assertFalse(
                mock_mount_smb_share.called)
        else:
            mock_parse_credentials.assert_called_once_with(
                self._FAKE_SMB_OPTIONS)
            mock_mount_smb_share.assert_called_once_with(
                self._FAKE_SHARE_NORMALIZED,
                username=self._FAKE_USERNAME,
                password=self._FAKE_PASSWORD)

    @mock.patch.object(volumeops.SMBFSVolumeDriver, '_parse_credentials')
    def test_ensure_mounted_missing_opts(self, mock_parse_credentials):
        self._smbutils.is_local_share.return_value = False
        mock_mount_smb_share = self._volume_driver._smbutils.mount_smb_share
        mock_check_smb_mapping = (
            self._volume_driver._smbutils.check_smb_mapping)
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
                                               mock.sentinel.min_iops,
                                               mock.sentinel.max_iops)

        mock_disk_path = mock_get_disk_path.return_value
        mock_get_disk_path.assert_called_once_with(
            mock.sentinel.connection_info)
        mock_set_qos_specs = self._volume_driver._vmutils.set_disk_qos_specs
        mock_set_qos_specs.assert_called_once_with(
            mock_disk_path,
            mock.sentinel.min_iops,
            mock.sentinel.max_iops)

    def test_disconnect_volume(self):
        self._volume_driver.disconnect_volume(self._FAKE_CONNECTION_INFO)

        mock_unmount_share = self._volume_driver._smbutils.unmount_smb_share
        mock_unmount_share.assert_called_once_with(
            self._FAKE_SHARE_NORMALIZED)


class FCVolumeDriverTestCase(test_base.HyperVBaseTestCase):
    def setUp(self):
        super(FCVolumeDriverTestCase, self).setUp()
        self._fc_driver = volumeops.FCVolumeDriver()
        self._fc_driver._fc_utils = mock.MagicMock()
        self._fc_driver._vmutils = mock.MagicMock()

        self._fc_utils = self._fc_driver._fc_utils
        self._vmutils = self._fc_driver._vmutils

    def _test_get_volume_connector_props(self, valid_fc_hba_ports=True):
        fake_fc_hba_ports = [{'node_name': mock.sentinel.node_name,
                              'port_name': mock.sentinel.port_name},
                             {'node_name': mock.sentinel.second_node_name,
                              'port_name': mock.sentinel.second_port_name}]
        self._fc_utils.get_fc_hba_ports.return_value = (
            fake_fc_hba_ports if valid_fc_hba_ports else [])

        resulted_fc_hba_ports = self._fc_driver.get_volume_connector_props()

        self._fc_utils.refresh_hba_configuration.assert_called_once_with()
        self._fc_utils.get_fc_hba_ports.assert_called_once_with()

        if valid_fc_hba_ports:
            expected_fc_hba_ports = {
                'wwpns': [mock.sentinel.port_name,
                          mock.sentinel.second_port_name],
                'wwnns': [mock.sentinel.node_name,
                          mock.sentinel.second_node_name]
            }
        else:
            expected_fc_hba_ports = {}

        self.assertItemsEqual(expected_fc_hba_ports, resulted_fc_hba_ports)

    def test_get_volume_connector_props(self):
        self._test_get_volume_connector_props()

    def test_get_volume_connector_props_missing_hbas(self):
        self._test_get_volume_connector_props(valid_fc_hba_ports=False)

    @mock.patch.object(volumeops.FCVolumeDriver, 'get_disk_resource_path')
    def test_connect_volume(self, mock_get_disk_path):
        self._fc_driver.connect_volume(mock.sentinel.conn_info)
        mock_get_disk_path.assert_called_once_with(mock.sentinel.conn_info)

    @mock.patch.object(volumeops.FCVolumeDriver,
                       '_get_mounted_disk_path_by_dev_name')
    @mock.patch.object(volumeops.FCVolumeDriver, '_get_fc_volume_mappings')
    @mock.patch.object(volumeops.FCVolumeDriver, '_check_device_paths')
    def _test_get_disk_resource_path(self, mock_check_dev_paths,
                                     mock_get_fc_mappings,
                                     mock_get_disk_path_by_dev,
                                     fc_mappings_side_effect,
                                     expected_rescan_count,
                                     retrieved_dev_name=None):
        mock_get_fc_mappings.side_effect = fc_mappings_side_effect
        mock_get_disk_path_by_dev.return_value = mock.sentinel.disk_path

        if retrieved_dev_name:
            disk_path = self._fc_driver.get_disk_resource_path(
                mock.sentinel.conn_info)
            self.assertEqual(mock.sentinel.disk_path, disk_path)
            mock_check_dev_paths.assert_called_once_with(
                set([retrieved_dev_name]))
            mock_get_disk_path_by_dev.assert_called_once_with(
                retrieved_dev_name)
        else:
            self.assertRaises(
                exception.DiskNotFound,
                self._fc_driver.get_disk_resource_path,
                mock.sentinel.conn_info)

        mock_get_fc_mappings.assert_any_call(mock.sentinel.conn_info)
        self.assertEqual(
            expected_rescan_count,
            self._fc_driver._diskutils.rescan_disks.call_count)

    def test_get_disk_resource_path_missing_dev_name(self):
        mock_mapping = dict(device_name='')
        fc_mappings_side_effect = [[]] + [[mock_mapping]] * 9

        self._test_get_disk_resource_path(
            fc_mappings_side_effect=fc_mappings_side_effect,
            expected_rescan_count=self._fc_driver._MAX_RESCAN_COUNT)

    def test_get_disk_resource_path_dev_name_found(self):
        dev_name = mock.sentinel.dev_name
        mock_mapping = dict(device_name=dev_name)

        self._test_get_disk_resource_path(
            fc_mappings_side_effect=[[mock_mapping]],
            expected_rescan_count=1,
            retrieved_dev_name=dev_name)

    @mock.patch.object(volumeops.FCVolumeDriver, '_get_fc_hba_mapping')
    def test_get_fc_volume_mappings(self, mock_get_fc_hba_mapping):
        fake_target_wwpn = 'FAKE_TARGET_WWPN'
        connection_info = get_fake_connection_info(
            target_lun=mock.sentinel.target_lun,
            target_wwn=[fake_target_wwpn])

        mock_hba_mapping = {mock.sentinel.node_name: mock.sentinel.hba_ports}
        mock_get_fc_hba_mapping.return_value = mock_hba_mapping

        all_target_mappings = [{'device_name': mock.sentinel.dev_name,
                                'port_name': fake_target_wwpn,
                                'lun': mock.sentinel.target_lun},
                               {'device_name': mock.sentinel.dev_name_1,
                                'port_name': mock.sentinel.target_port_name_1,
                                'lun': mock.sentinel.target_lun},
                               {'device_name': mock.sentinel.dev_name,
                                'port_name': mock.sentinel.target_port_name,
                                'lun': mock.sentinel.target_lun_1}]
        expected_mappings = [all_target_mappings[0]]

        self._fc_utils.get_fc_target_mappings.return_value = (
            all_target_mappings)

        volume_mappings = self._fc_driver._get_fc_volume_mappings(
            connection_info)
        self.assertEqual(expected_mappings, volume_mappings)

    def test_get_fc_hba_mapping(self):
        fake_fc_hba_ports = [{'node_name': mock.sentinel.node_name,
                              'port_name': mock.sentinel.port_name}]

        self._fc_utils.get_fc_hba_ports.return_value = fake_fc_hba_ports

        resulted_mapping = self._fc_driver._get_fc_hba_mapping()

        expected_mapping = volumeops.collections.defaultdict(list)
        expected_mapping[mock.sentinel.node_name].append(
            mock.sentinel.port_name)
        self.assertEqual(expected_mapping, resulted_mapping)
