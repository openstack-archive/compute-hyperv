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

import contextlib
import os

import ddt
import mock
from nova.compute import task_states
from nova import exception
from nova import test
from nova.tests.unit import fake_block_device
from os_brick.initiator import connector
from os_win import constants as os_win_const
from oslo_utils import units

from compute_hyperv.nova import block_device_manager
import compute_hyperv.nova.conf
from compute_hyperv.nova import constants
from compute_hyperv.nova import vmops
from compute_hyperv.nova import volumeops
from compute_hyperv.tests import fake_instance
from compute_hyperv.tests.unit import test_base

CONF = compute_hyperv.nova.conf.CONF

connection_data = {'volume_id': 'fake_vol_id',
                   'target_lun': mock.sentinel.fake_lun,
                   'target_iqn': mock.sentinel.fake_iqn,
                   'target_portal': mock.sentinel.fake_portal,
                   'auth_method': 'chap',
                   'auth_username': mock.sentinel.fake_user,
                   'auth_password': mock.sentinel.fake_pass}


def get_fake_block_dev_info(dev_count=1):
    return {'block_device_mapping': [
        fake_block_device.AnonFakeDbBlockDeviceDict({'source_type': 'volume'})
        for dev in range(dev_count)]
    }


def get_fake_connection_info(**kwargs):
    return {'data': dict(connection_data, **kwargs),
            'serial': mock.sentinel.serial}


