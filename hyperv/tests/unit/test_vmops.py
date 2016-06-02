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

from eventlet import timeout as etimeout
import mock
from nova.compute import vm_states
from nova import exception
from nova.objects import flavor as flavor_obj
from nova.tests.unit.objects import test_flavor
from nova.tests.unit.objects import test_virtual_interface
from nova.virt import hardware
from oslo_concurrency import processutils
from oslo_config import cfg
from oslo_utils import fileutils
from oslo_utils import units
import six

from hyperv.nova import block_device_manager
from hyperv.nova import constants
from hyperv.nova import vmops
from hyperv.nova import vmutils
from hyperv.nova import volumeops
from hyperv.tests import fake_instance
from hyperv.tests.unit import test_base

CONF = cfg.CONF


class VMOpsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V VMOps class."""

    _FAKE_TIMEOUT = 2
    FAKE_SIZE = 10
    FAKE_DIR = 'fake_dir'
    FAKE_ROOT_PATH = 'C:\\path\\to\\fake.%s'
    FAKE_CONFIG_DRIVE_ISO = 'configdrive.iso'
    FAKE_CONFIG_DRIVE_VHD = 'configdrive.vhd'
    FAKE_UUID = '4f54fb69-d3a2-45b7-bb9b-b6e6b3d893b3'
    FAKE_LOG = 'fake_log'
    _WIN_VERSION_6_3 = '6.3.0'
    _WIN_VERSION_6_4 = '6.4.0'

    ISO9660 = 'iso9660'
    _FAKE_CONFIGDRIVE_PATH = 'C:/fake_instance_dir/configdrive.vhd'

    def setUp(self):
        super(VMOpsTestCase, self).setUp()
        self.context = 'fake-context'

        self._vmops = vmops.VMOps()
        self._vmops._vmutils = mock.MagicMock()
        self._vmops._vhdutils = mock.MagicMock()
        self._vmops._pathutils = mock.MagicMock()
        self._vmops._hostutils = mock.MagicMock()
        self._vmops._serial_console_ops = mock.MagicMock()

    def test_get_vif_driver_cached(self):
        self._vmops._vif_driver_cache = mock.MagicMock()
        self._vmops._vif_driver_cache.get.return_value = mock.sentinel.VIF_DRV

        self._vmops._get_vif_driver(mock.sentinel.VIF_TYPE)
        self._vmops._vif_driver_cache.get.assert_called_with(
            mock.sentinel.VIF_TYPE)

    @mock.patch('hyperv.nova.vif.get_vif_driver')
    def test_get_vif_driver_not_cached(self, mock_get_vif_driver):
        mock_get_vif_driver.return_value = mock.sentinel.VIF_DRV

        self._vmops._get_vif_driver(mock.sentinel.VIF_TYPE)
        mock_get_vif_driver.assert_called_once_with(mock.sentinel.VIF_TYPE)
        self.assertEqual(mock.sentinel.VIF_DRV,
                self._vmops._vif_driver_cache[mock.sentinel.VIF_TYPE])

    def test_list_instances(self):
        mock_instance = mock.MagicMock()
        self._vmops._vmutils.list_instances.return_value = [mock_instance]
        response = self._vmops.list_instances()
        self._vmops._vmutils.list_instances.assert_called_once_with()
        self.assertEqual(response, [mock_instance])

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

        expected = hardware.InstanceInfo(state=constants.HYPERV_POWER_STATE[2],
                                         max_mem_kb=mock.sentinel.FAKE_MEM_KB,
                                         mem_kb=mock.sentinel.FAKE_MEM_KB,
                                         num_cpu=mock.sentinel.FAKE_NUM_CPU,
                                         cpu_time_ns=mock.sentinel.FAKE_CPU_NS)

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

    def _prepare_create_root_device_mocks(self, use_cow_images, vhd_format,
                                       vhd_size):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.root_gb = self.FAKE_SIZE
        self.flags(use_cow_images=use_cow_images)
        self._vmops._vhdutils.get_vhd_info.return_value = {'MaxInternalSize':
                                                           vhd_size * units.Gi}
        self._vmops._vhdutils.get_vhd_format.return_value = vhd_format
        root_vhd_internal_size = mock_instance.root_gb * units.Gi
        get_size = self._vmops._vhdutils.get_internal_vhd_size_by_file_size
        get_size.return_value = root_vhd_internal_size
        self._vmops._pathutils.exists.return_value = True

        return mock_instance

    @mock.patch('hyperv.nova.imagecache.ImageCache.get_cached_image')
    def _test_create_root_vhd_exception(self, mock_get_cached_image,
                                           vhd_format):
        mock_instance = self._prepare_create_root_device_mocks(
            use_cow_images=False, vhd_format=vhd_format,
            vhd_size=(self.FAKE_SIZE + 1))
        fake_vhd_path = self.FAKE_ROOT_PATH % vhd_format
        mock_get_cached_image.return_value = fake_vhd_path
        fake_root_path = self._vmops._pathutils.get_root_vhd_path.return_value

        self.assertRaises(vmutils.VHDResizeException,
                          self._vmops._create_root_vhd, self.context,
                          mock_instance)

        self.assertFalse(self._vmops._vhdutils.resize_vhd.called)
        self._vmops._pathutils.exists.assert_called_once_with(
            fake_root_path)
        self._vmops._pathutils.remove.assert_called_once_with(
            fake_root_path)

    @mock.patch('hyperv.nova.imagecache.ImageCache.get_cached_image')
    def _test_create_root_vhd_qcow(self, mock_get_cached_image, vhd_format):
        mock_instance = self._prepare_create_root_device_mocks(
            use_cow_images=True, vhd_format=vhd_format,
            vhd_size=(self.FAKE_SIZE - 1))
        fake_vhd_path = self.FAKE_ROOT_PATH % vhd_format
        mock_get_cached_image.return_value = fake_vhd_path

        fake_root_path = self._vmops._pathutils.get_root_vhd_path.return_value
        root_vhd_internal_size = mock_instance.root_gb * units.Gi
        get_size = self._vmops._vhdutils.get_internal_vhd_size_by_file_size

        response = self._vmops._create_root_vhd(context=self.context,
                                                instance=mock_instance)

        self.assertEqual(fake_root_path, response)
        self._vmops._pathutils.get_root_vhd_path.assert_called_with(
            mock_instance.name, vhd_format, False)
        differencing_vhd = self._vmops._vhdutils.create_differencing_vhd
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
                fake_root_path, root_vhd_internal_size, is_file_max_size=False)

    @mock.patch('hyperv.nova.imagecache.ImageCache.get_cached_image')
    def _test_create_root_vhd(self, mock_get_cached_image, vhd_format,
                              is_rescue_vhd=False):
        mock_instance = self._prepare_create_root_device_mocks(
            use_cow_images=False, vhd_format=vhd_format,
            vhd_size=(self.FAKE_SIZE - 1))
        fake_vhd_path = self.FAKE_ROOT_PATH % vhd_format
        mock_get_cached_image.return_value = fake_vhd_path
        rescue_image_id = (
            mock.sentinel.rescue_image_id if is_rescue_vhd else None)

        fake_root_path = self._vmops._pathutils.get_root_vhd_path.return_value
        root_vhd_internal_size = mock_instance.root_gb * units.Gi
        get_size = self._vmops._vhdutils.get_internal_vhd_size_by_file_size

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

        self._vmops._pathutils.copyfile.assert_called_once_with(
            fake_vhd_path, fake_root_path)
        get_size.assert_called_once_with(fake_vhd_path, root_vhd_internal_size)
        if is_rescue_vhd:
            self.assertFalse(self._vmops._vhdutils.resize_vhd.called)
        else:
            self._vmops._vhdutils.resize_vhd.assert_called_once_with(
                fake_root_path, root_vhd_internal_size,
                is_file_max_size=False)

    def test_create_root_vhd(self):
        self._test_create_root_vhd(vhd_format=constants.DISK_FORMAT_VHD)

    def test_create_root_vhd_ex(self):
        self._test_create_root_vhd(vhd_format=constants.DISK_FORMAT_VHDX)

    def test_create_root_vhd_use_cow_images_true(self):
        self._test_create_root_vhd_qcow(vhd_format=constants.DISK_FORMAT_VHD)

    def test_create_root_vhd_ex_use_cow_images_true(self):
        self._test_create_root_vhd_qcow(vhd_format=constants.DISK_FORMAT_VHDX)

    def test_create_rescue_vhd(self):
        self._test_create_root_vhd(vhd_format=constants.DISK_FORMAT_VHD,
                                   is_rescue_vhd=True)

    def test_create_root_vhd_ex_size_less_than_internal(self):
        self._test_create_root_vhd_exception(
            vhd_format=constants.DISK_FORMAT_VHD)

    def test_is_resize_needed_exception(self):
        inst = mock.MagicMock()
        self.assertRaises(
            vmutils.VHDResizeException, self._vmops._is_resize_needed,
            mock.sentinel.FAKE_PATH, self.FAKE_SIZE, self.FAKE_SIZE - 1, inst)

    def test_is_resize_needed_true(self):
        inst = mock.MagicMock()
        self.assertTrue(self._vmops._is_resize_needed(
            mock.sentinel.FAKE_PATH, self.FAKE_SIZE, self.FAKE_SIZE + 1, inst))

    def test_is_resize_needed_false(self):
        inst = mock.MagicMock()
        self.assertFalse(self._vmops._is_resize_needed(
            mock.sentinel.FAKE_PATH, self.FAKE_SIZE, self.FAKE_SIZE, inst))

    @mock.patch.object(vmops.VMOps, 'check_vm_image_type')
    @mock.patch.object(vmops.VMOps, '_create_root_vhd',
                       return_value=mock.sentinel.VHD_PATH)
    def test_create_root_device_type_disk(self, mock_create_root_device,
                                          mock_check_vm_image_type):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_root_disk_info = {'type': constants.DISK}

        self._vmops._create_root_device(self.context, mock_instance,
                                        mock_root_disk_info,
                                        mock.sentinel.VM_GEN_1)

        mock_create_root_device.assert_called_once_with(self.context,
            mock_instance)
        mock_check_vm_image_type.assert_called_once_with(
            mock.sentinel.VM_GEN_1, mock.sentinel.VHD_PATH)

    @mock.patch.object(vmops.VMOps, '_create_root_iso')
    def test_create_root_device_type_iso(self, mock_create_root_iso):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_root_disk_info = {'type': constants.DVD}

        self._vmops._create_root_device(self.context, mock_instance,
                                        mock_root_disk_info,
                                        mock.sentinel.VM_GEN_1)

        mock_create_root_iso.assert_called_once_with(self.context,
                                                     mock_instance)

    @mock.patch.object(vmops.imagecache.ImageCache, 'get_cached_image')
    def test_create_root_iso(self, mock_get_cached_image):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        mock_get_root_vhd_path = self._vmops._pathutils.get_root_vhd_path
        mock_get_root_vhd_path.return_value = mock.sentinel.ROOT_ISO_PATH
        mock_get_cached_image.return_value = mock.sentinel.CACHED_ISO_PATH

        self._vmops._create_root_iso(self.context, mock_instance)

        mock_get_cached_image.assert_called_once_with(self.context,
                                                      mock_instance)
        mock_get_root_vhd_path.assert_called_once_with(mock_instance.name,
                                                       'iso')
        self._vmops._pathutils.copyfile.assert_called_once_with(
            mock.sentinel.CACHED_ISO_PATH, mock.sentinel.ROOT_ISO_PATH)

    @mock.patch.object(vmops.VMOps, '_create_ephemeral_disk')
    def test_create_ephemerals(self, mock_create_ephemeral_disk):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        fake_ephemerals = [dict(), dict()]
        self._vmops._vhdutils.get_best_supported_vhd_format.return_value = (
            mock.sentinel.EPH_FORMAT)
        self._vmops._pathutils.get_ephemeral_vhd_path.side_effect = [
            mock.sentinel.FAKE_PATH0, mock.sentinel.FAKE_PATH1]

        self._vmops._create_ephemerals(mock_instance, fake_ephemerals)

        self._vmops._pathutils.get_ephemeral_vhd_path.assert_has_calls(
            [mock.call(mock_instance.name, mock.sentinel.EPH_FORMAT, 'eph0'),
             mock.call(mock_instance.name, mock.sentinel.EPH_FORMAT, 'eph1')])
        mock_create_ephemeral_disk.assert_has_calls(
            [mock.call(mock_instance.name, fake_ephemerals[0]),
             mock.call(mock_instance.name, fake_ephemerals[1])])

    def test_create_ephemeral_disk(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        mock_ephemeral_info = {'path': 'fake_eph_path',
                               'format': 'vhd',
                               'size': 10}

        mock_create_dynamic_vhd = self._vmops._vhdutils.create_dynamic_vhd

        self._vmops._create_ephemeral_disk(mock_instance.name,
                                           mock_ephemeral_info)

        mock_create_dynamic_vhd.assert_called_once_with('fake_eph_path',
                                                        10 * units.Gi, 'vhd')

    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       'get_boot_order')
    def test_set_boot_order(self, mock_bdm_get_boot_order):
        mock_bdm_get_boot_order.return_value = mock.sentinel.FAKE_BOOT_ORDER

        self._vmops.set_boot_order(mock.sentinel.FAKE_VM_GEN,
                                   mock.sentinel.FAKE_BDI,
                                   mock.sentinel.FAKE_INSTANCE_NAME)

        mock_bdm_get_boot_order.assert_called_once_with(
            mock.sentinel.FAKE_VM_GEN, mock.sentinel.FAKE_BDI)
        self._vmops._vmutils.set_boot_order.assert_called_once_with(
            mock.sentinel.FAKE_INSTANCE_NAME, mock.sentinel.FAKE_BOOT_ORDER)

    @mock.patch('hyperv.nova.vmops.VMOps.destroy')
    @mock.patch('hyperv.nova.vmops.VMOps.power_on')
    @mock.patch('hyperv.nova.vmops.VMOps.attach_config_drive')
    @mock.patch('hyperv.nova.vmops.VMOps._create_config_drive')
    @mock.patch('nova.virt.configdrive.required_by')
    @mock.patch('hyperv.nova.vmops.VMOps.create_instance')
    @mock.patch('hyperv.nova.vmops.VMOps.get_image_vm_generation')
    @mock.patch('hyperv.nova.vmops.VMOps._create_ephemerals')
    @mock.patch('hyperv.nova.vmops.VMOps._create_root_device')
    @mock.patch('hyperv.nova.volumeops.VolumeOps.'
                'ebs_root_in_block_devices')
    @mock.patch('hyperv.nova.vmops.VMOps._delete_disk_files')
    @mock.patch('hyperv.nova.vif.get_vif_driver')
    @mock.patch.object(block_device_manager.BlockDeviceInfoManager,
                       'validate_and_update_bdi')
    @mock.patch.object(vmops.VMOps, 'set_boot_order')
    def _test_spawn(self, mock_set_boot_order, mock_validate_and_update_bdi,
                    mock_get_vif_driver, mock_delete_disk_files,
                    mock_ebs_root_in_block_devices, mock_create_root_device,
                    mock_create_ephemerals, mock_get_image_vm_gen,
                    mock_create_instance, mock_configdrive_required,
                    mock_create_config_drive, mock_attach_config_drive,
                    mock_power_on, mock_destroy, exists, root_device_info,
                    block_device_info, configdrive_required, fail,
                    fake_vm_gen=constants.VM_GEN_2):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_image_meta = mock.MagicMock()
        root_device_info = mock.sentinel.ROOT_DEV_INFO

        mock_get_image_vm_gen.return_value = fake_vm_gen
        fake_config_drive_path = mock_create_config_drive.return_value
        fake_network_info = {'id': mock.sentinel.ID,
                             'address': mock.sentinel.ADDRESS}

        self._vmops._vmutils.vm_exists.return_value = exists
        mock_configdrive_required.return_value = configdrive_required
        mock_create_instance.side_effect = fail
        if exists:
            self.assertRaises(exception.InstanceExists, self._vmops.spawn,
                              self.context, mock_instance, mock_image_meta,
                              [mock.sentinel.FILE], mock.sentinel.PASSWORD,
                              mock.sentinel.INFO, block_device_info)
        elif fail is vmutils.HyperVException:
            self.assertRaises(vmutils.HyperVException, self._vmops.spawn,
                              self.context, mock_instance, mock_image_meta,
                              [mock.sentinel.FILE], mock.sentinel.PASSWORD,
                              mock.sentinel.INFO, block_device_info)
            mock_destroy.assert_called_once_with(mock_instance)
        else:
            self._vmops.spawn(self.context, mock_instance, mock_image_meta,
                              [mock.sentinel.FILE], mock.sentinel.PASSWORD,
                              [fake_network_info], block_device_info)
            self._vmops._vmutils.vm_exists.assert_called_once_with(
                mock_instance.name)
            mock_delete_disk_files.assert_called_once_with(
                mock_instance.name)
            mock_validate_and_update_bdi.assert_called_once_with(mock_instance,
                mock_image_meta, fake_vm_gen, block_device_info)
            mock_create_root_device.assert_called_once_with(self.context,
                                                            mock_instance,
                                                            root_device_info,
                                                            fake_vm_gen)
            mock_create_ephemerals.assert_called_once_with(mock_instance,
                block_device_info['ephemerals'])
            mock_get_image_vm_gen.assert_called_once_with(mock_image_meta)
            mock_create_instance.assert_called_once_with(
                mock_instance, [fake_network_info], root_device_info,
                block_device_info, fake_vm_gen, mock_image_meta)
            mock_configdrive_required.assert_called_once_with(mock_instance)
            if configdrive_required:
                mock_create_config_drive.assert_called_once_with(
                    mock_instance, [mock.sentinel.FILE],
                    mock.sentinel.PASSWORD,
                    [fake_network_info])
                mock_attach_config_drive.assert_called_once_with(
                    mock_instance, fake_config_drive_path, fake_vm_gen)
            mock_set_boot_order.assert_called_once_with(fake_vm_gen,
                block_device_info, mock_instance.name)
            mock_power_on.assert_called_once_with(
                mock_instance, network_info=[fake_network_info])

    def test_spawn(self):
        root_device_info = mock.sentinel.ROOT_DEV_INFO
        block_device_info = {'ephemerals': [], 'root_disk': root_device_info}
        self._test_spawn(exists=False, root_device_info=root_device_info,
                         block_device_info=block_device_info,
                         configdrive_required=True, fail=None)

    def test_spawn_instance_exists(self):
        self._test_spawn(exists=True, root_device_info=None,
                         block_device_info=None,
                         configdrive_required=True, fail=None)

    def test_spawn_create_instance_exception(self):
        root_device_info = mock.sentinel.ROOT_DEV_INFO
        block_device_info = {'ephemerals': [], 'root_disk': root_device_info}
        self._test_spawn(exists=False, root_device_info=root_device_info,
                         block_device_info=block_device_info,
                         configdrive_required=True,
                         fail=vmutils.HyperVException)

    def test_spawn_not_required(self):
        root_device_info = mock.sentinel.ROOT_DEV_INFO
        block_device_info = {'ephemerals': [], 'root_disk': root_device_info}
        self._test_spawn(exists=False, root_device_info=root_device_info,
                         block_device_info=block_device_info,
                         configdrive_required=False, fail=None)

    def test_spawn_no_admin_permissions(self):
        self._vmops._vmutils.check_admin_permissions.side_effect = (
            vmutils.HyperVException)
        self.assertRaises(vmutils.HyperVException,
                          self._vmops.spawn,
                          self.context, mock.DEFAULT, mock.DEFAULT,
                          [mock.sentinel.FILE], mock.sentinel.PASSWORD,
                          mock.sentinel.INFO, mock.sentinel.DEV_INFO)

    @mock.patch.object(vmops.VMOps, '_requires_secure_boot')
    @mock.patch.object(vmops.VMOps, '_requires_certificate')
    @mock.patch('hyperv.nova.vif.get_vif_driver')
    @mock.patch.object(vmops.VMOps, '_set_instance_disk_qos_specs')
    @mock.patch.object(vmops.volumeops.VolumeOps, 'attach_volumes')
    @mock.patch.object(vmops.VMOps, '_attach_root_device')
    @mock.patch.object(vmops.VMOps, '_attach_ephemerals')
    @mock.patch.object(vmops.VMOps, '_get_image_serial_port_settings')
    @mock.patch.object(vmops.VMOps, '_create_vm_com_port_pipes')
    @mock.patch.object(vmops.VMOps, '_configure_remotefx')
    @mock.patch.object(vmops.VMOps, '_get_instance_vnuma_config')
    def _test_create_instance(self, mock_get_instance_vnuma_config,
                              mock_configure_remotefx, mock_create_pipes,
                              mock_get_port_settings, mock_attach_ephemerals,
                              mock_attach_root_device, mock_attach_volumes,
                              mock_set_qos_specs, mock_get_vif_driver,
                              mock_requires_certificate,
                              mock_requires_secure_boot,
                              enable_instance_metrics,
                              vm_gen=constants.VM_GEN_1, vnuma_enabled=False,
                              requires_sec_boot=True, remotefx=False):
        mock_vif_driver = mock_get_vif_driver()
        self.flags(dynamic_memory_ratio=2.0, group='hyperv')
        self.flags(enable_instance_metrics_collection=enable_instance_metrics,
                   group='hyperv')
        root_device_info = mock.sentinel.ROOT_DEV_INFO
        block_device_info = {'ephemerals': [], 'block_device_mapping': []}
        fake_network_info = {'id': mock.sentinel.ID,
                             'address': mock.sentinel.ADDRESS}
        mock_instance = fake_instance.fake_instance_obj(self.context)
        instance_path = os.path.join(CONF.instances_path, mock_instance.name)
        mock_requires_secure_boot.return_value = requires_sec_boot

        if vnuma_enabled:
            mock_get_instance_vnuma_config.return_value = (
                mock.sentinel.mem_per_numa, mock.sentinel.cpus_per_numa)
            cpus_per_numa = mock.sentinel.numa_cpus
            mem_per_numa = mock.sentinel.mem_per_numa
            dynamic_memory_ratio = 1.0
        else:
            mock_get_instance_vnuma_config.return_value = (None, None)
            mem_per_numa, cpus_per_numa = (None, None)
            dynamic_memory_ratio = CONF.hyperv.dynamic_memory_ratio

        flavor = flavor_obj.Flavor(**test_flavor.fake_flavor)
        if remotefx is True:
            flavor.extra_specs['hyperv:remotefx'] = "1920x1200,2"
        mock_instance.flavor = flavor

        if remotefx is True and vm_gen == constants.VM_GEN_2:
            self.assertRaises(vmutils.HyperVException,
                              self._vmops.create_instance,
                              instance=mock_instance,
                              network_info=[fake_network_info],
                              block_device_info=block_device_info,
                              root_device=root_device_info,
                              vm_gen=vm_gen,
                              image_meta=mock.sentinel.image_meta)
        else:
            self._vmops.create_instance(
                    instance=mock_instance,
                    network_info=[fake_network_info],
                    block_device_info=block_device_info,
                    root_device=root_device_info,
                    vm_gen=vm_gen,
                    image_meta=mock.sentinel.image_meta)
            if remotefx is True:
                mock_configure_remotefx.assert_called_once_with(
                    mock_instance,
                    flavor.extra_specs['hyperv:remotefx'])

            self._vmops._vmutils.create_vm.assert_called_once_with(
                mock_instance.name, vnuma_enabled, vm_gen,
                instance_path, [mock_instance.uuid])
            self._vmops._vmutils.update_vm.assert_called_once_with(
                mock_instance.name, mock_instance.memory_mb, mem_per_numa,
                mock_instance.vcpus, cpus_per_numa,
                CONF.hyperv.limit_cpu_features, dynamic_memory_ratio)

            mock_create_scsi_ctrl = self._vmops._vmutils.create_scsi_controller
            mock_create_scsi_ctrl.assert_called_once_with(mock_instance.name)

            mock_attach_root_device.assert_called_once_with(mock_instance.name,
                root_device_info)
            mock_attach_ephemerals.assert_called_once_with(mock_instance.name,
                block_device_info['ephemerals'])
            mock_attach_volumes.assert_called_once_with(
                block_device_info['block_device_mapping'], mock_instance.name)

            mock_get_port_settings.assert_called_with(mock.sentinel.image_meta)
            mock_create_pipes.assert_called_once_with(
                mock_instance, mock_get_port_settings.return_value)

            self._vmops._vmutils.create_nic.assert_called_once_with(
                mock_instance.name, mock.sentinel.ID, mock.sentinel.ADDRESS)
            mock_vif_driver.plug.assert_called_once_with(mock_instance,
                                                         fake_network_info)
            mock_enable = self._vmops._vmutils.enable_vm_metrics_collection
            if enable_instance_metrics:
                mock_enable.assert_called_once_with(mock_instance.name)
            mock_set_qos_specs.assert_called_once_with(mock_instance)
            if requires_sec_boot:
                mock_requires_secure_boot.assert_called_once_with(
                    mock_instance, mock.sentinel.image_meta, vm_gen)
                mock_requires_certificate.assert_called_once_with(
                    mock_instance.uuid,
                    mock.sentinel.image_meta)
                enable_secure_boot = self._vmops._vmutils.enable_secure_boot
                enable_secure_boot.assert_called_once_with(
                    mock_instance.name, mock_requires_certificate.return_value)

    def test_create_instance(self):
        self._test_create_instance(enable_instance_metrics=True)

    def test_create_instance_exception(self):
        # Secure Boot requires Generation 2 VMs. If boot is required while the
        # vm_gen is 1, exception is raised.
        self._test_create_instance(enable_instance_metrics=True,
                                   vm_gen=constants.VM_GEN_1)

    def test_create_instance_enable_instance_metrics_false(self):
        self._test_create_instance(enable_instance_metrics=False)

    def test_create_instance_gen2(self):
        self._test_create_instance(enable_instance_metrics=False,
                                   vm_gen=constants.VM_GEN_2)

    def test_create_instance_with_remote_fx(self):
        self._test_create_instance(enable_instance_metrics=False,
                                   remotefx=True)

    def test_create_instance_with_remote_fx_gen2(self):
        self._test_create_instance(enable_instance_metrics=False,
                                   remotefx=True)

    @mock.patch.object(vmops.volumeops.VolumeOps, 'attach_volume')
    def test_attach_root_device_volume(self, mock_attach_volume):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        root_device_info = {'type': constants.VOLUME,
                            'connection_info': mock.sentinel.CONN_INFO,
                            'disk_bus': constants.CTRL_TYPE_IDE}

        self._vmops._attach_root_device(mock_instance.name, root_device_info)

        mock_attach_volume.assert_called_once_with(
            root_device_info['connection_info'], mock_instance.name,
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

        self._vmops._attach_root_device(mock_instance.name, root_device_info)

        mock_attach_drive.assert_called_once_with(
            mock_instance.name, root_device_info['path'],
            root_device_info['drive_addr'], root_device_info['ctrl_disk_addr'],
            root_device_info['disk_bus'], root_device_info['type'])

    @mock.patch.object(vmops.VMOps, '_attach_drive')
    def test_attach_ephemerals(self, mock_attach_drive):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        ephemerals = [{'path': mock.sentinel.PATH1,
                       'boot_index': 1,
                       'disk_bus': constants.CTRL_TYPE_IDE,
                       'device_type': 'disk',
                       'drive_addr': 0,
                       'ctrl_disk_addr': 1},
                      {'path': mock.sentinel.PATH2,
                       'boot_index': 2,
                       'disk_bus': constants.CTRL_TYPE_SCSI,
                       'device_type': 'disk',
                       'drive_addr': 0,
                       'ctrl_disk_addr': 0}]

        self._vmops._attach_ephemerals(mock_instance.name, ephemerals)

        mock_attach_drive.assert_has_calls(
            [mock.call(mock_instance.name, mock.sentinel.PATH1, 0,
                       1, constants.CTRL_TYPE_IDE, constants.DISK),
             mock.call(mock_instance.name, mock.sentinel.PATH2, 0,
                       0, constants.CTRL_TYPE_SCSI, constants.DISK)
        ])

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

    def _check_get_image_vm_gen_except(self, image_prop):
        image_meta = {"properties": {constants.IMAGE_PROP_VM_GEN: image_prop}}
        self._vmops._hostutils.get_supported_vm_types.return_value = [
            constants.IMAGE_PROP_VM_GEN_1, constants.IMAGE_PROP_VM_GEN_2]

        self.assertRaises(vmutils.HyperVException,
                          self._vmops.get_image_vm_generation,
                          image_meta)

    def test_get_image_vm_generation_default(self):
        image_meta = {"properties": {}}
        self._vmops._hostutils.get_default_vm_generation.return_value = (
            constants.IMAGE_PROP_VM_GEN_1)
        self._vmops._hostutils.get_supported_vm_types.return_value = [
            constants.IMAGE_PROP_VM_GEN_1, constants.IMAGE_PROP_VM_GEN_2]

        response = self._vmops.get_image_vm_generation(image_meta)

        self.assertEqual(constants.VM_GEN_1, response)

    def test_get_image_vm_generation_gen2(self):
        image_meta = {"properties": {
            constants.IMAGE_PROP_VM_GEN: constants.IMAGE_PROP_VM_GEN_2}}
        self._vmops._hostutils.get_supported_vm_types.return_value = [
            constants.IMAGE_PROP_VM_GEN_1, constants.IMAGE_PROP_VM_GEN_2]

        response = self._vmops.get_image_vm_generation(image_meta)

        self.assertEqual(constants.VM_GEN_2, response)

    def test_get_image_vm_generation_bad_prop(self):
        self._check_get_image_vm_gen_except(mock.sentinel.FAKE_IMAGE_PROP)

    def test_check_vm_image_type_exception(self):
        self._vmops._vhdutils.get_vhd_format = mock.MagicMock(
            return_value=constants.DISK_FORMAT_VHD)

        self.assertRaises(vmutils.HyperVException,
            self._vmops.check_vm_image_type, constants.VM_GEN_2,
            mock.sentinel.FAKE_VHD_PATH)

    @mock.patch('nova.api.metadata.base.InstanceMetadata')
    @mock.patch('nova.virt.configdrive.ConfigDriveBuilder')
    @mock.patch('nova.utils.execute')
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
            self.assertRaises(vmutils.UnsupportedConfigDriveFormatException,
                              self._vmops._create_config_drive,
                              mock_instance, [mock.sentinel.FILE],
                              mock.sentinel.PASSWORD,
                              mock.sentinel.NET_INFO,
                              rescue)
        elif side_effect is processutils.ProcessExecutionError:
            self.assertRaises(processutils.ProcessExecutionError,
                              self._vmops._create_config_drive,
                              mock_instance, [mock.sentinel.FILE],
                              mock.sentinel.PASSWORD,
                              mock.sentinel.NET_INFO,
                              rescue)
        else:
            path = self._vmops._create_config_drive(mock_instance,
                                                    [mock.sentinel.FILE],
                                                    mock.sentinel.PASSWORD,
                                                    mock.sentinel.NET_INFO,
                                                    rescue)
            mock_InstanceMetadata.assert_called_once_with(
                mock_instance, content=[mock.sentinel.FILE],
                extra_md={'admin_pass': mock.sentinel.PASSWORD},
                network_info=mock.sentinel.NET_INFO)
            mock_get_configdrive_path.assert_has_calls(
                expected_get_configdrive_path_calls)
            mock_ConfigDriveBuilder.assert_called_with(
                instance_md=mock_InstanceMetadata())
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
        self._test_create_config_drive(config_drive_format=mock.sentinel.OTHER,
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

    def test_delete_disk_files(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops._delete_disk_files(mock_instance.name)

        stop_console_handler = (
            self._vmops._serial_console_ops.stop_console_handler_unsync)
        stop_console_handler.assert_called_once_with(mock_instance.name)
        self._vmops._pathutils.get_instance_dir.assert_called_once_with(
            mock_instance.name, create_dir=False, remove_dir=True)

    @mock.patch('hyperv.nova.volumeops.VolumeOps.disconnect_volumes')
    @mock.patch('hyperv.nova.vmops.VMOps._delete_disk_files')
    @mock.patch('hyperv.nova.vmops.VMOps.power_off')
    @mock.patch('hyperv.nova.vmops.VMOps.unplug_vifs')
    def test_destroy(self, mock_unplug_vifs, mock_power_off,
                     mock_delete_disk_files, mock_disconnect_volumes):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops._vmutils.vm_exists.return_value = True

        self._vmops.destroy(instance=mock_instance,
                            block_device_info=mock.sentinel.FAKE_BD_INFO,
                            network_info=mock.sentinel.fake_network_info)

        self._vmops._vmutils.vm_exists.assert_called_with(
            mock_instance.name)
        mock_power_off.assert_called_once_with(mock_instance)
        self._vmops._vmutils.destroy_vm.assert_called_once_with(
            mock_instance.name)
        mock_disconnect_volumes.assert_called_once_with(
            mock.sentinel.FAKE_BD_INFO)
        mock_delete_disk_files.assert_called_once_with(
            mock_instance.name)
        mock_unplug_vifs.assert_called_once_with(
            mock_instance, mock.sentinel.fake_network_info)

    def test_destroy_inexistent_instance(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops._vmutils.vm_exists.return_value = False

        self._vmops.destroy(instance=mock_instance)
        self.assertFalse(self._vmops._vmutils.destroy_vm.called)

    @mock.patch('hyperv.nova.vmops.VMOps.power_off')
    def test_destroy_exception(self, mock_power_off):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops._vmutils.destroy_vm.side_effect = vmutils.HyperVException
        self._vmops._vmutils.vm_exists.return_value = True

        self.assertRaises(vmutils.HyperVException,
                          self._vmops.destroy, mock_instance)

    def test_reboot_hard(self):
        self._test_reboot(vmops.REBOOT_TYPE_HARD,
                          constants.HYPERV_VM_STATE_REBOOT)

    @mock.patch("hyperv.nova.vmops.VMOps._soft_shutdown")
    def test_reboot_soft(self, mock_soft_shutdown):
        mock_soft_shutdown.return_value = True
        self._test_reboot(vmops.REBOOT_TYPE_SOFT,
                          constants.HYPERV_VM_STATE_ENABLED)

    @mock.patch("hyperv.nova.vmops.VMOps._soft_shutdown")
    def test_reboot_soft_failed(self, mock_soft_shutdown):
        mock_soft_shutdown.return_value = False
        self._test_reboot(vmops.REBOOT_TYPE_SOFT,
                          constants.HYPERV_VM_STATE_REBOOT)

    @mock.patch("hyperv.nova.vmops.VMOps.power_on")
    @mock.patch("hyperv.nova.vmops.VMOps._soft_shutdown")
    def test_reboot_soft_exception(self, mock_soft_shutdown, mock_power_on):
        mock_soft_shutdown.return_value = True
        mock_power_on.side_effect = vmutils.HyperVException("Expected failure")
        instance = fake_instance.fake_instance_obj(self.context)

        self.assertRaises(vmutils.HyperVException, self._vmops.reboot,
                          instance, {}, vmops.REBOOT_TYPE_SOFT)

        mock_soft_shutdown.assert_called_once_with(instance)
        mock_power_on.assert_called_once_with(instance, network_info={})

    def _test_reboot(self, reboot_type, vm_state):
        instance = fake_instance.fake_instance_obj(self.context)
        with mock.patch.object(self._vmops, '_set_vm_state') as mock_set_state:
            self._vmops.reboot(instance, {}, reboot_type)
            mock_set_state.assert_called_once_with(instance, vm_state)

    @mock.patch("hyperv.nova.vmops.VMOps._wait_for_power_off")
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
        mock_shutdown_vm.side_effect = vmutils.HyperVException(
            "Expected failure.")

        result = self._vmops._soft_shutdown(instance, self._FAKE_TIMEOUT)

        mock_shutdown_vm.assert_called_once_with(instance.name)
        self.assertFalse(result)

    @mock.patch("hyperv.nova.vmops.VMOps._wait_for_power_off")
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

    @mock.patch("hyperv.nova.vmops.VMOps._wait_for_power_off")
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

    @mock.patch('hyperv.nova.vmops.VMOps._set_vm_state')
    def test_pause(self, mock_set_vm_state):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops.pause(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance, constants.HYPERV_VM_STATE_PAUSED)

    @mock.patch('hyperv.nova.vmops.VMOps._set_vm_state')
    def test_unpause(self, mock_set_vm_state):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops.unpause(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance, constants.HYPERV_VM_STATE_ENABLED)

    @mock.patch('hyperv.nova.vmops.VMOps._set_vm_state')
    def test_suspend(self, mock_set_vm_state):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops.suspend(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance, constants.HYPERV_VM_STATE_SUSPENDED)

    @mock.patch('hyperv.nova.vmops.VMOps._set_vm_state')
    def test_resume(self, mock_set_vm_state):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops.resume(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance, constants.HYPERV_VM_STATE_ENABLED)

    def _test_power_off(self, timeout, set_state_expected=True):
        instance = fake_instance.fake_instance_obj(self.context)
        with mock.patch.object(self._vmops, '_set_vm_state') as mock_set_state:
            self._vmops.power_off(instance, timeout)

            serialops = self._vmops._serial_console_ops
            serialops.stop_console_handler.assert_called_once_with(
                instance.name)
            if set_state_expected:
                mock_set_state.assert_called_once_with(
                    instance, constants.HYPERV_VM_STATE_DISABLED)

    def test_power_off_hard(self):
        self._test_power_off(timeout=0)

    @mock.patch("hyperv.nova.vmops.VMOps._soft_shutdown")
    def test_power_off_exception(self, mock_soft_shutdown):
        mock_soft_shutdown.return_value = False
        self._test_power_off(timeout=1)

    @mock.patch("hyperv.nova.vmops.VMOps._soft_shutdown")
    def test_power_off_unexisting_instance(self, mock_soft_shutdown):
        mock_soft_shutdown.side_effect = (
            exception.InstanceNotFound('fake_instance_uuid'))
        self._test_power_off(timeout=1, set_state_expected=False)

    @mock.patch("hyperv.nova.vmops.VMOps._set_vm_state")
    @mock.patch("hyperv.nova.vmops.VMOps._soft_shutdown")
    def test_power_off_soft(self, mock_soft_shutdown, mock_set_state):
        instance = fake_instance.fake_instance_obj(self.context)
        mock_soft_shutdown.return_value = True

        self._vmops.power_off(instance, 1, 0)

        mock_soft_shutdown.assert_called_once_with(
            instance, 1, vmops.SHUTDOWN_TIME_INCREMENT)
        self.assertFalse(mock_set_state.called)

    @mock.patch('hyperv.nova.vmops.VMOps._set_vm_state')
    def test_power_on(self, mock_set_vm_state):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        self._vmops.power_on(mock_instance)

        mock_set_vm_state.assert_called_once_with(
            mock_instance, constants.HYPERV_VM_STATE_ENABLED)

    @mock.patch('hyperv.nova.volumeops.VolumeOps'
                '.fix_instance_volume_disk_paths')
    @mock.patch('hyperv.nova.vmops.VMOps._set_vm_state')
    def test_power_on_having_block_devices(self, mock_set_vm_state,
                                           mock_fix_instance_vol_paths):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        self._vmops.power_on(mock_instance, mock.sentinel.block_device_info)

        mock_fix_instance_vol_paths.assert_called_once_with(
            mock_instance.name, mock.sentinel.block_device_info)
        mock_set_vm_state.assert_called_once_with(
            mock_instance, constants.HYPERV_VM_STATE_ENABLED)

    @mock.patch.object(vmops.VMOps, 'post_start_vifs')
    def test_power_on_with_network_info(self, mock_post_start_vifs):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        self._vmops.power_on(mock_instance,
                             network_info=mock.sentinel.fake_network_info)
        mock_post_start_vifs.assert_called_once_with(
            mock_instance, mock.sentinel.fake_network_info)

    def _test_set_vm_state(self, state):
        mock_instance = fake_instance.fake_instance_obj(self.context)

        self._vmops._set_vm_state(mock_instance, state)
        self._vmops._vmutils.set_vm_state.assert_called_once_with(
            mock_instance.name, state)

    def test_set_vm_state_disabled(self):
        self._test_set_vm_state(state=constants.HYPERV_VM_STATE_DISABLED)

    def test_set_vm_state_enabled(self):
        self._test_set_vm_state(state=constants.HYPERV_VM_STATE_ENABLED)

    def test_set_vm_state_reboot(self):
        self._test_set_vm_state(state=constants.HYPERV_VM_STATE_REBOOT)

    def test_set_vm_state_exception(self):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        self._vmops._vmutils.set_vm_state.side_effect = vmutils.HyperVException
        self.assertRaises(vmutils.HyperVException, self._vmops._set_vm_state,
                          mock_instance, mock.sentinel.STATE)

    def test_get_vm_state(self):
        summary_info = {'EnabledState': constants.HYPERV_VM_STATE_DISABLED}

        with mock.patch.object(self._vmops._vmutils,
                               'get_vm_summary_info') as mock_get_summary_info:
            mock_get_summary_info.return_value = summary_info

            response = self._vmops._get_vm_state(mock.sentinel.FAKE_VM_NAME)
            self.assertEqual(response, constants.HYPERV_VM_STATE_DISABLED)

    @mock.patch.object(vmops.VMOps, '_get_vm_state')
    def test_wait_for_power_off_true(self, mock_get_state):
        mock_get_state.return_value = constants.HYPERV_VM_STATE_DISABLED
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
        for port_number, port_type in six.iteritems(mock_serial_ports):
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
        mock_image_meta = {'id': mock.sentinel.rescue_image_id}
        mock_vm_gen = constants.VM_GEN_2
        mock_instance = fake_instance.fake_instance_obj(self.context)

        mock_configdrive_required.return_value = True
        mock_create_root_vhd.return_value = mock.sentinel.rescue_vhd_path
        mock_get_image_vm_gen.return_value = mock_vm_gen
        self._vmops._vmutils.get_vm_gen.return_value = mock_vm_gen
        self._vmops._pathutils.lookup_root_vhd_path.return_value = (
            mock.sentinel.root_vhd_path)
        mock_create_config_drive.return_value = (
            mock.sentinel.rescue_configdrive_path)

        self._vmops.rescue_instance(self.context,
                                    mock_instance,
                                    mock.sentinel.network_info,
                                    mock_image_meta,
                                    mock.sentinel.rescue_password)

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
            mock_instance,
            injected_files=None,
            admin_password=mock.sentinel.rescue_password,
            network_info=mock.sentinel.network_info,
            rescue=True)
        mock_attach_config_drive.assert_called_once_with(
            mock_instance, mock.sentinel.rescue_configdrive_path,
            mock_vm_gen)

    @mock.patch.object(vmops.VMOps, '_create_root_vhd')
    @mock.patch.object(vmops.VMOps, 'get_image_vm_generation')
    def _test_rescue_instance_exception(self, mock_get_image_vm_gen,
                                        mock_create_root_vhd,
                                        wrong_vm_gen=False,
                                        boot_from_volume=False):
        mock_vm_gen = constants.VM_GEN_1
        image_vm_gen = (mock_vm_gen
                        if not wrong_vm_gen else constants.VM_GEN_2)
        mock_image_meta = {'id': mock.sentinel.rescue_image_id}

        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_get_image_vm_gen.return_value = image_vm_gen
        self._vmops._vmutils.get_vm_gen.return_value = mock_vm_gen
        self._vmops._pathutils.lookup_root_vhd_path.return_value = (
            mock.sentinel.root_vhd_path if not boot_from_volume else None)

        self.assertRaises(vmutils.HyperVException,
                          self._vmops.rescue_instance,
                          self.context, mock_instance,
                          mock.sentinel.network_info,
                          mock_image_meta,
                          mock.sentinel.rescue_password)

    def test_rescue_instance_wrong_vm_gen(self):
        # Test the case when the rescue image requires a different
        # vm generation than the actual rescued instance.
        self._test_rescue_instance_exception(wrong_vm_gen=True)

    def test_rescue_instance_boot_from_volume(self):
        # Rescuing instances booted from volume is not supported.
        self._test_rescue_instance_exception(boot_from_volume=True)

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

        self._vmops._vmutils.get_vm_gen.return_value = mock_vm_gen
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
        mock_detach_config_drive.assert_called_once_with(
            mock_instance.name, rescue=True, delete=True)
        mock_delete_if_exists.assert_called_once_with(
            mock.sentinel.rescue_vhd_path)
        self._vmops._vmutils.is_disk_attached.assert_called_once_with(
            mock_instance.name, mock.sentinel.configdrive_path,
            is_physical=False)
        mock_attach_configdrive.assert_called_once_with(
            mock_instance, mock.sentinel.configdrive_path, mock_vm_gen)
        mock_power_on.assert_called_once_with(mock_instance)

    @mock.patch.object(vmops.VMOps, 'power_off')
    def test_unrescue_instance_missing_root_image(self, mock_power_off):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.vm_state = vm_states.RESCUED
        self._vmops._pathutils.lookup_root_vhd_path.return_value = None

        self.assertRaises(vmutils.HyperVException,
                          self._vmops.unrescue_instance,
                          mock_instance)

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
        cell1 = mock.MagicMock(cpuset=set([0]), memory=1024, cpu_pinning=None)
        cell2 = mock.MagicMock(cpuset=set([1, 2]), memory=1024,
                               cpu_pinning=None)
        self._check_get_instance_vnuma_config_exception(
            numa_cells=[cell1, cell2])

    def test_get_instance_vnuma_config_bad_memory(self):
        cell1 = mock.MagicMock(cpuset=set([0]), memory=1024, cpu_pinning=None)
        cell2 = mock.MagicMock(cpuset=set([1]), memory=2048, cpu_pinning=None)
        self._check_get_instance_vnuma_config_exception(
            numa_cells=[cell1, cell2])

    def test_get_instance_vnuma_config_cpu_pinning_requested(self):
        cell = mock.MagicMock(cpu_pinning={})
        self._check_get_instance_vnuma_config_exception(numa_cells=[cell])

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

    def _test_configure_remotefx(self, exception=False):
        self.flags(enable_remotefx=True, group='hyperv')
        mock_instance = fake_instance.fake_instance_obj(self.context)

        fake_resolution = "1920x1200"
        fake_monitor_count = 3
        fake_config = "%s,%s" % (fake_resolution, fake_monitor_count)

        self._vmops._vmutils.enable_remotefx_video_adapter = mock.MagicMock()
        enable_remotefx = self._vmops._vmutils.enable_remotefx_video_adapter
        self._vmops._hostutils.check_server_feature = mock.MagicMock()

        if exception:
            self._vmops._hostutils.check_server_feature.return_value = False
            self.assertRaises(vmutils.HyperVException,
                              self._vmops._configure_remotefx,
                              mock_instance, fake_config)
        else:
            self._vmops._configure_remotefx(mock_instance, fake_config)
            enable_remotefx.assert_called_once_with(mock_instance.name,
                                                    fake_monitor_count,
                                                    fake_resolution)

    def test_configure_remotefx_exception(self):
        self._test_configure_remotefx(exception=True)

    def test_configure_remotefx(self):
        self._test_configure_remotefx()

    @mock.patch.object(vmops.VMOps, '_get_vif_driver')
    def test_unplug_vifs(self, mock_get_vif_driver):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        fake_vif1 = {'id': mock.sentinel.ID1,
                     'type': mock.sentinel.vif_type1}
        fake_vif2 = {'id': mock.sentinel.ID2,
                     'type': mock.sentinel.vif_type2}
        mock_network_info = [fake_vif1, fake_vif2]
        fake_vif_driver = mock.MagicMock()
        mock_get_vif_driver.return_value = fake_vif_driver
        calls = [mock.call(mock_instance, fake_vif1),
                 mock.call(mock_instance, fake_vif2)]

        self._vmops.unplug_vifs(mock_instance,
                                network_info=mock_network_info)
        fake_vif_driver.unplug.assert_has_calls(calls)

    @mock.patch.object(vmops.VMOps, '_get_vif_driver')
    def test_post_start_vifs(self, mock_get_vif_driver):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        fake_vif1 = {'id': mock.sentinel.ID1,
                     'type': mock.sentinel.vif_type1}
        fake_vif2 = {'id': mock.sentinel.ID2,
                     'type': mock.sentinel.vif_type2}
        mock_network_info = [fake_vif1, fake_vif2]
        fake_vif_driver = mock.MagicMock()
        mock_get_vif_driver.return_value = fake_vif_driver
        calls = [mock.call(mock_instance, fake_vif1),
                 mock.call(mock_instance, fake_vif2)]

        self._vmops.post_start_vifs(mock_instance,
                                    network_info=mock_network_info)
        fake_vif_driver.post_start.assert_has_calls(calls)

    @mock.patch.object(vmops.VMOps, '_get_vm_state')
    def _test_check_hotplug_is_available(self, mock_get_vm_state, vm_gen,
                                         windows_version, vm_state):
        fake_vm = fake_instance.fake_instance_obj(self.context)
        mock_get_vm_state.return_value = vm_state
        self._vmops._vmutils.get_vm_gen.return_value = vm_gen
        fake_check_win_vers = self._vmops._hostutils.check_min_windows_version
        if windows_version == self._WIN_VERSION_6_3:
            fake_check_win_vers.return_value = False
        else:
            fake_check_win_vers.return_value = True

        if (windows_version == self._WIN_VERSION_6_3 or
                vm_gen == constants.VM_GEN_1):
            self.assertRaises(exception.InterfaceAttachFailed,
                self._vmops._check_hotplug_is_available, fake_vm)
        else:
            ret = self._vmops._check_hotplug_is_available(fake_vm)
            if vm_state == constants.HYPERV_VM_STATE_DISABLED:
                self.assertFalse(ret)
            else:
                self.assertTrue(ret)

    def test_check_if_hotplug_is_available_gen1(self):
        self._test_check_hotplug_is_available(vm_gen=constants.VM_GEN_1,
            windows_version=self._WIN_VERSION_6_4,
            vm_state=constants.HYPERV_VM_STATE_ENABLED)

    def test_check_if_hotplug_is_available_gen2(self):
        self._test_check_hotplug_is_available(vm_gen=constants.VM_GEN_2,
            windows_version=self._WIN_VERSION_6_4,
            vm_state=constants.HYPERV_VM_STATE_ENABLED)

    def test_check_if_hotplug_is_available_win_6_3(self):
        self._test_check_hotplug_is_available(vm_gen=constants.VM_GEN_2,
            windows_version=self._WIN_VERSION_6_3,
            vm_state=constants.HYPERV_VM_STATE_ENABLED)

    def test_check_if_hotplug_is_available_vm_disabled(self):
        self._test_check_hotplug_is_available(vm_gen=constants.VM_GEN_2,
            windows_version=self._WIN_VERSION_6_4,
            vm_state=constants.HYPERV_VM_STATE_DISABLED)

    @mock.patch.object(vmops.VMOps, '_get_vif_driver')
    def _test_create_and_attach_interface(self, mock_get_vif_driver, hot_plug):
        fake_vm = fake_instance.fake_instance_obj(self.context)
        fake_vif = test_virtual_interface.fake_vif
        fake_vif['type'] = mock.sentinel.VIF_TYPE
        fake_vif_driver = mock_get_vif_driver.return_value

        self._vmops._create_and_attach_interface(fake_vm, fake_vif, hot_plug)
        self._vmops._vmutils.create_nic.assert_called_with(fake_vm.name,
                fake_vif['id'], fake_vif['address'])
        mock_get_vif_driver.assert_called_once_with(mock.sentinel.VIF_TYPE)
        fake_vif_driver.plug.assert_called_once_with(fake_vm, fake_vif)
        if hot_plug:
            fake_vif_driver.post_start.assert_called_once_with(fake_vm,
                                                               fake_vif)

    def test_create_and_attach_interface_hot_plugged(self):
        self._test_create_and_attach_interface(hot_plug=True)

    def test_create_and_attach_interface(self):
        self._test_create_and_attach_interface(hot_plug=False)

    @mock.patch.object(vmops.VMOps, '_check_hotplug_is_available')
    @mock.patch.object(vmops.VMOps, '_create_and_attach_interface')
    def _test_attach_interface(self, mock_create_and_attach_interface,
                               mock_check_hotplug_is_available, hot_plug):
        mock_check_hotplug_is_available.return_value = hot_plug

        self._vmops.attach_interface(mock.sentinel.FAKE_VM,
                                     mock.sentinel.FAKE_VIF)
        mock_check_hotplug_is_available.assert_called_once_with(
            mock.sentinel.FAKE_VM)
        mock_create_and_attach_interface.assert_called_once_with(
            mock.sentinel.FAKE_VM, mock.sentinel.FAKE_VIF, hot_plug)

    def test_attach_interface_hot_plugged(self):
        self._test_attach_interface(hot_plug=True)

    def test_attach_interface(self):
        self._test_attach_interface(hot_plug=False)

    @mock.patch.object(vmops.VMOps, '_get_vif_driver')
    def test_detach_and_destroy_interface(self, mock_get_vif_driver):
        fake_vm = fake_instance.fake_instance_obj(self.context)
        fake_vif = test_virtual_interface.fake_vif
        fake_vif['type'] = mock.sentinel.VIF_TYPE
        fake_vif_driver = mock_get_vif_driver.return_value

        self._vmops._detach_and_destroy_interface(fake_vm, fake_vif)
        fake_vif_driver.unplug.assert_called_once_with(fake_vm, fake_vif)
        self._vmops._vmutils.destroy_nic.assert_called_once_with(
            fake_vm.name, fake_vif['id'])

    @mock.patch.object(vmops.VMOps, '_check_hotplug_is_available')
    @mock.patch.object(vmops.VMOps, '_detach_and_destroy_interface')
    def test_detach_interface(self, mock_detach_and_destroy_interface,
                              mock_check_hotplug_is_available):
        self._vmops.detach_interface(mock.sentinel.FAKE_VM,
                                     mock.sentinel.FAKE_VIF)
        mock_check_hotplug_is_available.assert_called_once_with(
            mock.sentinel.FAKE_VM)
        mock_detach_and_destroy_interface.assert_called_once_with(
            mock.sentinel.FAKE_VM, mock.sentinel.FAKE_VIF)

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
            self.assertRaises(vmutils.HyperVException,
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

    @mock.patch.object(vmops.VMOps, '_get_storage_qos_specs')
    @mock.patch.object(vmops.VMOps, '_get_instance_local_disks')
    def test_set_instance_disk_qos_specs(self, mock_get_local_disks,
                                         mock_get_qos_specs):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_local_disks = [mock.sentinel.root_vhd_path,
                            mock.sentinel.eph_vhd_path]

        mock_get_local_disks.return_value = mock_local_disks
        mock_set_qos_specs = self._vmops._vmutils.set_disk_qos_specs
        mock_get_qos_specs.return_value = [mock.sentinel.min_iops,
                                           mock.sentinel.max_iops]

        self._vmops._set_instance_disk_qos_specs(mock_instance)
        mock_get_local_disks.assert_called_once_with(mock_instance.name)
        expected_calls = [mock.call(mock_instance.name, disk_path,
                                    mock.sentinel.min_iops,
                                    mock.sentinel.max_iops)
                          for disk_path in mock_local_disks]
        mock_set_qos_specs.assert_has_calls(expected_calls)

    @mock.patch.object(volumeops.VolumeOps, 'parse_disk_qos_specs')
    def test_get_storage_qos_specs(self, mock_parse_specs):
        fake_extra_specs = {'spec_key': 'spec_value',
                            'storage_qos:min_bytes_sec':
                                mock.sentinel.min_bytes_sec,
                            'storage_qos:max_bytes_sec':
                                mock.sentinel.max_bytes_sec}

        mock_instance = mock.Mock(flavor={'extra_specs': fake_extra_specs})
        ret_val = self._vmops._get_storage_qos_specs(mock_instance)

        expected_qos_specs_dict = {
            'min_bytes_sec': mock.sentinel.min_bytes_sec,
            'max_bytes_sec': mock.sentinel.max_bytes_sec
        }

        self.assertEqual(mock_parse_specs.return_value, ret_val)
        mock_parse_specs.assert_called_once_with(expected_qos_specs_dict)

    def _test_requires_secure_boot(self, flavor_secure_boot,
                                   image_prop_secure_boot,
                                   fake_vm_gen=constants.VM_GEN_2):
        mock_instance = mock.MagicMock()
        flavor_secure_boot = {
            'extra_specs': {'os:secure_boot': flavor_secure_boot}}
        mock_image_meta = {'properties':
                           {'os_secure_boot': image_prop_secure_boot}}

        if flavor_secure_boot in ('required', 'disabled'):
            expected_result = constants.REQUIRED == flavor_secure_boot
        else:
            expected_result = image_prop_secure_boot == 'required'
        if fake_vm_gen != constants.VM_GEN_2 and expected_result:
            self.assertRaises(vmutils.HyperVException,
                              self._vmops._requires_secure_boot,
                              mock_instance, mock_image_meta)
        else:
            result = self._vmops._requires_secure_boot(mock_instance,
                                                       mock_image_meta,
                                                       fake_vm_gen)
            self.assertEqual(expected_result, result)

    def test_requires_secure_boot_disabled(self):
        self._test_requires_secure_boot(
            flavor_secure_boot=constants.DISABLED,
            image_prop_secure_boot=constants.REQUIRED)

    def test_requires_secure_boot_optional(self):
        self._test_requires_secure_boot(
            flavor_secure_boot=constants.OPTIONAL,
            image_prop_secure_boot=constants.OPTIONAL)

    def test_requires_secure_boot_required(self):
        self._test_requires_secure_boot(
            flavor_secure_boot=constants.REQUIRED,
            image_prop_secure_boot=constants.OPTIONAL)

    def test_requires_secure_boot_bad_vm_gen(self):
        self._test_requires_secure_boot(
            flavor_secure_boot=constants.REQUIRED,
            image_prop_secure_boot=constants.OPTIONAL,
            fake_vm_gen=constants.VM_GEN_1)

    def _test_requires_certificate(self, os_type):
        image_meta = {'properties': {'os_type': os_type}}
        if not os_type:
            self.assertRaises(vmutils.HyperVException,
                              self._vmops._requires_certificate, image_meta)
        else:
            expected_result = os_type == 'linux'
            result = self._vmops._requires_certificate(image_meta)
            self.assertEqual(expected_result, result)

    def test_requires_certificate_windows(self):
        self._test_requires_certificate(os_type='windows')

    def test_requires_certificate_linux(self):
        self._test_requires_certificate(os_type='linux')

    def test_requires_certificate_os_type_none(self):
        self._test_requires_certificate(os_type=None)
