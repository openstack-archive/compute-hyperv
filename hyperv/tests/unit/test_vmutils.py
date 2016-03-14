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

import mock
from nova import exception
import six

from hyperv.nova import constants
from hyperv.nova import vmutils
from hyperv.tests import test


class RetryDecoratorTestCase(test.NoDBTestCase):

    def _get_fake_func_with_retry_decorator(self, side_effect,
                                            *args, **kwargs):
        func_side_effect = mock.Mock(side_effect=side_effect)

        @vmutils.retry_decorator(*args, **kwargs)
        def fake_func(*_args, **_kwargs):
            return func_side_effect(*_args, **_kwargs)

        return fake_func, func_side_effect

    @mock.patch('time.sleep')
    def test_retry_decorator(self, mock_sleep):
        max_retry_count = 5
        max_sleep_time = 4

        raised_exc = vmutils.HyperVException(message='fake_exc')
        side_effect = [raised_exc] * max_retry_count
        side_effect.append(mock.sentinel.ret_val)

        fake_func = self._get_fake_func_with_retry_decorator(
            exceptions=vmutils.HyperVException,
            max_retry_count=max_retry_count,
            max_sleep_time=max_sleep_time,
            side_effect=side_effect)[0]

        ret_val = fake_func()
        self.assertEqual(mock.sentinel.ret_val, ret_val)
        mock_sleep.assert_has_calls([mock.call(sleep_time)
                                     for sleep_time in [1, 2, 3, 4, 4]])

    @mock.patch('time.sleep')
    def test_retry_decorator_unexpected_exc(self, mock_sleep):
        expected_exceptions = (IOError, AttributeError)
        raised_exc = vmutils.HyperVException(message='fake_exc')
        fake_func, fake_func_side_effect = (
            self._get_fake_func_with_retry_decorator(
                exceptions=expected_exceptions,
                side_effect=raised_exc))

        self.assertRaises(vmutils.HyperVException,
                          fake_func, mock.sentinel.arg,
                          fake_kwarg=mock.sentinel.kwarg)

        self.assertFalse(mock_sleep.called)
        fake_func_side_effect.assert_called_once_with(
            mock.sentinel.arg, fake_kwarg=mock.sentinel.kwarg)


