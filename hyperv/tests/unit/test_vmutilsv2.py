#  Copyright 2014 Cloudbase Solutions Srl
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
from hyperv.nova import vmutilsv2
from hyperv.tests.unit import test_vmutils


class VMUtilsV2TestCase(test_vmutils.VMUtilsTestCase):
    """Unit tests for the Hyper-V VMUtilsV2 class."""

    _DEFINE_SYSTEM = 'DefineSystem'
    _DESTROY_SYSTEM = 'DestroySystem'
    _DESTROY_SNAPSHOT = 'DestroySnapshot'

    _ADD_RESOURCE = 'AddResourceSettings'
    _REMOVE_RESOURCE = 'RemoveResourceSettings'
    _SETTING_TYPE = 'VirtualSystemType'
    _VM_GEN = constants.VM_GEN_2

    _VIRTUAL_SYSTEM_TYPE_REALIZED = 'Microsoft:Hyper-V:System:Realized'
    _FAKE_MONITOR_COUNT = 1

    def setUp(self):
        super(VMUtilsV2TestCase, self).setUp()
        self._vmutils = vmutilsv2.VMUtilsV2()
        self._vmutils._conn = mock.MagicMock()
        self._vmutils._pathutils = mock.MagicMock()

    def test_list_instance_notes(self):
        vs = mock.MagicMock()
        attrs = {'ElementName': 'fake_name',
                 'Notes': ['4f54fb69-d3a2-45b7-bb9b-b6e6b3d893b3']}
        vs.configure_mock(**attrs)
        vs2 = mock.MagicMock(ElementName='fake_name2', Notes=None)
        self._vmutils._conn.Msvm_VirtualSystemSettingData.return_value = [vs,
                                                                          vs2]
        response = self._vmutils.list_instance_notes()

        self.assertEqual([(attrs['ElementName'], attrs['Notes'])], response)
        self._vmutils._conn.Msvm_VirtualSystemSettingData.assert_called_with(
            ['ElementName', 'Notes'],
            VirtualSystemType=self._vmutils._VIRTUAL_SYSTEM_TYPE_REALIZED)

    def _get_fake_instance_notes(self):
        return [self._FAKE_VM_UUID]

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
        vm = self._vmutils._lookup_vm_check(self._FAKE_VM_NAME, as_vssd=False)
        self.assertEqual(mock_vm, vm)

    def test_lookup_vm_multiple(self):
        mockvm = mock.MagicMock()
        self._vmutils._conn.Msvm_ComputerSystem.return_value = [mockvm, mockvm]
        self.assertRaises(vmutils.HyperVException,
                          self._vmutils._lookup_vm_check,
                          self._FAKE_VM_NAME,
                          as_vssd=False)

    def test_lookup_vm_none(self):
        self._vmutils._conn.Msvm_ComputerSystem.return_value = []
        self.assertRaises(exception.InstanceNotFound,
                          self._vmutils._lookup_vm_check,
                          self._FAKE_VM_NAME,
                          as_vssd=False)

    def test_lookup_vm_as_vssd(self):
        self._vmutils._conn.Msvm_VirtualSystemSettingData.return_value = [
            mock.sentinel.fake_vssd]

        vssd = self._vmutils._lookup_vm_check(self._FAKE_VM_NAME)
        self.assertEqual(mock.sentinel.fake_vssd, vssd)

    def test_set_vm_memory_static(self):
        self._test_set_vm_memory_dynamic(dynamic_memory_ratio=1.0)

    def test_set_vm_memory_dynamic(self):
        self._test_set_vm_memory_dynamic(dynamic_memory_ratio=2.0)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_associated_class')
    def _test_set_vm_memory_dynamic(self, mock_get_associated_class,
                                    dynamic_memory_ratio,
                                    mem_per_numa_node=None):
        mock_s = self._vmutils._conn.Msvm_VirtualSystemSettingData()[0]
        mock_s.SystemType = 3

        mock_get_associated_class.return_value = [mock_s]

        self._vmutils._modify_virt_resource = mock.MagicMock()

        self._vmutils._set_vm_memory(mock_s,
                                     self._FAKE_MEMORY_MB,
                                     mem_per_numa_node,
                                     dynamic_memory_ratio)

        mock_get_associated_class.assert_called_once_with(
            self._vmutils._MEMORY_SETTING_DATA_CLASS,
            mock_s)
        self._vmutils._modify_virt_resource.assert_called_with(
            mock_s, mock_s.path_.return_value)

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

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_associated_class')
    def _check_set_vm_vcpus(self, mock_get_associated_class,
                            vcpus_per_numa_node=None):
        procsetting = mock.MagicMock()
        mock_vmsetting = mock.MagicMock()
        mock_get_associated_class.return_value = [procsetting]

        self._vmutils._modify_virt_resource = mock.MagicMock()

        self._vmutils._set_vm_vcpus(mock_vmsetting,
                                    self._FAKE_VCPUS_NUM,
                                    vcpus_per_numa_node,
                                    limit_cpu_features=False)

        mock_get_associated_class.assert_called_once_with(
            self._vmutils._PROCESSOR_SETTING_DATA_CLASS,
            mock_vmsetting)
        self._vmutils._modify_virt_resource.assert_called_once_with(
            procsetting, mock_vmsetting.path_.return_value)
        if vcpus_per_numa_node:
            self.assertEqual(vcpus_per_numa_node,
                             procsetting.MaxProcessorsPerNumaNode)

    def test_modify_virt_resource(self):
        side_effect = [
            (self._FAKE_JOB_PATH, mock.MagicMock(), self._FAKE_RET_VAL)]
        self._check_modify_virt_resource_max_retries(side_effect=side_effect)

    def test_modify_virt_resource_max_retries(self):
        side_effect = [vmutils.HyperVException] * 5 + [
            (self._FAKE_JOB_PATH, mock.MagicMock(), self._FAKE_RET_VAL)]
        self._check_modify_virt_resource_max_retries(side_effect=side_effect,
                                                     num_calls=5)

    @mock.patch('time.sleep')
    def _check_modify_virt_resource_max_retries(
            self, mock_sleep, side_effect, num_calls=1, expected_fail=False):
        mock_svc = self._vmutils._vs_man_svc
        mock_svc.ModifyResourceSettings.side_effect = side_effect
        mock_res_setting_data = mock.MagicMock()
        mock_res_setting_data.GetText_.return_value = mock.sentinel.res_data

        if expected_fail:
            self.assertRaises(vmutils.HyperVException,
                              self._vmutils._modify_virt_resource,
                              mock_res_setting_data, self._FAKE_VM_PATH)
        else:
            self._vmutils._modify_virt_resource(mock_res_setting_data,
                                                self._FAKE_VM_PATH)

        mock_calls = [
            mock.call(ResourceSettings=[self._FAKE_RES_DATA])] * num_calls
        mock_svc.ModifyResourceSettings.has_calls(mock_calls)
        mock_sleep.has_calls(mock.call(1) * num_calls)

    @mock.patch.object(vmutilsv2, 'wmi', create=True)
    @mock.patch.object(vmutilsv2.VMUtilsV2, 'check_ret_val')
    def test_take_vm_snapshot(self, mock_check_ret_val, mock_wmi):
        mock_vm = self._lookup_vm()

        mock_svc = self._get_snapshot_service()
        mock_svc.CreateSnapshot.return_value = (self._FAKE_JOB_PATH,
                                                mock.MagicMock(),
                                                self._FAKE_RET_VAL)
        mock_snp_setting_data = mock.MagicMock()
        self._vmutils._conn.query.return_value = [
            mock.Mock(Dependent=mock_snp_setting_data)]

        returned_snp_path = self._vmutils.take_vm_snapshot(self._FAKE_VM_NAME)

        mock_svc.CreateSnapshot.assert_called_with(
            AffectedSystem=self._FAKE_VM_PATH,
            SnapshotType=self._vmutils._SNAPSHOT_FULL)
        expected_query = (
            "SELECT * FROM %(class_name)s "
            "WHERE Antecedent = '%(vm_path)s'"
            % {'class_name': self._vmutils._MOST_CURRENT_SNAPSHOT_CLASS,
               'vm_path': mock_vm.path_.return_value})
        self._vmutils._conn.query.assert_called_once_with(expected_query)
        mock_check_ret_val.assert_called_once_with(self._FAKE_RET_VAL,
                                                   self._FAKE_JOB_PATH)
        expected_snp_path = mock_snp_setting_data.path_.return_value
        self.assertEqual(expected_snp_path, returned_snp_path)

    @mock.patch.object(vmutilsv2.VMUtilsV2, 'get_free_controller_slot')
    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_vm_scsi_controller')
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

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_new_resource_setting_data')
    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_vm_ide_controller')
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

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_add_virt_resource')
    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_new_setting_data')
    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_nic_data_by_name')
    def test_set_nic_connection(self, mock_get_nic_data, mock_get_new_sd,
                                mock_add_virt_res):
        self._lookup_vm()
        fake_eth_port = mock_get_new_sd.return_value

        self._vmutils.set_nic_connection(self._FAKE_VM_NAME, None, None)
        mock_add_virt_res.assert_called_with(fake_eth_port, self._FAKE_VM_PATH)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_vm_disks')
    def test_enable_vm_metrics_collection(self, mock_get_vm_disks):
        self._lookup_vm()
        mock_svc = self._vmutils._conn.Msvm_MetricService()[0]

        metric_def = mock.MagicMock()
        mock_disk = mock.MagicMock()
        mock_disk.path_.return_value = self._FAKE_RES_PATH
        mock_get_vm_disks.return_value = ([mock_disk], [mock_disk])

        fake_metric_def_paths = ['fake_0', 'fake_0', None]
        fake_metric_resource_paths = [self._FAKE_VM_PATH,
                                      self._FAKE_VM_PATH,
                                      self._FAKE_RES_PATH]

        metric_def.path_.side_effect = fake_metric_def_paths
        self._vmutils._conn.CIM_BaseMetricDefinition.return_value = [
            metric_def]

        self._vmutils.enable_vm_metrics_collection(self._FAKE_VM_NAME)

        calls = [mock.call(Name=def_name)
                 for def_name in [self._vmutils._METRIC_AGGR_CPU_AVG,
                                  self._vmutils._METRIC_AGGR_MEMORY_AVG]]
        self._vmutils._conn.CIM_BaseMetricDefinition.assert_has_calls(calls)

        calls = []
        for i in range(len(fake_metric_def_paths)):
            calls.append(mock.call(
                Subject=fake_metric_resource_paths[i],
                Definition=fake_metric_def_paths[i],
                MetricCollectionEnabled=self._vmutils._METRIC_ENABLED))

        mock_svc.ControlMetrics.assert_has_calls(calls, any_order=True)

    def _get_snapshot_service(self):
        return self._vmutils._conn.Msvm_VirtualSystemSnapshotService()[0]

    def _assert_add_resources(self, mock_svc):
        getattr(mock_svc, self._ADD_RESOURCE).assert_called_with(
            self._FAKE_VM_PATH, [self._FAKE_RES_DATA])

    def _assert_remove_resources(self, mock_svc):
        getattr(mock_svc, self._REMOVE_RESOURCE).assert_called_with(
            [self._FAKE_RES_PATH])

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_associated_class')
    def test_get_vm_disks(self, mock_get_associated_class):
        mock_vmsettings = self._lookup_vm()

        mock_rasds = self._create_mock_disks()
        mock_get_associated_class.return_value = mock_rasds

        (disks, volumes) = self._vmutils._get_vm_disks(mock_vmsettings)

        expected_calls = [
            mock.call(self._vmutils._STORAGE_ALLOC_SETTING_DATA_CLASS,
                      mock_vmsettings),
            mock.call(self._vmutils._RESOURCE_ALLOC_SETTING_DATA_CLASS,
                      mock_vmsettings)]

        mock_get_associated_class.assert_has_calls(expected_calls)

        self.assertEqual([mock_rasds[0]], disks)
        self.assertEqual([mock_rasds[1]], volumes)

    def test_soft_shutdown_vm(self):
        mock_vm = self._lookup_vm()
        mock_shutdown = mock.MagicMock()
        mock_shutdown.InitiateShutdown.return_value = (self._FAKE_RET_VAL, )
        self._vmutils._conn.Msvm_ShutdownComponent.return_value = [
            mock_shutdown]

        with mock.patch.object(self._vmutils, 'check_ret_val') as mock_check:
            self._vmutils.soft_shutdown_vm(self._FAKE_VM_NAME)

            mock_shutdown.InitiateShutdown.assert_called_once_with(
                Force=False, Reason=mock.ANY)
            mock_check.assert_called_once_with(self._FAKE_RET_VAL, None)
            self._vmutils._conn.Msvm_ShutdownComponent.assert_called_once_with(
                SystemName=mock_vm.Name)

    def test_soft_shutdown_vm_no_component(self):
        mock_vm = self._lookup_vm()
        self._vmutils._conn.Msvm_ShutdownComponent.return_value = []

        with mock.patch.object(self._vmutils, 'check_ret_val') as mock_check:
            self._vmutils.soft_shutdown_vm(self._FAKE_VM_NAME)
            self.assertFalse(mock_check.called)
            self._vmutils._conn.Msvm_ShutdownComponent.assert_called_once_with(
                SystemName=mock_vm.Name)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_vm_disks')
    def test_get_vm_storage_paths(self, mock_get_vm_disks):
        self._lookup_vm()
        mock_rasds = self._create_mock_disks()
        mock_get_vm_disks.return_value = ([mock_rasds[0]], [mock_rasds[1]])

        storage = self._vmutils.get_vm_storage_paths(self._FAKE_VM_NAME)
        (disk_files, volume_drives) = storage

        self.assertEqual([self._FAKE_VHD_PATH], disk_files)
        self.assertEqual([self._FAKE_VOLUME_DRIVE_PATH], volume_drives)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_set_vm_vcpus')
    @mock.patch.object(vmutilsv2.VMUtilsV2, '_set_vm_memory')
    def test_update_vm(self, mock_set_mem, mock_set_vcpus):
        mock_vm = self._lookup_vm()

        self._vmutils.update_vm(
            mock.sentinel.vm_name, mock.sentinel.memory_mb,
            mock.sentinel.memory_per_numa, mock.sentinel.vcpus_num,
            mock.sentinel.vcpus_per_numa, mock.sentinel.limit_cpu_features,
            mock.sentinel.dynamic_mem_ratio)

        mock_set_mem.assert_called_once_with(
            mock_vm, mock.sentinel.memory_mb,
            mock.sentinel.memory_per_numa, mock.sentinel.dynamic_mem_ratio)
        mock_set_vcpus.assert_called_once_with(
            mock_vm, mock.sentinel.vcpus_num,
            mock.sentinel.vcpus_per_numa, mock.sentinel.limit_cpu_features)

    @mock.patch('hyperv.nova.vmutilsv2.VMUtilsV2.check_ret_val')
    @mock.patch('hyperv.nova.vmutilsv2.VMUtilsV2._get_wmi_obj')
    def _test_create_vm_obj(self, mock_get_wmi_obj, mock_check_ret_val,
                            vnuma_enabled=True):
        mock_vs_data = mock.MagicMock()
        fake_job_path = 'fake job path'
        fake_ret_val = 'fake return value'
        fake_vm_name = 'fake_vm_name'
        conn_vssd = self._vmutils._conn.Msvm_VirtualSystemSettingData

        mock_check_ret_val.return_value = mock.sentinel.job
        conn_vssd.new.return_value = mock_vs_data
        mock_vs_man_svc = self._vmutils._vs_man_svc
        mock_vs_man_svc.DefineSystem.return_value = (fake_job_path,
                                                     mock.sentinel.vm_path,
                                                     fake_ret_val)

        self._vmutils._create_vm_obj(vm_name=fake_vm_name,
                                     vm_gen=constants.VM_GEN_2,
                                     notes='fake notes',
                                     vnuma_enabled=vnuma_enabled,
                                     instance_path=mock.sentinel.instance_path)

        conn_vssd.new.assert_called_once_with()
        self.assertEqual(mock_vs_data.ElementName, fake_vm_name)
        mock_vs_man_svc.DefineSystem.assert_called_once_with(
            ResourceSettings=[], ReferenceConfiguration=None,
            SystemSettings=mock_vs_data.GetText_(1))
        mock_check_ret_val.assert_called_once_with(fake_ret_val, fake_job_path)

        self.assertEqual(vnuma_enabled, mock_vs_data.VirtualNumaEnabled)
        self.assertEqual(self._vmutils._VIRTUAL_SYSTEM_SUBTYPE_GEN2,
                         mock_vs_data.VirtualSystemSubType)
        self.assertEqual(mock_vs_data.Notes, 'fake notes')
        self.assertEqual(mock.sentinel.instance_path,
                         mock_vs_data.ConfigurationDataRoot)
        self.assertEqual(mock.sentinel.instance_path, mock_vs_data.LogDataRoot)
        self.assertEqual(mock.sentinel.instance_path,
                         mock_vs_data.SnapshotDataRoot)
        self.assertEqual(mock.sentinel.instance_path,
                         mock_vs_data.SuspendDataRoot)
        self.assertEqual(mock.sentinel.instance_path,
                         mock_vs_data.SwapFileDataRoot)

    def test_create_vm_obj(self):
        self._test_create_vm_obj()

    def test_create_vm_obj_vnuma_disabled(self):
        self._test_create_vm_obj(vnuma_enabled=False)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_associated_class')
    def test_get_vm_serial_ports(self, mock_get_associated_class):
        mock_vmsettings = self._lookup_vm()

        fake_serial_port = mock.MagicMock()
        fake_serial_port.ResourceSubType = (
            self._vmutils._SERIAL_PORT_RES_SUB_TYPE)

        mock_rasds = [fake_serial_port]
        mock_get_associated_class.return_value = mock_rasds

        ret_val = self._vmutils._get_vm_serial_ports(mock_vmsettings)

        self.assertEqual(mock_rasds, ret_val)
        mock_get_associated_class.assert_called_once_with(
            self._vmutils._SERIAL_PORT_SETTING_DATA_CLASS,
            mock_vmsettings)

    def test_get_associated_class(self):
        self._vmutils._conn.query.return_value = mock.sentinel.assoc_class

        resulted_assoc_class = self._vmutils._get_associated_class(
            mock.sentinel.class_name,
            mock.Mock(ConfigurationID=mock.sentinel.conf_id))

        expected_query = (
            "SELECT * FROM %(class_name)s WHERE InstanceID LIKE "
            "'Microsoft:%(instance_id)s%%'" % {
                'class_name': mock.sentinel.class_name,
                'instance_id': mock.sentinel.conf_id})
        self._vmutils._conn.query.assert_called_once_with(expected_query)
        self.assertEqual(mock.sentinel.assoc_class, resulted_assoc_class)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_associated_class')
    def test_get_vm_scsi_controller(self, mock_get_associated_class):
        self._prepare_get_vm_controller(self._vmutils._SCSI_CTRL_RES_SUB_TYPE,
                                        mock_get_associated_class)
        path = self._vmutils.get_vm_scsi_controller(self._FAKE_VM_NAME)
        self.assertEqual(self._FAKE_RES_PATH, path)

    def test_list_instances(self):
        vs = mock.MagicMock()
        attrs = {'ElementName': 'fake_name'}
        vs.configure_mock(**attrs)
        self._vmutils._conn.Msvm_VirtualSystemSettingData.return_value = [vs]
        response = self._vmutils.list_instances()

        self.assertEqual([(attrs['ElementName'])], response)
        self._vmutils._conn.Msvm_VirtualSystemSettingData.assert_called_with(
            ['ElementName'],
            VirtualSystemType=self._vmutils._VIRTUAL_SYSTEM_TYPE_REALIZED)

    def test_get_attached_disks(self):
        mock_scsi_ctrl_path = mock.MagicMock()
        expected_query = ("SELECT * FROM %(class_name)s "
                          "WHERE (ResourceSubType='%(res_sub_type)s' OR "
                          "ResourceSubType='%(res_sub_type_virt)s' OR "
                          "ResourceSubType='%(res_sub_type_dvd)s') AND "
                          "Parent = '%(parent)s'" %
                          {"class_name":
                           self._vmutils._RESOURCE_ALLOC_SETTING_DATA_CLASS,
                           "res_sub_type":
                           self._vmutils._PHYS_DISK_RES_SUB_TYPE,
                           "res_sub_type_virt":
                           self._vmutils._DISK_DRIVE_RES_SUB_TYPE,
                           "res_sub_type_dvd":
                           self._vmutils._DVD_DRIVE_RES_SUB_TYPE,
                           "parent": mock_scsi_ctrl_path.replace("'", "''")})
        expected_disks = self._vmutils._conn.query.return_value

        ret_disks = self._vmutils.get_attached_disks(mock_scsi_ctrl_path)

        self._vmutils._conn.query.assert_called_once_with(expected_query)
        self.assertEqual(expected_disks, ret_disks)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_associated_class')
    def test_get_vm_dvd_disk_paths(self, mock_get_associated_class):
        self._lookup_vm()
        mock_sasd1 = mock.MagicMock(
            ResourceSubType=self._vmutils._DVD_DISK_RES_SUB_TYPE,
            HostResource=[mock.sentinel.FAKE_DVD_PATH1])
        mock_get_associated_class.return_value = [mock_sasd1]

        ret_val = self._vmutils.get_vm_dvd_disk_paths(self._FAKE_VM_NAME)
        self.assertEqual(mock.sentinel.FAKE_DVD_PATH1, ret_val[0])

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_associated_class')
    def test_get_vm_ide_controller(self, mock_get_associated_class):
        self._prepare_get_vm_controller(
            self._vmutils._IDE_CTRL_RES_SUB_TYPE,
            mock_get_associated_class)
        path = self._vmutils.get_vm_ide_controller(
            mock.sentinel.FAKE_VM_SETTINGS, self._FAKE_ADDRESS)
        self.assertEqual(self._FAKE_RES_PATH, path)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_associated_class')
    def test_get_vm_ide_controller_none(self, mock_get_associated_class):
        self._prepare_get_vm_controller(
            self._vmutils._IDE_CTRL_RES_SUB_TYPE,
            mock_get_associated_class)
        path = self._vmutils.get_vm_ide_controller(
            mock.sentinel.FAKE_VM_SETTINGS, mock.sentinel.FAKE_NOT_FOUND_ADDR)
        self.assertNotEqual(self._FAKE_RES_PATH, path)

    def _prepare_get_vm_controller(self, resource_sub_type,
                                   mock_get_associated_class):
        self._lookup_vm()
        mock_rasds = mock.MagicMock()
        mock_rasds.path_.return_value = self._FAKE_RES_PATH
        mock_rasds.ResourceSubType = resource_sub_type
        mock_rasds.Address = self._FAKE_ADDRESS
        mock_get_associated_class.return_value = [mock_rasds]

    def _test_get_vm_gen(self, vm_gen):
        mock_settings = self._lookup_vm()
        vm_gen_string = "Microsoft:Hyper-V:SubType:" + str(vm_gen)
        mock_settings.VirtualSystemSubType = vm_gen_string

        ret = self._vmutils.get_vm_gen(mock_settings)
        self.assertEqual(vm_gen, ret)

    def test_get_vm_generation_gen1(self):
        self._test_get_vm_gen(vm_gen=constants.VM_GEN_1)

    def test_get_vm_generation_gen2(self):
        self._test_get_vm_gen(vm_gen=constants.VM_GEN_2)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_associated_class')
    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_new_resource_setting_data')
    @mock.patch.object(vmutilsv2.VMUtilsV2, '_add_virt_resource')
    @mock.patch.object(vmutilsv2.VMUtilsV2, '_modify_virt_resource')
    @mock.patch.object(vmutilsv2.VMUtilsV2, '_remove_virt_resource')
    def test_enable_remotefx_video_adapter(self,
                                           mock_remove_virt_resource,
                                           mock_modify_virt_resource,
                                           mock_add_virt_res,
                                           mock_new_res_setting_data,
                                           mock_get_associated_class):
        mock_vm = self._lookup_vm()

        mock_r1 = mock.MagicMock()
        mock_r1.ResourceSubType = self._vmutils._SYNTH_DISP_CTRL_RES_SUB_TYPE

        mock_r2 = mock.MagicMock()
        mock_r2.ResourceSubType = self._vmutils._S3_DISP_CTRL_RES_SUB_TYPE

        mock_get_associated_class.return_value = [mock_r1, mock_r2]

        self._vmutils._conn.Msvm_Synth3dVideoPool()[0].IsGpuCapable = True
        self._vmutils._conn.Msvm_Synth3dVideoPool()[0].IsSlatCapable = True
        self._vmutils._conn.Msvm_Synth3dVideoPool()[0].DirectXVersion = '11.1'

        mock_synth_3d_disp_ctrl_res = mock.MagicMock(
            MaximumMonitors=self._FAKE_MONITOR_COUNT,
            MaximumScreenResolution=0)
        mock_new_res_setting_data.return_value = mock_synth_3d_disp_ctrl_res

        self._vmutils.enable_remotefx_video_adapter(
            mock.sentinel.fake_vm_name,
            self._FAKE_MONITOR_COUNT,
            constants.REMOTEFX_MAX_RES_1024x768)

        mock_get_associated_class.assert_called_once_with(
            self._vmutils._CIM_RES_ALLOC_SETTING_DATA_CLASS,
            mock_vm)

        mock_remove_virt_resource.assert_called_once_with(mock_r1,
                                                          mock_vm.path_())
        mock_new_res_setting_data.assert_called_once_with(
            self._vmutils._SYNTH_3D_DISP_CTRL_RES_SUB_TYPE,
            self._vmutils._SYNTH_3D_DISP_ALLOCATION_SETTING_DATA_CLASS)
        mock_add_virt_res.assert_called_once_with(mock_synth_3d_disp_ctrl_res,
                                                  mock_vm.path_())

        mock_modify_virt_resource.assert_called_once_with(mock_r2,
                                                          mock_vm.path_())
        self.assertEqual(self._vmutils._DISP_CTRL_ADDRESS_DX_11,
                         mock_r2.Address)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_get_associated_class')
    def test_enable_remotefx_video_adapter_already_configured(
            self,
            mock_get_associated_class):
        self._lookup_vm()

        mock_r = mock.MagicMock()
        mock_r.ResourceSubType = self._vmutils._SYNTH_3D_DISP_CTRL_RES_SUB_TYPE

        mock_get_associated_class.return_value = [mock_r]

        self.assertRaises(vmutils.HyperVException,
                          self._vmutils.enable_remotefx_video_adapter,
                          mock.sentinel.fake_vm_name, self._FAKE_MONITOR_COUNT,
                          constants.REMOTEFX_MAX_RES_1024x768)

    def test_enable_remotefx_video_adapter_no_gpu(self):
        self._lookup_vm()

        self._vmutils._conn.Msvm_Synth3dVideoPool()[0].IsGpuCapable = False

        self.assertRaises(vmutils.HyperVException,
                          self._vmutils.enable_remotefx_video_adapter,
                          mock.sentinel.fake_vm_name, self._FAKE_MONITOR_COUNT,
                          constants.REMOTEFX_MAX_RES_1024x768)

    def test_enable_remotefx_video_adapter_no_slat(self):
        self._lookup_vm()

        self._vmutils._conn.Msvm_Synth3dVideoPool()[0].IsSlatCapable = False

        self.assertRaises(vmutils.HyperVException,
                          self._vmutils.enable_remotefx_video_adapter,
                          mock.sentinel.fake_vm_name, self._FAKE_MONITOR_COUNT,
                          constants.REMOTEFX_MAX_RES_1024x768)

    @mock.patch.object(vmutilsv2.VMUtilsV2,
                       '_get_mounted_disk_resource_from_path')
    @mock.patch.object(vmutilsv2.VMUtilsV2, '_modify_virt_resource')
    def _test_set_disk_qos_specs(self, mock_modify_virt_resource,
                                 mock_get_disk_resource, qos_available=True):
        mock_disk = mock.Mock()
        if not qos_available:
            type(mock_disk).IOPSLimit = mock.PropertyMock(
                side_effect=AttributeError)
        mock_get_disk_resource.return_value = mock_disk

        self._vmutils.set_disk_qos_specs(mock.sentinel.vm_name,
                                         mock.sentinel.disk_path,
                                         mock.sentinel.min_iops,
                                         mock.sentinel.max_iops)

        mock_get_disk_resource.assert_called_once_with(
            mock.sentinel.disk_path, is_physical=False)

        if qos_available:
            self.assertEqual(mock.sentinel.max_iops, mock_disk.IOPSLimit)
            self.assertEqual(mock.sentinel.min_iops, mock_disk.IOPSReservation)
            mock_modify_virt_resource.assert_called_once_with(mock_disk,
                                                              None)
        else:
            self.assertFalse(mock_modify_virt_resource.called)

    def test_set_disk_qos_specs(self):
        self._test_set_disk_qos_specs()

    def test_set_disk_qos_specs_unsupported_feature(self):
        self._test_set_disk_qos_specs(qos_available=False)

    @mock.patch.object(vmutilsv2.VMUtilsV2, 'check_ret_val')
    def test_modify_virtual_system(self, mock_check_ret_val):
        mock_vmsettings = mock.MagicMock()
        mock_vs_man_svc = self._vmutils._vs_man_svc
        mock_vs_man_svc.ModifySystemSettings.return_value = (
            mock.sentinel.fake_job_path, mock.sentinel.fake_ret_val)
        self._vmutils._modify_virtual_system(vm_path=None,
                                             vmsetting=mock_vmsettings)
        mock_vs_man_svc.ModifySystemSettings.assert_called_once_with(
            SystemSettings=mock_vmsettings.GetText_.return_value)
        mock_check_ret_val.assert_called_once_with(mock.sentinel.fake_ret_val,
                                                   mock.sentinel.fake_job_path)

    def test_set_secure_boot(self):
        vs_data = mock.MagicMock()
        self._vmutils._set_secure_boot(vs_data, certificate_required=False)

        self.assertTrue(vs_data.SecureBootEnabled)

    def test_set_secure_boot_certificate_required(self):
        self.assertRaises(vmutils.HyperVException,
                          self._vmutils._set_secure_boot,
                          mock.MagicMock(), True)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_modify_virtual_system')
    @mock.patch.object(vmutilsv2.VMUtilsV2, '_lookup_vm_check')
    def test_enable_secure_boot(self, mock_lookup_vm_check,
                                mock_modify_virtual_system):
        vs_data = mock_lookup_vm_check.return_value

        with mock.patch.object(self._vmutils,
                               '_set_secure_boot') as mock_set_secure_boot:
            self._vmutils.enable_secure_boot(
                mock.sentinel.VM_NAME, mock.sentinel.certificate_required)

            mock_lookup_vm_check.assert_called_with(mock.sentinel.VM_NAME)
            mock_set_secure_boot.assert_called_once_with(
                vs_data, mock.sentinel.certificate_required)
            mock_modify_virtual_system.assert_called_once_with(None, vs_data)

    def test_instance_notes(self):
        mock_vm_settings = self._lookup_vm()
        mock_vm_settings.Notes = self._get_fake_instance_notes()

        notes = self._vmutils._get_instance_notes(mock.sentinel.vm_name)

        self.assertEqual(notes[0], self._FAKE_VM_UUID)

    def test_stop_vm_jobs(self):
        mock_vm = self._lookup_vm()

        mock_job1 = mock.MagicMock(Cancellable=True)
        mock_job2 = mock.MagicMock(Cancellable=True)
        mock_job3 = mock.MagicMock(Cancellable=True)

        mock_job1.JobState = 2
        mock_job2.JobState = 3
        mock_job3.JobState = constants.JOB_STATE_KILLED

        mock_jobs_affecting_vm = [
            mock.Mock(AffectingElement=x) for x in [
                mock_job1, mock_job2, mock_job3]]

        self._vmutils._conn.query.return_value = mock_jobs_affecting_vm

        self._vmutils.stop_vm_jobs(mock.sentinel.FAKE_VM_NAME)

        expected_query = (
            "SELECT * FROM %(class_name)s "
            "WHERE AffectedElement = '%(vm_path)s'"
            % {'class_name': self._vmutils._AFFECTED_JOB_ELEMENT_CLASS,
               'vm_path': mock_vm.path_.return_value})
        self._vmutils._conn.query.assert_called_once_with(expected_query)
        mock_job1.RequestStateChange.assert_called_once_with(
            self._vmutils._KILL_JOB_STATE_CHANGE_REQUEST)
        mock_job2.RequestStateChange.assert_called_once_with(
            self._vmutils._KILL_JOB_STATE_CHANGE_REQUEST)
        self.assertFalse(mock_job3.RequestStateChange.called)

    def _test_is_drive_physical(self, is_physical):
        self._vmutils._pathutils.exists.return_value = not is_physical
        ret = self._vmutils._is_drive_physical(mock.sentinel.fake_drive_path)

        self.assertEqual(is_physical, ret)

    def test_is_drive_phyisical_true(self):
        self._test_is_drive_physical(is_physical=True)

    def test_is_drive_physical_false(self):
        self._test_is_drive_physical(is_physical=False)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_is_drive_physical')
    @mock.patch.object(vmutilsv2.VMUtilsV2,
                       '_get_mounted_disk_resource_from_path')
    @mock.patch.object(vmutilsv2, 'wmi', create=True)
    def test_drive_to_boot_source(self, mock_wmi, mock_get_disk_res_from_path,
                                  mock_is_drive_physical):
        mock_is_drive_physical.return_value = True
        mock_drive = mock.MagicMock()
        mock_same_element = mock.MagicMock()
        mock_logical_identity = mock.Mock(SameElement=mock_same_element)

        mock_rasd_path = mock_drive.path_.return_value
        mock_logical_identities = [mock_logical_identity]
        self._vmutils._conn.query.return_value = mock_logical_identities
        mock_get_disk_res_from_path.return_value = mock_drive

        ret = self._vmutils._drive_to_boot_source(mock.sentinel.drive_path)

        expected_query = (
            "SELECT * FROM %(class_name)s "
            "WHERE SystemElement = '%(rasd_path)s'"
            % {'class_name': self._vmutils._LOGICAL_IDENTITY_CLASS,
               'rasd_path': mock_rasd_path})
        self._vmutils._conn.query.assert_called_once_with(expected_query)
        mock_is_drive_physical.assert_called_once_with(
            mock.sentinel.drive_path)
        mock_get_disk_res_from_path.assert_called_once_with(
            mock.sentinel.drive_path, is_physical=True)
        expected_bssd_path = mock_same_element.path_.return_value
        self.assertEqual(expected_bssd_path, ret)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_set_boot_order')
    @mock.patch.object(vmutilsv2.VMUtilsV2, '_set_boot_order_gen2')
    @mock.patch.object(vmutilsv2.VMUtilsV2, 'get_vm_gen')
    def _test_set_boot_order(self, mock_get_vm_gen, mock_set_boot_order_gen2,
                             mock_set_boot_order_gen1, vm_gen):
        mock_get_vm_gen.return_value = vm_gen
        self._vmutils.set_boot_order(mock.sentinel.fake_vm_name,
                                     mock.sentinel.boot_order)
        if vm_gen == constants.VM_GEN_1:
            mock_set_boot_order_gen1.assert_called_once_with(
                mock.sentinel.fake_vm_name, mock.sentinel.boot_order)
        else:
            mock_set_boot_order_gen2.assert_called_once_with(
                mock.sentinel.fake_vm_name, mock.sentinel.boot_order)

    def test_set_boot_order_gen1_vm(self):
        self._test_set_boot_order(vm_gen=constants.VM_GEN_1)

    def test_set_boot_order_gen2_vm(self):
        self._test_set_boot_order(vm_gen=constants.VM_GEN_2)

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_modify_virtual_system')
    def test_set_boot_order_gen1(self, mock_modify_virt_syst):
        mock_vssd = self._lookup_vm()

        fake_dev_boot_order = [mock.sentinel.BOOT_DEV1,
                               mock.sentinel.BOOT_DEV2]
        self._vmutils._set_boot_order(
            mock_vssd.name, fake_dev_boot_order)

        mock_modify_virt_syst.assert_called_once_with(
            mock_vssd.path_.return_value, mock_vssd)
        self.assertEqual(mock_vssd.BootOrder, tuple(fake_dev_boot_order))

    @mock.patch.object(vmutilsv2.VMUtilsV2, '_drive_to_boot_source')
    @mock.patch.object(vmutilsv2.VMUtilsV2, '_modify_virtual_system')
    def test_set_boot_order_gen2(self, mock_modify_virtual_system,
                                 mock_drive_to_boot_source):
        fake_boot_dev1 = mock.MagicMock()
        fake_boot_dev2 = mock.MagicMock()
        fake_boot_source1 = mock.MagicMock()
        fake_boot_source2 = mock.MagicMock()
        fake_boot_source_net = mock.MagicMock()

        fake_boot_source1.upper.return_value = mock.sentinel.boot_source1
        fake_boot_source2.upper.return_value = mock.sentinel.boot_source2
        fake_boot_source_net.upper.return_value = mock.sentinel.boot_source_net

        fake_boot_dev1.upper.return_value = mock.sentinel.boot_source1
        fake_boot_dev2.upper.return_value = mock.sentinel.boot_source2

        fake_dev_order = [fake_boot_dev1, fake_boot_dev2]
        mock_drive_to_boot_source.side_effect = fake_dev_order
        mock_vssd = self._lookup_vm()
        old_boot_order = tuple([fake_boot_source2,
                                fake_boot_source1,
                                fake_boot_source_net])
        expected_boot_order = tuple([mock.sentinel.boot_source1,
                                     mock.sentinel.boot_source2,
                                     mock.sentinel.boot_source_net])
        mock_vssd.BootSourceOrder = old_boot_order

        self._vmutils._set_boot_order_gen2(mock_vssd.name, fake_dev_order)

        mock_modify_virtual_system.assert_called_once_with(
            None, mock_vssd)
        self.assertEqual(expected_boot_order, mock_vssd.BootSourceOrder)