@ddt.ddt
class VolumeOpsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for VolumeOps class."""

    _autospec_classes = [
        volumeops.cinder.API,
    ]

    def setUp(self):
        super(VolumeOpsTestCase, self).setUp()
        self._volumeops = volumeops.VolumeOps()
        self._volume_api = self._volumeops._volume_api

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

    def test_validate_host_configuration(self):
        self._volumeops.volume_drivers = {
            constants.STORAGE_PROTOCOL_SMBFS: mock.Mock(
                side_effect=exception.ValidationError),
            constants.STORAGE_PROTOCOL_ISCSI: mock.Mock(
                side_effect=exception.ValidationError),
            constants.STORAGE_PROTOCOL_FC: mock.Mock()
        }

        self._volumeops.validate_host_configuration()

        for volume_drv in self._volumeops.volume_drivers.values():
            volume_drv.validate_host_configuration.assert_called_once_with()

    @mock.patch.object(volumeops.VolumeOps, 'attach_volume')
    def test_attach_volumes(self, mock_attach_volume):
        block_device_info = get_fake_block_dev_info()

        self._volumeops.attach_volumes(
            mock.sentinel.context,
            block_device_info['block_device_mapping'],
            mock.sentinel.instance)

        mock_attach_volume.assert_called_once_with(
            mock.sentinel.context,
            block_device_info['block_device_mapping'][0]['connection_info'],
            mock.sentinel.instance)

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
    def test_disconnect_volumes(self, mock_get_volume_driver):
        block_device_info = get_fake_block_dev_info()
        block_device_mapping = block_device_info['block_device_mapping']
        fake_volume_driver = mock_get_volume_driver.return_value

        self._volumeops.disconnect_volumes(block_device_info)
        fake_volume_driver.disconnect_volume.assert_called_once_with(
            block_device_mapping[0]['connection_info'])

    @ddt.data({},
              {'attach_failed': True},
              {'update_device_metadata': True})
    @ddt.unpack
    @mock.patch('time.sleep')
    @mock.patch.object(volumeops.VolumeOps, 'detach_volume')
    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    @mock.patch.object(vmops.VMOps, 'update_device_metadata')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       'set_volume_bdm_connection_info')
    def test_attach_volume(self, mock_set_bdm_conn_info,
                           mock_update_dev_meta,
                           mock_get_volume_driver,
                           mock_detach,
                           mock_sleep,
                           attach_failed=False,
                           update_device_metadata=False):
        mock_instance = fake_instance.fake_instance_obj()
        fake_conn_info = get_fake_connection_info(
            qos_specs=mock.sentinel.qos_specs)
        fake_volume_driver = mock_get_volume_driver.return_value

        expected_try_count = 1
        if attach_failed:
            expected_try_count += CONF.hyperv.volume_attach_retry_count

            fake_volume_driver.set_disk_qos_specs.side_effect = (
                test.TestingException)

            self.assertRaises(exception.VolumeAttachFailed,
                              self._volumeops.attach_volume,
                              mock.sentinel.context,
                              fake_conn_info,
                              mock_instance,
                              mock.sentinel.disk_bus,
                              update_device_metadata)
        else:
            self._volumeops.attach_volume(
                mock.sentinel.context,
                fake_conn_info,
                mock_instance,
                mock.sentinel.disk_bus,
                update_device_metadata)

        mock_get_volume_driver.assert_any_call(
            fake_conn_info)
        fake_volume_driver.attach_volume.assert_has_calls(
            [mock.call(fake_conn_info,
                       mock_instance.name,
                       mock.sentinel.disk_bus)] * expected_try_count)
        fake_volume_driver.set_disk_qos_specs.assert_has_calls(
            [mock.call(fake_conn_info,
                       mock.sentinel.qos_specs)] * expected_try_count)

        if update_device_metadata:
            mock_set_bdm_conn_info.assert_has_calls(
                [mock.call(mock.sentinel.context,
                           mock_instance,
                           fake_conn_info)] * expected_try_count)
            mock_update_dev_meta.assert_has_calls(
                [mock.call(mock.sentinel.context,
                           mock_instance)] * expected_try_count)
        else:
            mock_set_bdm_conn_info.assert_not_called()
            mock_update_dev_meta.assert_not_called()

        if attach_failed:
            mock_detach.assert_called_once_with(
                mock.sentinel.context,
                fake_conn_info,
                mock_instance,
                update_device_metadata)
            mock_sleep.assert_has_calls(
                [mock.call(CONF.hyperv.volume_attach_retry_interval)] *
                    CONF.hyperv.volume_attach_retry_count)
        else:
            mock_sleep.assert_not_called()

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    def test_disconnect_volume(self, mock_get_volume_driver):
        fake_volume_driver = mock_get_volume_driver.return_value

        self._volumeops.disconnect_volume(mock.sentinel.conn_info)

        mock_get_volume_driver.assert_called_once_with(
            mock.sentinel.conn_info)
        fake_volume_driver.disconnect_volume.assert_called_once_with(
            mock.sentinel.conn_info)

    @ddt.data(True, False)
    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    @mock.patch.object(vmops.VMOps, 'update_device_metadata')
    def test_detach_volume(self, update_device_metadata,
                           mock_update_dev_meta,
                           mock_get_volume_driver):
        mock_instance = fake_instance.fake_instance_obj()
        fake_volume_driver = mock_get_volume_driver.return_value
        fake_conn_info = {'data': 'fake_conn_info_data'}

        self._volumeops.detach_volume(mock.sentinel.context,
                                      fake_conn_info,
                                      mock_instance,
                                      update_device_metadata)

        mock_get_volume_driver.assert_called_once_with(
            fake_conn_info)
        fake_volume_driver.detach_volume.assert_called_once_with(
            fake_conn_info, mock_instance.name)
        fake_volume_driver.disconnect_volume.assert_called_once_with(
            fake_conn_info)

        if update_device_metadata:
            mock_update_dev_meta.assert_called_once_with(
                mock.sentinel.context, mock_instance)
        else:
            mock_update_dev_meta.assert_not_called()

    @mock.patch.object(connector, 'get_connector_properties')
    def test_get_volume_connector(self, mock_get_connector):
        conn = self._volumeops.get_volume_connector()

        mock_get_connector.assert_called_once_with(
            root_helper=None,
            my_ip=CONF.my_block_storage_ip,
            multipath=CONF.hyperv.use_multipath_io,
            enforce_multipath=True,
            host=CONF.host)
        self.assertEqual(mock_get_connector.return_value, conn)

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    def test_connect_volumes(self, mock_get_volume_driver):
        block_device_info = get_fake_block_dev_info()

        self._volumeops.connect_volumes(block_device_info)

        init_vol_conn = (
            mock_get_volume_driver.return_value.connect_volume)
        init_vol_conn.assert_called_once_with(
            block_device_info['block_device_mapping'][0]['connection_info'])

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

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    def test_get_disk_resource_path(self, mock_get_volume_driver):
        fake_conn_info = get_fake_connection_info()
        fake_volume_driver = mock_get_volume_driver.return_value

        resulted_disk_path = self._volumeops.get_disk_resource_path(
            fake_conn_info)

        mock_get_volume_driver.assert_called_once_with(fake_conn_info)
        get_mounted_disk = fake_volume_driver.get_disk_resource_path
        get_mounted_disk.assert_called_once_with(fake_conn_info)
        self.assertEqual(get_mounted_disk.return_value,
                         resulted_disk_path)

    def test_bytes_per_sec_to_iops(self):
        no_bytes = 15 * units.Ki
        expected_iops = 2

        resulted_iops = self._volumeops.bytes_per_sec_to_iops(no_bytes)
        self.assertEqual(expected_iops, resulted_iops)

    @mock.patch.object(volumeops.LOG, 'warning')
    def test_validate_qos_specs(self, mock_warning):
        supported_qos_specs = [mock.sentinel.spec1, mock.sentinel.spec2]
        requested_qos_specs = {mock.sentinel.spec1: mock.sentinel.val,
                               mock.sentinel.spec3: mock.sentinel.val2}

        self._volumeops.validate_qos_specs(requested_qos_specs,
                                           supported_qos_specs)
        self.assertTrue(mock_warning.called)

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    @mock.patch.object(volumeops.driver_block_device, 'convert_volume')
    @mock.patch.object(volumeops.objects, 'BlockDeviceMapping')
    def test_volume_snapshot_create(self, mock_bdm_cls, mock_convert_volume,
                                    mock_get_vol_drv):
        mock_instance = mock.Mock()
        fake_create_info = {'snapshot_id': mock.sentinel.snapshot_id}

        mock_bdm = mock_bdm_cls.get_by_volume_and_instance.return_value
        mock_driver_bdm = mock_convert_volume.return_value
        mock_vol_driver = mock_get_vol_drv.return_value

        self._volumeops.volume_snapshot_create(
            mock.sentinel.context, mock_instance,
            mock.sentinel.volume_id, fake_create_info)

        mock_bdm_cls.get_by_volume_and_instance.assert_called_once_with(
            mock.sentinel.context, mock.sentinel.volume_id,
            mock_instance.uuid)
        mock_convert_volume.assert_called_once_with(mock_bdm)
        mock_get_vol_drv.assert_called_once_with(
            mock_driver_bdm['connection_info'])

        mock_vol_driver.create_snapshot.assert_called_once_with(
            mock_driver_bdm['connection_info'],
            mock_instance,
            fake_create_info)
        mock_driver_bdm.save.assert_called_once_with()

        self._volume_api.update_snapshot_status.assert_called_once_with(
            mock.sentinel.context,
            mock.sentinel.snapshot_id,
            'creating')

        self.assertIsNone(mock_instance.task_state)
        mock_instance.save.assert_has_calls(
            [mock.call(expected_task_state=[None]),
             mock.call(expected_task_state=[
                 task_states.IMAGE_SNAPSHOT_PENDING])])

    @mock.patch.object(volumeops.objects, 'BlockDeviceMapping')
    def test_volume_snapshot_create_exc(self, mock_bdm_cls):
        mock_instance = mock.Mock()
        fake_create_info = {'snapshot_id': mock.sentinel.snapshot_id}

        mock_bdm_cls.get_by_volume_and_instance.side_effect = (
            test.TestingException)

        self.assertRaises(test.TestingException,
                          self._volumeops.volume_snapshot_create,
                          mock.sentinel.context,
                          mock_instance,
                          mock.sentinel.volume_id,
                          fake_create_info)
        self._volume_api.update_snapshot_status.assert_called_once_with(
            mock.sentinel.context, mock.sentinel.snapshot_id, 'error')

        self.assertIsNone(mock_instance.task_state)
        mock_instance.save.assert_has_calls(
            [mock.call(expected_task_state=[None]),
             mock.call(expected_task_state=[
                 task_states.IMAGE_SNAPSHOT_PENDING])])

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    @mock.patch.object(volumeops.driver_block_device, 'convert_volume')
    @mock.patch.object(volumeops.objects, 'BlockDeviceMapping')
    def test_volume_snapshot_delete(self, mock_bdm_cls, mock_convert_volume,
                                     mock_get_vol_drv):
        mock_instance = mock.Mock()

        mock_bdm = mock_bdm_cls.get_by_volume_and_instance.return_value
        mock_driver_bdm = mock_convert_volume.return_value
        mock_vol_driver = mock_get_vol_drv.return_value

        self._volumeops.volume_snapshot_delete(
            mock.sentinel.context, mock_instance,
            mock.sentinel.volume_id,
            mock.sentinel.snapshot_id,
            mock.sentinel.delete_info)

        mock_bdm_cls.get_by_volume_and_instance.assert_called_once_with(
            mock.sentinel.context, mock.sentinel.volume_id,
            mock_instance.uuid)
        mock_convert_volume.assert_called_once_with(mock_bdm)
        mock_get_vol_drv.assert_called_once_with(
            mock_driver_bdm['connection_info'])

        mock_vol_driver.delete_snapshot.assert_called_once_with(
            mock_driver_bdm['connection_info'],
            mock_instance,
            mock.sentinel.delete_info)
        mock_driver_bdm.save.assert_called_once_with()

        self._volume_api.update_snapshot_status.assert_called_once_with(
            mock.sentinel.context,
            mock.sentinel.snapshot_id,
            'deleting')

        self.assertIsNone(mock_instance.task_state)
        mock_instance.save.assert_has_calls(
            [mock.call(expected_task_state=[None]),
             mock.call(expected_task_state=[
                 task_states.IMAGE_SNAPSHOT_PENDING])])

    @mock.patch.object(volumeops.objects, 'BlockDeviceMapping')
    def test_volume_snapshot_delete_exc(self, mock_bdm_cls):
        mock_instance = mock.Mock()

        mock_bdm_cls.get_by_volume_and_instance.side_effect = (
            test.TestingException)

        self.assertRaises(test.TestingException,
                          self._volumeops.volume_snapshot_delete,
                          mock.sentinel.context,
                          mock_instance,
                          mock.sentinel.volume_id,
                          mock.sentinel.snapshot_id,
                          mock.sentinel.delete_info)
        self._volume_api.update_snapshot_status.assert_called_once_with(
            mock.sentinel.context, mock.sentinel.snapshot_id, 'error_deleting')

        self.assertIsNone(mock_instance.task_state)
        mock_instance.save.assert_has_calls(
            [mock.call(expected_task_state=[None]),
             mock.call(expected_task_state=[
                 task_states.IMAGE_SNAPSHOT_PENDING])])

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    def test_get_disk_attachment_info(self, mock_get_volume_driver):
        fake_conn_info = get_fake_connection_info()
        ret_val = self._volumeops.get_disk_attachment_info(fake_conn_info)

        mock_vol_driver = mock_get_volume_driver.return_value
        mock_vol_driver.get_disk_attachment_info.assert_called_once_with(
            fake_conn_info)

        self.assertEqual(
            mock_vol_driver.get_disk_attachment_info.return_value,
            ret_val)

    @mock.patch.object(volumeops.VolumeOps, '_get_volume_driver')
    def test_extend_volume(self, mock_get_volume_driver):
        fake_conn_info = get_fake_connection_info()
        self._volumeops.extend_volume(fake_conn_info)

        mock_vol_driver = mock_get_volume_driver.return_value
        mock_vol_driver.extend_volume.assert_called_once_with(
            fake_conn_info)


@ddt.ddt
class BaseVolumeDriverTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for Hyper-V BaseVolumeDriver class."""

    def setUp(self):
        super(BaseVolumeDriverTestCase, self).setUp()

        self._base_vol_driver = volumeops.BaseVolumeDriver()
        self._base_vol_driver._conn = mock.Mock()
        self._vmutils = self._base_vol_driver._vmutils
        self._migrutils = self._base_vol_driver._migrutils
        self._diskutils = self._base_vol_driver._diskutils
        self._metricsutils = self._base_vol_driver._metricsutils
        self._conn = self._base_vol_driver._conn

    @mock.patch.object(connector.InitiatorConnector, 'factory')
    def test_connector(self, mock_conn_factory):
        self._base_vol_driver._conn = None
        self._base_vol_driver._protocol = mock.sentinel.protocol
        self._base_vol_driver._extra_connector_args = dict(
            fake_conn_arg=mock.sentinel.conn_val)

        conn = self._base_vol_driver._connector

        self.assertEqual(mock_conn_factory.return_value, conn)
        mock_conn_factory.assert_called_once_with(
            protocol=mock.sentinel.protocol,
            root_helper=None,
            use_multipath=CONF.hyperv.use_multipath_io,
            device_scan_attempts=CONF.hyperv.mounted_disk_query_retry_count,
            device_scan_interval=(
                CONF.hyperv.mounted_disk_query_retry_interval),
            **self._base_vol_driver._extra_connector_args)

    def test_connect_volume(self):
        conn_info = get_fake_connection_info()

        dev_info = self._base_vol_driver.connect_volume(conn_info)
        expected_dev_info = self._conn.connect_volume.return_value

        self.assertEqual(expected_dev_info, dev_info)
        self._conn.connect_volume.assert_called_once_with(
            conn_info['data'])

    def test_disconnect_volume(self):
        conn_info = get_fake_connection_info()

        self._base_vol_driver.disconnect_volume(conn_info)

        self._conn.disconnect_volume.assert_called_once_with(
            conn_info['data'])

    @mock.patch.object(volumeops.BaseVolumeDriver, '_get_disk_res_path')
    def _test_get_disk_resource_path_by_conn_info(self,
                                                  mock_get_disk_res_path,
                                                  disk_found=True):
        conn_info = get_fake_connection_info()
        mock_vol_paths = [mock.sentinel.disk_path] if disk_found else []
        self._conn.get_volume_paths.return_value = mock_vol_paths

        if disk_found:
            disk_res_path = self._base_vol_driver.get_disk_resource_path(
                conn_info)

            self._conn.get_volume_paths.assert_called_once_with(
                conn_info['data'])
            self.assertEqual(mock_get_disk_res_path.return_value,
                             disk_res_path)
            mock_get_disk_res_path.assert_called_once_with(
                mock.sentinel.disk_path)
        else:
            self.assertRaises(exception.DiskNotFound,
                              self._base_vol_driver.get_disk_resource_path,
                              conn_info)

    def test_get_existing_disk_res_path(self):
        self._test_get_disk_resource_path_by_conn_info()

    def test_get_unfound_disk_res_path(self):
        self._test_get_disk_resource_path_by_conn_info(disk_found=False)

    def test_get_block_dev_res_path(self):
        self._base_vol_driver._is_block_dev = True

        mock_get_dev_number = (
            self._diskutils.get_device_number_from_device_name)
        mock_get_dev_number.return_value = mock.sentinel.dev_number
        self._vmutils.get_mounted_disk_by_drive_number.return_value = (
            mock.sentinel.disk_path)

        disk_path = self._base_vol_driver._get_disk_res_path(
            mock.sentinel.dev_name)

        mock_get_dev_number.assert_called_once_with(mock.sentinel.dev_name)
        self._vmutils.get_mounted_disk_by_drive_number.assert_called_once_with(
            mock.sentinel.dev_number)

        self.assertEqual(mock.sentinel.disk_path, disk_path)

    def test_get_block_dev_res_path_missing(self):
        self._base_vol_driver._is_block_dev = True

        self._vmutils.get_mounted_disk_by_drive_number.return_value = None

        self.assertRaises(exception.DiskNotFound,
                          self._base_vol_driver._get_disk_res_path,
                          mock.sentinel.dev_name)

    def test_get_virt_disk_res_path(self):
        # For virtual disk images, we expect the resource path to be the
        # actual image path, as opposed to passthrough disks, in which case we
        # need the Msvm_DiskDrive resource path when attaching it to a VM.
        self._base_vol_driver._is_block_dev = False

        path = self._base_vol_driver._get_disk_res_path(
            mock.sentinel.disk_path)
        self.assertEqual(mock.sentinel.disk_path, path)

    @mock.patch.object(volumeops.BaseVolumeDriver,
        '_check_san_policy')
    @ddt.data(True, False)
    def test_validate_host_configuration(self, is_block_dev,
                                         fake_check_san_policy):
        self._base_vol_driver._is_block_dev = is_block_dev

        self._base_vol_driver.validate_host_configuration()

        if is_block_dev:
            fake_check_san_policy.assert_called_once_with()
        else:
            fake_check_san_policy.assert_not_called()

    @ddt.data(os_win_const.DISK_POLICY_OFFLINE_ALL,
              os_win_const.DISK_POLICY_ONLINE_ALL)
    def test_check_san_policy(self, disk_policy):
        self._diskutils.get_new_disk_policy.return_value = disk_policy

        accepted_policies = [os_win_const.DISK_POLICY_OFFLINE_SHARED,
                             os_win_const.DISK_POLICY_OFFLINE_ALL]

        if disk_policy not in accepted_policies:
            self.assertRaises(
                exception.ValidationError,
                self._base_vol_driver._check_san_policy)
        else:
            self._base_vol_driver._check_san_policy()

    @mock.patch.object(volumeops.BaseVolumeDriver,
                       '_configure_disk_metrics')
    @mock.patch.object(volumeops.BaseVolumeDriver,
                       '_get_disk_res_path')
    @mock.patch.object(volumeops.BaseVolumeDriver, '_get_disk_ctrl_and_slot')
    @mock.patch.object(volumeops.BaseVolumeDriver,
                       'connect_volume')
    @mock.patch.object(volumeops.BaseVolumeDriver,
                       'validate_host_configuration')
    def _test_attach_volume(self, mock_validate_host_config,
                            mock_connect_volume,
                            mock_get_disk_ctrl_and_slot,
                            mock_get_disk_res_path,
                            mock_configure_metrics,
                            is_block_dev=True):
        connection_info = get_fake_connection_info()
        self._base_vol_driver._is_block_dev = is_block_dev
        mock_connect_volume.return_value = dict(path=mock.sentinel.raw_path)

        mock_get_disk_res_path.return_value = (
            mock.sentinel.disk_path)
        mock_get_disk_ctrl_and_slot.return_value = (
            mock.sentinel.ctrller_path,
            mock.sentinel.slot)

        self._base_vol_driver.attach_volume(
            connection_info=connection_info,
            instance_name=mock.sentinel.instance_name,
            disk_bus=mock.sentinel.disk_bus)

        if is_block_dev:
            self._vmutils.attach_volume_to_controller.assert_called_once_with(
                mock.sentinel.instance_name,
                mock.sentinel.ctrller_path,
                mock.sentinel.slot,
                mock.sentinel.disk_path,
                serial=connection_info['serial'])
        else:
            self._vmutils.attach_drive.assert_called_once_with(
                mock.sentinel.instance_name,
                mock.sentinel.disk_path,
                mock.sentinel.ctrller_path,
                mock.sentinel.slot)

        mock_get_disk_res_path.assert_called_once_with(
            mock.sentinel.raw_path)
        mock_get_disk_ctrl_and_slot.assert_called_once_with(
            mock.sentinel.instance_name, mock.sentinel.disk_bus)
        mock_validate_host_config.assert_called_once_with()
        mock_configure_metrics.assert_called_once_with(mock.sentinel.disk_path)

    def test_attach_volume_image_file(self):
        self._test_attach_volume(is_block_dev=False)

    def test_attach_volume_block_dev(self):
        self._test_attach_volume(is_block_dev=True)

    def test_detach_volume_planned_vm(self):
        self._base_vol_driver.detach_volume(mock.sentinel.connection_info,
                                            mock.sentinel.inst_name)
        self._vmutils.detach_vm_disk.assert_not_called()

    @ddt.data({},
              {'metrics_enabled': False},
              {'is_block_dev': True})
    @ddt.unpack
    def test_configure_disk_metrics(self, metrics_enabled=True,
                                    is_block_dev=False):
        self.flags(enable_instance_metrics_collection=metrics_enabled,
                   group='hyperv')
        self._base_vol_driver._is_block_dev = is_block_dev

        enable_metrics = self._metricsutils.enable_disk_metrics_collection

        self._base_vol_driver._configure_disk_metrics(mock.sentinel.disk_path)

        if metrics_enabled and not is_block_dev:
            enable_metrics.assert_called_once_with(
                mock.sentinel.disk_path,
                is_physical=is_block_dev)
        else:
            enable_metrics.assert_not_called()

    @ddt.data(True, False)
    @mock.patch.object(volumeops.BaseVolumeDriver,
                       'get_disk_resource_path')
    def test_detach_volume(self, is_block_dev, mock_get_disk_resource_path):
        self._migrutils.planned_vm_exists.return_value = False
        connection_info = get_fake_connection_info()
        self._base_vol_driver._is_block_dev = is_block_dev

        self._base_vol_driver.detach_volume(connection_info,
                                            mock.sentinel.instance_name)

        if is_block_dev:
            exp_serial = connection_info['serial']
            exp_disk_res_path = None
            self.assertFalse(mock_get_disk_resource_path.called)
        else:
            exp_serial = None
            exp_disk_res_path = mock_get_disk_resource_path.return_value
            mock_get_disk_resource_path.assert_called_once_with(
                connection_info)

        self._vmutils.detach_vm_disk.assert_called_once_with(
            mock.sentinel.instance_name,
            exp_disk_res_path,
            is_physical=is_block_dev,
            serial=exp_serial)

    def test_get_disk_ctrl_and_slot_ide(self):
        ctrl, slot = self._base_vol_driver._get_disk_ctrl_and_slot(
            mock.sentinel.instance_name,
            disk_bus=constants.CTRL_TYPE_IDE)

        expected_ctrl = self._vmutils.get_vm_ide_controller.return_value
        expected_slot = 0

        self._vmutils.get_vm_ide_controller.assert_called_once_with(
            mock.sentinel.instance_name, 0)

        self.assertEqual(expected_ctrl, ctrl)
        self.assertEqual(expected_slot, slot)

    def test_get_disk_ctrl_and_slot_scsi(self):
        ctrl, slot = self._base_vol_driver._get_disk_ctrl_and_slot(
            mock.sentinel.instance_name,
            disk_bus=constants.CTRL_TYPE_SCSI)

        expected_ctrl = self._vmutils.get_vm_scsi_controller.return_value
        expected_slot = (
            self._vmutils.get_free_controller_slot.return_value)

        self._vmutils.get_vm_scsi_controller.assert_called_once_with(
            mock.sentinel.instance_name)
        self._vmutils.get_free_controller_slot(
           self._vmutils.get_vm_scsi_controller.return_value)

        self.assertEqual(expected_ctrl, ctrl)
        self.assertEqual(expected_slot, slot)

    def test_set_disk_qos_specs(self):
        # This base method is a noop, we'll just make sure
        # it doesn't error out.
        self._base_vol_driver.set_disk_qos_specs(
            mock.sentinel.conn_info, mock.sentinel.disk_qos_spes)

    @ddt.data(True, False)
    @mock.patch.object(volumeops.BaseVolumeDriver,
                       'get_disk_resource_path')
    def test_get_disk_attachment_info(self, is_block_dev,
                                      mock_get_disk_resource_path):
        connection_info = get_fake_connection_info()
        self._base_vol_driver._is_block_dev = is_block_dev

        self._base_vol_driver.get_disk_attachment_info(connection_info)

        if is_block_dev:
            exp_serial = connection_info['serial']
            exp_disk_res_path = None
            self.assertFalse(mock_get_disk_resource_path.called)
        else:
            exp_serial = None
            exp_disk_res_path = mock_get_disk_resource_path.return_value
            mock_get_disk_resource_path.assert_called_once_with(
                connection_info)

        self._vmutils.get_disk_attachment_info.assert_called_once_with(
            exp_disk_res_path,
            is_physical=is_block_dev,
            serial=exp_serial)

    def test_extend_volume(self):
        conn_info = get_fake_connection_info()

        self._base_vol_driver.extend_volume(conn_info)

        self._conn.extend_volume.assert_called_once_with(
            conn_info['data'])


class ISCSIVolumeDriverTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for Hyper-V BaseVolumeDriver class."""

    def test_extra_conn_args(self):
        fake_iscsi_initiator = (
            'PCI\\VEN_1077&DEV_2031&SUBSYS_17E8103C&REV_02\\'
            '4&257301f0&0&0010_0')
        self.flags(iscsi_initiator_list=[fake_iscsi_initiator],
                   group='hyperv')
        expected_extra_conn_args = dict(
            initiator_list=[fake_iscsi_initiator])

        vol_driver = volumeops.ISCSIVolumeDriver()

        self.assertEqual(expected_extra_conn_args,
                         vol_driver._extra_connector_args)


@ddt.ddt
class SMBFSVolumeDriverTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V SMBFSVolumeDriver class."""

    _autospec_classes = [
        volumeops.pathutils.PathUtils,
    ]

    _FAKE_EXPORT_PATH = '//ip/share/'
    _FAKE_CONN_INFO = get_fake_connection_info(export=_FAKE_EXPORT_PATH)

    def setUp(self):
        super(SMBFSVolumeDriverTestCase, self).setUp()
        self._volume_driver = volumeops.SMBFSVolumeDriver()
        self._volume_driver._conn = mock.Mock()
        self._conn = self._volume_driver._conn
        self._vmutils = self._volume_driver._vmutils
        self._pathutils = self._volume_driver._pathutils
        self._vhdutils = self._volume_driver._vhdutils

    def test_get_export_path(self):
        export_path = self._volume_driver._get_export_path(
            self._FAKE_CONN_INFO)
        expected_path = self._FAKE_EXPORT_PATH.replace('/', '\\')
        self.assertEqual(expected_path, export_path)

    @mock.patch.object(volumeops.BaseVolumeDriver, 'attach_volume')
    def test_attach_volume(self, mock_attach):
        # The tested method will just apply a lock before calling
        # the superclass method.
        self._volume_driver.attach_volume(
            self._FAKE_CONN_INFO,
            mock.sentinel.instance_name,
            disk_bus=mock.sentinel.disk_bus)

        mock_attach.assert_called_once_with(
            self._FAKE_CONN_INFO,
            mock.sentinel.instance_name,
            disk_bus=mock.sentinel.disk_bus)

    @mock.patch.object(volumeops.BaseVolumeDriver, 'detach_volume')
    def test_detach_volume(self, mock_detach):
        self._volume_driver.detach_volume(
            self._FAKE_CONN_INFO,
            instance_name=mock.sentinel.instance_name)

        mock_detach.assert_called_once_with(
            self._FAKE_CONN_INFO,
            instance_name=mock.sentinel.instance_name)

    @mock.patch.object(volumeops.VolumeOps, 'bytes_per_sec_to_iops')
    @mock.patch.object(volumeops.VolumeOps, 'validate_qos_specs')
    @mock.patch.object(volumeops.BaseVolumeDriver, 'get_disk_resource_path')
    def test_set_disk_qos_specs(self, mock_get_disk_path,
                                mock_validate_qos_specs,
                                mock_bytes_per_sec_to_iops):
        fake_total_bytes_sec = 8
        fake_total_iops_sec = 1

        storage_qos_specs = {'total_bytes_sec': fake_total_bytes_sec}
        expected_supported_specs = ['total_iops_sec', 'total_bytes_sec']
        mock_set_qos_specs = self._volume_driver._vmutils.set_disk_qos_specs
        mock_bytes_per_sec_to_iops.return_value = fake_total_iops_sec
        mock_get_disk_path.return_value = mock.sentinel.disk_path

        self._volume_driver.set_disk_qos_specs(self._FAKE_CONN_INFO,
                                               storage_qos_specs)

        mock_validate_qos_specs.assert_called_once_with(
            storage_qos_specs, expected_supported_specs)
        mock_bytes_per_sec_to_iops.assert_called_once_with(
            fake_total_bytes_sec)
        mock_get_disk_path.assert_called_once_with(self._FAKE_CONN_INFO)
        mock_set_qos_specs.assert_called_once_with(
            mock.sentinel.disk_path,
            fake_total_iops_sec)

    @contextlib.contextmanager
    def check_prepare_for_vol_snap_mock(self, *args, **kwargs):
        # Mocks the according context manager and ensures that
        # it has been called with the expected arguments.
        mock_prepare_for_vol_snap = mock.MagicMock()

        patcher = mock.patch.object(vmops.VMOps,
                                   'prepare_for_volume_snapshot',
                                    mock_prepare_for_vol_snap)
        patcher.start()
        self.addCleanup(patcher.stop)

        try:
            yield
        finally:
            mock_prepare_for_vol_snap.assert_called_once_with(
                *args, **kwargs)

    def _get_fake_disk_attachment_info(self,
                                       ctrl_type=constants.CTRL_TYPE_SCSI):
        return dict(controller_type=ctrl_type,
                    controller_path=mock.sentinel.ctrl_path,
                    controller_slot=mock.sentinel.ctrl_slot)

    @ddt.data(constants.CTRL_TYPE_SCSI, constants.CTRL_TYPE_IDE)
    @mock.patch.object(volumeops.SMBFSVolumeDriver, '_create_snapshot_ide')
    @mock.patch.object(volumeops.SMBFSVolumeDriver, '_create_snapshot_scsi')
    @mock.patch.object(volumeops.SMBFSVolumeDriver, 'get_disk_resource_path')
    def test_create_snapshot(self, ctrl_type, mock_get_disk_res_path,
                             mock_create_snap_scsi, mock_create_snap_ide):
        mock_instance = mock.Mock()

        conn_info = get_fake_connection_info()
        mock_att_info = self._get_fake_disk_attachment_info(ctrl_type)
        mock_attached_disk_dir = 'fake_share'
        mock_attached_disk_name = 'volume-vol_id-hv_guid.vhdx'
        mock_attached_disk_path = os.path.join(mock_attached_disk_dir,
                                               mock_attached_disk_name)
        mock_new_file_name = 'volume-vol_id-snap_id.vhdx'
        fake_create_info = {'new_file': mock_new_file_name}
        expected_new_file_path = os.path.join(mock_attached_disk_dir,
                                              mock_new_file_name)

        mock_get_disk_res_path.return_value = mock_attached_disk_path
        self._vmutils.get_disk_attachment_info.return_value = mock_att_info

        self._volume_driver.create_snapshot(conn_info,
                                            mock_instance,
                                            fake_create_info)

        if ctrl_type == constants.CTRL_TYPE_SCSI:
            mock_create_snap_scsi.assert_called_once_with(
                mock_instance, mock_att_info,
                mock_attached_disk_path, expected_new_file_path)
        else:
            mock_create_snap_ide.assert_called_once_with(
                mock_instance, mock_attached_disk_path,
                expected_new_file_path)

        mock_get_disk_res_path.assert_called_once_with(conn_info)
        self._vmutils.get_disk_attachment_info.assert_called_once_with(
            mock_attached_disk_path, is_physical=False)

        self.assertEqual(mock_new_file_name,
                         conn_info['data']['name'])

    def test_create_snapshot_ide(self):
        mock_instance = mock.Mock()

        with self.check_prepare_for_vol_snap_mock(mock_instance):
            self._volume_driver._create_snapshot_ide(
                mock_instance,
                mock.sentinel.attached_path,
                mock.sentinel.new_path)

        self._vhdutils.create_differencing_vhd.assert_called_once_with(
            mock.sentinel.new_path, mock.sentinel.attached_path)
        self._vmutils.update_vm_disk_path.assert_called_once_with(
            mock.sentinel.attached_path,
            mock.sentinel.new_path,
            is_physical=False)

    def test_create_snapshot_scsi(self):
        mock_instance = mock.Mock()
        mock_att_info = self._get_fake_disk_attachment_info()

        with self.check_prepare_for_vol_snap_mock(mock_instance,
                                                  allow_paused=True):
            self._volume_driver._create_snapshot_scsi(
                mock_instance,
                mock_att_info,
                mock.sentinel.attached_path,
                mock.sentinel.new_path)

        self._vmutils.detach_vm_disk.assert_called_once_with(
            mock_instance.name, mock.sentinel.attached_path,
            is_physical=False)
        self._vhdutils.create_differencing_vhd.assert_called_once_with(
            mock.sentinel.new_path, mock.sentinel.attached_path)
        self._vmutils.attach_drive.assert_called_once_with(
            mock_instance.name, mock.sentinel.new_path,
            mock_att_info['controller_path'],
            mock_att_info['controller_slot'])

    @ddt.data({'merge_latest': True},
              {'ctrl_type': constants.CTRL_TYPE_IDE,
               'prep_vm_state': os_win_const.HYPERV_VM_STATE_SUSPENDED},
              {'prep_vm_state': os_win_const.HYPERV_VM_STATE_PAUSED},
              {'merge_latest': True,
               'prep_vm_state': os_win_const.HYPERV_VM_STATE_PAUSED})
    @ddt.unpack
    @mock.patch.object(volumeops.SMBFSVolumeDriver,
                      '_do_delete_snapshot')
    @mock.patch.object(volumeops.SMBFSVolumeDriver, 'get_disk_resource_path')
    def test_delete_snapshot(
            self, mock_get_disk_res_path,
            mock_delete_snap,
            merge_latest=False,
            ctrl_type=constants.CTRL_TYPE_SCSI,
            prep_vm_state=os_win_const.HYPERV_VM_STATE_DISABLED):
        mock_instance = mock.Mock()

        conn_info = get_fake_connection_info()

        mock_att_info = self._get_fake_disk_attachment_info(ctrl_type)
        mock_attached_disk_dir = 'fake_share'
        mock_attached_disk_name = 'volume-vol_id-hv_guid.vhdx'
        mock_attached_disk_path = os.path.join(mock_attached_disk_dir,
                                               mock_attached_disk_name)
        mock_new_top_img = (mock_attached_disk_path if not merge_latest
                            else 'parent.vhdx')
        mock_file_to_merge = (mock_attached_disk_name
                              if merge_latest
                              else 'volume-vol_id-snap_id.vhdx')
        exp_file_to_merge_path = os.path.join(mock_attached_disk_dir,
                                              mock_file_to_merge)

        mock_delete_info = {'file_to_merge': mock_file_to_merge}

        self._vmutils.get_disk_attachment_info.return_value = mock_att_info
        self._vmutils.get_vm_state.return_value = prep_vm_state
        mock_get_disk_res_path.return_value = mock_attached_disk_path
        mock_delete_snap.return_value = mock_new_top_img

        exp_detach = prep_vm_state == os_win_const.HYPERV_VM_STATE_PAUSED
        exp_allow_paused = ctrl_type == constants.CTRL_TYPE_SCSI

        with self.check_prepare_for_vol_snap_mock(
                mock_instance,
                allow_paused=exp_allow_paused):
            self._volume_driver.delete_snapshot(conn_info,
                                                mock_instance,
                                                mock_delete_info)

        mock_get_disk_res_path.assert_called_once_with(conn_info)
        self._vmutils.get_disk_attachment_info.assert_called_once_with(
            mock_attached_disk_path, is_physical=False)
        self._vmutils.get_vm_state.assert_called_once_with(
            mock_instance.name)

        mock_delete_snap.assert_called_once_with(mock_attached_disk_path,
                                                 exp_file_to_merge_path)

        if exp_detach:
            self._vmutils.detach_vm_disk.assert_called_once_with(
                mock_instance.name,
                mock_attached_disk_path,
                is_physical=False)
            self._vmutils.attach_drive.assert_called_once_with(
                mock_instance.name,
                mock_new_top_img,
                mock_att_info['controller_path'],
                mock_att_info['controller_slot'])
        else:
            self.assertFalse(self._vmutils.detach_vm_disk.called)
            self.assertFalse(self._vmutils.attach_drive.called)

            if merge_latest:
                self._vmutils.update_vm_disk_path.assert_called_once_with(
                    mock_attached_disk_path,
                    mock_new_top_img,
                    is_physical=False)
            else:
                self.assertFalse(self._vmutils.update_vm_disk_path.called)

        self.assertEqual(os.path.basename(mock_new_top_img),
                         conn_info['data']['name'])

    @ddt.data({'merge_latest': True},
              {'merge_latest': False})
    @ddt.unpack
    @mock.patch.object(volumeops.SMBFSVolumeDriver,
                      '_get_higher_image_from_chain')
    def test_do_delete_snapshot(self, mock_get_higher_img,
                                merge_latest=False):
        mock_attached_disk_path = 'fake-attached-disk.vhdx'
        mock_file_to_merge = (mock_attached_disk_path
                              if merge_latest
                              else 'fake-file-to-merge.vhdx')

        self._vhdutils.get_vhd_parent_path.return_value = (
            mock.sentinel.vhd_parent_path)
        mock_get_higher_img.return_value = mock.sentinel.higher_img

        exp_new_top_img = (mock.sentinel.vhd_parent_path if merge_latest
                           else mock_attached_disk_path)

        new_top_img = self._volume_driver._do_delete_snapshot(
            mock_attached_disk_path,
            mock_file_to_merge)

        self.assertEqual(exp_new_top_img, new_top_img)

        self._vhdutils.get_vhd_parent_path.assert_called_once_with(
            mock_file_to_merge)
        self._vhdutils.merge_vhd.assert_called_once_with(
            mock_file_to_merge, delete_merged_image=False)

        if not merge_latest:
            mock_get_higher_img.assert_called_once_with(
                mock_file_to_merge,
                mock_attached_disk_path)
            self._vhdutils.reconnect_parent_vhd.assert_called_once_with(
                mock.sentinel.higher_img,
                mock.sentinel.vhd_parent_path)
        else:
            mock_get_higher_img.assert_not_called()
            self._vhdutils.reconnect_parent_vhd.assert_not_called()

    @ddt.data(2, 3, 4, 5)
    def test_get_higher_image(self, vhd_idx):
        vhd_chain_length = 5
        vhd_chain = ['vhd-%s.vhdx' % idx
                     for idx in range(vhd_chain_length)][::-1]
        vhd_path = 'vhd-%s.vhdx' % vhd_idx

        self._vhdutils.get_vhd_parent_path.side_effect = (
            vhd_chain[1:] + [None])

        if vhd_idx in range(vhd_chain_length - 1):
            exp_higher_vhd_path = 'vhd-%s.vhdx' % (vhd_idx + 1)
            result = self._volume_driver._get_higher_image_from_chain(
                vhd_path,
                vhd_chain[0])

            self.assertEqual(exp_higher_vhd_path, result)

            self._vhdutils.get_vhd_parent_path.assert_has_calls(
                [mock.call(path)
                 for path in vhd_chain[:vhd_chain_length - vhd_idx - 1]])
        else:
            self.assertRaises(
                exception.ImageNotFound,
                self._volume_driver._get_higher_image_from_chain,
                vhd_path,
                vhd_chain[0])