class VMUtilsTestCase(test.NoDBTestCase):
    """Unit tests for the Hyper-V VMUtils class."""

    _FAKE_VM_NAME = 'fake_vm'
    _FAKE_MEMORY_MB = 2
    _FAKE_VCPUS_NUM = 4
    _FAKE_JOB_PATH = 'fake_job_path'
    _FAKE_RET_VAL = 0
    _FAKE_RET_VAL_BAD = -1
    _FAKE_PATH = "fake_path"
    _FAKE_CTRL_PATH = 'fake_ctrl_path'
    _FAKE_CTRL_ADDR = 0
    _FAKE_DRIVE_ADDR = 0
    _FAKE_MOUNTED_DISK_PATH = 'fake_mounted_disk_path'
    _FAKE_VM_PATH = "fake_vm_path"
    _FAKE_VHD_PATH = "fake_vhd_path"
    _FAKE_DVD_PATH = "fake_dvd_path"
    _FAKE_VOLUME_DRIVE_PATH = "fake_volume_drive_path"
    _FAKE_VM_UUID = "04e79212-39bc-4065-933c-50f6d48a57f6"
    _FAKE_INSTANCE = {"name": _FAKE_VM_NAME,
                      "uuid": _FAKE_VM_UUID}
    _FAKE_SNAPSHOT_PATH = "fake_snapshot_path"
    _FAKE_RES_DATA = "fake_res_data"
    _FAKE_HOST_RESOURCE = "fake_host_resource"
    _FAKE_CLASS = "FakeClass"
    _FAKE_RES_PATH = "fake_res_path"
    _FAKE_RES_NAME = 'fake_res_name'
    _FAKE_ADDRESS = "fake_address"
    _FAKE_JOB_STATUS_DONE = 7
    _FAKE_JOB_STATUS_BAD = -1
    _FAKE_JOB_DESCRIPTION = "fake_job_description"
    _FAKE_ERROR = "fake_error"
    _FAKE_ELAPSED_TIME = 0
    _CONCRETE_JOB = "Msvm_ConcreteJob"
    _FAKE_DYNAMIC_MEMORY_RATIO = 1.0

    _FAKE_SUMMARY_INFO = {'NumberOfProcessors': 4,
                          'EnabledState': 2,
                          'MemoryUsage': 2,
                          'UpTime': 1}

    _DEFINE_SYSTEM = 'DefineVirtualSystem'
    _DESTROY_SYSTEM = 'DestroyVirtualSystem'
    _DESTROY_SNAPSHOT = 'RemoveVirtualSystemSnapshot'
    _ADD_RESOURCE = 'AddVirtualSystemResources'
    _REMOVE_RESOURCE = 'RemoveVirtualSystemResources'
    _SETTING_TYPE = 'SettingType'
    _VM_GEN = constants.VM_GEN_1

    _VIRTUAL_SYSTEM_TYPE_REALIZED = 3

    def setUp(self):
        self._vmutils = vmutils.VMUtils()
        self._vmutils._conn = mock.MagicMock()

        super(VMUtilsTestCase, self).setUp()

    def test_vs_man_svc(self):
        expected = self._vmutils._conn.Msvm_VirtualSystemManagementService()[0]
        self.assertEqual(expected, self._vmutils._vs_man_svc)

    def test_vs_man_svc_cached(self):
        self._vmutils._vs_man_svc_attr = mock.sentinel.fake_svc
        self.assertEqual(mock.sentinel.fake_svc, self._vmutils._vs_man_svc)

    def test_enable_vm_metrics_collection(self):
        self.assertRaises(NotImplementedError,
                          self._vmutils.enable_vm_metrics_collection,
                          self._FAKE_VM_NAME)

    def test_get_vm_summary_info(self):
        self._lookup_vm()

        mock_summary = mock.MagicMock()
        mock_svc = self._vmutils._vs_man_svc
        mock_svc.GetSummaryInformation.return_value = (self._FAKE_RET_VAL,
                                                       [mock_summary])

        for key, val in six.iteritems(self._FAKE_SUMMARY_INFO):
            setattr(mock_summary, key, val)

        summary = self._vmutils.get_vm_summary_info(self._FAKE_VM_NAME)
        self.assertEqual(self._FAKE_SUMMARY_INFO, summary)

    def _lookup_vm(self):
        mock_vm = mock.MagicMock()
        self._vmutils._lookup_vm_check = mock.MagicMock(
            return_value=mock_vm)
        mock_vm.path_.return_value = self._FAKE_VM_PATH
        return mock_vm

    def test_lookup_vm_ok(self):
        mock_vm = mock.MagicMock()
        self._vmutils._conn.Msvm_ComputerSystem.return_value = [mock_vm]
        vm = self._vmutils._lookup_vm_check(self._FAKE_VM_NAME)
        self.assertEqual(mock_vm, vm)

    def test_lookup_vm_multiple(self):
        mockvm = mock.MagicMock()
        self._vmutils._conn.Msvm_ComputerSystem.return_value = [mockvm, mockvm]
        self.assertRaises(vmutils.HyperVException,
                          self._vmutils._lookup_vm_check,
                          self._FAKE_VM_NAME)

    def test_lookup_vm_none(self):
        self._vmutils._conn.Msvm_ComputerSystem.return_value = []
        self.assertRaises(exception.InstanceNotFound,
                          self._vmutils._lookup_vm_check,
                          self._FAKE_VM_NAME)

    def test_set_vm_memory_static(self):
        self._test_set_vm_memory_dynamic(1.0)

    def test_set_vm_memory_dynamic(self):
        self._test_set_vm_memory_dynamic(2.0)

    def _test_set_vm_memory_dynamic(self, dynamic_memory_ratio,
                                    mem_per_numa_node=None):
        mock_vm = self._lookup_vm()

        mock_s = self._vmutils._conn.Msvm_VirtualSystemSettingData()[0]
        mock_s.SystemType = 3

        mock_vmsetting = mock.MagicMock()
        mock_vmsetting.associators.return_value = [mock_s]

        self._vmutils._modify_virt_resource = mock.MagicMock()

        self._vmutils._set_vm_memory(mock_vm, mock_vmsetting,
                                     self._FAKE_MEMORY_MB,
                                     mem_per_numa_node,
                                     dynamic_memory_ratio)

        self._vmutils._modify_virt_resource.assert_called_with(
            mock_s, self._FAKE_VM_PATH)

        if mem_per_numa_node:
            self.assertEqual(mem_per_numa_node,
                             mock_s.MaxMemoryBlocksPerNumaNode)
        if dynamic_memory_ratio > 1:
            self.assertTrue(mock_s.DynamicMemoryEnabled)
        else:
            self.assertFalse(mock_s.DynamicMemoryEnabled)

    def test_set_vm_vcpus(self):
        self._check_set_vm_vcpus()

    def test_set_vm_vcpus_per_vnuma_node(self):
        self._check_set_vm_vcpus(vcpus_per_numa_node=1)

    def _check_set_vm_vcpus(self, vcpus_per_numa_node=None):
        mock_vm = self._lookup_vm()

        procsetting = mock.MagicMock()
        mock_vmsetting = mock.MagicMock()
        mock_vmsetting.associators.return_value = [procsetting]

        self._vmutils._modify_virt_resource = mock.MagicMock()

        self._vmutils._set_vm_vcpus(mock_vm, mock_vmsetting,
                                    self._FAKE_VCPUS_NUM,
                                    vcpus_per_numa_node,
                                    limit_cpu_features=False)

        self._vmutils._modify_virt_resource.assert_called_once_with(
            procsetting, self._FAKE_VM_PATH)
        if vcpus_per_numa_node:
            self.assertEqual(vcpus_per_numa_node,
                             procsetting.MaxProcessorsPerNumaNode)

    def test_soft_shutdown_vm(self):
        mock_vm = self._lookup_vm()
        mock_shutdown = mock.MagicMock()
        mock_shutdown.InitiateShutdown.return_value = (self._FAKE_RET_VAL, )
        mock_vm.associators.return_value = [mock_shutdown]

        with mock.patch.object(self._vmutils, 'check_ret_val') as mock_check:
            self._vmutils.soft_shutdown_vm(self._FAKE_VM_NAME)

            mock_shutdown.InitiateShutdown.assert_called_once_with(
                Force=False, Reason=mock.ANY)
            mock_check.assert_called_once_with(self._FAKE_RET_VAL, None)

    def test_soft_shutdown_vm_no_component(self):
        mock_vm = self._lookup_vm()
        mock_vm.associators.return_value = []

        with mock.patch.object(self._vmutils, 'check_ret_val') as mock_check:
            self._vmutils.soft_shutdown_vm(self._FAKE_VM_NAME)
            self.assertFalse(mock_check.called)

    @mock.patch('hyperv.nova.vmutils.VMUtils._get_vm_disks')
    def test_get_vm_storage_paths(self, mock_get_vm_disks):
        self._lookup_vm()
        mock_rasds = self._create_mock_disks()
        mock_get_vm_disks.return_value = ([mock_rasds[0]], [mock_rasds[1]])

        storage = self._vmutils.get_vm_storage_paths(self._FAKE_VM_NAME)
        (disk_files, volume_drives) = storage

        self.assertEqual([self._FAKE_VHD_PATH], disk_files)
        self.assertEqual([self._FAKE_VOLUME_DRIVE_PATH], volume_drives)

    def test_get_vm_disks(self):
        mock_vm = self._lookup_vm()
        mock_vmsettings = [mock.MagicMock()]
        mock_vm.associators.return_value = mock_vmsettings

        mock_rasds = self._create_mock_disks()
        mock_vmsettings[0].associators.return_value = mock_rasds

        (disks, volumes) = self._vmutils._get_vm_disks(mock_vm)

        mock_vm.associators.assert_called_with(
            wmi_result_class=self._vmutils._VIRTUAL_SYSTEM_SETTING_DATA_CLASS)
        mock_vmsettings[0].associators.assert_called_with(
            wmi_result_class=self._vmutils._RESOURCE_ALLOC_SETTING_DATA_CLASS)
        self.assertEqual([mock_rasds[0]], disks)
        self.assertEqual([mock_rasds[1]], volumes)

    def _create_mock_disks(self):
        mock_rasd1 = mock.MagicMock()
        mock_rasd1.ResourceSubType = self._vmutils._HARD_DISK_RES_SUB_TYPE
        mock_rasd1.HostResource = [self._FAKE_VHD_PATH]
        mock_rasd1.Connection = [self._FAKE_VHD_PATH]
        mock_rasd1.Parent = self._FAKE_CTRL_PATH
        mock_rasd1.Address = self._FAKE_ADDRESS
        mock_rasd1.HostResource = [self._FAKE_VHD_PATH]

        mock_rasd2 = mock.MagicMock()
        mock_rasd2.ResourceSubType = self._vmutils._PHYS_DISK_RES_SUB_TYPE
        mock_rasd2.HostResource = [self._FAKE_VOLUME_DRIVE_PATH]

        return [mock_rasd1, mock_rasd2]

    @mock.patch.object(vmutils.VMUtils, '_set_vm_vcpus')
    @mock.patch.object(vmutils.VMUtils, '_set_vm_memory')
    def test_update_vm(self, mock_set_mem, mock_set_vcpus):
        mock_vm = self._lookup_vm()

        with mock.patch.object(self._vmutils,
                               '_get_vm_setting_data') as mock_get_vmsd:
            vmsettings = mock_get_vmsd.return_value

            self._vmutils.update_vm(
                mock.sentinel.vm_name, mock.sentinel.memory_mb,
                mock.sentinel.memory_per_numa, mock.sentinel.vcpus_num,
                mock.sentinel.vcpus_per_numa, mock.sentinel.limit_cpu_features,
                mock.sentinel.dynamic_mem_ratio)

            mock_set_mem.assert_called_once_with(
                mock_vm, vmsettings, mock.sentinel.memory_mb,
                mock.sentinel.memory_per_numa, mock.sentinel.dynamic_mem_ratio)
            mock_set_vcpus.assert_called_once_with(
                mock_vm, vmsettings, mock.sentinel.vcpus_num,
                mock.sentinel.vcpus_per_numa, mock.sentinel.limit_cpu_features)

    def test_create_vm(self):
        with mock.patch.object(self._vmutils,
                               '_create_vm_obj') as mock_create_vm_obj:
            self._vmutils.create_vm(self._FAKE_VM_NAME,
                                    mock.sentinel.vnuma_enabled,
                                    self._VM_GEN,
                                    mock.sentinel.instance_path)

            mock_create_vm_obj.assert_called_once_with(
                self._FAKE_VM_NAME, mock.sentinel.vnuma_enabled,
                self._VM_GEN, mock.sentinel.instance_path, None)

    def test_get_vm_scsi_controller(self):
        self._prepare_get_vm_controller(self._vmutils._SCSI_CTRL_RES_SUB_TYPE)
        path = self._vmutils.get_vm_scsi_controller(self._FAKE_VM_NAME)
        self.assertEqual(self._FAKE_RES_PATH, path)

    @mock.patch("hyperv.nova.vmutils.VMUtils.get_attached_disks")
    def test_get_free_controller_slot(self, mock_get_attached_disks):
        with mock.patch.object(self._vmutils,
                               '_get_disk_resource_address') as mock_get_addr:
            mock_get_addr.return_value = 3
            mock_get_attached_disks.return_value = [mock.sentinel.disk]

            response = self._vmutils.get_free_controller_slot(
                self._FAKE_CTRL_PATH)

            mock_get_attached_disks.assert_called_once_with(
                self._FAKE_CTRL_PATH)

            self.assertEqual(response, 0)

    def test_get_free_controller_slot_exception(self):
        mock_get_address = mock.Mock()
        mock_get_address.side_effect = list(range(
            constants.SCSI_CONTROLLER_SLOTS_NUMBER))

        mock_get_attached_disks = mock.Mock()
        mock_get_attached_disks.return_value = (
            [mock.sentinel.drive] * constants.SCSI_CONTROLLER_SLOTS_NUMBER)

        with mock.patch.multiple(self._vmutils,
                                 get_attached_disks=mock_get_attached_disks,
                                 _get_disk_resource_address=mock_get_address):
            self.assertRaises(vmutils.HyperVException,
                              self._vmutils.get_free_controller_slot,
                              mock.sentinel.scsi_controller_path)

    def test_get_vm_ide_controller(self):
        self._prepare_get_vm_controller(self._vmutils._IDE_CTRL_RES_SUB_TYPE)
        path = self._vmutils.get_vm_ide_controller(self._FAKE_VM_NAME,
                                                   self._FAKE_ADDRESS)
        self.assertEqual(self._FAKE_RES_PATH, path)

    def test_get_vm_ide_controller_none(self):
        self._prepare_get_vm_controller(self._vmutils._IDE_CTRL_RES_SUB_TYPE)
        path = self._vmutils.get_vm_ide_controller(
            mock.sentinel.FAKE_VM_NAME, mock.sentinel.FAKE_NOT_FOUND_ADDR)
        self.assertNotEqual(self._FAKE_RES_PATH, path)

    def _prepare_get_vm_controller(self, resource_sub_type):
        mock_vm = self._lookup_vm()
        mock_vm_settings = mock.MagicMock()
        mock_rasds = mock.MagicMock()
        mock_rasds.path_.return_value = self._FAKE_RES_PATH
        mock_rasds.ResourceSubType = resource_sub_type
        mock_rasds.Address = self._FAKE_ADDRESS
        mock_vm_settings.associators.return_value = [mock_rasds]
        mock_vm.associators.return_value = [mock_vm_settings]

    def _prepare_resources(self, mock_path, mock_subtype, mock_vm_settings):
        mock_rasds = mock_vm_settings.associators.return_value[0]
        mock_rasds.path_.return_value = mock_path
        mock_rasds.ResourceSubType = mock_subtype
        return mock_rasds

    @mock.patch("hyperv.nova.vmutils.VMUtils.get_free_controller_slot")
    @mock.patch("hyperv.nova.vmutils.VMUtils._get_vm_scsi_controller")
    def test_attach_scsi_drive(self, mock_get_vm_scsi_controller,
                               mock_get_free_controller_slot):
        mock_vm = self._lookup_vm()
        mock_get_vm_scsi_controller.return_value = self._FAKE_CTRL_PATH
        mock_get_free_controller_slot.return_value = self._FAKE_DRIVE_ADDR

        with mock.patch.object(self._vmutils,
                               'attach_drive') as mock_attach_drive:
            self._vmutils.attach_scsi_drive(mock_vm, self._FAKE_PATH,
                                            constants.DISK)

            mock_get_vm_scsi_controller.assert_called_once_with(mock_vm)
            mock_get_free_controller_slot.assert_called_once_with(
                self._FAKE_CTRL_PATH)
            mock_attach_drive.assert_called_once_with(
                mock_vm, self._FAKE_PATH, self._FAKE_CTRL_PATH,
                self._FAKE_DRIVE_ADDR, constants.DISK)

    @mock.patch.object(vmutils.VMUtils, '_get_new_resource_setting_data')
    @mock.patch.object(vmutils.VMUtils, '_get_vm_ide_controller')
    def test_attach_ide_drive(self, mock_get_ide_ctrl, mock_get_new_rsd):
        mock_vm = self._lookup_vm()
        mock_rsd = mock_get_new_rsd.return_value

        with mock.patch.object(self._vmutils,
                               '_add_virt_resource') as mock_add_virt_res:
            self._vmutils.attach_ide_drive(self._FAKE_VM_NAME,
                                           self._FAKE_CTRL_PATH,
                                           self._FAKE_CTRL_ADDR,
                                           self._FAKE_DRIVE_ADDR)

            mock_add_virt_res.assert_called_with(mock_rsd,
                                                 mock_vm.path_.return_value)

        mock_get_ide_ctrl.assert_called_with(mock_vm, self._FAKE_CTRL_ADDR)
        self.assertTrue(mock_get_new_rsd.called)

    @mock.patch.object(vmutils.VMUtils, '_get_new_resource_setting_data')
    def test_create_scsi_controller(self, mock_get_new_rsd):
        mock_vm = self._lookup_vm()
        with mock.patch.object(self._vmutils,
                               '_add_virt_resource') as mock_add_virt_res:
            self._vmutils.create_scsi_controller(self._FAKE_VM_NAME)

            mock_add_virt_res.assert_called_with(mock_get_new_rsd.return_value,
                                                 mock_vm.path_.return_value)

    @mock.patch.object(vmutils.VMUtils, '_get_new_resource_setting_data')
    def test_attach_volume_to_controller(self, mock_get_new_rsd):
        mock_vm = self._lookup_vm()
        with mock.patch.object(self._vmutils,
                               '_add_virt_resource') as mock_add_virt_res:
            self._vmutils.attach_volume_to_controller(
                self._FAKE_VM_NAME, self._FAKE_CTRL_PATH, self._FAKE_CTRL_ADDR,
                self._FAKE_MOUNTED_DISK_PATH)

            mock_add_virt_res.assert_called_with(mock_get_new_rsd.return_value,
                                                 mock_vm.path_.return_value)

    @mock.patch.object(vmutils.VMUtils, '_modify_virt_resource')
    @mock.patch.object(vmutils.VMUtils, '_get_nic_data_by_name')
    def test_set_nic_connection(self, mock_get_nic_conn, mock_modify_virt_res):
        self._lookup_vm()
        mock_nic = mock_get_nic_conn.return_value
        self._vmutils.set_nic_connection(self._FAKE_VM_NAME, None, None)

        mock_modify_virt_res.assert_called_with(mock_nic, self._FAKE_VM_PATH)

    @mock.patch.object(vmutils.VMUtils, '_get_new_setting_data')
    def test_create_nic(self, mock_get_new_virt_res):
        self._lookup_vm()
        mock_nic = mock_get_new_virt_res.return_value

        with mock.patch.object(self._vmutils,
                               '_add_virt_resource') as mock_add_virt_res:
            self._vmutils.create_nic(
                self._FAKE_VM_NAME, self._FAKE_RES_NAME, self._FAKE_ADDRESS)

            mock_add_virt_res.assert_called_with(mock_nic, self._FAKE_VM_PATH)

    @mock.patch.object(vmutils.VMUtils, '_get_nic_data_by_name')
    def test_destroy_nic(self, mock_get_nic_data_by_name):
        self._lookup_vm()
        fake_nic_data = mock_get_nic_data_by_name.return_value

        with mock.patch.object(self._vmutils,
                               '_remove_virt_resource') as mock_rem_virt_res:
            self._vmutils.destroy_nic(self._FAKE_VM_NAME,
                                      mock.sentinel.FAKE_NIC_NAME)
            mock_rem_virt_res.assert_called_once_with(fake_nic_data,
                                                      self._FAKE_VM_PATH)

    def test_set_vm_state(self):
        mock_vm = self._lookup_vm()
        mock_vm.RequestStateChange.return_value = (
            self._FAKE_JOB_PATH, self._FAKE_RET_VAL)

        self._vmutils.set_vm_state(self._FAKE_VM_NAME,
                                   constants.HYPERV_VM_STATE_ENABLED)
        mock_vm.RequestStateChange.assert_called_with(
            constants.HYPERV_VM_STATE_ENABLED)

    def test_destroy_vm(self):
        self._lookup_vm()

        mock_svc = self._vmutils._vs_man_svc
        getattr(mock_svc, self._DESTROY_SYSTEM).return_value = (
            self._FAKE_JOB_PATH, self._FAKE_RET_VAL)

        self._vmutils.destroy_vm(self._FAKE_VM_NAME)

        getattr(mock_svc, self._DESTROY_SYSTEM).assert_called_with(
            self._FAKE_VM_PATH)

    @mock.patch.object(vmutils.VMUtils, '_wait_for_job')
    def test_check_ret_val_ok(self, mock_wait_for_job):
        self._vmutils.check_ret_val(constants.WMI_JOB_STATUS_STARTED,
                                    self._FAKE_JOB_PATH)
        mock_wait_for_job.assert_called_once_with(self._FAKE_JOB_PATH)

    def test_check_ret_val_exception(self):
        self.assertRaises(vmutils.HyperVException,
                          self._vmutils.check_ret_val,
                          self._FAKE_RET_VAL_BAD,
                          self._FAKE_JOB_PATH)

    def test_wait_for_job_done(self):
        mockjob = self._prepare_wait_for_job(constants.WMI_JOB_STATE_COMPLETED)
        job = self._vmutils._wait_for_job(self._FAKE_JOB_PATH)
        self.assertEqual(mockjob, job)

    def test_wait_for_job_killed(self):
        mockjob = self._prepare_wait_for_job(constants.JOB_STATE_KILLED)
        job = self._vmutils._wait_for_job(self._FAKE_JOB_PATH)
        self.assertEqual(mockjob, job)

    def test_wait_for_job_exception_concrete_job(self):
        mock_job = self._prepare_wait_for_job()
        mock_job.path.return_value.Class = self._CONCRETE_JOB
        self.assertRaises(vmutils.HyperVException,
                          self._vmutils._wait_for_job,
                          self._FAKE_JOB_PATH)

    def test_wait_for_job_exception_with_error(self):
        mock_job = self._prepare_wait_for_job()
        mock_job.GetError.return_value = (self._FAKE_ERROR, self._FAKE_RET_VAL)
        self.assertRaises(vmutils.HyperVException,
                          self._vmutils._wait_for_job,
                          self._FAKE_JOB_PATH)

    def test_wait_for_job_exception_no_error(self):
        mock_job = self._prepare_wait_for_job()
        mock_job.GetError.return_value = (None, None)
        self.assertRaises(vmutils.HyperVException,
                          self._vmutils._wait_for_job,
                          self._FAKE_JOB_PATH)

    def _prepare_wait_for_job(self, state=_FAKE_JOB_STATUS_BAD):
        mock_job = mock.MagicMock()
        mock_job.JobState = state
        mock_job.Description = self._FAKE_JOB_DESCRIPTION
        mock_job.ElapsedTime = self._FAKE_ELAPSED_TIME

        self._vmutils._get_wmi_obj = mock.MagicMock(return_value=mock_job)
        return mock_job

    def test_add_virt_resource(self):
        mock_svc = self._vmutils._vs_man_svc
        getattr(mock_svc, self._ADD_RESOURCE).return_value = (
            self._FAKE_JOB_PATH, mock.MagicMock(), self._FAKE_RET_VAL)
        mock_res_setting_data = mock.MagicMock()
        mock_res_setting_data.GetText_.return_value = self._FAKE_RES_DATA

        self._vmutils._add_virt_resource(mock_res_setting_data,
                                         self._FAKE_VM_PATH)
        self._assert_add_resources(mock_svc)

    def test_modify_virt_resource(self):
        side_effect = [(self._FAKE_JOB_PATH, self._FAKE_RET_VAL)]
        self._check_modify_virt_resource_max_retries(side_effect=side_effect)

    def test_modify_virt_resource_max_retries_exception(self):
        side_effect = vmutils.HyperVException
        self._check_modify_virt_resource_max_retries(
            side_effect=side_effect, num_calls=6, expected_fail=True)

    def test_modify_virt_resource_max_retries(self):
        side_effect = [vmutils.HyperVException] * 5 + [(self._FAKE_JOB_PATH,
                                                        self._FAKE_RET_VAL)]
        self._check_modify_virt_resource_max_retries(side_effect=side_effect,
                                                     num_calls=5)

    @mock.patch('time.sleep')
    def _check_modify_virt_resource_max_retries(
            self, mock_sleep, side_effect, num_calls=1, expected_fail=False):
        mock_svc = self._vmutils._vs_man_svc
        mock_svc.ModifyVirtualSystemResources.side_effect = side_effect
        mock_res_setting_data = mock.MagicMock()
        mock_res_setting_data.GetText_.return_value = mock.sentinel.res_data

        if expected_fail:
            self.assertRaises(vmutils.HyperVException,
                              self._vmutils._modify_virt_resource,
                              mock_res_setting_data, self._FAKE_VM_PATH)
        else:
            self._vmutils._modify_virt_resource(mock_res_setting_data,
                                                self._FAKE_VM_PATH)

        mock_calls = [mock.call(ResourceSettingData=[mock.sentinel.res_data],
                                ComputerSystem=self._FAKE_VM_PATH)] * num_calls
        mock_svc.ModifyVirtualSystemResources.has_calls(mock_calls)
        mock_sleep.has_calls(mock.call(1) * num_calls)

    def test_remove_virt_resource(self):
        mock_svc = self._vmutils._vs_man_svc
        getattr(mock_svc, self._REMOVE_RESOURCE).return_value = (
            self._FAKE_JOB_PATH, self._FAKE_RET_VAL)
        mock_res_setting_data = mock.MagicMock()
        mock_res_setting_data.path_.return_value = self._FAKE_RES_PATH

        self._vmutils._remove_virt_resource(mock_res_setting_data,
                                            self._FAKE_VM_PATH)
        self._assert_remove_resources(mock_svc)

    def test_set_disk_host_resource(self):
        self._lookup_vm()
        mock_rasds = self._create_mock_disks()

        self._vmutils._get_vm_disks = mock.MagicMock(
            return_value=([mock_rasds[0]], [mock_rasds[1]]))
        self._vmutils._modify_virt_resource = mock.MagicMock()
        self._vmutils._get_disk_resource_address = mock.MagicMock(
            return_value=self._FAKE_ADDRESS)

        self._vmutils.set_disk_host_resource(
            self._FAKE_VM_NAME,
            self._FAKE_CTRL_PATH,
            self._FAKE_ADDRESS,
            mock.sentinel.fake_new_mounted_disk_path)
        self._vmutils._get_disk_resource_address.assert_called_with(
            mock_rasds[0])
        self._vmutils._modify_virt_resource.assert_called_with(
            mock_rasds[0], self._FAKE_VM_PATH)
        self.assertEqual(
            mock.sentinel.fake_new_mounted_disk_path,
            mock_rasds[0].HostResource[0])

    @mock.patch.object(vmutils, 'wmi', create=True)
    @mock.patch.object(vmutils.VMUtils, 'check_ret_val')
    def test_take_vm_snapshot(self, mock_check_ret_val, mock_wmi):
        self._lookup_vm()

        mock_svc = self._get_snapshot_service()
        mock_svc.CreateVirtualSystemSnapshot.return_value = (
            self._FAKE_JOB_PATH, self._FAKE_RET_VAL, mock.MagicMock())

        self._vmutils.take_vm_snapshot(self._FAKE_VM_NAME)

        mock_svc.CreateVirtualSystemSnapshot.assert_called_with(
            self._FAKE_VM_PATH)

        mock_check_ret_val.assert_called_once_with(self._FAKE_RET_VAL,
                                                   self._FAKE_JOB_PATH)

    def test_remove_vm_snapshot(self):
        mock_svc = self._get_snapshot_service()
        getattr(mock_svc, self._DESTROY_SNAPSHOT).return_value = (
            self._FAKE_JOB_PATH, self._FAKE_RET_VAL)

        self._vmutils.remove_vm_snapshot(self._FAKE_SNAPSHOT_PATH)
        getattr(mock_svc, self._DESTROY_SNAPSHOT).assert_called_with(
            self._FAKE_SNAPSHOT_PATH)

    @mock.patch.object(vmutils.VMUtils,
                       '_get_mounted_disk_resource_from_path')
    def test_is_disk_attached(self, mock_get_mounted_disk_from_path):
        is_physical = True

        is_attached = self._vmutils.is_disk_attached(mock.sentinel.vm_name,
                                                     mock.sentinel.disk_path,
                                                     is_physical=is_physical)

        self.assertTrue(is_attached)
        mock_get_mounted_disk_from_path.assert_called_once_with(
            mock.sentinel.disk_path, is_physical)

    def test_detach_vm_disk(self):
        self._lookup_vm()
        mock_disk = self._prepare_mock_disk()

        with mock.patch.object(self._vmutils,
                               '_remove_virt_resource') as mock_rm_virt_res:
            self._vmutils.detach_vm_disk(self._FAKE_VM_NAME,
                                         self._FAKE_HOST_RESOURCE)

            mock_rm_virt_res.assert_called_with(mock_disk, self._FAKE_VM_PATH)

    def _test_get_mounted_disk_resource_from_path(self, is_physical):
        mock_disk_1 = mock.MagicMock()
        mock_disk_2 = mock.MagicMock()
        conn_attr = (self._vmutils._PHYS_DISK_CONNECTION_ATTR if is_physical
                     else self._vmutils._VIRT_DISK_CONNECTION_ATTR)
        setattr(mock_disk_2, conn_attr, [self._FAKE_MOUNTED_DISK_PATH])
        self._vmutils._conn.query.return_value = [mock_disk_1, mock_disk_2]

        mounted_disk = self._vmutils._get_mounted_disk_resource_from_path(
            self._FAKE_MOUNTED_DISK_PATH, is_physical)

        self.assertEqual(mock_disk_2, mounted_disk)

    def test_get_physical_mounted_disk_resource_from_path(self):
        self._test_get_mounted_disk_resource_from_path(is_physical=True)

    def test_get_virtual_mounted_disk_resource_from_path(self):
        self._test_get_mounted_disk_resource_from_path(is_physical=False)

    def test_get_controller_volume_paths(self):
        self._prepare_mock_disk()
        mock_disks = {self._FAKE_RES_PATH: self._FAKE_HOST_RESOURCE}
        disks = self._vmutils.get_controller_volume_paths(self._FAKE_RES_PATH)
        self.assertEqual(mock_disks, disks)

    def _prepare_mock_disk(self):
        mock_disk = mock.MagicMock()
        mock_disk.HostResource = [self._FAKE_HOST_RESOURCE]
        mock_disk.path.return_value.RelPath = self._FAKE_RES_PATH
        mock_disk.ResourceSubType = self._vmutils._HARD_DISK_RES_SUB_TYPE
        self._vmutils._conn.query.return_value = [mock_disk]

        return mock_disk

    def _get_snapshot_service(self):
        return self._vmutils._vs_man_svc

    def _assert_add_resources(self, mock_svc):
        getattr(mock_svc, self._ADD_RESOURCE).assert_called_with(
            [self._FAKE_RES_DATA], self._FAKE_VM_PATH)

    def _assert_remove_resources(self, mock_svc):
        getattr(mock_svc, self._REMOVE_RESOURCE).assert_called_with(
            [self._FAKE_RES_PATH], self._FAKE_VM_PATH)

    def test_get_active_instances(self):
        fake_vm = mock.MagicMock()

        type(fake_vm).ElementName = mock.PropertyMock(
            side_effect=['active_vm', 'inactive_vm'])
        type(fake_vm).EnabledState = mock.PropertyMock(
            side_effect=[constants.HYPERV_VM_STATE_ENABLED,
                         constants.HYPERV_VM_STATE_DISABLED])
        self._vmutils.list_instances = mock.MagicMock(
            return_value=[mock.sentinel.fake_vm_name] * 2)
        self._vmutils._lookup_vm = mock.MagicMock(side_effect=[fake_vm] * 2)
        active_instances = self._vmutils.get_active_instances()

        self.assertEqual(['active_vm'], active_instances)

    def test_get_vm_serial_ports(self):
        mock_vm = self._lookup_vm()
        mock_vmsettings = [mock.MagicMock()]
        mock_vm.associators.return_value = mock_vmsettings

        fake_serial_port = mock.MagicMock()
        fake_serial_port.ResourceSubType = (
            self._vmutils._SERIAL_PORT_RES_SUB_TYPE)

        mock_rasds = [fake_serial_port]
        mock_vmsettings[0].associators.return_value = mock_rasds

        ret_val = self._vmutils._get_vm_serial_ports(mock_vm)

        mock_vmsettings[0].associators.assert_called_once_with(
            wmi_result_class=self._vmutils._SERIAL_PORT_SETTING_DATA_CLASS)
        self.assertEqual(mock_rasds, ret_val)

    def test_set_vm_serial_port_conn(self):
        mock_vm = self._lookup_vm()
        mock_com_1 = mock.Mock()
        mock_com_2 = mock.Mock()

        self._vmutils._get_vm_serial_ports = mock.Mock(
            return_value=[mock_com_1, mock_com_2])
        self._vmutils._modify_virt_resource = mock.Mock()

        self._vmutils.set_vm_serial_port_connection(
            mock.sentinel.vm_name,
            port_number=1,
            pipe_path=mock.sentinel.pipe_path)

        self.assertEqual([mock.sentinel.pipe_path], mock_com_1.Connection)
        self._vmutils._modify_virt_resource.assert_called_once_with(
            mock_com_1, mock_vm.path_())

    def test_get_serial_port_conns(self):
        self._lookup_vm()

        mock_com_1 = mock.Mock()
        mock_com_1.Connection = []

        mock_com_2 = mock.Mock()
        mock_com_2.Connection = [mock.sentinel.pipe_path]

        self._vmutils._get_vm_serial_ports = mock.Mock(
            return_value=[mock_com_1, mock_com_2])

        ret_val = self._vmutils.get_vm_serial_port_connections(
            mock.sentinel.vm_name)
        expected_ret_val = [mock.sentinel.pipe_path]

        self.assertEqual(expected_ret_val, ret_val)

    def test_list_instance_notes(self):
        vs = mock.MagicMock()
        attrs = {'ElementName': 'fake_name',
                 'Notes': '4f54fb69-d3a2-45b7-bb9b-b6e6b3d893b3'}
        vs.configure_mock(**attrs)
        vs2 = mock.MagicMock(ElementName='fake_name2', Notes=None)
        self._vmutils._conn.Msvm_VirtualSystemSettingData.return_value = [vs,
                                                                          vs2]
        response = self._vmutils.list_instance_notes()

        self.assertEqual([(attrs['ElementName'], [attrs['Notes']])], response)
        self._vmutils._conn.Msvm_VirtualSystemSettingData.assert_called_with(
            ['ElementName', 'Notes'],
            SettingType=self._vmutils._VIRTUAL_SYSTEM_CURRENT_SETTINGS)

    @mock.patch('hyperv.nova.vmutils.VMUtils.check_ret_val')
    def test_modify_virtual_system(self, mock_check_ret_val):
        mock_vmsetting = mock.MagicMock()
        fake_path = 'fake path'
        fake_job_path = 'fake job path'
        fake_ret_val = 'fake return value'

        mock_vs_man_svc = self._vmutils._vs_man_svc
        mock_vs_man_svc.ModifyVirtualSystem.return_value = (0, fake_job_path,
                                                            fake_ret_val)

        self._vmutils._modify_virtual_system(vm_path=fake_path,
                                             vmsetting=mock_vmsetting)

        mock_vs_man_svc.ModifyVirtualSystem.assert_called_once_with(
            ComputerSystem=fake_path,
            SystemSettingData=mock_vmsetting.GetText_(1))
        mock_check_ret_val.assert_called_once_with(fake_ret_val, fake_job_path)

    @mock.patch('hyperv.nova.vmutils.VMUtils.check_ret_val')
    @mock.patch('hyperv.nova.vmutils.VMUtils._get_wmi_obj')
    @mock.patch('hyperv.nova.vmutils.VMUtils._modify_virtual_system')
    @mock.patch('hyperv.nova.vmutils.VMUtils._get_vm_setting_data')
    def test_create_vm_obj(self, mock_get_vm_setting_data,
                           mock_modify_virtual_system,
                           mock_get_wmi_obj, mock_check_ret_val):
        mock_vs_gs_data = mock.MagicMock()
        fake_vm_path = 'fake vm path'
        fake_job_path = 'fake job path'
        fake_ret_val = 'fake return value'
        _conn = self._vmutils._conn.Msvm_VirtualSystemGlobalSettingData

        _conn.new.return_value = mock_vs_gs_data
        mock_vs_man_svc = self._vmutils._vs_man_svc
        mock_vs_man_svc.DefineVirtualSystem.return_value = (fake_vm_path,
                                                            fake_job_path,
                                                            fake_ret_val)

        response = self._vmutils._create_vm_obj(
            vm_name='fake vm', vm_gen='fake vm gen',
            notes='fake notes', vnuma_enabled=mock.sentinel.vnuma_enabled,
            instance_path=mock.sentinel.instance_path)

        _conn.new.assert_called_once_with()
        self.assertEqual(mock_vs_gs_data.ElementName, 'fake vm')
        mock_vs_man_svc.DefineVirtualSystem.assert_called_once_with(
            [], None, mock_vs_gs_data.GetText_(1))
        mock_check_ret_val.assert_called_once_with(fake_ret_val, fake_job_path)
        self.assertEqual(mock.sentinel.instance_path,
                         mock_vs_gs_data.ExternalDataRoot)
        self.assertEqual(mock.sentinel.instance_path,
                         mock_vs_gs_data.SnapshotDataRoot)

        mock_get_wmi_obj.assert_called_with(fake_vm_path)
        mock_get_vm_setting_data.assert_called_once_with(mock_get_wmi_obj())
        mock_modify_virtual_system.assert_called_once_with(
            fake_vm_path, mock_get_vm_setting_data())

        self.assertEqual(mock_get_vm_setting_data().Notes,
                         '\n'.join('fake notes'))
        self.assertEqual(response, mock_get_wmi_obj())

    def test_list_instances(self):
        vs = mock.MagicMock()
        attrs = {'ElementName': 'fake_name'}
        vs.configure_mock(**attrs)
        self._vmutils._conn.Msvm_VirtualSystemSettingData.return_value = [vs]
        response = self._vmutils.list_instances()

        self.assertEqual([(attrs['ElementName'])], response)
        self._vmutils._conn.Msvm_VirtualSystemSettingData.assert_called_with(
            ['ElementName'],
            SettingType=self._vmutils._VIRTUAL_SYSTEM_CURRENT_SETTINGS)

    @mock.patch.object(vmutils.VMUtils, "_clone_wmi_obj")
    def _test_check_clone_wmi_obj(self, mock_clone_wmi_obj, clone_objects):
        mock_obj = mock.MagicMock()
        self._vmutils._clone_wmi_objs = clone_objects

        response = self._vmutils._check_clone_wmi_obj(class_name="fakeClass",
                                                      obj=mock_obj)
        if not clone_objects:
            self.assertEqual(mock_obj, response)
        else:
            mock_clone_wmi_obj.assert_called_once_with("fakeClass", mock_obj)
            self.assertEqual(mock_clone_wmi_obj.return_value, response)

    def test_check_clone_wmi_obj_true(self):
        self._test_check_clone_wmi_obj(clone_objects=True)

    def test_check_clone_wmi_obj_false(self):
        self._test_check_clone_wmi_obj(clone_objects=False)

    def test_clone_wmi_obj(self):
        mock_obj = mock.MagicMock()
        mock_value = mock.MagicMock()
        mock_value.Value = mock.sentinel.fake_value
        mock_obj._properties = [mock.sentinel.property]
        mock_obj.Properties_.Item.return_value = mock_value

        response = self._vmutils._clone_wmi_obj(
            class_name="FakeClass", obj=mock_obj)

        compare = self._vmutils._conn.FakeClass.new()
        self.assertEqual(mock.sentinel.fake_value,
                         compare.Properties_.Item().Value)
        self.assertEqual(compare, response)

    def test_get_attached_disks(self):
        mock_scsi_ctrl_path = mock.MagicMock()
        expected_query = ("SELECT * FROM %(class_name)s "
                          "WHERE (ResourceSubType='%(res_sub_type)s' OR "
                          "ResourceSubType='%(res_sub_type_virt)s')"
                          " AND Parent='%(parent)s'" %
                          {"class_name":
                           self._vmutils._RESOURCE_ALLOC_SETTING_DATA_CLASS,
                           "res_sub_type":
                           self._vmutils._PHYS_DISK_RES_SUB_TYPE,
                           "res_sub_type_virt":
                           self._vmutils._DISK_DRIVE_RES_SUB_TYPE,
                           "parent":
                           mock_scsi_ctrl_path.replace("'", "''")})
        expected_disks = self._vmutils._conn.query.return_value

        ret_disks = self._vmutils.get_attached_disks(mock_scsi_ctrl_path)

        self._vmutils._conn.query.assert_called_once_with(expected_query)
        self.assertEqual(expected_disks, ret_disks)

    def _get_fake_instance_notes(self):
        return self._FAKE_VM_UUID

    def test_instance_notes(self):
        self._lookup_vm()
        mock_vm_settings = mock.Mock()
        mock_vm_settings.Notes = self._get_fake_instance_notes()
        self._vmutils._get_vm_setting_data = mock.Mock(
            return_value=mock_vm_settings)

        notes = self._vmutils._get_instance_notes(mock.sentinel.vm_name)

        self.assertEqual(notes[0], self._FAKE_VM_UUID)

    def test_get_event_wql_query(self):
        cls = self._vmutils._COMPUTER_SYSTEM_CLASS
        field = self._vmutils._VM_ENABLED_STATE_PROP
        timeframe = 10
        filtered_states = [constants.HYPERV_VM_STATE_ENABLED,
                           constants.HYPERV_VM_STATE_DISABLED]

        expected_checks = ' OR '.join(
            ["TargetInstance.%s = '%s'" % (field, state)
             for state in filtered_states])
        expected_query = (
            "SELECT %(field)s, TargetInstance "
            "FROM __InstanceModificationEvent "
            "WITHIN %(timeframe)s "
            "WHERE TargetInstance ISA '%(class)s' "
            "AND TargetInstance.%(field)s != "
            "PreviousInstance.%(field)s "
            "AND (%(checks)s)" %
                {'class': cls,
                 'field': field,
                 'timeframe': timeframe,
                 'checks': expected_checks})

        query = self._vmutils._get_event_wql_query(
            cls=cls, field=field, timeframe=timeframe,
            filtered_states=filtered_states)
        self.assertEqual(expected_query, query)

    def test_get_vm_power_state_change_listener(self):
        with mock.patch.object(self._vmutils,
                               '_get_event_wql_query') as mock_get_query:
            listener = self._vmutils.get_vm_power_state_change_listener(
                mock.sentinel.timeframe,
                mock.sentinel.filtered_states)

            mock_get_query.assert_called_once_with(
                cls=self._vmutils._COMPUTER_SYSTEM_CLASS,
                field=self._vmutils._VM_ENABLED_STATE_PROP,
                timeframe=mock.sentinel.timeframe,
                filtered_states=mock.sentinel.filtered_states)
            watcher = self._vmutils._conn.Msvm_ComputerSystem.watch_for
            watcher.assert_called_once_with(
                raw_wql=mock_get_query.return_value,
                fields=[self._vmutils._VM_ENABLED_STATE_PROP])

            self.assertEqual(watcher.return_value, listener)

    def test_stop_vm_jobs(self):
        mock_vm = self._lookup_vm()

        mock_job1 = mock.MagicMock(Cancellable=True)
        mock_job2 = mock.MagicMock(Cancellable=True)
        mock_job3 = mock.MagicMock(Cancellable=True)

        mock_job1.JobState = 2
        mock_job2.JobState = 3
        mock_job3.JobState = constants.JOB_STATE_KILLED

        mock_vm_jobs = [mock_job1, mock_job2, mock_job3]

        mock_vm.associators.return_value = mock_vm_jobs

        self._vmutils.stop_vm_jobs(mock.sentinel.FAKE_VM_NAME)

        mock_job1.RequestStateChange.assert_called_once_with(
            self._vmutils._KILL_JOB_STATE_CHANGE_REQUEST)
        mock_job2.RequestStateChange.assert_called_once_with(
            self._vmutils._KILL_JOB_STATE_CHANGE_REQUEST)
        self.assertFalse(mock_job3.RequestStateChange.called)

    def test_is_job_completed_true(self):
        job = mock.MagicMock(JobState=constants.JOB_STATE_COMPLETED)

        self.assertTrue(self._vmutils._is_job_completed(job))

    def test_is_job_completed_false(self):
        job = mock.MagicMock(JobState=constants.WMI_JOB_STATE_RUNNING)

        self.assertFalse(self._vmutils._is_job_completed(job))

    @mock.patch.object(vmutils.VMUtils, '_get_vm_setting_data')
    @mock.patch.object(vmutils.VMUtils, '_modify_virtual_system')
    def test_set_boot_order_gen1(self, mock_modify_virt_syst,
                            mock_get_vm_setting_data):
        mock_vm = self._lookup_vm()

        mock_vssd = mock_get_vm_setting_data.return_value
        fake_dev_boot_order = [mock.sentinel.BOOT_DEV1,
                               mock.sentinel.BOOT_DEV2]

        self._vmutils._set_boot_order(mock_vm.name, fake_dev_boot_order)

        mock_modify_virt_syst.assert_called_once_with(
            mock_vm.path_.return_value, mock_vssd)
        self.assertEqual(tuple(fake_dev_boot_order), mock_vssd.BootOrder)
