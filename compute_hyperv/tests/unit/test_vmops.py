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
from eventlet import timeout as etimeout
import mock
from nova.compute import task_states
from nova.compute import vm_states
from nova import exception
from nova import objects
from nova.objects import fields
from nova.tests.unit.objects import test_virtual_interface
from nova import utils
from nova.virt import event as virtevent
from nova.virt import hardware
from os_win import constants as os_win_const
from os_win import exceptions as os_win_exc
from oslo_concurrency import processutils
from oslo_utils import fileutils
from oslo_utils import units

import compute_hyperv.nova.conf
from compute_hyperv.nova import constants
from compute_hyperv.nova import eventhandler
from compute_hyperv.nova import vmops
from compute_hyperv.tests import fake_instance
from compute_hyperv.tests.unit import test_base

CONF = compute_hyperv.nova.conf.CONF


@ddt.ddt
class VMOpsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V VMOps class."""

    _autospec_classes = [
        vmops.pathutils.PathUtils,
        vmops.volumeops.VolumeOps,
        vmops.imagecache.ImageCache,
        vmops.serialconsoleops.SerialConsoleOps,
        vmops.block_device_manager.BlockDeviceInfoManager,
        vmops.vif_utils.HyperVVIFDriver,
        vmops.pdk.PDK,
    ]

    _FAKE_TIMEOUT = 2
    FAKE_SIZE = 10
    FAKE_DIR = 'fake_dir'
    FAKE_ROOT_PATH = 'C:\\path\\to\\fake.%s'
    FAKE_CONFIG_DRIVE_ISO = 'configdrive.iso'
    FAKE_CONFIG_DRIVE_VHD = 'configdrive.vhd'
    FAKE_UUID = '4f54fb69-d3a2-45b7-bb9b-b6e6b3d893b3'
    FAKE_LOG = 'fake_log'
    _FAKE_PDK_FILE_PATH = 'C:\\path\\to\\fakepdk.pdk'
    _FAKE_FSK_FILE_PATH = 'C:\\path\\to\\fakefsk.fsk'

    _WIN_VERSION_6_3 = '6.3.0'
    _WIN_VERSION_10 = '10.0'

    ISO9660 = 'iso9660'
    VFAT = 'vfat'
    _FAKE_CONFIGDRIVE_PATH = 'C:/fake_instance_dir/configdrive.vhd'

    def setUp(self):
        super(VMOpsTestCase, self).setUp()
        self.context = 'fake-context'

        self._vmops = vmops.VMOps(virtapi=mock.MagicMock())
        self._pathutils = self._vmops._pathutils
        self._vmutils = self._vmops._vmutils
        self._metricsutils = self._vmops._metricsutils
        self._vif_driver = self._vmops._vif_driver

    def test_list_instances(self):
        mock_instance = mock.MagicMock()
        self._vmops._vmutils.list_instances.return_value = [mock_instance]
        response = self._vmops.list_instances()
        self._vmops._vmutils.list_instances.assert_called_once_with()
        self.assertEqual(response, [mock_instance])

    @ddt.data(True, False)
    def test_estimate_instance_overhead(self, instance_automatic_shutdown):
        self.flags(instance_automatic_shutdown=instance_automatic_shutdown,
                   group='hyperv')

        instance_info = {'memory_mb': 512}
        expected_disk_overhead = 0 if instance_automatic_shutdown else 1
        overhead = self._vmops.estimate_instance_overhead(instance_info)
        self.assertEqual(0, overhead['memory_mb'])
        self.assertEqual(expected_disk_overhead, overhead['disk_gb'])

        instance_info = {'memory_mb': 500}
        overhead = self._vmops.estimate_instance_overhead(instance_info)
        self.assertEqual(0, overhead['disk_gb'])

    def _test_get_info(self, vm_exists):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_info = mock.MagicMock(spec_set=dict)
        fake_info = {'EnabledState': 2,
                     'MemoryUsage': mock.sentinel.FAKE_MEM_KB,
                     'NumberOfProcessors': mock.sentinel.FAKE_NUM_CPU,
                     'UpTime': mock.sentinel.FAKE_CPU_NS}

        def getitem(key):
            return fake_info[key]
        mock_info.__getitem__.side_effect = getitem

        expected = hardware.InstanceInfo(state=constants.HYPERV_POWER_STATE[2])

        self._vmops._vmutils.vm_exists.return_value = vm_exists
        self._vmops._vmutils.get_vm_summary_info.return_value = mock_info

        if not vm_exists:
            self.assertRaises(exception.InstanceNotFound,
                              self._vmops.get_info, mock_instance)
        else:
            response = self._vmops.get_info(mock_instance)
            self._vmops._vmutils.vm_exists.assert_called_once_with(
                mock_instance.name)
            self._vmops._vmutils.get_vm_summary_info.assert_called_once_with(
                mock_instance.name)
            self.assertEqual(response, expected)

    def test_get_info(self):
        self._test_get_info(vm_exists=True)

    def test_get_info_exception(self):
        self._test_get_info(vm_exists=False)

    @mock.patch.object(vmops.VMOps, 'check_vm_image_type')
    @mock.patch.object(vmops.VMOps, '_create_root_vhd')
    def test_create_root_device_type_disk(self, mock_create_root_device,
                                          mock_check_vm_image_type):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_root_disk_info = {'type': constants.DISK}

        self._vmops._create_root_device(self.context, mock_instance,
                                        mock_root_disk_info,
                                        mock.sentinel.VM_GEN_1)

        mock_create_root_device.assert_called_once_with(
            self.context, mock_instance)
        mock_check_vm_image_type.assert_called_once_with(
            mock_instance.uuid, mock.sentinel.VM_GEN_1,
            mock_create_root_device.return_value)

    @mock.patch.object(vmops.VMOps, '_create_root_iso')
    def test_create_root_device_type_iso(self, mock_create_root_iso):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_root_disk_info = {'type': constants.DVD}

        self._vmops._create_root_device(self.context, mock_instance,
                                        mock_root_disk_info,
                                        mock.sentinel.VM_GEN_1)

        mock_create_root_iso.assert_called_once_with(self.context,
                                                     mock_instance)

    @mock.patch('os.path.exists')
    def _test_create_root_iso(self, mock_os_path_exists,
                              iso_already_exists=False):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        mock_get_root_vhd_path = self._vmops._pathutils.get_root_vhd_path
        mock_get_root_vhd_path.return_value = mock.sentinel.ROOT_ISO_PATH
        mock_get_cached_image = self._vmops._imagecache.get_cached_image
        mock_get_cached_image.return_value = mock.sentinel.CACHED_ISO_PATH
        mock_os_path_exists.return_value = iso_already_exists

        self._vmops._create_root_iso(self.context, mock_instance)

        mock_get_cached_image.assert_called_once_with(self.context,
                                                      mock_instance)
        mock_get_root_vhd_path.assert_called_once_with(mock_instance.name,
                                                       'iso')
        if not iso_already_exists:
            self._vmops._pathutils.copyfile.assert_called_once_with(
                mock.sentinel.CACHED_ISO_PATH, mock.sentinel.ROOT_ISO_PATH)
        else:
            self._vmops._pathutils.copyfile.assert_not_called()

    def test_create_root_iso(self):
        self._test_create_root_iso()

    def test_create_root_iso_already_existing_image(self):
        self._test_create_root_iso(iso_already_exists=True)

    def _prepare_create_root_device_mocks(self, use_cow_images, vhd_format,
                                       vhd_size):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.flavor.root_gb = self.FAKE_SIZE
        self.flags(use_cow_images=use_cow_images)
        self._vmops._vhdutils.get_vhd_info.return_value = {'VirtualSize':
                                                           vhd_size * units.Gi}
        self._vmops._vhdutils.get_vhd_format.return_value = vhd_format
        root_vhd_internal_size = mock_instance.flavor.root_gb * units.Gi
        get_size = self._vmops._vhdutils.get_internal_vhd_size_by_file_size
        get_size.return_value = root_vhd_internal_size
        self._vmops._pathutils.exists.return_value = True

        return mock_instance

    @mock.patch('os.path.exists')
    def _test_create_root_vhd_exception(self, mock_os_path_exists, vhd_format):
        mock_instance = self._prepare_create_root_device_mocks(
            use_cow_images=False, vhd_format=vhd_format,
            vhd_size=(self.FAKE_SIZE + 1))
        fake_vhd_path = self.FAKE_ROOT_PATH % vhd_format
        self._vmops._imagecache.get_cached_image.return_value = fake_vhd_path
        fake_root_path = self._vmops._pathutils.get_root_vhd_path.return_value
        mock_os_path_exists.return_value = False

        self.assertRaises(exception.FlavorDiskSmallerThanImage,
                          self._vmops._create_root_vhd, self.context,
                          mock_instance)

        self.assertFalse(self._vmops._vhdutils.resize_vhd.called)
        self._vmops._pathutils.exists.assert_called_once_with(
            fake_root_path)
        self._vmops._pathutils.remove.assert_called_once_with(
            fake_root_path)

    @mock.patch('os.path.exists')
    def _test_create_root_vhd_qcow(self, mock_os_path_exists, vhd_format,
                                   vhd_already_exists=False):
        mock_instance = self._prepare_create_root_device_mocks(
            use_cow_images=True, vhd_format=vhd_format,
            vhd_size=(self.FAKE_SIZE - 1))
        fake_vhd_path = self.FAKE_ROOT_PATH % vhd_format
        self._vmops._imagecache.get_cached_image.return_value = fake_vhd_path
        mock_os_path_exists.return_value = vhd_already_exists

        fake_root_path = self._vmops._pathutils.get_root_vhd_path.return_value
        root_vhd_internal_size = mock_instance.flavor.root_gb * units.Gi
        get_size = self._vmops._vhdutils.get_internal_vhd_size_by_file_size

        response = self._vmops._create_root_vhd(context=self.context,
                                                instance=mock_instance)

        self.assertEqual(fake_root_path, response)
        self._vmops._pathutils.get_root_vhd_path.assert_called_with(
            mock_instance.name, vhd_format, False)

        differencing_vhd = self._vmops._vhdutils.create_differencing_vhd

        if not vhd_already_exists:
            differencing_vhd.assert_called_with(fake_root_path, fake_vhd_path)
            self._vmops._vhdutils.get_vhd_info.assert_called_once_with(
                fake_vhd_path)

            if vhd_format is constants.DISK_FORMAT_VHD:
                self.assertFalse(get_size.called)
                self.assertFalse(self._vmops._vhdutils.resize_vhd.called)
            else:
                get_size.assert_called_once_with(fake_vhd_path,
                                                 root_vhd_internal_size)
                self._vmops._vhdutils.resize_vhd.assert_called_once_with(
                    fake_root_path, root_vhd_internal_size,
                    is_file_max_size=False)
        else:
            differencing_vhd.assert_not_called()
            self._vmops._vhdutils.resize_vhd.assert_not_called()

    @mock.patch('os.path.exists')
    def _test_create_root_vhd(self, mock_os_path_exists,
                              vhd_format, is_rescue_vhd=False,
                              vhd_already_exists=False):
        mock_instance = self._prepare_create_root_device_mocks(
            use_cow_images=False, vhd_format=vhd_format,
            vhd_size=(self.FAKE_SIZE - 1))
        fake_vhd_path = self.FAKE_ROOT_PATH % vhd_format
        mock_get_cached_image = self._vmops._imagecache.get_cached_image
        mock_get_cached_image.return_value = fake_vhd_path
        rescue_image_id = (
            mock.sentinel.rescue_image_id if is_rescue_vhd else None)

        fake_root_path = self._vmops._pathutils.get_root_vhd_path.return_value
        root_vhd_internal_size = mock_instance.flavor.root_gb * units.Gi
        get_size = self._vmops._vhdutils.get_internal_vhd_size_by_file_size
        mock_os_path_exists.return_value = vhd_already_exists

        response = self._vmops._create_root_vhd(
            context=self.context,
            instance=mock_instance,
            rescue_image_id=rescue_image_id)

        self.assertEqual(fake_root_path, response)
        mock_get_cached_image.assert_called_once_with(self.context,
                                                      mock_instance,
                                                      rescue_image_id)
        self._vmops._pathutils.get_root_vhd_path.assert_called_with(
            mock_instance.name, vhd_format, is_rescue_vhd)

        if not vhd_already_exists:
            self._vmops._pathutils.copyfile.assert_called_once_with(
                fake_vhd_path, fake_root_path)
            get_size.assert_called_once_with(fake_vhd_path,
                                             root_vhd_internal_size)

            if is_rescue_vhd:
                self.assertFalse(self._vmops._vhdutils.resize_vhd.called)
            else:
                self._vmops._vhdutils.resize_vhd.assert_called_once_with(
                    fake_root_path, root_vhd_internal_size,
                    is_file_max_size=False)
        else:
            self._vmops._pathutils.copyfile.assert_not_called()

    def test_create_root_vhd(self):
        self._test_create_root_vhd(vhd_format=constants.DISK_FORMAT_VHD)

    def test_create_root_vhdx(self):
        self._test_create_root_vhd(vhd_format=constants.DISK_FORMAT_VHDX)

    def test_create_root_vhd_existing_disk(self):
        self._test_create_root_vhd(vhd_format=constants.DISK_FORMAT_VHD,
                                   vhd_already_exists=True)

    def test_create_root_vhdx_existing_disk(self):
        self._test_create_root_vhd(vhd_format=constants.DISK_FORMAT_VHDX,
                                   vhd_already_exists=True)

    def test_create_root_vhd_use_cow_images_true(self):
        self._test_create_root_vhd_qcow(vhd_format=constants.DISK_FORMAT_VHD)

    def test_create_root_vhdx_use_cow_images_true(self):
        self._test_create_root_vhd_qcow(vhd_format=constants.DISK_FORMAT_VHDX)

    def test_create_root_vhd_use_already_existing_cow_images(self):
        self._test_create_root_vhd_qcow(vhd_format=constants.DISK_FORMAT_VHD,
                                        vhd_already_exists=True)

    def test_create_root_vhdx_use_already_existing_cow_images(self):
        self._test_create_root_vhd_qcow(vhd_format=constants.DISK_FORMAT_VHDX,
                                        vhd_already_exists=True)

    def test_create_rescue_vhd(self):
        self._test_create_root_vhd(vhd_format=constants.DISK_FORMAT_VHD,
                                   is_rescue_vhd=True)

    def test_create_root_vhdx_size_less_than_internal(self):
        self._test_create_root_vhd_exception(
            vhd_format=constants.DISK_FORMAT_VHD)

    def test_is_resize_needed_exception(self):
        inst = mock.MagicMock()
        self.assertRaises(
            exception.FlavorDiskSmallerThanImage,
            self._vmops._is_resize_needed,
            mock.sentinel.FAKE_PATH, self.FAKE_SIZE, self.FAKE_SIZE - 1, inst)

    def test_is_resize_needed_true(self):
        inst = mock.MagicMock()
        self.assertTrue(self._vmops._is_resize_needed(
            mock.sentinel.FAKE_PATH, self.FAKE_SIZE, self.FAKE_SIZE + 1, inst))

    def test_is_resize_needed_false(self):
        inst = mock.MagicMock()
        self.assertFalse(self._vmops._is_resize_needed(
            mock.sentinel.FAKE_PATH, self.FAKE_SIZE, self.FAKE_SIZE, inst))

    @mock.patch.object(vmops.VMOps, 'create_ephemeral_disk')
    def test_create_ephemerals(self, mock_create_ephemeral_disk):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        fake_ephemerals = [dict(), dict()]
        self._vmops._vhdutils.get_best_supported_vhd_format.return_value = (
            mock.sentinel.format)
        self._vmops._pathutils.get_ephemeral_vhd_path.side_effect = [
            mock.sentinel.FAKE_PATH0, mock.sentinel.FAKE_PATH1]

        self._vmops._create_ephemerals(mock_instance, fake_ephemerals)

        self._vmops._pathutils.get_ephemeral_vhd_path.assert_has_calls(
            [mock.call(mock_instance.name, mock.sentinel.format, 'eph0'),
             mock.call(mock_instance.name, mock.sentinel.format, 'eph1')])
        mock_create_ephemeral_disk.assert_has_calls(
            [mock.call(mock_instance.name, fake_ephemerals[0]),
             mock.call(mock_instance.name, fake_ephemerals[1])])

    def test_create_ephemeral_disk(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_ephemeral_info = {'path': 'fake_eph_path',
                               'size': 10}

        self._vmops.create_ephemeral_disk(mock_instance.name,
                                          mock_ephemeral_info)

        mock_create_dynamic_vhd = self._vmops._vhdutils.create_dynamic_vhd
        mock_create_dynamic_vhd.assert_called_once_with('fake_eph_path',
                                                        10 * units.Gi)

    def test_get_attached_ephemeral_disks(self):
        ephemeral_disks = [os.path.join('image_dir', img_name)
                           for img_name in ['eph0.vhdx', 'eph1.vhdx']]
        image_disks = ephemeral_disks + [
            os.path.join('image_dir', 'root.vhdx')]

        self._vmutils.get_vm_storage_paths.return_value = (
            image_disks, mock.sentinel.passthrough_disks)

        ret_val = self._vmops.get_attached_ephemeral_disks(
            mock.sentinel.instance_name)

        self.assertEqual(ephemeral_disks, ret_val)
        self._vmutils.get_vm_storage_paths.assert_called_once_with(
            mock.sentinel.instance_name)

    @mock.patch.object(vmops.objects, 'PCIDeviceBus')
    @mock.patch.object(vmops.objects, 'NetworkInterfaceMetadata')
    @mock.patch.object(vmops.objects.VirtualInterfaceList,
                       'get_by_instance_uuid')
    def test_get_vif_metadata(self, mock_get_by_inst_uuid,
                              mock_NetworkInterfaceMetadata, mock_PCIDevBus):
        mock_vif = mock.MagicMock(tag='taggy')
        mock_vif.__contains__.side_effect = (
            lambda attr: getattr(mock_vif, attr, None) is not None)
        mock_get_by_inst_uuid.return_value = [mock_vif,
                                              mock.MagicMock(tag=None)]

        vif_metadata = self._vmops._get_vif_metadata(self.context,
                                                     mock.sentinel.instance_id)

        mock_get_by_inst_uuid.assert_called_once_with(
            self.context, mock.sentinel.instance_id)
        mock_NetworkInterfaceMetadata.assert_called_once_with(
            mac=mock_vif.address,
            bus=mock_PCIDevBus.return_value,
            tags=[mock_vif.tag])
        self.assertEqual([mock_NetworkInterfaceMetadata.return_value],
                         vif_metadata)

    @mock.patch.object(vmops.objects, 'InstanceDeviceMetadata')
    @mock.patch.object(vmops.VMOps, '_get_vif_metadata')
    def test_update_device_metadata(self, mock_get_vif_metadata,
                                    mock_InstanceDeviceMetadata):
        mock_instance = mock.MagicMock()
        mock_get_vif_metadata.return_value = [mock.sentinel.vif_metadata]
        self._vmops._block_dev_man.get_bdm_metadata.return_value = [
            mock.sentinel.bdm_metadata]

        self._vmops.update_device_metadata(self.context, mock_instance)

        mock_get_vif_metadata.assert_called_once_with(self.context,
                                                      mock_instance.uuid)
        self._vmops._block_dev_man.get_bdm_metadata.assert_called_once_with(
            self.context, mock_instance)

        expected_metadata = [mock.sentinel.vif_metadata,
                             mock.sentinel.bdm_metadata]
        mock_InstanceDeviceMetadata.assert_called_once_with(
            devices=expected_metadata)
        self.assertEqual(mock_InstanceDeviceMetadata.return_value,
                         mock_instance.device_metadata)

    def test_set_boot_order(self):
        self._vmops.set_boot_order(mock.sentinel.instance_name,
                                   mock.sentinel.vm_gen,
                                   mock.sentinel.bdi)

        mock_get_boot_order = self._vmops._block_dev_man.get_boot_order
        mock_get_boot_order.assert_called_once_with(
            mock.sentinel.vm_gen, mock.sentinel.bdi)
        self._vmops._vmutils.set_boot_order.assert_called_once_with(
            mock.sentinel.instance_name, mock_get_boot_order.return_value)

    @mock.patch.object(vmops.VMOps, 'plug_vifs')
    @mock.patch('compute_hyperv.nova.vmops.VMOps.destroy')
    @mock.patch('compute_hyperv.nova.vmops.VMOps.power_on')
    @mock.patch.object(vmops.VMOps, 'set_boot_order')
    @mock.patch('compute_hyperv.nova.vmops.VMOps.attach_config_drive')
    @mock.patch('compute_hyperv.nova.vmops.VMOps._create_config_drive')
    @mock.patch('nova.virt.configdrive.required_by')
    @mock.patch('compute_hyperv.nova.vmops.VMOps.update_device_metadata')
    @mock.patch('compute_hyperv.nova.vmops.VMOps.create_instance')
    @mock.patch('compute_hyperv.nova.vmops.VMOps.get_image_vm_generation')
    @mock.patch('compute_hyperv.nova.vmops.VMOps._create_ephemerals')
    @mock.patch('compute_hyperv.nova.vmops.VMOps._create_root_device')
    @mock.patch('compute_hyperv.nova.vmops.VMOps._delete_disk_files')
    @mock.patch('compute_hyperv.nova.vmops.VMOps._get_neutron_events',
                return_value=[])
    def _test_spawn(self, mock_get_neutron_events,
                    mock_delete_disk_files,
                    mock_create_root_device,
                    mock_create_ephemerals, mock_get_image_vm_gen,
                    mock_create_instance, mock_update_device_metadata,
                    mock_configdrive_required,
                    mock_create_config_drive, mock_attach_config_drive,
                    mock_set_boot_order,
                    mock_power_on, mock_destroy, mock_plug_vifs,
                    exists, configdrive_required, fail,
                    fake_vm_gen=constants.VM_GEN_2):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_image_meta = mock.MagicMock()
        root_device_info = mock.sentinel.ROOT_DEV_INFO
        mock_get_image_vm_gen.return_value = fake_vm_gen
        fake_config_drive_path = mock_create_config_drive.return_value
        block_device_info = {'ephemerals': [], 'root_disk': root_device_info}

        self._vmops._pathutils.get_instance_dir.return_value = (
            'fake-instance-dir')
        self._vmops._vmutils.vm_exists.return_value = exists
        mock_configdrive_required.return_value = configdrive_required
        mock_create_instance.side_effect = fail
        if exists:
            self.assertRaises(exception.InstanceExists, self._vmops.spawn,
                              self.context, mock_instance, mock_image_meta,
                              [mock.sentinel.FILE], mock.sentinel.PASSWORD,
                              mock.sentinel.network_info, block_device_info)
        elif fail is os_win_exc.HyperVException:
            self.assertRaises(os_win_exc.HyperVException, self._vmops.spawn,
                              self.context, mock_instance, mock_image_meta,
                              [mock.sentinel.FILE], mock.sentinel.PASSWORD,
                              mock.sentinel.network_info, block_device_info)
            mock_destroy.assert_called_once_with(mock_instance,
                                                 mock.sentinel.network_info,
                                                 block_device_info)
        else:
            self._vmops.spawn(self.context, mock_instance, mock_image_meta,
                              [mock.sentinel.FILE], mock.sentinel.PASSWORD,
                              mock.sentinel.network_info, block_device_info)
            self._vmops._vmutils.vm_exists.assert_called_once_with(
                mock_instance.name)
            self._vmops._pathutils.get_instance_dir.assert_called_once_with(
                mock_instance.name, create_dir=False)
            mock_validate_and_update_bdi = (
                self._vmops._block_dev_man.validate_and_update_bdi)
            mock_validate_and_update_bdi.assert_called_once_with(
                mock_instance, mock_image_meta, fake_vm_gen, block_device_info)
            mock_create_root_device.assert_called_once_with(self.context,
                                                            mock_instance,
                                                            root_device_info,
                                                            fake_vm_gen)
            mock_create_ephemerals.assert_called_once_with(
                mock_instance, block_device_info['ephemerals'])
            mock_get_neutron_events.assert_called_once_with(
                mock.sentinel.network_info)
            mock_get_image_vm_gen.assert_called_once_with(mock_instance.uuid,
                                                          mock_image_meta)
            mock_create_instance.assert_called_once_with(
                self.context, mock_instance, mock.sentinel.network_info,
                block_device_info, fake_vm_gen, mock_image_meta)
            mock_plug_vifs.assert_called_once_with(mock_instance,
                                                   mock.sentinel.network_info)
            mock_update_device_metadata.assert_called_once_with(
                self.context, mock_instance)
            mock_configdrive_required.assert_called_once_with(mock_instance)
            if configdrive_required:
                mock_create_config_drive.assert_called_once_with(
                    self.context, mock_instance, [mock.sentinel.FILE],
                    mock.sentinel.PASSWORD,
                    mock.sentinel.network_info)
                mock_attach_config_drive.assert_called_once_with(
                    mock_instance, fake_config_drive_path, fake_vm_gen)
            mock_set_boot_order.assert_called_once_with(
                mock_instance.name, fake_vm_gen, block_device_info)
            mock_power_on.assert_called_once_with(
                mock_instance,
                network_info=mock.sentinel.network_info,
                should_plug_vifs=False)

    def test_spawn(self):
        self._test_spawn(exists=False, configdrive_required=True, fail=None)

    def test_spawn_instance_exists(self):
        self._test_spawn(exists=True, configdrive_required=True, fail=None)

    def test_spawn_create_instance_exception(self):
        self._test_spawn(exists=False, configdrive_required=True,
                         fail=os_win_exc.HyperVException)

    def test_spawn_not_required(self):
        self._test_spawn(exists=False, configdrive_required=False, fail=None)

    def test_spawn_no_admin_permissions(self):
        self._vmops._vmutils.check_admin_permissions.side_effect = (
            os_win_exc.HyperVException)
        self.assertRaises(os_win_exc.HyperVException,
                          self._vmops.spawn,
                          self.context, mock.DEFAULT, mock.DEFAULT,
                          [mock.sentinel.FILE], mock.sentinel.PASSWORD,
                          mock.sentinel.INFO, mock.sentinel.DEV_INFO)

    @mock.patch.object(vmops.VMOps, '_get_neutron_events')
    def test_wait_vif_plug_events(self, mock_get_events):
        self._vmops._virtapi.wait_for_instance_event.side_effect = (
            etimeout.Timeout)
        self.flags(vif_plugging_timeout=1)
        self.flags(vif_plugging_is_fatal=True)

        def _context_user():
            with self._vmops.wait_vif_plug_events(mock.sentinel.instance,
                                                  mock.sentinel.network_info):
                pass

        self.assertRaises(exception.VirtualInterfaceCreateException,
                          _context_user)

        mock_get_events.assert_called_once_with(mock.sentinel.network_info)
        self._vmops._virtapi.wait_for_instance_event.assert_called_once_with(
            mock.sentinel.instance, mock_get_events.return_value,
            deadline=CONF.vif_plugging_timeout,
            error_callback=self._vmops._neutron_failed_callback)

    @mock.patch.object(vmops.VMOps, '_get_neutron_events')
    def test_wait_vif_plug_events_port_binding_failed(self, mock_get_events):
        mock_get_events.side_effect = exception.PortBindingFailed(
            port_id='fake_id')

        def _context_user():
            with self._vmops.wait_vif_plug_events(mock.sentinel.instance,
                                                  mock.sentinel.network_info):
                pass

        self.assertRaises(exception.PortBindingFailed, _context_user)

    def test_neutron_failed_callback(self):
        self.flags(vif_plugging_is_fatal=True)
        self.assertRaises(exception.VirtualInterfaceCreateException,
                          self._vmops._neutron_failed_callback,
                          mock.sentinel.event_name, mock.sentinel.instance)

    @mock.patch.object(vmops.utils, 'is_neutron')
    def test_get_neutron_events(self, mock_is_neutron):
        network_info = [{'id': mock.sentinel.vif_id1, 'active': True},
                        {'id': mock.sentinel.vif_id2, 'active': False},
                        {'id': mock.sentinel.vif_id3}]

        events = self._vmops._get_neutron_events(network_info)
        self.assertEqual([('network-vif-plugged', mock.sentinel.vif_id2)],
                         events)
        mock_is_neutron.assert_called_once_with()

    @mock.patch.object(vmops.utils, 'is_neutron')
    def test_get_neutron_events_no_timeout(self, mock_is_neutron):
        self.flags(vif_plugging_timeout=0)
        network_info = [{'id': mock.sentinel.vif_id1, 'active': True}]

        events = self._vmops._get_neutron_events(network_info)
        self.assertEqual([], events)
        mock_is_neutron.assert_called_once_with()

    @mock.patch.object(vmops.VMOps, 'configure_instance_metrics')
    @mock.patch.object(vmops.VMOps, 'update_vm_resources')
    @mock.patch.object(vmops.VMOps, '_configure_secure_vm')
    @mock.patch.object(vmops.VMOps, '_requires_secure_boot')
    @mock.patch.object(vmops.VMOps, '_requires_certificate')
    @mock.patch.object(vmops.VMOps, '_get_instance_vnuma_config')
    @mock.patch.object(vmops.VMOps, '_attach_root_device')
    @mock.patch.object(vmops.VMOps, 'configure_remotefx')
    @mock.patch.object(vmops.VMOps, '_get_image_serial_port_settings')
    @mock.patch.object(vmops.VMOps, '_create_vm_com_port_pipes')
    @mock.patch.object(vmops.VMOps, 'attach_ephemerals')
    def test_create_instance(self, mock_attach_ephemerals,
                             mock_create_pipes,
                             mock_get_port_settings,
                             mock_configure_remotefx,
                             mock_attach_root_device,
                             mock_get_vnuma_config,
                             mock_requires_certificate,
                             mock_requires_secure_boot,
                             mock_configure_secure_vm,
                             mock_update_vm_resources,
                             mock_configure_metrics):
        root_device_info = mock.sentinel.ROOT_DEV_INFO
        block_device_info = {'root_disk': root_device_info, 'ephemerals': [],
                             'block_device_mapping': []}
        fake_network_info = {'id': mock.sentinel.ID,
                             'address': mock.sentinel.ADDRESS}
        mock_instance = fake_instance.fake_instance_obj(self.context)
        instance_path = os.path.join(CONF.instances_path, mock_instance.name)

        mock_get_vnuma_config.return_value = (mock.sentinel.mem_per_numa_node,
                                      mock.sentinel.vnuma_cpus)

        self._vmops.create_instance(context=self.context,
                                    instance=mock_instance,
                                    network_info=[fake_network_info],
                                    block_device_info=block_device_info,
                                    vm_gen=mock.sentinel.vm_gen,
                                    image_meta=mock.sentinel.image_meta)

        mock_get_vnuma_config.assert_called_once_with(mock_instance,
                                                      mock.sentinel.image_meta)
        self._vmops._vmutils.create_vm.assert_called_once_with(
            mock_instance.name, True, mock.sentinel.vm_gen,
            instance_path, [mock_instance.uuid])

        mock_configure_remotefx.assert_called_once_with(
            mock_instance, mock.sentinel.vm_gen)

        mock_create_scsi_ctrl = self._vmops._vmutils.create_scsi_controller
        mock_create_scsi_ctrl.assert_called_once_with(mock_instance.name)

        mock_attach_root_device.assert_called_once_with(
            self.context, mock_instance, root_device_info)
        mock_attach_ephemerals.assert_called_once_with(mock_instance.name,
            block_device_info['ephemerals'])
        self._vmops._volumeops.attach_volumes.assert_called_once_with(
            self.context, block_device_info['block_device_mapping'],
            mock_instance)

        mock_get_port_settings.assert_called_with(mock.sentinel.image_meta)
        mock_create_pipes.assert_called_once_with(
            mock_instance, mock_get_port_settings.return_value)

        self._vmops._vmutils.create_nic.assert_called_once_with(
            mock_instance.name, mock.sentinel.ID, mock.sentinel.ADDRESS)
        mock_configure_metrics.assert_called_once_with(mock_instance.name)
        mock_requires_secure_boot.assert_called_once_with(
            mock_instance, mock.sentinel.image_meta, mock.sentinel.vm_gen)
        mock_requires_certificate.assert_called_once_with(
            mock.sentinel.image_meta)
        enable_secure_boot = self._vmops._vmutils.enable_secure_boot
        enable_secure_boot.assert_called_once_with(
            mock_instance.name,
            msft_ca_required=mock_requires_certificate.return_value)
        mock_configure_secure_vm.assert_called_once_with(self.context,
            mock_instance, mock.sentinel.image_meta,
            mock_requires_secure_boot.return_value)
        mock_update_vm_resources.assert_called_once_with(
            mock_instance, mock.sentinel.vm_gen, mock.sentinel.image_meta)

    @mock.patch.object(vmops.VMOps, '_attach_pci_devices')
    @mock.patch.object(vmops.VMOps, '_set_instance_disk_qos_specs')
    @mock.patch.object(vmops.VMOps, '_get_instance_dynamic_memory_ratio')
    @mock.patch.object(vmops.VMOps, '_requires_nested_virt')
    @mock.patch.object(vmops.VMOps, '_get_instance_vnuma_config')
    def _check_update_vm_resources(self, mock_get_vnuma_config,
                                   mock_requires_nested_virt,
                                   mock_get_dynamic_memory_ratio,
                                   mock_set_qos_specs,
                                   mock_attach_pci_devices,
                                   pci_requests=None,
                                   instance_automatic_shutdown=False):
        self.flags(instance_automatic_shutdown=instance_automatic_shutdown,
                   group='hyperv')

        mock_get_vnuma_config.return_value = (mock.sentinel.mem_per_numa_node,
                                              mock.sentinel.vnuma_cpus)
        dynamic_memory_ratio = mock_get_dynamic_memory_ratio.return_value
        mock_instance = fake_instance.fake_instance_obj(self.context)

        instance_pci_requests = objects.InstancePCIRequests(
            requests=pci_requests or [], instance_uuid=mock_instance.uuid)
        mock_instance.pci_requests = instance_pci_requests
        host_shutdown_action = (os_win_const.HOST_SHUTDOWN_ACTION_SHUTDOWN
                                if pci_requests or
                                    instance_automatic_shutdown
                                else None)

        self._vmops.update_vm_resources(mock_instance, mock.sentinel.vm_gen,
                                        mock.sentinel.image_meta,
                                        mock.sentinel.instance_path,
                                        mock.sentinel.is_resize)

        mock_get_vnuma_config.assert_called_once_with(mock_instance,
                                                      mock.sentinel.image_meta)
        mock_requires_nested_virt.assert_called_once_with(
            mock_instance, mock.sentinel.image_meta)
        mock_get_dynamic_memory_ratio.assert_called_once_with(
            mock_instance, True, mock_requires_nested_virt.return_value)
        self._vmops._vmutils.update_vm.assert_called_once_with(
            mock_instance.name, mock_instance.flavor.memory_mb,
            mock.sentinel.mem_per_numa_node, mock_instance.flavor.vcpus,
            mock.sentinel.vnuma_cpus, CONF.hyperv.limit_cpu_features,
            dynamic_memory_ratio,
            configuration_root_dir=mock.sentinel.instance_path,
            host_shutdown_action=host_shutdown_action,
            vnuma_enabled=True)
        mock_set_qos_specs.assert_called_once_with(mock_instance,
                                                   mock.sentinel.is_resize)
        mock_attach_pci_devices.assert_called_once_with(
            mock_instance, mock.sentinel.is_resize)
        self._vmops._vmutils.set_nested_virtualization(
            mock_instance.name, state=mock_requires_nested_virt.return_value)

    def test_update_vm_resources(self):
        self._check_update_vm_resources()

    def test_update_vm_resources_pci_requested(self):
        vendor_id = 'fake_vendor_id'
        product_id = 'fake_product_id'
        spec = {'vendor_id': vendor_id, 'product_id': product_id}
        request = objects.InstancePCIRequest(count=1, spec=[spec])
        self._check_update_vm_resources(pci_requests=[request])

    def test_create_instance_automatic_shutdown(self):
        self._check_update_vm_resources(instance_automatic_shutdown=True)

    def test_attach_pci_devices(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        vendor_id = 'fake_vendor_id'
        product_id = 'fake_product_id'
        spec = {'vendor_id': vendor_id, 'product_id': product_id}
        request = objects.InstancePCIRequest(count=2, spec=[spec])
        instance_pci_requests = objects.InstancePCIRequests(
            requests=[request], instance_uuid=mock_instance.uuid)
        mock_instance.pci_requests = instance_pci_requests

        self._vmops._attach_pci_devices(mock_instance, True)

        self._vmops._vmutils.remove_all_pci_devices.assert_called_once_with(
            mock_instance.name)
        self._vmops._vmutils.add_pci_device.assert_has_calls(
            [mock.call(mock_instance.name, vendor_id, product_id)] * 2)

    @mock.patch.object(vmops.hardware, 'numa_get_constraints')
    @mock.patch.object(vmops.objects.ImageMeta, 'from_dict')
    def _check_get_instance_vnuma_config_exception(self, mock_from_dict,
                                                   mock_get_numa, numa_cells):
        flavor = {'extra_specs': {}}
        mock_instance = mock.MagicMock(flavor=flavor)
        image_meta = mock.MagicMock(properties={})
        mock_get_numa.return_value.cells = numa_cells

        self.assertRaises(exception.InstanceUnacceptable,
                          self._vmops._get_instance_vnuma_config,
                          mock_instance, image_meta)

    def test_get_instance_vnuma_config_bad_cpuset(self):
        cell1 = mock.MagicMock(cpuset=set([0]), memory=1024)
        cell2 = mock.MagicMock(cpuset=set([1, 2]), memory=1024)
        self._check_get_instance_vnuma_config_exception(
            numa_cells=[cell1, cell2])

    def test_get_instance_vnuma_config_bad_memory(self):
        cell1 = mock.MagicMock(cpuset=set([0]), memory=1024)
        cell2 = mock.MagicMock(cpuset=set([1]), memory=2048)
        self._check_get_instance_vnuma_config_exception(
            numa_cells=[cell1, cell2])

    @mock.patch.object(vmops.hardware, 'numa_get_constraints')
    @mock.patch.object(vmops.objects.ImageMeta, 'from_dict')
    def _check_get_instance_vnuma_config(
                self, mock_from_dict, mock_get_numa, numa_topology=None,
                expected_mem_per_numa=None, expected_cpus_per_numa=None):
        mock_instance = mock.MagicMock()
        image_meta = mock.MagicMock()
        mock_get_numa.return_value = numa_topology

        result_memory_per_numa, result_cpus_per_numa = (
            self._vmops._get_instance_vnuma_config(mock_instance, image_meta))

        self.assertEqual(expected_cpus_per_numa, result_cpus_per_numa)
        self.assertEqual(expected_mem_per_numa, result_memory_per_numa)

    def test_get_instance_vnuma_config(self):
        cell1 = mock.MagicMock(cpuset=set([0]), memory=2048, cpu_pinning=None)
        cell2 = mock.MagicMock(cpuset=set([1]), memory=2048, cpu_pinning=None)
        mock_topology = mock.MagicMock(cells=[cell1, cell2])
        self._check_get_instance_vnuma_config(numa_topology=mock_topology,
                                              expected_cpus_per_numa=1,
                                              expected_mem_per_numa=2048)

    def test_get_instance_vnuma_config_no_topology(self):
        self._check_get_instance_vnuma_config()

    @ddt.data((True, False),
              (False, True),
              (False, False))
    @ddt.unpack
    def test_get_instance_dynamic_memory_ratio(self, vnuma_enabled,
                                               nested_virt_enabled):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        expected_dyn_memory_ratio = 2.0
        self.flags(dynamic_memory_ratio=expected_dyn_memory_ratio,
                   group='hyperv')
        if vnuma_enabled or nested_virt_enabled:
            expected_dyn_memory_ratio = 1.0

        response = self._vmops._get_instance_dynamic_memory_ratio(
            mock_instance, vnuma_enabled, nested_virt_enabled)
        self.assertEqual(expected_dyn_memory_ratio, response)

    def test_attach_root_device_volume(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        root_device_info = {'type': constants.VOLUME,
                            'connection_info': mock.sentinel.CONN_INFO,
                            'disk_bus': constants.CTRL_TYPE_IDE}

        self._vmops._attach_root_device(self.context,
                                        mock_instance, root_device_info)

        self._vmops._volumeops.attach_volume.assert_called_once_with(
            self.context,
            root_device_info['connection_info'], mock_instance,
            disk_bus=root_device_info['disk_bus'])

    @mock.patch.object(vmops.VMOps, '_attach_drive')
    def test_attach_root_device_disk(self, mock_attach_drive):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        root_device_info = {'type': constants.DISK,
                            'boot_index': 0,
                            'disk_bus': constants.CTRL_TYPE_IDE,
                            'path': 'fake_path',
                            'drive_addr': 0,
                            'ctrl_disk_addr': 1}

        self._vmops._attach_root_device(
            self.context, mock_instance, root_device_info)

        mock_attach_drive.assert_called_once_with(
            mock_instance.name, root_device_info['path'],
            root_device_info['drive_addr'], root_device_info['ctrl_disk_addr'],
            root_device_info['disk_bus'], root_device_info['type'])

    @mock.patch.object(vmops.VMOps, '_attach_drive')
    def test_attach_ephemerals(self, mock_attach_drive):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        class FakeBDM(dict):
            _bdm_obj = mock.sentinel.bdm_obj

        ephemerals = [{'path': os.path.join('eph_dir', 'eph0_path'),
                       'boot_index': 1,
                       'disk_bus': constants.CTRL_TYPE_IDE,
                       'device_type': 'disk',
                       'drive_addr': 0,
                       'ctrl_disk_addr': 1},
                      {'path': os.path.join('eph_dir', 'eph1_path'),
                       'boot_index': 2,
                       'disk_bus': constants.CTRL_TYPE_SCSI,
                       'device_type': 'disk',
                       'drive_addr': 0,
                       'ctrl_disk_addr': 0},
                      {'path': None}]
        ephemerals = [FakeBDM(ephemerals[0]),
                      ephemerals[1],
                      FakeBDM(ephemerals[2])]

        self._vmops.attach_ephemerals(mock_instance.name, ephemerals)

        mock_attach_drive.assert_has_calls(
            [mock.call(mock_instance.name, ephemerals[0]['path'], 0,
                       1, constants.CTRL_TYPE_IDE, constants.DISK),
             mock.call(mock_instance.name, ephemerals[1]['path'], 0,
                       0, constants.CTRL_TYPE_SCSI, constants.DISK)
        ])
        mock_update_conn = (
            self._vmops._block_dev_man.update_bdm_connection_info)
        mock_update_conn.assert_called_once_with(
            mock.sentinel.bdm_obj,
            eph_filename=os.path.basename(ephemerals[0]['path']))

    def test_attach_drive_vm_to_scsi(self):
        self._vmops._attach_drive(
            mock.sentinel.FAKE_VM_NAME, mock.sentinel.FAKE_PATH,
            mock.sentinel.FAKE_DRIVE_ADDR, mock.sentinel.FAKE_CTRL_DISK_ADDR,
            constants.CTRL_TYPE_SCSI)

        self._vmops._vmutils.attach_scsi_drive.assert_called_once_with(
            mock.sentinel.FAKE_VM_NAME, mock.sentinel.FAKE_PATH,
            constants.DISK)

    def test_attach_drive_vm_to_ide(self):
        self._vmops._attach_drive(
            mock.sentinel.FAKE_VM_NAME, mock.sentinel.FAKE_PATH,
            mock.sentinel.FAKE_DRIVE_ADDR, mock.sentinel.FAKE_CTRL_DISK_ADDR,
            constants.CTRL_TYPE_IDE)

        self._vmops._vmutils.attach_ide_drive.assert_called_once_with(
            mock.sentinel.FAKE_VM_NAME, mock.sentinel.FAKE_PATH,
            mock.sentinel.FAKE_DRIVE_ADDR, mock.sentinel.FAKE_CTRL_DISK_ADDR,
            constants.DISK)

    def test_get_image_vm_generation_default(self):
        image_meta = {"properties": {}}
        self._vmops._hostutils.get_default_vm_generation.return_value = (
            constants.IMAGE_PROP_VM_GEN_1)
        self._vmops._hostutils.get_supported_vm_types.return_value = [
            constants.IMAGE_PROP_VM_GEN_1, constants.IMAGE_PROP_VM_GEN_2]

        response = self._vmops.get_image_vm_generation(
            mock.sentinel.instance_id, image_meta)

        self.assertEqual(constants.VM_GEN_1, response)

    def test_get_image_vm_generation_gen2(self):
        image_meta = {"properties": {
            constants.IMAGE_PROP_VM_GEN: constants.IMAGE_PROP_VM_GEN_2}}
        self._vmops._hostutils.get_supported_vm_types.return_value = [
            constants.IMAGE_PROP_VM_GEN_1, constants.IMAGE_PROP_VM_GEN_2]

        response = self._vmops.get_image_vm_generation(
            mock.sentinel.instance_id, image_meta)

        self.assertEqual(constants.VM_GEN_2, response)

    def test_get_image_vm_generation_bad_prop(self):
        image_meta = {"properties":
            {constants.IMAGE_PROP_VM_GEN: mock.sentinel.bad_prop}}
        self._vmops._hostutils.get_supported_vm_types.return_value = [
            constants.IMAGE_PROP_VM_GEN_1, constants.IMAGE_PROP_VM_GEN_2]

        self.assertRaises(exception.InstanceUnacceptable,
                          self._vmops.get_image_vm_generation,
                          mock.sentinel.instance_id,
                          image_meta)

    def test_check_vm_image_type_exception(self):
        self._vmops._vhdutils.get_vhd_format.return_value = (
            constants.DISK_FORMAT_VHD)

        self.assertRaises(exception.InstanceUnacceptable,
                          self._vmops.check_vm_image_type,
                          mock.sentinel.instance_id, constants.VM_GEN_2,
                          mock.sentinel.FAKE_PATH)

    def _check_requires_certificate(self, os_type):
        mock_image_meta = {'properties': {'os_type': os_type}}

        expected_result = os_type == fields.OSType.LINUX
        result = self._vmops._requires_certificate(mock_image_meta)
        self.assertEqual(expected_result, result)

    def test_requires_certificate_windows(self):
        self._check_requires_certificate(os_type=fields.OSType.WINDOWS)

    def test_requires_certificate_linux(self):
        self._check_requires_certificate(os_type=fields.OSType.LINUX)

    def _check_requires_secure_boot(
            self, image_prop_os_type=fields.OSType.LINUX,
            image_prop_secure_boot=fields.SecureBoot.REQUIRED,
            flavor_secure_boot=fields.SecureBoot.REQUIRED,
            vm_gen=constants.VM_GEN_2, expected_exception=True):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        if flavor_secure_boot:
            mock_instance.flavor.extra_specs = {
                constants.FLAVOR_SPEC_SECURE_BOOT: flavor_secure_boot}
        mock_image_meta = {'properties': {'os_type': image_prop_os_type}}
        if image_prop_secure_boot:
            mock_image_meta['properties']['os_secure_boot'] = (
                image_prop_secure_boot)

        if expected_exception:
            self.assertRaises(exception.InstanceUnacceptable,
                              self._vmops._requires_secure_boot,
                              mock_instance, mock_image_meta, vm_gen)
        else:
            result = self._vmops._requires_secure_boot(mock_instance,
                                                       mock_image_meta,
                                                       vm_gen)

            requires_sb = fields.SecureBoot.REQUIRED in [
                flavor_secure_boot, image_prop_secure_boot]
            self.assertEqual(requires_sb, result)

    def test_requires_secure_boot_ok(self):
        self._check_requires_secure_boot(
            expected_exception=False)

    def test_requires_secure_boot_image_img_prop_none(self):
        self._check_requires_secure_boot(
            image_prop_secure_boot=None,
            expected_exception=False)

    def test_requires_secure_boot_image_extra_spec_none(self):
        self._check_requires_secure_boot(
            flavor_secure_boot=None,
            expected_exception=False)

    def test_requires_secure_boot_flavor_no_os_type(self):
        self._check_requires_secure_boot(
            image_prop_os_type=None)

    def test_requires_secure_boot_flavor_no_os_type_no_exc(self):
        self._check_requires_secure_boot(
            image_prop_os_type=None,
            image_prop_secure_boot=fields.SecureBoot.DISABLED,
            flavor_secure_boot=fields.SecureBoot.DISABLED,
            expected_exception=False)

    def test_requires_secure_boot_flavor_disabled(self):
        self._check_requires_secure_boot(
            flavor_secure_boot=fields.SecureBoot.DISABLED)

    def test_requires_secure_boot_image_disabled(self):
        self._check_requires_secure_boot(
            image_prop_secure_boot=fields.SecureBoot.DISABLED)

    def test_requires_secure_boot_generation_1(self):
        self._check_requires_secure_boot(vm_gen=constants.VM_GEN_1)

    def _check_requires_nested_virt(self, extra_spec='', img_prop=None,
                                    expected=True):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.flavor.extra_specs['hw:cpu_features'] = extra_spec
        image_meta = {"properties": {'hw_cpu_features': img_prop or ''}}

        requires_nested = self._vmops._requires_nested_virt(mock_instance,
                                                            image_meta)
        self.assertEqual(expected, requires_nested)

    def test_requires_nested_virt_flavor(self):
        self._check_requires_nested_virt(extra_spec='vmx')

    def test_requires_nested_virt_image(self):
        self._check_requires_nested_virt(img_prop='vmx')

    def test_requires_nested_virt_False(self):
        self._check_requires_nested_virt(expected=False)

    def test_requires_nested_virt_unsupported(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.flavor.extra_specs['hw:cpu_features'] = 'vmx'
        mock_image_meta = mock.MagicMock()
        self._vmops._hostutils.supports_nested_virtualization.return_value = (
            False)

        self.assertRaises(exception.InstanceUnacceptable,
                          self._vmops._requires_nested_virt,
                          mock_instance, mock_image_meta)

    @mock.patch('nova.api.metadata.base.InstanceMetadata')
    @mock.patch('nova.virt.configdrive.ConfigDriveBuilder')
    @mock.patch('oslo_concurrency.processutils.execute')
    def _test_create_config_drive(self, mock_execute, mock_ConfigDriveBuilder,
                                  mock_InstanceMetadata, config_drive_format,
                                  config_drive_cdrom, side_effect,
                                  rescue=False):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self.flags(config_drive_format=config_drive_format)
        self.flags(config_drive_cdrom=config_drive_cdrom, group='hyperv')
        self.flags(config_drive_inject_password=True, group='hyperv')
        mock_ConfigDriveBuilder().__enter__().make_drive.side_effect = [
            side_effect]

        path_iso = os.path.join(self.FAKE_DIR, self.FAKE_CONFIG_DRIVE_ISO)
        path_vhd = os.path.join(self.FAKE_DIR, self.FAKE_CONFIG_DRIVE_VHD)

        def fake_get_configdrive_path(instance_name, disk_format,
                                      rescue=False):
            return (path_iso
                    if disk_format == constants.DVD_FORMAT else path_vhd)

        mock_get_configdrive_path = self._vmops._pathutils.get_configdrive_path
        mock_get_configdrive_path.side_effect = fake_get_configdrive_path
        expected_get_configdrive_path_calls = [mock.call(mock_instance.name,
                                                         constants.DVD_FORMAT,
                                                         rescue=rescue)]
        if not config_drive_cdrom:
            expected_call = mock.call(mock_instance.name,
                                      constants.DISK_FORMAT_VHD,
                                      rescue=rescue)
            expected_get_configdrive_path_calls.append(expected_call)

        if config_drive_format != self.ISO9660:
            self.assertRaises(exception.ConfigDriveUnsupportedFormat,
                              self._vmops._create_config_drive,
                              self.context,
                              mock_instance,
                              [mock.sentinel.FILE],
                              mock.sentinel.PASSWORD,
                              mock.sentinel.NET_INFO,
                              rescue)
        elif side_effect is processutils.ProcessExecutionError:
            self.assertRaises(processutils.ProcessExecutionError,
                              self._vmops._create_config_drive,
                              self.context,
                              mock_instance,
                              [mock.sentinel.FILE],
                              mock.sentinel.PASSWORD,
                              mock.sentinel.NET_INFO,
                              rescue)
        else:
            path = self._vmops._create_config_drive(self.context,
                                                    mock_instance,
                                                    [mock.sentinel.FILE],
                                                    mock.sentinel.PASSWORD,
                                                    mock.sentinel.NET_INFO,
                                                    rescue)
            mock_InstanceMetadata.assert_called_once_with(
                mock_instance, content=[mock.sentinel.FILE],
                extra_md={'admin_pass': mock.sentinel.PASSWORD},
                network_info=mock.sentinel.NET_INFO,
                request_context=self.context)
            mock_get_configdrive_path.assert_has_calls(
                expected_get_configdrive_path_calls)
            mock_ConfigDriveBuilder.assert_called_with(
                instance_md=mock_InstanceMetadata.return_value)
            mock_make_drive = mock_ConfigDriveBuilder().__enter__().make_drive
            mock_make_drive.assert_called_once_with(path_iso)
            if not CONF.hyperv.config_drive_cdrom:
                expected = path_vhd
                mock_execute.assert_called_once_with(
                    CONF.hyperv.qemu_img_cmd,
                    'convert', '-f', 'raw', '-O', 'vpc',
                    path_iso, path_vhd, attempts=1)
                self._vmops._pathutils.remove.assert_called_once_with(
                    os.path.join(self.FAKE_DIR, self.FAKE_CONFIG_DRIVE_ISO))
            else:
                expected = path_iso

            self.assertEqual(expected, path)

    def test_create_config_drive_cdrom(self):
        self._test_create_config_drive(config_drive_format=self.ISO9660,
                                       config_drive_cdrom=True,
                                       side_effect=None)

    def test_create_config_drive_vhd(self):
        self._test_create_config_drive(config_drive_format=self.ISO9660,
                                       config_drive_cdrom=False,
                                       side_effect=None)

    def test_create_rescue_config_drive_vhd(self):
        self._test_create_config_drive(config_drive_format=self.ISO9660,
                                       config_drive_cdrom=False,
                                       side_effect=None,
                                       rescue=True)

    def test_create_config_drive_other_drive_format(self):
        self._test_create_config_drive(config_drive_format=self.VFAT,
                                       config_drive_cdrom=False,
                                       side_effect=None)

    def test_create_config_drive_execution_error(self):
        self._test_create_config_drive(
            config_drive_format=self.ISO9660,
            config_drive_cdrom=False,
            side_effect=processutils.ProcessExecutionError)

    def test_attach_config_drive_exception(self):
        instance = fake_instance.fake_instance_obj(self.context)
        self.assertRaises(exception.InvalidDiskFormat,
                          self._vmops.attach_config_drive,
                          instance, 'C:/fake_instance_dir/configdrive.xxx',
                          constants.VM_GEN_1)

    @mock.patch.object(vmops.VMOps, '_attach_drive')
    def test_attach_config_drive(self, mock_attach_drive):
        instance = fake_instance.fake_instance_obj(self.context)
        self._vmops.attach_config_drive(instance,
                                        self._FAKE_CONFIGDRIVE_PATH,
                                        constants.VM_GEN_1)
        mock_attach_drive.assert_called_once_with(
            instance.name, self._FAKE_CONFIGDRIVE_PATH,
            1, 0, constants.CTRL_TYPE_IDE, constants.DISK)

    @mock.patch.object(vmops.VMOps, '_attach_drive')
    def test_attach_config_drive_gen2(self, mock_attach_drive):
        instance = fake_instance.fake_instance_obj(self.context)
        self._vmops.attach_config_drive(instance,
                                        self._FAKE_CONFIGDRIVE_PATH,
                                        constants.VM_GEN_2)
        mock_attach_drive.assert_called_once_with(
            instance.name, self._FAKE_CONFIGDRIVE_PATH,
            1, 0, constants.CTRL_TYPE_SCSI, constants.DISK)

    def test_detach_config_drive(self):
        is_rescue_configdrive = True
        mock_lookup_configdrive = (
            self._vmops._pathutils.lookup_configdrive_path)
        mock_lookup_configdrive.return_value = mock.sentinel.configdrive_path

        self._vmops._detach_config_drive(mock.sentinel.instance_name,
                                         rescue=is_rescue_configdrive,
                                         delete=True)

        mock_lookup_configdrive.assert_called_once_with(
            mock.sentinel.instance_name,
            rescue=is_rescue_configdrive)
        self._vmops._vmutils.detach_vm_disk.assert_called_once_with(
            mock.sentinel.instance_name, mock.sentinel.configdrive_path,
            is_physical=False)
        self._vmops._pathutils.remove.assert_called_once_with(
            mock.sentinel.configdrive_path)

    @ddt.data({'passed_instance_path': True},
              {'cleanup_migr_files': True})
    @ddt.unpack
    def test_delete_disk_files(self, passed_instance_path=None,
                               cleanup_migr_files=False):
        mock_instance = mock.Mock(
            system_metadata=dict(
                backup_location=mock.sentinel.backup_location))
        self._vmops._delete_disk_files(mock_instance,
                                       passed_instance_path,
                                       cleanup_migr_files)

        stop_console_handler = (
            self._vmops._serial_console_ops.stop_console_handler_unsync)
        stop_console_handler.assert_called_once_with(mock_instance.name)

        if passed_instance_path:
            self.assertFalse(self._vmops._pathutils.get_instance_dir.called)
        else:
            self._pathutils.get_instance_dir.assert_called_once_with(
                mock_instance.name)

        exp_inst_path = (passed_instance_path or
                         self._pathutils.get_instance_dir.return_value)

        exp_check_remove_dir_calls = [mock.call(exp_inst_path)]

        mock_get_migr_dir = self._pathutils.get_instance_migr_revert_dir
        if cleanup_migr_files:
            mock_get_migr_dir.assert_called_once_with(
                exp_inst_path, remove_dir=True)
            exp_check_remove_dir_calls.append(
                mock.call(mock.sentinel.backup_location))

        self._pathutils.check_remove_dir.assert_has_calls(
            exp_check_remove_dir_calls)

    @ddt.data({"force_destroy": True, "destroy_disks": False},
              {'vm_exists': False, 'planned_vm_exists': False},
              {'vm_exists': False, 'planned_vm_exists': True},
              {'task_state': task_states.RESIZE_REVERTING},
              {'cleanup_migr_files': False})
    @ddt.unpack
    @mock.patch('compute_hyperv.nova.vmops.VMOps._delete_disk_files')
    @mock.patch('compute_hyperv.nova.vmops.VMOps.power_off')
    @mock.patch('compute_hyperv.nova.vmops.VMOps.unplug_vifs')
    def test_destroy(self, mock_unplug_vifs, mock_power_off,
                     mock_delete_disk_files, vm_exists=True,
                     planned_vm_exists=False,
                     force_destroy=False,
                     task_state=task_states.DELETING,
                     destroy_disks=True,
                     cleanup_migr_files=True):
        self.flags(force_destroy_instances=force_destroy, group="hyperv")

        mock_instance = fake_instance.fake_instance_obj(
            self.context, task_state=task_state)
        self._vmops._vmutils.vm_exists.return_value = vm_exists
        self._vmops._migrutils.planned_vm_exists.return_value = (
            planned_vm_exists)

        self._vmops.destroy(
            instance=mock_instance,
            block_device_info=mock.sentinel.FAKE_BD_INFO,
            network_info=mock.sentinel.fake_network_info,
            cleanup_migration_files=cleanup_migr_files,
            destroy_disks=destroy_disks)

        self._vmops._vmutils.vm_exists.assert_called_with(
            mock_instance.name)

        if vm_exists:
            self._vmops._vmutils.stop_vm_jobs.assert_called_once_with(
                mock_instance.name)
            mock_power_off.assert_called_once_with(mock_instance)
            self._vmops._vmutils.destroy_vm.assert_called_once_with(
                mock_instance.name)
        elif planned_vm_exists:
            self._vmops._migrutils.planned_vm_exists.assert_called_once_with(
                mock_instance.name)
            destroy_planned_vm = (
                self._vmops._migrutils.destroy_existing_planned_vm)
            destroy_planned_vm.assert_called_once_with(
                mock_instance.name)
            self.assertFalse(self._vmops._vmutils.destroy_vm.called)
        else:
            self.assertFalse(
                self._vmops._migrutils.destroy_existing_planned_vm.called)

        mock_unplug_vifs.assert_called_once_with(
            mock_instance, mock.sentinel.fake_network_info)
        self._vmops._volumeops.disconnect_volumes.assert_called_once_with(
            mock.sentinel.FAKE_BD_INFO)

        reverting_resize = task_state == task_states.RESIZE_REVERTING
        exp_migr_files_cleanup = cleanup_migr_files and not reverting_resize
        if destroy_disks or reverting_resize:
            mock_delete_disk_files.assert_called_once_with(
                mock_instance,
                self._pathutils.get_instance_dir.return_value,
                exp_migr_files_cleanup)
        else:
            mock_delete_disk_files.assert_not_called()

    @mock.patch('compute_hyperv.nova.vmops.VMOps.power_off')
    def test_destroy_exception(self, mock_power_off):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops._vmutils.destroy_vm.side_effect = (
            os_win_exc.HyperVException)
        self._vmops._vmutils.vm_exists.return_value = True

        self.assertRaises(os_win_exc.HyperVException,
                          self._vmops.destroy, mock_instance,
                          mock.sentinel.network_info,
                          mock.sentinel.block_device_info)

    def test_reboot_hard(self):
        self._test_reboot(vmops.REBOOT_TYPE_HARD,
                          os_win_const.HYPERV_VM_STATE_REBOOT)

    @mock.patch("compute_hyperv.nova.vmops.VMOps._soft_shutdown")
    def test_reboot_soft(self, mock_soft_shutdown):
        mock_soft_shutdown.return_value = True
        self._test_reboot(vmops.REBOOT_TYPE_SOFT,
                          os_win_const.HYPERV_VM_STATE_ENABLED)

    @mock.patch("compute_hyperv.nova.vmops.VMOps._soft_shutdown")
    def test_reboot_soft_failed(self, mock_soft_shutdown):
        mock_soft_shutdown.return_value = False
        self._test_reboot(vmops.REBOOT_TYPE_SOFT,
                          os_win_const.HYPERV_VM_STATE_REBOOT)

    @mock.patch("compute_hyperv.nova.vmops.VMOps.power_on")
    @mock.patch("compute_hyperv.nova.vmops.VMOps._soft_shutdown")
    def test_reboot_soft_exception(self, mock_soft_shutdown, mock_power_on):
        mock_soft_shutdown.return_value = True
        mock_power_on.side_effect = os_win_exc.HyperVException(
            "Expected failure")
        instance = fake_instance.fake_instance_obj(self.context)

        self.assertRaises(os_win_exc.HyperVException, self._vmops.reboot,
                          instance, {}, vmops.REBOOT_TYPE_SOFT)

        mock_soft_shutdown.assert_called_once_with(instance)
        mock_power_on.assert_called_once_with(instance, network_info={})

    def _test_reboot(self, reboot_type, vm_state):
        instance = fake_instance.fake_instance_obj(self.context)
        with mock.patch.object(self._vmops, '_set_vm_state') as mock_set_state:
            self._vmops.reboot(instance, {}, reboot_type)
            mock_set_state.assert_called_once_with(instance, vm_state)

    @mock.patch("compute_hyperv.nova.vmops.VMOps._wait_for_power_off")
    def test_soft_shutdown(self, mock_wait_for_power_off):
        instance = fake_instance.fake_instance_obj(self.context)
        mock_wait_for_power_off.return_value = True

        result = self._vmops._soft_shutdown(instance, self._FAKE_TIMEOUT)

        mock_shutdown_vm = self._vmops._vmutils.soft_shutdown_vm
        mock_shutdown_vm.assert_called_once_with(instance.name)
        mock_wait_for_power_off.assert_called_once_with(
            instance.name, self._FAKE_TIMEOUT)

        self.assertTrue(result)

    @mock.patch("time.sleep")
    def test_soft_shutdown_failed(self, mock_sleep):
        instance = fake_instance.fake_instance_obj(self.context)

        mock_shutdown_vm = self._vmops._vmutils.soft_shutdown_vm
        mock_shutdown_vm.side_effect = os_win_exc.HyperVException(
            "Expected failure.")

        result = self._vmops._soft_shutdown(instance, self._FAKE_TIMEOUT)

        mock_shutdown_vm.assert_called_once_with(instance.name)
        self.assertFalse(result)

    @mock.patch("compute_hyperv.nova.vmops.VMOps._wait_for_power_off")
    def test_soft_shutdown_wait(self, mock_wait_for_power_off):
        instance = fake_instance.fake_instance_obj(self.context)
        mock_wait_for_power_off.side_effect = [False, True]

        result = self._vmops._soft_shutdown(instance, self._FAKE_TIMEOUT, 1)

        calls = [mock.call(instance.name, 1),
                 mock.call(instance.name, self._FAKE_TIMEOUT - 1)]
        mock_shutdown_vm = self._vmops._vmutils.soft_shutdown_vm
        mock_shutdown_vm.assert_called_with(instance.name)
        mock_wait_for_power_off.assert_has_calls(calls)

        self.assertTrue(result)

    @mock.patch("compute_hyperv.nova.vmops.VMOps._wait_for_power_off")
    def test_soft_shutdown_wait_timeout(self, mock_wait_for_power_off):
        instance = fake_instance.fake_instance_obj(self.context)
        mock_wait_for_power_off.return_value = False

        result = self._vmops._soft_shutdown(instance, self._FAKE_TIMEOUT, 1.5)

        calls = [mock.call(instance.name, 1.5),
                 mock.call(instance.name, self._FAKE_TIMEOUT - 1.5)]
        mock_shutdown_vm = self._vmops._vmutils.soft_shutdown_vm
        mock_shutdown_vm.assert_called_with(instance.name)
        mock_wait_for_power_off.assert_has_calls(calls)

        self.assertFalse(result)

    @mock.patch('compute_hyperv.nova.vmops.VMOps._set_vm_state')
    def test_pause(self, mock_set_vm_state):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops.pause(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance, os_win_const.HYPERV_VM_STATE_PAUSED)

    @mock.patch('compute_hyperv.nova.vmops.VMOps._set_vm_state')
    def test_unpause(self, mock_set_vm_state):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops.unpause(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance, os_win_const.HYPERV_VM_STATE_ENABLED)

    @mock.patch('compute_hyperv.nova.vmops.VMOps._set_vm_state')
    def test_suspend(self, mock_set_vm_state):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops.suspend(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance, os_win_const.HYPERV_VM_STATE_SUSPENDED)

    @mock.patch('compute_hyperv.nova.vmops.VMOps._set_vm_state')
    def test_resume(self, mock_set_vm_state):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops.resume(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance, os_win_const.HYPERV_VM_STATE_ENABLED)

    def _test_power_off(self, timeout, set_state_expected=True):
        instance = fake_instance.fake_instance_obj(self.context)
        with mock.patch.object(self._vmops, '_set_vm_state') as mock_set_state:
            self._vmops.power_off(instance, timeout)

            serialops = self._vmops._serial_console_ops
            serialops.stop_console_handler.assert_called_once_with(
                instance.name)
            if set_state_expected:
                mock_set_state.assert_called_once_with(
                    instance, os_win_const.HYPERV_VM_STATE_DISABLED)

    def test_power_off_hard(self):
        self._test_power_off(timeout=0)

    @mock.patch("compute_hyperv.nova.vmops.VMOps._soft_shutdown")
    def test_power_off_exception(self, mock_soft_shutdown):
        mock_soft_shutdown.return_value = False
        self._test_power_off(timeout=1)

    @mock.patch("compute_hyperv.nova.vmops.VMOps._set_vm_state")
    @mock.patch("compute_hyperv.nova.vmops.VMOps._soft_shutdown")
    def test_power_off_soft(self, mock_soft_shutdown, mock_set_state):
        instance = fake_instance.fake_instance_obj(self.context)
        mock_soft_shutdown.return_value = True

        self._vmops.power_off(instance, 1, 0)

        serialops = self._vmops._serial_console_ops
        serialops.stop_console_handler.assert_called_once_with(
            instance.name)
        mock_soft_shutdown.assert_called_once_with(
            instance, 1, vmops.SHUTDOWN_TIME_INCREMENT)
        self.assertFalse(mock_set_state.called)

    @mock.patch("compute_hyperv.nova.vmops.VMOps._soft_shutdown")
    def test_power_off_unexisting_instance(self, mock_soft_shutdown):
        mock_soft_shutdown.side_effect = os_win_exc.HyperVVMNotFoundException(
            vm_name=mock.sentinel.vm_name)
        self._test_power_off(timeout=1, set_state_expected=False)

    @mock.patch('compute_hyperv.nova.vmops.VMOps._set_vm_state')
    def test_power_on(self, mock_set_vm_state):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        self._vmops.power_on(mock_instance)

        mock_set_vm_state.assert_called_once_with(
            mock_instance, os_win_const.HYPERV_VM_STATE_ENABLED)

    @mock.patch('compute_hyperv.nova.vmops.VMOps._set_vm_state')
    def test_power_on_having_block_devices(self, mock_set_vm_state):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        self._vmops.power_on(mock_instance, mock.sentinel.block_device_info)

        mock_fix_instance_vol_paths = (
            self._vmops._volumeops.fix_instance_volume_disk_paths)
        mock_fix_instance_vol_paths.assert_called_once_with(
            mock_instance.name, mock.sentinel.block_device_info)
        mock_set_vm_state.assert_called_once_with(
            mock_instance, os_win_const.HYPERV_VM_STATE_ENABLED)

    @mock.patch.object(vmops.VMOps, 'plug_vifs')
    def test_power_on_with_network_info(self, mock_plug_vifs):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        self._vmops.power_on(mock_instance,
                             network_info=mock.sentinel.fake_network_info)
        mock_plug_vifs.assert_called_once_with(
            mock_instance, mock.sentinel.fake_network_info)

    @mock.patch.object(vmops.VMOps, 'plug_vifs')
    def test_power_on_vifs_already_plugged(self, mock_plug_vifs):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        self._vmops.power_on(mock_instance,
                             should_plug_vifs=False)
        self.assertFalse(mock_plug_vifs.called)

    def _test_set_vm_state(self, state):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        self._vmops._set_vm_state(mock_instance, state)
        self._vmops._vmutils.set_vm_state.assert_called_once_with(
            mock_instance.name, state)

    def test_set_vm_state_disabled(self):
        self._test_set_vm_state(state=os_win_const.HYPERV_VM_STATE_DISABLED)

    def test_set_vm_state_enabled(self):
        self._test_set_vm_state(state=os_win_const.HYPERV_VM_STATE_ENABLED)

    def test_set_vm_state_reboot(self):
        self._test_set_vm_state(state=os_win_const.HYPERV_VM_STATE_REBOOT)

    def test_set_vm_state_exception(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops._vmutils.set_vm_state.side_effect = (
            os_win_exc.HyperVException)
        self.assertRaises(os_win_exc.HyperVException,
                          self._vmops._set_vm_state,
                          mock_instance, mock.sentinel.STATE)

    def test_get_vm_state(self):
        summary_info = {'EnabledState': os_win_const.HYPERV_VM_STATE_DISABLED}

        with mock.patch.object(self._vmops._vmutils,
                               'get_vm_summary_info') as mock_get_summary_info:
            mock_get_summary_info.return_value = summary_info

            response = self._vmops._get_vm_state(mock.sentinel.FAKE_VM_NAME)
            self.assertEqual(response, os_win_const.HYPERV_VM_STATE_DISABLED)

    @mock.patch.object(vmops.VMOps, '_get_vm_state')
    def test_wait_for_power_off_true(self, mock_get_state):
        mock_get_state.return_value = os_win_const.HYPERV_VM_STATE_DISABLED
        result = self._vmops._wait_for_power_off(
            mock.sentinel.FAKE_VM_NAME, vmops.SHUTDOWN_TIME_INCREMENT)
        mock_get_state.assert_called_with(mock.sentinel.FAKE_VM_NAME)
        self.assertTrue(result)

    @mock.patch.object(vmops.etimeout, "with_timeout")
    def test_wait_for_power_off_false(self, mock_with_timeout):
        mock_with_timeout.side_effect = etimeout.Timeout()
        result = self._vmops._wait_for_power_off(
            mock.sentinel.FAKE_VM_NAME, vmops.SHUTDOWN_TIME_INCREMENT)
        self.assertFalse(result)

    def test_create_vm_com_port_pipes(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_serial_ports = {
            1: constants.SERIAL_PORT_TYPE_RO,
            2: constants.SERIAL_PORT_TYPE_RW
        }

        self._vmops._create_vm_com_port_pipes(mock_instance,
                                              mock_serial_ports)
        expected_calls = []
        for port_number, port_type in mock_serial_ports.items():
            expected_pipe = r'\\.\pipe\%s_%s' % (mock_instance.uuid,
                                                 port_type)
            expected_calls.append(mock.call(mock_instance.name,
                                            port_number,
                                            expected_pipe))

        mock_set_conn = self._vmops._vmutils.set_vm_serial_port_connection
        mock_set_conn.assert_has_calls(expected_calls)

    def test_list_instance_uuids(self):
        fake_uuid = '4f54fb69-d3a2-45b7-bb9b-b6e6b3d893b3'
        with mock.patch.object(self._vmops._vmutils,
                               'list_instance_notes') as mock_list_notes:
            mock_list_notes.return_value = [('fake_name', [fake_uuid])]

            response = self._vmops.list_instance_uuids()
            mock_list_notes.assert_called_once_with()

        self.assertEqual(response, [fake_uuid])

    def test_copy_vm_dvd_disks(self):
        fake_paths = [mock.sentinel.FAKE_DVD_PATH1,
                      mock.sentinel.FAKE_DVD_PATH2]
        mock_copy = self._vmops._pathutils.copyfile
        mock_get_dvd_disk_paths = self._vmops._vmutils.get_vm_dvd_disk_paths
        mock_get_dvd_disk_paths.return_value = fake_paths
        self._vmops._pathutils.get_instance_dir.return_value = (
            mock.sentinel.FAKE_DEST_PATH)

        self._vmops.copy_vm_dvd_disks(mock.sentinel.FAKE_VM_NAME,
                                      mock.sentinel.FAKE_DEST_HOST)

        mock_get_dvd_disk_paths.assert_called_with(mock.sentinel.FAKE_VM_NAME)
        self._vmops._pathutils.get_instance_dir.assert_called_once_with(
            mock.sentinel.FAKE_VM_NAME,
            remote_server=mock.sentinel.FAKE_DEST_HOST)
        mock_copy.has_calls(mock.call(mock.sentinel.FAKE_DVD_PATH1,
                                      mock.sentinel.FAKE_DEST_PATH),
                            mock.call(mock.sentinel.FAKE_DVD_PATH2,
                                      mock.sentinel.FAKE_DEST_PATH))

    def test_plug_vifs(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        fake_vif1 = {'id': mock.sentinel.ID1,
                     'type': mock.sentinel.vif_type1}
        fake_vif2 = {'id': mock.sentinel.ID2,
                     'type': mock.sentinel.vif_type2}
        mock_network_info = [fake_vif1, fake_vif2]
        calls = [mock.call(mock_instance, fake_vif1),
                 mock.call(mock_instance, fake_vif2)]

        self._vmops.plug_vifs(mock_instance,
                              network_info=mock_network_info)
        self._vmops._vif_driver.plug.assert_has_calls(calls)

    def test_plug_vifs_failed(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        fake_vif1 = {'id': mock.sentinel.ID1,
                     'type': mock.sentinel.vif_type1}
        mock_network_info = [fake_vif1]

        self._vmops._vif_driver.plug.side_effect = exception.NovaException

        self.assertRaises(exception.VirtualInterfacePlugException,
                          self._vmops.plug_vifs,
                          mock_instance, mock_network_info)

    def test_unplug_vifs(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        fake_vif1 = {'id': mock.sentinel.ID1,
                     'type': mock.sentinel.vif_type1}
        fake_vif2 = {'id': mock.sentinel.ID2,
                     'type': mock.sentinel.vif_type2}
        mock_network_info = [fake_vif1, fake_vif2]
        calls = [mock.call(mock_instance, fake_vif1),
                 mock.call(mock_instance, fake_vif2)]

        self._vmops.unplug_vifs(mock_instance,
                                network_info=mock_network_info)
        self._vmops._vif_driver.unplug.assert_has_calls(calls)

    @ddt.data({},
              {'metrics_enabled': False},
              {'enable_network_metrics': False})
    @ddt.unpack
    def test_configure_instance_metrics(self, metrics_enabled=True,
                                        enable_network_metrics=True):
        port_names = ['port1', 'port2']

        enable_vm_metrics = self._metricsutils.enable_vm_metrics_collection
        self._vmutils.get_vm_nic_names.return_value = port_names

        self.flags(enable_instance_metrics_collection=metrics_enabled,
                   group='hyperv')

        self._vmops.configure_instance_metrics(
            mock.sentinel.instance_name,
            enable_network_metrics=enable_network_metrics)

        if metrics_enabled:
            enable_vm_metrics.assert_called_once_with(
                mock.sentinel.instance_name)
            if enable_network_metrics:
                self._vmutils.get_vm_nic_names.assert_called_once_with(
                    mock.sentinel.instance_name)
                self._vif_driver.enable_metrics.assert_has_calls(
                    [mock.call(mock.sentinel.instance_name, port_name)
                     for port_name in port_names])
        else:
            enable_vm_metrics.assert_not_called()

        if not (metrics_enabled and enable_network_metrics):
            self._vif_driver.enable_metrics.assert_not_called()

    def _setup_remotefx_mocks(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.flavor.extra_specs = {
            'os:resolution': os_win_const.REMOTEFX_MAX_RES_1920x1200,
            'os:monitors': '2',
            'os:vram': '256'}

        return mock_instance

    def test_configure_remotefx_not_required(self):
        self.flags(enable_remotefx=False, group='hyperv')
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.old_flavor.extra_specs['os:resolution'] = (
            os_win_const.REMOTEFX_MAX_RES_1920x1200)

        self._vmops.configure_remotefx(mock_instance, mock.sentinel.VM_GEN,
                                       True)

        disable_remotefx = self._vmops._vmutils.disable_remotefx_video_adapter
        disable_remotefx.assert_called_once_with(mock_instance.name)

    def test_configure_remotefx_exception_enable_config(self):
        self.flags(enable_remotefx=False, group='hyperv')
        mock_instance = self._setup_remotefx_mocks()

        self.assertRaises(exception.InstanceUnacceptable,
                          self._vmops.configure_remotefx,
                          mock_instance, mock.sentinel.VM_GEN)

    def test_configure_remotefx_exception_server_feature(self):
        self.flags(enable_remotefx=True, group='hyperv')
        mock_instance = self._setup_remotefx_mocks()
        self._vmops._hostutils.check_server_feature.return_value = False

        self.assertRaises(exception.InstanceUnacceptable,
                          self._vmops.configure_remotefx,
                          mock_instance, mock.sentinel.VM_GEN)

    def test_configure_remotefx_exception_vm_gen(self):
        self.flags(enable_remotefx=True, group='hyperv')
        mock_instance = self._setup_remotefx_mocks()
        self._vmops._hostutils.check_server_feature.return_value = True
        self._vmops._vmutils.vm_gen_supports_remotefx.return_value = False

        self.assertRaises(exception.InstanceUnacceptable,
                          self._vmops.configure_remotefx,
                          mock_instance, mock.sentinel.VM_GEN)

    def test_configure_remotefx(self):
        self.flags(enable_remotefx=True, group='hyperv')
        mock_instance = self._setup_remotefx_mocks()
        self._vmops._hostutils.check_server_feature.return_value = True
        self._vmops._vmutils.vm_gen_supports_remotefx.return_value = True
        extra_specs = mock_instance.flavor.extra_specs

        self._vmops.configure_remotefx(mock_instance, constants.VM_GEN_1)
        mock_enable_remotefx = (
            self._vmops._vmutils.enable_remotefx_video_adapter)
        mock_enable_remotefx.assert_called_once_with(
            mock_instance.name, int(extra_specs['os:monitors']),
            extra_specs['os:resolution'],
            int(extra_specs['os:vram']) * units.Mi)

    @mock.patch.object(vmops.VMOps, '_get_vm_state')
    def test_check_hotplug_available_vm_disabled(self, mock_get_vm_state):
        fake_vm = fake_instance.fake_instance_obj(self.context)
        mock_get_vm_state.return_value = os_win_const.HYPERV_VM_STATE_DISABLED

        result = self._vmops._check_hotplug_available(fake_vm)

        self.assertTrue(result)
        mock_get_vm_state.assert_called_once_with(fake_vm.name)
        self.assertFalse(
            self._vmops._hostutils.check_min_windows_version.called)
        self.assertFalse(self._vmops._vmutils.get_vm_generation.called)

    @mock.patch.object(vmops.VMOps, '_get_vm_state')
    def _test_check_hotplug_available(
            self, mock_get_vm_state, expected_result=False,
            vm_gen=constants.VM_GEN_2, windows_version=_WIN_VERSION_10):

        fake_vm = fake_instance.fake_instance_obj(self.context)
        mock_get_vm_state.return_value = os_win_const.HYPERV_VM_STATE_ENABLED
        self._vmops._vmutils.get_vm_generation.return_value = vm_gen
        fake_check_win_vers = self._vmops._hostutils.check_min_windows_version
        fake_check_win_vers.return_value = (
            windows_version == self._WIN_VERSION_10)

        result = self._vmops._check_hotplug_available(fake_vm)

        self.assertEqual(expected_result, result)
        mock_get_vm_state.assert_called_once_with(fake_vm.name)
        fake_check_win_vers.assert_called_once_with(10, 0)

    def test_check_if_hotplug_available(self):
        self._test_check_hotplug_available(expected_result=True)

    def test_check_if_hotplug_available_gen1(self):
        self._test_check_hotplug_available(
            expected_result=False, vm_gen=constants.VM_GEN_1)

    def test_check_if_hotplug_available_win_6_3(self):
        self._test_check_hotplug_available(
            expected_result=False, windows_version=self._WIN_VERSION_6_3)

    @mock.patch.object(vmops.VMOps, 'update_device_metadata')
    @mock.patch.object(vmops.VMOps, '_check_hotplug_available')
    def test_attach_interface(self, mock_check_hotplug_available,
                              mock_update_dev_meta):
        mock_check_hotplug_available.return_value = True
        fake_vm = fake_instance.fake_instance_obj(self.context)
        fake_vif = test_virtual_interface.fake_vif

        self._vmops.attach_interface(
            mock.sentinel.context, fake_vm, fake_vif)

        mock_check_hotplug_available.assert_called_once_with(fake_vm)
        self._vmops._vif_driver.plug.assert_called_once_with(
            fake_vm, fake_vif)
        self._vmops._vmutils.create_nic.assert_called_once_with(
            fake_vm.name, fake_vif['id'], fake_vif['address'])
        mock_update_dev_meta.assert_called_once_with(
            mock.sentinel.context, fake_vm)

    @mock.patch.object(vmops.VMOps, '_check_hotplug_available')
    def test_attach_interface_failed(self, mock_check_hotplug_available):
        mock_check_hotplug_available.return_value = False
        self.assertRaises(exception.InterfaceAttachFailed,
                          self._vmops.attach_interface,
                          mock.sentinel.context,
                          mock.MagicMock(), mock.sentinel.fake_vif)

    @mock.patch.object(vmops.VMOps, '_check_hotplug_available')
    def test_detach_interface(self, mock_check_hotplug_available):
        mock_check_hotplug_available.return_value = True
        fake_vm = fake_instance.fake_instance_obj(self.context)
        fake_vif = test_virtual_interface.fake_vif

        self._vmops.detach_interface(fake_vm, fake_vif)

        mock_check_hotplug_available.assert_called_once_with(fake_vm)
        self._vmops._vif_driver.unplug.assert_called_once_with(
            fake_vm, fake_vif)
        self._vmops._vmutils.destroy_nic.assert_called_once_with(
            fake_vm.name, fake_vif['id'])

    @mock.patch.object(vmops.VMOps, '_check_hotplug_available')
    def test_detach_interface_failed(self, mock_check_hotplug_available):
        mock_check_hotplug_available.return_value = False
        self.assertRaises(exception.InterfaceDetachFailed,
                          self._vmops.detach_interface,
                          mock.MagicMock(), mock.sentinel.fake_vif)

    @mock.patch.object(vmops.VMOps, '_check_hotplug_available')
    def test_detach_interface_missing_instance(self, mock_check_hotplug):
        mock_check_hotplug.side_effect = os_win_exc.HyperVVMNotFoundException(
            vm_name='fake_vm')
        self.assertRaises(exception.InterfaceDetachFailed,
                          self._vmops.detach_interface,
                          mock.MagicMock(), mock.sentinel.fake_vif)

    @mock.patch('nova.virt.configdrive.required_by')
    @mock.patch.object(vmops.VMOps, '_create_root_vhd')
    @mock.patch.object(vmops.VMOps, 'get_image_vm_generation')
    @mock.patch.object(vmops.VMOps, '_attach_drive')
    @mock.patch.object(vmops.VMOps, '_create_config_drive')
    @mock.patch.object(vmops.VMOps, 'attach_config_drive')
    @mock.patch.object(vmops.VMOps, '_detach_config_drive')
    @mock.patch.object(vmops.VMOps, 'power_on')
    def test_rescue_instance(self, mock_power_on,
                             mock_detach_config_drive,
                             mock_attach_config_drive,
                             mock_create_config_drive,
                             mock_attach_drive,
                             mock_get_image_vm_gen,
                             mock_create_root_vhd,
                             mock_configdrive_required):
        mock_image_meta = mock.MagicMock()
        mock_vm_gen = constants.VM_GEN_2
        mock_instance = fake_instance.fake_instance_obj(self.context)

        mock_configdrive_required.return_value = True
        mock_create_root_vhd.return_value = mock.sentinel.rescue_vhd_path
        mock_get_image_vm_gen.return_value = mock_vm_gen
        self._vmops._vmutils.get_vm_generation.return_value = mock_vm_gen
        self._vmops._pathutils.lookup_root_vhd_path.return_value = (
            mock.sentinel.root_vhd_path)
        mock_create_config_drive.return_value = (
            mock.sentinel.rescue_configdrive_path)

        self._vmops.rescue_instance(self.context,
                                    mock_instance,
                                    mock.sentinel.network_info,
                                    mock_image_meta,
                                    mock.sentinel.rescue_password)

        mock_get_image_vm_gen.assert_called_once_with(
            mock_instance.uuid, mock_image_meta)
        self._vmops._vmutils.detach_vm_disk.assert_called_once_with(
            mock_instance.name, mock.sentinel.root_vhd_path,
            is_physical=False)
        mock_attach_drive.assert_called_once_with(
            mock_instance.name, mock.sentinel.rescue_vhd_path, 0,
            self._vmops._ROOT_DISK_CTRL_ADDR,
            vmops.VM_GENERATIONS_CONTROLLER_TYPES[mock_vm_gen])
        self._vmops._vmutils.attach_scsi_drive.assert_called_once_with(
            mock_instance.name, mock.sentinel.root_vhd_path,
            drive_type=constants.DISK)
        mock_detach_config_drive.assert_called_once_with(mock_instance.name)
        mock_create_config_drive.assert_called_once_with(
            self.context, mock_instance,
            injected_files=None,
            admin_password=mock.sentinel.rescue_password,
            network_info=mock.sentinel.network_info,
            rescue=True)
        mock_attach_config_drive.assert_called_once_with(
            mock_instance, mock.sentinel.rescue_configdrive_path,
            mock_vm_gen)

    @mock.patch.object(vmops.VMOps, '_create_root_vhd')
    @mock.patch.object(vmops.VMOps, 'get_image_vm_generation')
    @mock.patch.object(vmops.VMOps, 'unrescue_instance')
    def _test_rescue_instance_exception(self, mock_unrescue,
                                        mock_get_image_vm_gen,
                                        mock_create_root_vhd,
                                        wrong_vm_gen=False,
                                        boot_from_volume=False,
                                        expected_exc=None):
        mock_vm_gen = constants.VM_GEN_1
        image_vm_gen = (mock_vm_gen
                        if not wrong_vm_gen else constants.VM_GEN_2)
        mock_image_meta = mock.MagicMock()

        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_get_image_vm_gen.return_value = image_vm_gen
        self._vmops._vmutils.get_vm_generation.return_value = mock_vm_gen
        self._vmops._pathutils.lookup_root_vhd_path.return_value = (
            mock.sentinel.root_vhd_path if not boot_from_volume else None)

        self.assertRaises(expected_exc,
                          self._vmops.rescue_instance,
                          self.context, mock_instance,
                          mock.sentinel.network_info,
                          mock_image_meta,
                          mock.sentinel.rescue_password)
        mock_unrescue.assert_called_once_with(mock_instance)

    def test_rescue_instance_wrong_vm_gen(self):
        # Test the case when the rescue image requires a different
        # vm generation than the actual rescued instance.
        self._test_rescue_instance_exception(
            wrong_vm_gen=True,
            expected_exc=exception.ImageUnacceptable)

    def test_rescue_instance_boot_from_volume(self):
        # Rescuing instances booted from volume is not supported.
        self._test_rescue_instance_exception(
            boot_from_volume=True,
            expected_exc=exception.InstanceNotRescuable)

    @mock.patch.object(fileutils, 'delete_if_exists')
    @mock.patch.object(vmops.VMOps, '_attach_drive')
    @mock.patch.object(vmops.VMOps, 'attach_config_drive')
    @mock.patch.object(vmops.VMOps, '_detach_config_drive')
    @mock.patch.object(vmops.VMOps, 'power_on')
    @mock.patch.object(vmops.VMOps, 'power_off')
    def test_unrescue_instance(self, mock_power_on, mock_power_off,
                               mock_detach_config_drive,
                               mock_attach_configdrive,
                               mock_attach_drive,
                               mock_delete_if_exists):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_vm_gen = constants.VM_GEN_2

        self._vmops._vmutils.get_vm_generation.return_value = mock_vm_gen
        self._vmops._vmutils.is_disk_attached.return_value = False
        self._vmops._pathutils.lookup_root_vhd_path.side_effect = (
            mock.sentinel.root_vhd_path, mock.sentinel.rescue_vhd_path)
        self._vmops._pathutils.lookup_configdrive_path.return_value = (
            mock.sentinel.configdrive_path)

        self._vmops.unrescue_instance(mock_instance)

        self._vmops._pathutils.lookup_root_vhd_path.assert_has_calls(
            [mock.call(mock_instance.name),
             mock.call(mock_instance.name, rescue=True)])
        self._vmops._vmutils.detach_vm_disk.assert_has_calls(
            [mock.call(mock_instance.name,
                       mock.sentinel.root_vhd_path,
                       is_physical=False),
             mock.call(mock_instance.name,
                       mock.sentinel.rescue_vhd_path,
                       is_physical=False)])
        mock_attach_drive.assert_called_once_with(
            mock_instance.name, mock.sentinel.root_vhd_path, 0,
            self._vmops._ROOT_DISK_CTRL_ADDR,
            vmops.VM_GENERATIONS_CONTROLLER_TYPES[mock_vm_gen])
        mock_detach_config_drive.assert_called_once_with(mock_instance.name,
                                                         rescue=True,
                                                         delete=True)
        mock_delete_if_exists.assert_called_once_with(
            mock.sentinel.rescue_vhd_path)
        self._vmops._vmutils.is_disk_attached.assert_called_once_with(
            mock.sentinel.configdrive_path,
            is_physical=False)
        mock_attach_configdrive.assert_called_once_with(
            mock_instance, mock.sentinel.configdrive_path, mock_vm_gen)
        mock_power_on.assert_called_once_with(mock_instance)

    @mock.patch.object(vmops.VMOps, 'power_off')
    def test_unrescue_instance_missing_root_image(self, mock_power_off):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.vm_state = vm_states.RESCUED
        self._vmops._pathutils.lookup_root_vhd_path.return_value = None

        self.assertRaises(exception.InstanceNotRescuable,
                          self._vmops.unrescue_instance,
                          mock_instance)

    @ddt.data((1, True),
              (0, True),
              (0, False))
    @ddt.unpack
    @mock.patch.object(vmops.VMOps, '_get_scoped_flavor_extra_specs')
    @mock.patch.object(vmops.VMOps, '_get_instance_local_disks')
    def test_set_instance_disk_qos_specs(self, total_iops_sec, is_resize,
                                         mock_get_local_disks,
                                         mock_get_scoped_specs):
        fake_total_bytes_sec = 8
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_local_disks = [mock.sentinel.root_vhd_path,
                            mock.sentinel.eph_vhd_path]

        mock_get_local_disks.return_value = mock_local_disks
        mock_set_qos_specs = self._vmops._vmutils.set_disk_qos_specs
        mock_get_scoped_specs.return_value = dict(
            disk_total_bytes_sec=fake_total_bytes_sec)
        mock_bytes_per_sec_to_iops = (
            self._vmops._volumeops.bytes_per_sec_to_iops)
        mock_bytes_per_sec_to_iops.return_value = total_iops_sec

        self._vmops._set_instance_disk_qos_specs(mock_instance, is_resize)

        mock_bytes_per_sec_to_iops.assert_called_once_with(
            fake_total_bytes_sec)

        if total_iops_sec or is_resize:
            mock_get_local_disks.assert_called_once_with(mock_instance.name)
            expected_calls = [mock.call(disk_path, total_iops_sec)
                              for disk_path in mock_local_disks]
            mock_set_qos_specs.assert_has_calls(expected_calls)
        else:
            self.assertFalse(mock_get_local_disks.called)
            self.assertFalse(mock_set_qos_specs.called)

    def test_get_instance_local_disks(self):
        fake_instance_dir = 'fake_instance_dir'
        fake_local_disks = [os.path.join(fake_instance_dir, disk_name)
                            for disk_name in ['root.vhd', 'configdrive.iso']]
        fake_instance_disks = ['fake_remote_disk'] + fake_local_disks

        mock_get_storage_paths = self._vmops._vmutils.get_vm_storage_paths
        mock_get_storage_paths.return_value = [fake_instance_disks, []]
        mock_get_instance_dir = self._vmops._pathutils.get_instance_dir
        mock_get_instance_dir.return_value = fake_instance_dir

        ret_val = self._vmops._get_instance_local_disks(
            mock.sentinel.instance_name)

        self.assertEqual(fake_local_disks, ret_val)

    def test_get_scoped_flavor_extra_specs(self):
        # The flavor extra spect dict contains only string values.
        fake_total_bytes_sec = '8'

        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.flavor.extra_specs = {
            'spec_key': 'spec_value',
            'quota:total_bytes_sec': fake_total_bytes_sec}

        ret_val = self._vmops._get_scoped_flavor_extra_specs(
            mock_instance, scope='quota')

        expected_specs = {
            'total_bytes_sec': fake_total_bytes_sec
        }
        self.assertEqual(expected_specs, ret_val)

    def _mock_get_port_settings(self, logging_port, interactive_port):
        mock_image_port_settings = {
            constants.IMAGE_PROP_LOGGING_SERIAL_PORT: logging_port,
            constants.IMAGE_PROP_INTERACTIVE_SERIAL_PORT: interactive_port
        }
        mock_image_meta = {'properties': mock_image_port_settings}

        acceptable_ports = [1, 2]
        expected_exception = not (logging_port in acceptable_ports and
                                  interactive_port in acceptable_ports)
        if expected_exception:
            self.assertRaises(exception.ImageSerialPortNumberInvalid,
                              self._vmops._get_image_serial_port_settings,
                              mock_image_meta)
        else:
            return self._vmops._get_image_serial_port_settings(
                mock_image_meta)

    def test_get_image_serial_port_settings(self):
        logging_port = 1
        interactive_port = 2

        ret_val = self._mock_get_port_settings(logging_port, interactive_port)

        expected_serial_ports = {
            logging_port: constants.SERIAL_PORT_TYPE_RO,
            interactive_port: constants.SERIAL_PORT_TYPE_RW,
        }

        self.assertEqual(expected_serial_ports, ret_val)

    def test_get_image_serial_port_settings_exception(self):
        self._mock_get_port_settings(1, 3)

    def test_get_image_serial_port_settings_single_port(self):
        interactive_port = 1

        ret_val = self._mock_get_port_settings(interactive_port,
                                               interactive_port)

        expected_serial_ports = {
            interactive_port: constants.SERIAL_PORT_TYPE_RW
        }
        self.assertEqual(expected_serial_ports, ret_val)

    @mock.patch.object(vmops.VMOps, '_check_vtpm_requirements')
    @mock.patch.object(vmops.VMOps, '_feature_requested')
    @mock.patch.object(vmops.VMOps, '_create_fsk')
    def _test_configure_secure_vm(self, mock_create_fsk,
                                  mock_feature_requested,
                                  mock_check_vtpm_requirements,
                                  requires_shielded, requires_encryption):
        instance = mock.MagicMock()
        mock_tmp_file = self._vmops._pathutils.temporary_file
        mock_tmp_file.return_value.__enter__.side_effect = [
            self._FAKE_FSK_FILE_PATH, self._FAKE_PDK_FILE_PATH]
        mock_feature_requested.side_effect = [requires_shielded,
                                              requires_encryption]

        self._vmops._configure_secure_vm(mock.sentinel.context, instance,
                                         mock.sentinel.image_meta,
                                         mock.sentinel.secure_boot_enabled)

        expected_calls = [mock.call(instance,
                                    mock.sentinel.image_meta,
                                    constants.IMAGE_PROP_VTPM_SHIELDED)]
        if not requires_shielded:
            expected_calls.append(mock.call(instance,
                                            mock.sentinel.image_meta,
                                            constants.IMAGE_PROP_VTPM))
        mock_feature_requested.has_calls(expected_calls)

        mock_check_vtpm_requirements.assert_called_with(instance,
            mock.sentinel.image_meta, mock.sentinel.secure_boot_enabled)
        self._vmops._vmutils.add_vtpm.assert_called_once_with(
            instance.name, self._FAKE_PDK_FILE_PATH,
            shielded=requires_shielded)
        self._vmops._vmutils.provision_vm.assert_called_once_with(
            instance.name, self._FAKE_FSK_FILE_PATH, self._FAKE_PDK_FILE_PATH)

    def test_configure_secure_vm_shielded(self):
        self._test_configure_secure_vm(requires_shielded=True,
                                       requires_encryption=True)

    def test_configure_secure_vm_encryption(self):
        self._test_configure_secure_vm(requires_shielded=False,
                                       requires_encryption=True)

    @mock.patch.object(vmops.VMOps, '_check_vtpm_requirements')
    @mock.patch.object(vmops.VMOps, '_feature_requested')
    def test_configure_regular_vm(self, mock_feature_requested,
                                  mock_check_vtpm_requirements):
        mock_feature_requested.side_effect = [False, False]

        self._vmops._configure_secure_vm(mock.sentinel.context,
                                         mock.MagicMock(),
                                         mock.sentinel.image_meta,
                                         mock.sentinel.secure_boot_enabled)

        self.assertFalse(mock_check_vtpm_requirements.called)

    def _test_feature_requested(self, image_prop, image_prop_required):
        mock_instance = mock.MagicMock()
        mock_image_meta = {'properties': {image_prop: image_prop_required}}

        feature_requested = image_prop_required == constants.REQUIRED

        result = self._vmops._feature_requested(mock_instance,
                                                mock_image_meta,
                                                image_prop)
        self.assertEqual(feature_requested, result)

    def test_vtpm_image_required(self):
        self._test_feature_requested(
            image_prop=constants.IMAGE_PROP_VTPM_SHIELDED,
            image_prop_required=constants.REQUIRED)

    def test_vtpm_image_disabled(self):
        self._test_feature_requested(
            image_prop=constants.IMAGE_PROP_VTPM_SHIELDED,
            image_prop_required=constants.DISABLED)

    def _test_check_vtpm_requirements(self, os_type='windows',
                                      secure_boot_enabled=True,
                                      guarded_host=True):
        mock_instance = mock.MagicMock()
        mock_image_meta = {'properties': {'os_type': os_type}}
        guarded_host = self._vmops._hostutils.is_host_guarded.return_value

        if (not secure_boot_enabled or not guarded_host or
                os_type not in os_win_const.VTPM_SUPPORTED_OS):
            self.assertRaises(exception.InstanceUnacceptable,
                              self._vmops._check_vtpm_requirements,
                              mock_instance,
                              mock_image_meta,
                              secure_boot_enabled)
        else:
            self._vmops._check_vtpm_requirements(mock_instance,
                                                 mock_image_meta,
                                                 secure_boot_enabled)

    def test_vtpm_requirements_all_satisfied(self):
        self._test_check_vtpm_requirements()

    def test_vtpm_requirement_no_secureboot(self):
        self._test_check_vtpm_requirements(secure_boot_enabled=False)

    def test_vtpm_requirement_not_supported_os(self):
        self._test_check_vtpm_requirements(
            os_type=mock.sentinel.unsupported_os)

    def test_vtpm_requirement_host_not_guarded(self):
        self._test_check_vtpm_requirements(guarded_host=False)

    @mock.patch.object(vmops.VMOps, '_get_fsk_data')
    def test_create_fsk(self, mock_get_fsk_data):
        mock_instance = mock.MagicMock()
        fsk_pairs = mock_get_fsk_data.return_value

        self._vmops._create_fsk(mock_instance, mock.sentinel.fsk_filename)
        mock_get_fsk_data.assert_called_once_with(mock_instance)
        self._vmops._vmutils.populate_fsk.assert_called_once_with(
            mock.sentinel.fsk_filename, fsk_pairs)

    def _test_get_fsk_data(self, metadata, instance_name,
                           expected_fsk_pairs=None):
        mock_instance = mock.MagicMock()
        mock_instance.metadata = metadata
        mock_instance.hostname = instance_name

        result = self._vmops._get_fsk_data(mock_instance)
        self.assertEqual(expected_fsk_pairs, result)

    def test_get_fsk_data_no_computername(self):
        metadata = {'TimeZone': mock.sentinel.timezone}
        expected_fsk_pairs = {'@@ComputerName@@': mock.sentinel.instance_name}
        self._test_get_fsk_data(metadata,
                                mock.sentinel.instance_name,
                                expected_fsk_pairs)

    def test_get_fsk_data_with_computername(self):
        metadata = {'fsk:ComputerName': mock.sentinel.instance_name,
                    'fsk:TimeZone': mock.sentinel.timezone}
        expected_fsk_pairs = {'@@ComputerName@@': mock.sentinel.instance_name,
                              '@@TimeZone@@': mock.sentinel.timezone}
        self._test_get_fsk_data(metadata,
                                mock.sentinel.instance_name,
                                expected_fsk_pairs)

    def test_get_fsk_data_computername_exception(self):
        mock_instance = mock.MagicMock()
        mock_instance.metadata = {
            'fsk:ComputerName': mock.sentinel.computer_name,
            'fsk:TimeZone': mock.sentinel.timezone}
        mock_instance.hostname = mock.sentinel.instance_name

        self.assertRaises(exception.InstanceUnacceptable,
                          self._vmops._get_fsk_data,
                          mock_instance)

    @ddt.data({'vm_state': os_win_const.HYPERV_VM_STATE_DISABLED},
              {'vm_state': os_win_const.HYPERV_VM_STATE_SUSPENDED},
              {'vm_state': os_win_const.HYPERV_VM_STATE_SUSPENDED,
               'allow_paused': True},
              {'vm_state': os_win_const.HYPERV_VM_STATE_PAUSED},
              {'vm_state': os_win_const.HYPERV_VM_STATE_PAUSED,
               'allow_paused': True},
              {'allow_paused': True})
    @ddt.unpack
    @mock.patch.object(vmops.VMOps, 'pause')
    @mock.patch.object(vmops.VMOps, 'suspend')
    @mock.patch.object(vmops.VMOps, '_set_vm_state')
    def test_prepare_for_volume_snapshot(
            self, mock_set_state, mock_suspend, mock_pause,
            vm_state=os_win_const.HYPERV_VM_STATE_ENABLED,
            allow_paused=False):
        self._vmops._vmutils.get_vm_state.return_value = vm_state

        expect_instance_suspend = not allow_paused and vm_state not in [
            os_win_const.HYPERV_VM_STATE_DISABLED,
            os_win_const.HYPERV_VM_STATE_SUSPENDED]
        expect_instance_pause = allow_paused and vm_state == (
            os_win_const.HYPERV_VM_STATE_ENABLED)

        with self._vmops.prepare_for_volume_snapshot(
                mock.sentinel.instance, allow_paused):
            self._vmutils.get_vm_state.assert_called_once_with(
                mock.sentinel.instance.name)

            if expect_instance_suspend:
                mock_suspend.assert_called_once_with(mock.sentinel.instance)
            else:
                mock_suspend.assert_not_called()

            if expect_instance_pause:
                mock_pause.assert_called_once_with(mock.sentinel.instance)
            else:
                mock_pause.assert_not_called()

        # We expect the previous instance state to be restored.
        if expect_instance_suspend or expect_instance_pause:
            mock_set_state.assert_called_once_with(mock.sentinel.instance,
                                                   vm_state)
        else:
            mock_set_state.assert_not_called()

    @ddt.data({},
              {'instance_found': False},
              {'uuid_found': True})
    def test_get_instance_uuid(self, instance_found=True, uuid_found=True):
        if instance_found:
            side_effect = (mock.sentinel.instance_uuid
                           if uuid_found else None, )
        else:
            side_effect = os_win_exc.HyperVVMNotFoundException(
                vm_name=mock.sentinel.instance_name)

        self._vmutils.get_instance_uuid.side_effect = side_effect

        instance_uuid = self._vmops.get_instance_uuid(
            mock.sentinel.instance_name)

        self._vmutils.get_instance_uuid.assert_called_once_with(
            mock.sentinel.instance_name)
        expected_uuid = (mock.sentinel.instance_uuid
                         if instance_found and uuid_found else None)
        self.assertEqual(expected_uuid, instance_uuid)

    def test_get_instance_uuid_missing_but_expected(self):
        self._vmutils.get_instance_uuid.side_effect = (
            os_win_exc.HyperVVMNotFoundException(
                vm_name=mock.sentinel.instance_name))

        self.assertRaises(os_win_exc.HyperVVMNotFoundException,
                          self._vmops.get_instance_uuid,
                          mock.sentinel.instance_name,
                          expect_existing=True)

    @ddt.data(virtevent.EVENT_LIFECYCLE_STARTED,
              virtevent.EVENT_LIFECYCLE_STOPPED)
    @mock.patch.object(vmops.VMOps, 'configure_instance_metrics')
    @mock.patch.object(utils, 'spawn_n',
                       lambda f, *args, **kwargs: f(*args, **kwargs))
    def test_instance_state_change_callback(self, transition,
                                            mock_configure_metrics):
        event = eventhandler.HyperVLifecycleEvent(
            mock.sentinel.uuid,
            mock.sentinel.name,
            transition)

        self._vmops.instance_state_change_callback(event)

        serialops = self._vmops._serial_console_ops
        if transition == virtevent.EVENT_LIFECYCLE_STARTED:
            serialops.start_console_handler.assert_called_once_with(event.name)
            mock_configure_metrics.assert_called_once_with(
                event.name, enable_network_metrics=True)
        else:
            serialops.stop_console_handler.assert_called_once_with(event.name)
