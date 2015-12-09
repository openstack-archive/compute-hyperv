# Copyright 2013 Cloudbase Solutions Srl
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
"""
Utility class for VM related operations.
Based on the "root/virtualization/v2" namespace available starting with
Hyper-V Server / Windows Server 2012.
"""

import sys
import uuid

if sys.platform == 'win32':
    import wmi

from oslo_config import cfg
from oslo_log import log as logging
from oslo_service import loopingcall

from hyperv.i18n import _, _LW
from hyperv.nova import constants
from hyperv.nova import pathutils
from hyperv.nova import vmutils

CONF = cfg.CONF
LOG = logging.getLogger(__name__)


class VMUtilsV2(vmutils.VMUtils):

    _PHYS_DISK_RES_SUB_TYPE = 'Microsoft:Hyper-V:Physical Disk Drive'
    _DISK_DRIVE_RES_SUB_TYPE = 'Microsoft:Hyper-V:Synthetic Disk Drive'
    _DVD_DRIVE_RES_SUB_TYPE = 'Microsoft:Hyper-V:Synthetic DVD Drive'
    _SCSI_RES_SUBTYPE = 'Microsoft:Hyper-V:Synthetic SCSI Controller'
    _HARD_DISK_RES_SUB_TYPE = 'Microsoft:Hyper-V:Virtual Hard Disk'
    _DVD_DISK_RES_SUB_TYPE = 'Microsoft:Hyper-V:Virtual CD/DVD Disk'
    _IDE_CTRL_RES_SUB_TYPE = 'Microsoft:Hyper-V:Emulated IDE Controller'
    _SCSI_CTRL_RES_SUB_TYPE = 'Microsoft:Hyper-V:Synthetic SCSI Controller'
    _SERIAL_PORT_RES_SUB_TYPE = 'Microsoft:Hyper-V:Serial Port'

    _S3_DISP_CTRL_RES_SUB_TYPE = 'Microsoft:Hyper-V:S3 Display Controller'
    _SYNTH_DISP_CTRL_RES_SUB_TYPE = ('Microsoft:Hyper-V:Synthetic Display '
                                     'Controller')
    _SYNTH_3D_DISP_CTRL_RES_SUB_TYPE = ('Microsoft:Hyper-V:Synthetic 3D '
                                        'Display Controller')
    _SYNTH_3D_DISP_ALLOCATION_SETTING_DATA_CLASS = (
        'Msvm_Synthetic3DDisplayControllerSettingData')

    _LOGICAL_IDENTITY_CLASS = 'Msvm_LogicalIdentity'

    _VIRTUAL_SYSTEM_TYPE_REALIZED = 'Microsoft:Hyper-V:System:Realized'
    _VIRTUAL_SYSTEM_SUBTYPE_GEN1 = 'Microsoft:Hyper-V:SubType:1'
    _VIRTUAL_SYSTEM_SUBTYPE_GEN2 = 'Microsoft:Hyper-V:SubType:2'

    _SNAPSHOT_FULL = 2

    _METRIC_AGGR_CPU_AVG = 'Aggregated Average CPU Utilization'
    _METRIC_AGGR_MEMORY_AVG = 'Aggregated Average Memory Utilization'
    _METRIC_ENABLED = 2

    _STORAGE_ALLOC_SETTING_DATA_CLASS = 'Msvm_StorageAllocationSettingData'
    _ETHERNET_PORT_ALLOCATION_SETTING_DATA_CLASS = \
    'Msvm_EthernetPortAllocationSettingData'

    _VIRT_DISK_CONNECTION_ATTR = "HostResource"

    _AUTOMATIC_STARTUP_ACTION_NONE = 2

    _remote_fx_res_map = {
        constants.REMOTEFX_MAX_RES_1024x768: 0,
        constants.REMOTEFX_MAX_RES_1280x1024: 1,
        constants.REMOTEFX_MAX_RES_1600x1200: 2,
        constants.REMOTEFX_MAX_RES_1920x1200: 3,
        constants.REMOTEFX_MAX_RES_2560x1600: 4
    }

    _vm_power_states_map = {constants.HYPERV_VM_STATE_ENABLED: 2,
                            constants.HYPERV_VM_STATE_DISABLED: 3,
                            constants.HYPERV_VM_STATE_SHUTTING_DOWN: 4,
                            constants.HYPERV_VM_STATE_REBOOT: 11,
                            constants.HYPERV_VM_STATE_PAUSED: 9,
                            constants.HYPERV_VM_STATE_SUSPENDED: 6}

    _DISP_CTRL_ADDRESS_DX_11 = "02C1,00000000,01"

    def __init__(self, host='.'):
        if sys.platform == 'win32':
            self._pathutils = pathutils.PathUtils()
        super(VMUtilsV2, self).__init__(host)

    def _init_hyperv_wmi_conn(self, host):
        self._conn = wmi.WMI(moniker='//%s/root/virtualization/v2' % host)

    def list_instance_notes(self):
        instance_notes = []

        for vs in self._conn.Msvm_VirtualSystemSettingData(
                ['ElementName', 'Notes'],
                VirtualSystemType=self._VIRTUAL_SYSTEM_TYPE_REALIZED):
            if vs.Notes is not None:
                instance_notes.append(
                    (vs.ElementName, [v for v in vs.Notes if v]))

        return instance_notes

    def list_instances(self):
        """Return the names of all the instances known to Hyper-V."""
        return [v.ElementName for v in
                self._conn.Msvm_VirtualSystemSettingData(
                    ['ElementName'],
                    VirtualSystemType=self._VIRTUAL_SYSTEM_TYPE_REALIZED)]

    def _create_vm_obj(self, vs_man_svc, vm_name, vnuma_enabled, vm_gen,
                       instance_path, notes):
        vs_data = self._conn.Msvm_VirtualSystemSettingData.new()
        vs_data.ElementName = vm_name
        vs_data.Notes = notes
        # Don't start automatically on host boot
        vs_data.AutomaticStartupAction = self._AUTOMATIC_STARTUP_ACTION_NONE

        vs_data.VirtualNumaEnabled = vnuma_enabled

        if vm_gen == constants.VM_GEN_2:
            vs_data.VirtualSystemSubType = self._VIRTUAL_SYSTEM_SUBTYPE_GEN2
            vs_data.SecureBootEnabled = False

        # Created VMs must have their *DataRoot paths in the same location as
        # the instances' path.
        vs_data.ConfigurationDataRoot = instance_path
        vs_data.LogDataRoot = instance_path
        vs_data.SnapshotDataRoot = instance_path
        vs_data.SuspendDataRoot = instance_path
        vs_data.SwapFileDataRoot = instance_path

        (job_path,
         vm_path,
         ret_val) = vs_man_svc.DefineSystem(ResourceSettings=[],
                                            ReferenceConfiguration=None,
                                            SystemSettings=vs_data.GetText_(1))
        job = self.check_ret_val(ret_val, job_path)
        if not vm_path and job:
            vm_path = job.associators(self._AFFECTED_JOB_ELEMENT_CLASS)[0]
        return self._get_wmi_obj(vm_path)

    def _get_vm_setting_data(self, vm):
        vmsettings = vm.associators(
            wmi_result_class=self._VIRTUAL_SYSTEM_SETTING_DATA_CLASS)
        # Avoid snapshots
        return [s for s in vmsettings if
                s.VirtualSystemType == self._VIRTUAL_SYSTEM_TYPE_REALIZED][0]

    def _get_attached_disks_query_string(self, scsi_controller_path):
        # DVD Drives can be attached to SCSI as well, if the VM Generation is 2
        return ("SELECT * FROM Msvm_ResourceAllocationSettingData WHERE ("
                "ResourceSubType='%(res_sub_type)s' OR "
                "ResourceSubType='%(res_sub_type_virt)s' OR "
                "ResourceSubType='%(res_sub_type_dvd)s') AND "
                "Parent = '%(parent)s'" % {
                    'res_sub_type': self._PHYS_DISK_RES_SUB_TYPE,
                    'res_sub_type_virt': self._DISK_DRIVE_RES_SUB_TYPE,
                    'res_sub_type_dvd': self._DVD_DRIVE_RES_SUB_TYPE,
                    'parent': scsi_controller_path.replace("'", "''")})

    def attach_drive(self, vm_name, path, ctrller_path, drive_addr,
                     drive_type=constants.DISK):
        """Create a drive and attach it to the vm."""

        vm = self._lookup_vm_check(vm_name)

        if drive_type == constants.DISK:
            res_sub_type = self._DISK_DRIVE_RES_SUB_TYPE
        elif drive_type == constants.DVD:
            res_sub_type = self._DVD_DRIVE_RES_SUB_TYPE

        drive = self._get_new_resource_setting_data(res_sub_type)

        # Set the ctrller as parent.
        drive.Parent = ctrller_path
        drive.Address = drive_addr
        drive.AddressOnParent = drive_addr
        # Add the cloned disk drive object to the vm.
        new_resources = self._add_virt_resource(drive, vm.path_())
        drive_path = new_resources[0]

        if drive_type == constants.DISK:
            res_sub_type = self._HARD_DISK_RES_SUB_TYPE
        elif drive_type == constants.DVD:
            res_sub_type = self._DVD_DISK_RES_SUB_TYPE

        res = self._get_new_resource_setting_data(
            res_sub_type, self._STORAGE_ALLOC_SETTING_DATA_CLASS)

        res.Parent = drive_path
        res.HostResource = [path]

        self._add_virt_resource(res, vm.path_())

    def attach_volume_to_controller(self, vm_name, controller_path, address,
                                    mounted_disk_path):
        """Attach a volume to a controller."""

        vm = self._lookup_vm_check(vm_name)

        diskdrive = self._get_new_resource_setting_data(
            self._PHYS_DISK_RES_SUB_TYPE)

        diskdrive.AddressOnParent = address
        diskdrive.Parent = controller_path
        diskdrive.HostResource = [mounted_disk_path]

        self._add_virt_resource(diskdrive, vm.path_())

    def _get_disk_resource_address(self, disk_resource):
        return disk_resource.AddressOnParent

    def create_scsi_controller(self, vm_name):
        """Create an iscsi controller ready to mount volumes."""
        scsicontrl = self._get_new_resource_setting_data(
            self._SCSI_RES_SUBTYPE)

        scsicontrl.VirtualSystemIdentifiers = ['{' + str(uuid.uuid4()) + '}']

        vm = self._lookup_vm_check(vm_name)
        self._add_virt_resource(scsicontrl, vm.path_())

    def _get_disk_resource_disk_path(self, disk_resource):
        return disk_resource.HostResource

    def destroy_vm(self, vm_name):
        vm = self._lookup_vm_check(vm_name)

        vs_man_svc = self._conn.Msvm_VirtualSystemManagementService()[0]
        # Remove the VM. It does not destroy any associated virtual disk.
        (job_path, ret_val) = vs_man_svc.DestroySystem(vm.path_())
        self.check_ret_val(ret_val, job_path)

    def _add_virt_resource(self, res_setting_data, vm_path):
        """Adds a new resource to the VM."""
        vs_man_svc = self._conn.Msvm_VirtualSystemManagementService()[0]
        res_xml = [res_setting_data.GetText_(1)]
        (job_path,
         new_resources,
         ret_val) = vs_man_svc.AddResourceSettings(vm_path, res_xml)
        self.check_ret_val(ret_val, job_path)
        return new_resources

    # _modify_virt_resource can fail, especially while setting up the VM's
    # serial port connection. Retrying the operation will yield success.
    @loopingcall.RetryDecorator(max_retry_count=5, max_sleep_time=1,
                                exceptions=(vmutils.HyperVException, ))
    def _modify_virt_resource(self, res_setting_data, vm_path):
        """Updates a VM resource."""
        vs_man_svc = self._conn.Msvm_VirtualSystemManagementService()[0]
        (job_path,
         out_res_setting_data,
         ret_val) = vs_man_svc.ModifyResourceSettings(
            ResourceSettings=[res_setting_data.GetText_(1)])
        self.check_ret_val(ret_val, job_path)

    def _remove_virt_resource(self, res_setting_data, vm_path):
        """Removes a VM resource."""
        vs_man_svc = self._conn.Msvm_VirtualSystemManagementService()[0]
        res_path = [res_setting_data.path_()]
        (job_path, ret_val) = vs_man_svc.RemoveResourceSettings(res_path)
        self.check_ret_val(ret_val, job_path)

    def get_vm_state(self, vm_name):
        settings = self.get_vm_summary_info(vm_name)
        return settings['EnabledState']

    def take_vm_snapshot(self, vm_name):
        vm = self._lookup_vm_check(vm_name)
        vs_snap_svc = self._conn.Msvm_VirtualSystemSnapshotService()[0]

        (job_path, snp_setting_data, ret_val) = vs_snap_svc.CreateSnapshot(
            AffectedSystem=vm.path_(),
            SnapshotType=self._SNAPSHOT_FULL)
        self.check_ret_val(ret_val, job_path)

        job_wmi_path = job_path.replace('\\', '/')
        job = wmi.WMI(moniker=job_wmi_path)
        snp_setting_data = job.associators(
            wmi_result_class=self._VIRTUAL_SYSTEM_SETTING_DATA_CLASS)[0]

        return snp_setting_data.path_()

    def remove_vm_snapshot(self, snapshot_path):
        vs_snap_svc = self._conn.Msvm_VirtualSystemSnapshotService()[0]
        (job_path, ret_val) = vs_snap_svc.DestroySnapshot(snapshot_path)
        self.check_ret_val(ret_val, job_path)

    def set_nic_connection(self, vm_name, nic_name, vswitch_conn_data):
        nic_data = self._get_nic_data_by_name(nic_name)

        eth_port_data = self._get_new_setting_data(
            self._ETHERNET_PORT_ALLOCATION_SETTING_DATA_CLASS)

        eth_port_data.HostResource = [vswitch_conn_data]
        eth_port_data.Parent = nic_data.path_()
        eth_port_data.ElementName = nic_name

        vm = self._lookup_vm_check(vm_name)
        self._add_virt_resource(eth_port_data, vm.path_())

    def enable_vm_metrics_collection(self, vm_name):
        metric_names = [self._METRIC_AGGR_CPU_AVG,
                        self._METRIC_AGGR_MEMORY_AVG]

        vm = self._lookup_vm_check(vm_name)
        metric_svc = self._conn.Msvm_MetricService()[0]
        (disks, volumes) = self._get_vm_disks(vm)
        filtered_disks = [d for d in disks if
                          d.ResourceSubType is not self._DVD_DISK_RES_SUB_TYPE]

        # enable metrics for disk.
        for disk in filtered_disks:
            self._enable_metrics(metric_svc, disk)

        for metric_name in metric_names:
            metric_def = self._conn.CIM_BaseMetricDefinition(Name=metric_name)
            if not metric_def:
                LOG.debug("Metric not found: %s", metric_name)
            else:
                self._enable_metrics(metric_svc, vm, metric_def[0].path_())

    def _enable_metrics(self, metric_svc, element, definition_path=None):
        metric_svc.ControlMetrics(
            Subject=element.path_(),
            Definition=definition_path,
            MetricCollectionEnabled=self._METRIC_ENABLED)

    def get_vm_dvd_disk_paths(self, vm_name):
        vm = self._lookup_vm_check(vm_name)

        settings = vm.associators(
            wmi_result_class=self._VIRTUAL_SYSTEM_SETTING_DATA_CLASS)[0]
        sasds = settings.associators(
            wmi_result_class=self._STORAGE_ALLOC_SETTING_DATA_CLASS)

        dvd_paths = [sasd.HostResource[0] for sasd in sasds
                     if sasd.ResourceSubType == self._DVD_DISK_RES_SUB_TYPE]

        return dvd_paths

    def get_vm_gen(self, instance_name):
        vm = self._lookup_vm_check(instance_name)
        vm_settings = self._get_vm_setting_data(vm)
        vm_gen = getattr(vm_settings, 'VirtualSystemSubType',
                         self._VIRTUAL_SYSTEM_SUBTYPE_GEN1)
        return int(vm_gen.split(':')[-1])

    def enable_remotefx_video_adapter(self, vm_name, monitor_count,
                                      max_resolution):
        vm = self._lookup_vm_check(vm_name)

        max_res_value = self._remote_fx_res_map.get(max_resolution)
        if max_res_value is None:
            raise vmutils.HyperVException(_("Unsupported RemoteFX resolution: "
                                            "%s") % max_resolution)

        synth_3d_video_pool = self._conn.Msvm_Synth3dVideoPool()[0]
        if not synth_3d_video_pool.IsGpuCapable:
            raise vmutils.HyperVException(_("To enable RemoteFX on Hyper-V at "
                                            "least one GPU supporting DirectX "
                                            "11 is required"))
        if not synth_3d_video_pool.IsSlatCapable:
            raise vmutils.HyperVException(_("To enable RemoteFX on Hyper-V it "
                                            "is required that the host CPUs "
                                            "support SLAT"))

        vmsettings = vm.associators(
            wmi_result_class=self._VIRTUAL_SYSTEM_SETTING_DATA_CLASS)
        rasds = vmsettings[0].associators(
            wmi_result_class=self._CIM_RES_ALLOC_SETTING_DATA_CLASS)

        if [r for r in rasds if r.ResourceSubType ==
                self._SYNTH_3D_DISP_CTRL_RES_SUB_TYPE]:
            raise vmutils.HyperVException(_("RemoteFX is already configured "
                                            "for this VM"))

        synth_disp_ctrl_res_list = [r for r in rasds if r.ResourceSubType ==
                                    self._SYNTH_DISP_CTRL_RES_SUB_TYPE]
        if synth_disp_ctrl_res_list:
            self._remove_virt_resource(synth_disp_ctrl_res_list[0], vm.path_())

        synth_3d_disp_ctrl_res = self._get_new_resource_setting_data(
            self._SYNTH_3D_DISP_CTRL_RES_SUB_TYPE,
            self._SYNTH_3D_DISP_ALLOCATION_SETTING_DATA_CLASS)

        synth_3d_disp_ctrl_res.MaximumMonitors = monitor_count
        synth_3d_disp_ctrl_res.MaximumScreenResolution = max_res_value

        self._add_virt_resource(synth_3d_disp_ctrl_res, vm.path_())

        s3_disp_ctrl_res = [r for r in rasds if r.ResourceSubType ==
                            self._S3_DISP_CTRL_RES_SUB_TYPE][0]

        s3_disp_ctrl_res.Address = self._DISP_CTRL_ADDRESS_DX_11

        self._modify_virt_resource(s3_disp_ctrl_res, vm.path_())

    def _get_instance_notes(self, vm_name):
        vm = self._lookup_vm_check(vm_name)
        vmsettings = self._get_vm_setting_data(vm)
        return [note for note in vmsettings.Notes if note]

    def set_disk_qos_specs(self, vm_name, disk_path, min_iops, max_iops):
        disk_resource = self._get_mounted_disk_resource_from_path(
            disk_path, is_physical=False)
        try:
            disk_resource.IOPSLimit = max_iops
            disk_resource.IOPSReservation = min_iops
        except AttributeError:
            LOG.warn(_LW("This Windows version does not support disk QoS. "
                         "Ignoring QoS specs."))
            return
        # VMUtilsV2._modify_virt_resource does not require the vm path.
        self._modify_virt_resource(disk_resource, None)

    def enable_secure_boot(self, vm_name, certificate_required):
        vm = self._lookup_vm_check(vm_name)
        vs_data = self._get_vm_setting_data(vm)
        self._set_secure_boot(vs_data, certificate_required)
        vs_man_svc = self._conn.Msvm_VirtualSystemManagementService()[0]

        self._modify_virtual_system(vs_man_svc, vm.path_(), vs_data)

    def _set_secure_boot(self, vs_data, certificate_required):
        vs_data.SecureBootEnabled = True
        if certificate_required:
            raise vmutils.HyperVException(
                _('UEFI SecureBoot is supported only on Windows instances.'))

    def _modify_virtual_system(self, vs_man_svc, vm_path, vmsetting):
        (job_path, ret_val) = vs_man_svc.ModifySystemSettings(
            SystemSettings=vmsetting.GetText_(1))
        self.check_ret_val(ret_val, job_path)

    def _is_drive_physical(self, drive_path):
        # TODO(atuvenie): Find better way to check if path represents
        # physical or virtual drive.
        return not self._pathutils.exists(drive_path)

    def _drive_to_boot_source(self, drive_path):

        is_physical = self._is_drive_physical(drive_path)
        drive = self._get_mounted_disk_resource_from_path(
            drive_path, is_physical=is_physical)

        if is_physical:
            bssd = drive.associators(
                wmi_association_class=self._LOGICAL_IDENTITY_CLASS)[0]
        else:
            rasd = wmi.WMI(moniker=drive.Parent)
            bssd = rasd.associators(
                wmi_association_class=self._LOGICAL_IDENTITY_CLASS)[0]
        return bssd

    def set_boot_order(self, vm_name, device_boot_order):
        if self.get_vm_gen(vm_name) == constants.VM_GEN_1:
            self._set_boot_order(vm_name, device_boot_order)
        else:
            self._set_boot_order_gen2(vm_name, device_boot_order)

    def _set_boot_order_gen2(self, vm_name, device_boot_order):
        new_boot_order = [(self._drive_to_boot_source(device)).path_()
                           for device in device_boot_order if device]

        vm = self._lookup_vm_check(vm_name)

        vs_man_svc = self._conn.Msvm_VirtualSystemManagementService()[0]
        vssd = self._get_vm_setting_data(vm)
        old_boot_order = vssd.BootSourceOrder
        network_boot_devs = set(old_boot_order) ^ set(new_boot_order)
        vssd.BootSourceOrder = tuple(new_boot_order) + tuple(network_boot_devs)
        self._modify_virtual_system(vs_man_svc, None, vssd)
