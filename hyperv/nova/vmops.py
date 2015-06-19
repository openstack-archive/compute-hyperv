# Copyright (c) 2010 Cloud.com, Inc
# Copyright 2012 Cloudbase Solutions Srl
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
Management class for basic VM operations.
"""
import functools
import os
import time

from eventlet import timeout as etimeout
from nova.api.metadata import base as instance_metadata
from nova.compute import vm_states
from nova import exception
from nova.openstack.common import fileutils
from nova.openstack.common import loopingcall
from nova import utils
from nova.virt import configdrive
from nova.virt import hardware
from oslo_concurrency import processutils
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import excutils
from oslo_utils import units
from oslo_utils import uuidutils

from hyperv.i18n import _, _LI, _LE, _LW
from hyperv.nova import constants
from hyperv.nova import imagecache
from hyperv.nova import serialconsoleops
from hyperv.nova import utilsfactory
from hyperv.nova import vif as vif_utils
from hyperv.nova import vmutils
from hyperv.nova import volumeops

LOG = logging.getLogger(__name__)

hyperv_opts = [
    cfg.BoolOpt('limit_cpu_features',
                default=False,
                help='Required for live migration among '
                     'hosts with different CPU features'),
    cfg.BoolOpt('config_drive_inject_password',
                default=False,
                help='Sets the admin password in the config drive image'),
    cfg.StrOpt('qemu_img_cmd',
               default="qemu-img.exe",
               help='Path of qemu-img command which is used to convert '
                    'between different image types'),
    cfg.BoolOpt('config_drive_cdrom',
                default=False,
                help='Attaches the Config Drive image as a cdrom drive '
                     'instead of a disk drive'),
    cfg.BoolOpt('enable_instance_metrics_collection',
                default=False,
                help='Enables metrics collections for an instance by using '
                     'Hyper-V\'s metric APIs. Collected data can by retrieved '
                     'by other apps and services, e.g.: Ceilometer. '
                     'Requires Hyper-V / Windows Server 2012 and above'),
    cfg.FloatOpt('dynamic_memory_ratio',
                 default=1.0,
                 help='Enables dynamic memory allocation (ballooning) when '
                      'set to a value greater than 1. The value expresses '
                      'the ratio between the total RAM assigned to an '
                      'instance and its startup RAM amount. For example a '
                      'ratio of 2.0 for an instance with 1024MB of RAM '
                      'implies 512MB of RAM allocated at startup'),
    cfg.IntOpt('wait_soft_reboot_seconds',
               default=60,
               help='Number of seconds to wait for instance to shut down after'
                    ' soft reboot request is made. We fall back to hard reboot'
                    ' if instance does not shutdown within this window.'),
    cfg.BoolOpt('enable_remotefx',
                default=False,
                help='Enables RemoteFX. This requires at least one DirectX 11 '
                     'capable graphic adapter for Windows Server 2012 R2 and '
                     'RDS-Virtualization feature has to be enabled')
]

CONF = cfg.CONF
CONF.register_opts(hyperv_opts, 'hyperv')
CONF.import_opt('use_cow_images', 'nova.virt.driver')

SHUTDOWN_TIME_INCREMENT = 5
REBOOT_TYPE_SOFT = 'SOFT'
REBOOT_TYPE_HARD = 'HARD'

VM_GENERATIONS = {
    constants.IMAGE_PROP_VM_GEN_1: constants.VM_GEN_1,
    constants.IMAGE_PROP_VM_GEN_2: constants.VM_GEN_2
}

VM_GENERATIONS_CONTROLLER_TYPES = {
    constants.VM_GEN_1: constants.CTRL_TYPE_IDE,
    constants.VM_GEN_2: constants.CTRL_TYPE_SCSI
}


def check_admin_permissions(function):
    @functools.wraps(function)
    def wrapper(self, *args, **kwds):

        # Make sure the windows account has the required admin permissions.
        self._vmutils.check_admin_permissions()
        return function(self, *args, **kwds)
    return wrapper


class VMOps(object):
    _ROOT_DISK_CTRL_ADDR = 0

    def __init__(self):
        self._vmutils = utilsfactory.get_vmutils()
        self._vhdutils = utilsfactory.get_vhdutils()
        self._pathutils = utilsfactory.get_pathutils()
        self._hostutils = utilsfactory.get_hostutils()
        self._serial_console_ops = serialconsoleops.SerialConsoleOps()
        self._volumeops = volumeops.VolumeOps()
        self._imagecache = imagecache.ImageCache()
        self._vif_driver_cache = {}

    def list_instance_uuids(self):
        instance_uuids = []
        for (instance_name, notes) in self._vmutils.list_instance_notes():
            if notes and uuidutils.is_uuid_like(notes[0]):
                instance_uuids.append(str(notes[0]))
            else:
                LOG.debug("Notes not found or not resembling a GUID for "
                          "instance: %s" % instance_name)
        return instance_uuids

    def list_instances(self):
        return self._vmutils.list_instances()

    def get_info(self, instance):
        """Get information about the VM."""
        LOG.debug("get_info called for instance", instance=instance)

        instance_name = instance.name
        if not self._vmutils.vm_exists(instance_name):
            raise exception.InstanceNotFound(instance_id=instance.uuid)

        info = self._vmutils.get_vm_summary_info(instance_name)

        state = constants.HYPERV_POWER_STATE[info['EnabledState']]
        return hardware.InstanceInfo(state=state,
                                     max_mem_kb=info['MemoryUsage'],
                                     mem_kb=info['MemoryUsage'],
                                     num_cpu=info['NumberOfProcessors'],
                                     cpu_time_ns=info['UpTime'])

    def _create_root_vhd(self, context, instance, rescue_image_id=None):
        is_rescue_vhd = rescue_image_id is not None

        base_vhd_path = self._imagecache.get_cached_image(context, instance,
                                                          rescue_image_id)
        base_vhd_info = self._vhdutils.get_vhd_info(base_vhd_path)
        base_vhd_size = base_vhd_info['MaxInternalSize']
        format_ext = base_vhd_path.split('.')[-1]

        root_vhd_path = self._pathutils.get_root_vhd_path(instance.name,
                                                          format_ext,
                                                          is_rescue_vhd)
        root_vhd_size = instance.root_gb * units.Gi

        try:
            if CONF.use_cow_images:
                LOG.debug("Creating differencing VHD. Parent: "
                          "%(base_vhd_path)s, Target: %(root_vhd_path)s",
                          {'base_vhd_path': base_vhd_path,
                           'root_vhd_path': root_vhd_path},
                          instance=instance)
                self._vhdutils.create_differencing_vhd(root_vhd_path,
                                                       base_vhd_path)
                vhd_type = self._vhdutils.get_vhd_format(base_vhd_path)
                if vhd_type == constants.DISK_FORMAT_VHD:
                    # The base image has already been resized. As differencing
                    # vhdx images support it, the root image will be resized
                    # instead if needed.
                    return root_vhd_path
            else:
                LOG.debug("Copying VHD image %(base_vhd_path)s to target: "
                          "%(root_vhd_path)s",
                          {'base_vhd_path': base_vhd_path,
                           'root_vhd_path': root_vhd_path},
                          instance=instance)
                self._pathutils.copyfile(base_vhd_path, root_vhd_path)

            root_vhd_internal_size = (
                self._vhdutils.get_internal_vhd_size_by_file_size(
                    base_vhd_path, root_vhd_size))

            if not is_rescue_vhd and self._is_resize_needed(
                    root_vhd_path, base_vhd_size,
                    root_vhd_internal_size, instance):
                self._vhdutils.resize_vhd(root_vhd_path,
                                          root_vhd_internal_size,
                                          is_file_max_size=False)
        except Exception:
            with excutils.save_and_reraise_exception():
                if self._pathutils.exists(root_vhd_path):
                    self._pathutils.remove(root_vhd_path)

        return root_vhd_path

    def _get_vif_driver(self, vif_type):
        vif_driver = self._vif_driver_cache.get(vif_type)
        if vif_driver:
            return vif_driver
        vif_driver = vif_utils.get_vif_driver(vif_type)
        self._vif_driver_cache[vif_type] = vif_driver
        return vif_driver

    def _is_resize_needed(self, vhd_path, old_size, new_size, instance):
        if new_size < old_size:
            error_msg = _("Cannot resize a VHD to a smaller size, the"
                          " original size is %(old_size)s, the"
                          " newer size is %(new_size)s"
                          ) % {'old_size': old_size,
                               'new_size': new_size}
            raise vmutils.VHDResizeException(error_msg)
        elif new_size > old_size:
            LOG.debug("Resizing VHD %(vhd_path)s to new "
                      "size %(new_size)s" %
                      {'new_size': new_size,
                       'vhd_path': vhd_path},
                      instance=instance)
            return True
        return False

    def create_ephemeral_vhd(self, instance):
        eph_vhd_size = instance.get('ephemeral_gb', 0) * units.Gi
        if eph_vhd_size:
            vhd_format = self._vhdutils.get_best_supported_vhd_format()

            eph_vhd_path = self._pathutils.get_ephemeral_vhd_path(
                instance.name, vhd_format)
            self._vhdutils.create_dynamic_vhd(eph_vhd_path, eph_vhd_size,
                                              vhd_format)
            return eph_vhd_path

    @check_admin_permissions
    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info, block_device_info=None):
        """Create a new VM and start it."""
        LOG.info(_LI("Spawning new instance"), instance=instance)

        instance_name = instance.name
        if self._vmutils.vm_exists(instance_name):
            raise exception.InstanceExists(name=instance_name)

        # Make sure we're starting with a clean slate.
        self._delete_disk_files(instance_name)

        if self._volumeops.ebs_root_in_block_devices(block_device_info):
            root_vhd_path = None
        else:
            root_vhd_path = self._create_root_vhd(context, instance)

        eph_vhd_path = self.create_ephemeral_vhd(instance)
        # TODO(lpetrut): move this to the create_instance method.
        vm_gen = self.get_image_vm_generation(root_vhd_path, image_meta)

        try:
            self.create_instance(instance, network_info, block_device_info,
                                 root_vhd_path, eph_vhd_path,
                                 vm_gen, image_meta)

            if configdrive.required_by(instance):
                configdrive_path = self._create_config_drive(instance,
                                                             injected_files,
                                                             admin_password,
                                                             network_info)

                self.attach_config_drive(instance, configdrive_path, vm_gen)

            self.power_on(instance, network_info=network_info)
        except Exception:
            with excutils.save_and_reraise_exception():
                self.destroy(instance)

    def create_instance(self, instance, network_info, block_device_info,
                        root_vhd_path, eph_vhd_path, vm_gen, image_meta):
        instance_name = instance.name
        instance_path = os.path.join(CONF.instances_path, instance_name)

        self._vmutils.create_vm(instance_name,
                                instance.memory_mb,
                                instance.vcpus,
                                CONF.hyperv.limit_cpu_features,
                                CONF.hyperv.dynamic_memory_ratio,
                                vm_gen,
                                instance_path,
                                [instance.uuid])

        flavor_extra_specs = instance.flavor.extra_specs
        remote_fx_config = flavor_extra_specs.get(
                constants.FLAVOR_REMOTE_FX_EXTRA_SPEC_KEY)
        if remote_fx_config:
            if vm_gen == constants.VM_GEN_2:
                raise vmutils.HyperVException(_("RemoteFX is not supported "
                                                "on generation 2 virtual "
                                                "machines."))
            else:
                self._configure_remotefx(instance, remote_fx_config)

        self._vmutils.create_scsi_controller(instance_name)
        controller_type = VM_GENERATIONS_CONTROLLER_TYPES[vm_gen]

        ctrl_disk_addr = 0
        if root_vhd_path:
            self._attach_drive(instance_name, root_vhd_path, 0, ctrl_disk_addr,
                               controller_type)
            ctrl_disk_addr += 1

        if eph_vhd_path:
            self._attach_drive(instance_name, eph_vhd_path, 0, ctrl_disk_addr,
                               controller_type)

        # If ebs_root is False, the first volume will be attached to SCSI
        # controller. Generation 2 VMs only has a SCSI controller.
        ebs_root = vm_gen is not constants.VM_GEN_2 and root_vhd_path is None
        self._volumeops.attach_volumes(block_device_info,
                                       instance_name,
                                       ebs_root)

        serial_ports = self._get_image_serial_port_settings(image_meta)
        self._create_vm_com_port_pipes(instance, serial_ports)
        self._set_instance_disk_qos_specs(instance)

        for vif in network_info:
            LOG.debug('Creating nic for instance', instance=instance)
            self._vmutils.create_nic(instance_name,
                                     vif['id'],
                                     vif['address'])
            vif_driver = self._get_vif_driver(vif.get('type'))
            vif_driver.plug(instance, vif)

        if CONF.hyperv.enable_instance_metrics_collection:
            self._vmutils.enable_vm_metrics_collection(instance_name)

    def _attach_drive(self, instance_name, path, drive_addr, ctrl_disk_addr,
                      controller_type, drive_type=constants.DISK):
        if controller_type == constants.CTRL_TYPE_SCSI:
            self._vmutils.attach_scsi_drive(instance_name, path, drive_type)
        else:
            self._vmutils.attach_ide_drive(instance_name, path, drive_addr,
                                           ctrl_disk_addr, drive_type)

    def get_image_vm_generation(self, root_vhd_path, image_meta):
        image_props = image_meta['properties']
        default_vm_gen = self._hostutils.get_default_vm_generation()
        image_prop_vm = image_props.get(constants.IMAGE_PROP_VM_GEN,
                                        default_vm_gen)
        if image_prop_vm not in self._hostutils.get_supported_vm_types():
            LOG.error(_LE('Requested VM Generation %s is not supported on '
                         ' this OS.'), image_prop_vm)
            raise vmutils.HyperVException(
                _('Requested VM Generation %s is not supported on this '
                  'OS.') % image_prop_vm)

        vm_gen = VM_GENERATIONS[image_prop_vm]

        if (vm_gen != constants.VM_GEN_1 and root_vhd_path and
                self._vhdutils.get_vhd_format(
                    root_vhd_path) == constants.DISK_FORMAT_VHD):
            LOG.error(_LE('Requested VM Generation %s, but provided VHD '
                          'instead of VHDX.'), vm_gen)
            raise vmutils.HyperVException(
                _('Requested VM Generation %s, but provided VHD instead of '
                  'VHDX.') % vm_gen)

        return vm_gen

    def _create_config_drive(self, instance, injected_files, admin_password,
                             network_info, rescue=False):
        if CONF.config_drive_format != 'iso9660':
            raise vmutils.UnsupportedConfigDriveFormatException(
                _('Invalid config_drive_format "%s"') %
                CONF.config_drive_format)

        LOG.info(_LI('Using config drive for instance'), instance=instance)

        extra_md = {}
        if admin_password and CONF.hyperv.config_drive_inject_password:
            extra_md['admin_pass'] = admin_password

        inst_md = instance_metadata.InstanceMetadata(instance,
                                                     content=injected_files,
                                                     extra_md=extra_md,
                                                     network_info=network_info)

        configdrive_path_iso = self._pathutils.get_configdrive_path(
            instance.name, constants.DVD_FORMAT, rescue=rescue)
        LOG.info(_LI('Creating config drive at %(path)s'),
                 {'path': configdrive_path_iso}, instance=instance)

        with configdrive.ConfigDriveBuilder(instance_md=inst_md) as cdb:
            try:
                cdb.make_drive(configdrive_path_iso)
            except processutils.ProcessExecutionError as e:
                with excutils.save_and_reraise_exception():
                    LOG.error(_LE('Creating config drive failed with '
                                  'error: %s'),
                              e, instance=instance)

        if not CONF.hyperv.config_drive_cdrom:
            configdrive_path = self._pathutils.get_configdrive_path(
                instance.name, constants.DISK_FORMAT_VHD, rescue=rescue)
            utils.execute(CONF.hyperv.qemu_img_cmd,
                          'convert',
                          '-f',
                          'raw',
                          '-O',
                          'vpc',
                          configdrive_path_iso,
                          configdrive_path,
                          attempts=1)
            self._pathutils.remove(configdrive_path_iso)
        else:
            configdrive_path = configdrive_path_iso

        return configdrive_path

    def _configure_remotefx(self, instance, config):
        if not CONF.hyperv.enable_remotefx:
            raise vmutils.HyperVException(
                _("enable_remotefx configuration option needs to be set to "
                  "True in order to use RemoteFX"))

        if not self._hostutils.check_server_feature(
                        self._hostutils.FEATURE_RDS_VIRTUALIZATION):
                    raise vmutils.HyperVException(
                        _("The RDS-Virtualization feature must be installed "
                          "in order to use RemoteFX"))

        instance_name = instance.name
        LOG.debug('Configuring RemoteFX for instance: %s', instance_name)

        (remotefx_max_resolution, remotefx_monitor_count) = config.split(',')
        remotefx_monitor_count = int(remotefx_monitor_count)

        self._vmutils.enable_remotefx_video_adapter(
            instance_name,
            remotefx_monitor_count,
            remotefx_max_resolution)

    def attach_config_drive(self, instance, configdrive_path, vm_gen):
        configdrive_ext = configdrive_path[(configdrive_path.rfind('.') + 1):]
        # Do the attach here and if there is a certain file format that isn't
        # supported in constants.DISK_FORMAT_MAP then bomb out.
        try:
            drive_type = constants.DISK_FORMAT_MAP[configdrive_ext]
            controller_type = VM_GENERATIONS_CONTROLLER_TYPES[vm_gen]
            self._attach_drive(instance.name, configdrive_path, 1, 0,
                               controller_type, drive_type)
        except KeyError:
            raise exception.InvalidDiskFormat(disk_format=configdrive_ext)

    def _detach_config_drive(self, instance_name, rescue=False, delete=False):
        configdrive_path = self._pathutils.lookup_configdrive_path(
            instance_name, rescue=rescue)

        if configdrive_path:
            self._vmutils.detach_vm_disk(instance_name,
                                         configdrive_path,
                                         is_physical=False)
            if delete:
                self._pathutils.remove(configdrive_path)

    def _delete_disk_files(self, instance_name):
        self._pathutils.get_instance_dir(instance_name,
                                         create_dir=False,
                                         remove_dir=True)

    def destroy(self, instance, network_info=None, block_device_info=None,
                destroy_disks=True):
        instance_name = instance.name
        LOG.info(_LI("Got request to destroy instance"), instance=instance)
        try:
            if self._vmutils.vm_exists(instance_name):
                self.power_off(instance)

                self._vmutils.destroy_vm(instance_name)
                self._volumeops.disconnect_volumes(block_device_info)
            else:
                LOG.debug("Instance not found", instance=instance)

            if destroy_disks:
                self._delete_disk_files(instance_name)
            if network_info:
                for vif in network_info:
                    vif_driver = self._get_vif_driver(vif.get('type'))
                    vif_driver.unplug(instance, vif)
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.exception(_LE('Failed to destroy instance: %s'),
                              instance_name)

    def reboot(self, instance, network_info, reboot_type):
        """Reboot the specified instance."""
        LOG.debug("Rebooting instance", instance=instance)

        if reboot_type == REBOOT_TYPE_SOFT:
            if self._soft_shutdown(instance):
                self.power_on(instance, network_info=network_info)
                return

        self._set_vm_state(instance,
                           constants.HYPERV_VM_STATE_REBOOT)

    def _soft_shutdown(self, instance,
                       timeout=CONF.hyperv.wait_soft_reboot_seconds,
                       retry_interval=SHUTDOWN_TIME_INCREMENT):
        """Perform a soft shutdown on the VM.

           :return: True if the instance was shutdown within time limit,
                    False otherwise.
        """
        LOG.debug("Performing Soft shutdown on instance", instance=instance)

        while timeout > 0:
            # Perform a soft shutdown on the instance.
            # Wait maximum timeout for the instance to be shutdown.
            # If it was not shutdown, retry until it succeeds or a maximum of
            # time waited is equal to timeout.
            wait_time = min(retry_interval, timeout)
            try:
                LOG.debug("Soft shutdown instance, timeout remaining: %d",
                          timeout, instance=instance)
                self._vmutils.soft_shutdown_vm(instance.name)
                if self._wait_for_power_off(instance.name, wait_time):
                    LOG.info(_LI("Soft shutdown succeeded."),
                             instance=instance)
                    return True
            except vmutils.HyperVException as e:
                # Exception is raised when trying to shutdown the instance
                # while it is still booting.
                LOG.debug("Soft shutdown failed: %s", e, instance=instance)
                time.sleep(wait_time)

            timeout -= retry_interval

        LOG.warning(_LW("Timed out while waiting for soft shutdown."),
                    instance=instance)
        return False

    def pause(self, instance):
        """Pause VM instance."""
        LOG.debug("Pause instance", instance=instance)
        self._set_vm_state(instance,
                           constants.HYPERV_VM_STATE_PAUSED)

    def unpause(self, instance):
        """Unpause paused VM instance."""
        LOG.debug("Unpause instance", instance=instance)
        self._set_vm_state(instance,
                           constants.HYPERV_VM_STATE_ENABLED)

    def suspend(self, instance):
        """Suspend the specified instance."""
        LOG.debug("Suspend instance", instance=instance)
        self._set_vm_state(instance,
                           constants.HYPERV_VM_STATE_SUSPENDED)

    def resume(self, instance):
        """Resume the suspended VM instance."""
        LOG.debug("Resume instance", instance=instance)
        self._set_vm_state(instance,
                           constants.HYPERV_VM_STATE_ENABLED)

    def power_off(self, instance, timeout=0, retry_interval=0):
        """Power off the specified instance."""
        LOG.debug("Power off instance", instance=instance)

        # We must make sure that the console log workers are stopped,
        # otherwise we won't be able to delete / move VM log files.
        self._serial_console_ops.stop_console_handler(instance.name)

        if retry_interval <= 0:
            retry_interval = SHUTDOWN_TIME_INCREMENT
        try:
            if timeout and self._soft_shutdown(instance,
                                               timeout,
                                               retry_interval):
                return

            self._set_vm_state(instance,
                               constants.HYPERV_VM_STATE_DISABLED)
        except exception.NotFound:
            pass

    def power_on(self, instance, block_device_info=None, network_info=None):
        """Power on the specified instance."""
        LOG.debug("Power on instance", instance=instance)

        if block_device_info:
            self._volumeops.fix_instance_volume_disk_paths(instance.name,
                                                           block_device_info)

        self._set_vm_state(instance, constants.HYPERV_VM_STATE_ENABLED)
        if network_info:
            for vif in network_info:
                vif_driver = self._get_vif_driver(vif.get('type'))
                vif_driver.post_start(instance, vif)

    def _set_vm_state(self, instance, req_state):
        instance_name = instance.name

        try:
            self._vmutils.set_vm_state(instance_name, req_state)

            LOG.debug("Successfully changed state of VM %(instance_name)s"
                      " to: %(req_state)s", {'instance_name': instance_name,
                                             'req_state': req_state})
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.error(_LE("Failed to change vm state of %(instance_name)s"
                              " to %(req_state)s"),
                          {'instance_name': instance_name,
                           'req_state': req_state})

    def _get_vm_state(self, instance_name):
        summary_info = self._vmutils.get_vm_summary_info(instance_name)
        return summary_info['EnabledState']

    def _wait_for_power_off(self, instance_name, time_limit):
        """Waiting for a VM to be in a disabled state.

           :return: True if the instance is shutdown within time_limit,
                    False otherwise.
        """

        desired_vm_states = [constants.HYPERV_VM_STATE_DISABLED]

        def _check_vm_status(instance_name):
            if self._get_vm_state(instance_name) in desired_vm_states:
                raise loopingcall.LoopingCallDone()

        periodic_call = loopingcall.FixedIntervalLoopingCall(_check_vm_status,
                                                             instance_name)

        try:
            # add a timeout to the periodic call.
            periodic_call.start(interval=SHUTDOWN_TIME_INCREMENT)
            etimeout.with_timeout(time_limit, periodic_call.wait)
        except etimeout.Timeout:
            # VM did not shutdown in the expected time_limit.
            return False
        finally:
            # stop the periodic call, in case of exceptions or Timeout.
            periodic_call.stop()

        return True

    def resume_state_on_host_boot(self, context, instance, network_info,
                                  block_device_info=None):
        """Resume guest state when a host is booted."""
        self.power_on(instance, block_device_info, network_info)

    def _create_vm_com_port_pipes(self, instance, serial_ports):
        for port_number, port_type in serial_ports.iteritems():
            pipe_path = r'\\.\pipe\%s_%s' % (instance.uuid, port_type)
            self._vmutils.set_vm_serial_port_connection(
                instance.name, port_number, pipe_path)

    def copy_vm_dvd_disks(self, vm_name, dest_host):
        dvd_disk_paths = self._vmutils.get_vm_dvd_disk_paths(vm_name)
        dest_path = self._pathutils.get_instance_dir(
            vm_name, remote_server=dest_host)
        for path in dvd_disk_paths:
            self._pathutils.copyfile(path, dest_path)

    def _get_image_serial_port_settings(self, image_meta):
        image_props = image_meta['properties']
        serial_ports = {}

        for image_prop, port_type in constants.SERIAL_PORT_TYPES.iteritems():
            port_number = int(image_props.get(
                image_prop,
                constants.DEFAULT_SERIAL_CONSOLE_PORT))

            if port_number not in [1, 2]:
                err_msg = _("Invalid serial port number: %(port_number)s. "
                            "Only COM 1 and COM 2 are available.")
                raise vmutils.HyperVException(
                    err_msg % {'port_number': port_number})

            existing_type = serial_ports.get(port_number)
            if (not existing_type or
                    existing_type == constants.SERIAL_PORT_TYPE_RO):
                serial_ports[port_number] = port_type

        return serial_ports

    def rescue_instance(self, context, instance, network_info, image_meta,
                        rescue_password):
        rescue_image_id = image_meta.get('id') or instance.image_ref
        rescue_vhd_path = self._create_root_vhd(
            context, instance, rescue_image_id=rescue_image_id)

        rescue_vm_gen = self.get_image_vm_generation(rescue_vhd_path,
                                                     image_meta)
        vm_gen = self._vmutils.get_vm_gen(instance.name)
        if rescue_vm_gen != vm_gen:
            err_msg = _('The requested rescue image requires a different VM '
                        'generation than the actual rescued instance. '
                        'Rescue image VM generation: %(rescue_vm_gen)s. '
                        'Rescued instance VM generation: %(vm_gen)s.')
            raise vmutils.HyperVException(err_msg %
                {'rescue_vm_gen': rescue_vm_gen,
                 'vm_gen': vm_gen})

        root_vhd_path = self._pathutils.lookup_root_vhd_path(instance.name)
        if not root_vhd_path:
            err_msg = _('Instance root disk image could not be found. '
                        'Rescuing instances booted from volume is '
                        'not supported.')
            raise vmutils.HyperVException(err_msg)

        controller_type = VM_GENERATIONS_CONTROLLER_TYPES[vm_gen]

        self._vmutils.detach_vm_disk(instance.name, root_vhd_path,
                                     is_physical=False)
        self._attach_drive(instance.name, rescue_vhd_path, 0,
                           self._ROOT_DISK_CTRL_ADDR, controller_type)
        self._vmutils.attach_scsi_drive(instance.name, root_vhd_path,
                                        drive_type=constants.DISK)

        if configdrive.required_by(instance):
            self._detach_config_drive(instance.name)
            rescue_configdrive_path = self._create_config_drive(
                instance,
                injected_files=None,
                admin_password=rescue_password,
                network_info=network_info,
                rescue=True)
            self.attach_config_drive(instance, rescue_configdrive_path,
                                     vm_gen)

        self.power_on(instance)

    def unrescue_instance(self, instance):
        self.power_off(instance)

        root_vhd_path = self._pathutils.lookup_root_vhd_path(instance.name)
        rescue_vhd_path = self._pathutils.lookup_root_vhd_path(instance.name,
                                                               rescue=True)

        if (instance.vm_state == vm_states.RESCUED and
                not (rescue_vhd_path and root_vhd_path)):
            err_msg = _('Missing instance root and/or rescue image. '
                        'The instance cannot be unrescued.')
            raise vmutils.HyperVException(err_msg)

        vm_gen = self._vmutils.get_vm_gen(instance.name)
        controller_type = VM_GENERATIONS_CONTROLLER_TYPES[vm_gen]

        self._vmutils.detach_vm_disk(instance.name, root_vhd_path,
                                     is_physical=False)
        if rescue_vhd_path:
            self._vmutils.detach_vm_disk(instance.name, rescue_vhd_path,
                                         is_physical=False)
            fileutils.delete_if_exists(rescue_vhd_path)
        self._attach_drive(instance.name, root_vhd_path, 0,
                           self._ROOT_DISK_CTRL_ADDR, controller_type)
        self._detach_config_drive(instance.name, rescue=True, delete=True)

        self.power_on(instance)

    def _check_hotplug_is_available(self, instance):
        if (self._get_vm_state(instance.name) ==
                constants.HYPERV_VM_STATE_DISABLED):
            return False

        if not self._hostutils.check_min_windows_version(6, 4):
            LOG.error(_LE("This version of Windows does not support vNIC "
                          "hot plugging."))
            raise exception.InterfaceAttachFailed(
                instance_uuid=instance.uuid)

        if (self._vmutils.get_vm_gen(instance.name) ==
                constants.VM_GEN_1):
            LOG.error(_LE("Cannot hot plug vNIC to a first generation "
                          "VM."))
            raise exception.InterfaceAttachFailed(
                instance_uuid=instance.uuid)

        return True

    def attach_interface(self, instance, vif):
        hot_plug = self._check_hotplug_is_available(instance)
        self._create_and_attach_interface(instance, vif, hot_plug)

    def _create_and_attach_interface(self, instance, vif, hot_plug):
        self._vmutils.create_nic(instance.name,
                                 vif['id'],
                                 vif['address'])
        vif_driver = self._get_vif_driver(vif.get('type'))
        vif_driver.plug(instance, vif)
        if hot_plug:
            vif_driver.post_start(instance, vif)

    def detach_interface(self, instance, vif):
        self._check_hotplug_is_available(instance)
        self._detach_and_destroy_interface(instance, vif)

    def _detach_and_destroy_interface(self, instance, vif):
        vif_driver = self._get_vif_driver(vif.get('type'))
        vif_driver.unplug(instance, vif)
        self._vmutils.destroy_nic(instance.name, vif['id'])

    def _set_instance_disk_qos_specs(self, instance):
        min_iops, max_iops = self._get_storage_qos_specs(instance)
        if min_iops or max_iops:
            local_disks = self._get_instance_local_disks(instance.name)
            for disk_path in local_disks:
                self._vmutils.set_disk_qos_specs(instance.name, disk_path,
                                                 min_iops, max_iops)

    def _get_instance_local_disks(self, instance_name):
        instance_path = self._pathutils.get_instance_dir(instance_name)
        instance_disks = self._vmutils.get_vm_storage_paths(instance_name)[0]
        local_disks = [disk_path for disk_path in instance_disks
                       if disk_path.find(instance_path) != -1]
        return local_disks

    def _get_storage_qos_specs(self, instance):
        extra_specs = instance.flavor.get('extra_specs') or {}
        storage_qos_specs = {}
        for spec, value in extra_specs.iteritems():
            if ':' in spec:
                scope, key = spec.split(':')
                if scope == 'storage_qos':
                    storage_qos_specs[key] = value
        return self._volumeops.parse_disk_qos_specs(storage_qos_specs)
