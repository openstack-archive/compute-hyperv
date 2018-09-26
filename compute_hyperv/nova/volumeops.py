# Copyright 2012 Pedro Navarro Perez
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
Management class for Storage-related functions (attach, detach, etc).
"""
import inspect
import os
import time

from nova.compute import task_states
from nova import exception
from nova import objects
from nova import utils
from nova.virt import block_device as driver_block_device
from nova.virt import driver
from nova.volume import cinder
from os_brick.initiator import connector
from os_win import constants as os_win_const
from os_win import utilsfactory
from oslo_log import log as logging
from oslo_utils import excutils
from oslo_utils import importutils
from oslo_utils import strutils

from compute_hyperv.i18n import _
import compute_hyperv.nova.conf
from compute_hyperv.nova import constants
from compute_hyperv.nova import pathutils

LOG = logging.getLogger(__name__)

CONF = compute_hyperv.nova.conf.CONF


def volume_snapshot_lock(f):
    """Synchronizes volume snapshot related operations.

    The locks will be applied on a per-instance basis. The decorated method
    must accept an instance object.
    """
    def inner(*args, **kwargs):
        all_args = inspect.getcallargs(f, *args, **kwargs)
        instance = all_args['instance']

        lock_name = "volume-snapshot-%s" % instance.name

        @utils.synchronized(lock_name)
        def synchronized():
            return f(*args, **kwargs)

        return synchronized()
    return inner


class VolumeOps(object):
    """Management class for Volume-related tasks
    """

    def __init__(self):
        self._volume_api = cinder.API()
        self._vmops_prop = None
        self._block_dev_man_prop = None

        self._vmutils = utilsfactory.get_vmutils()
        self._default_root_device = 'vda'

        self._load_volume_drivers()

    def _load_volume_drivers(self):
        self.volume_drivers = {
            constants.STORAGE_PROTOCOL_SMBFS: SMBFSVolumeDriver(),
            constants.STORAGE_PROTOCOL_ISCSI: ISCSIVolumeDriver(),
            constants.STORAGE_PROTOCOL_FC: FCVolumeDriver()}

    @property
    def _vmops(self):
        # We have to avoid a circular dependency.
        if not self._vmops_prop:
            self._vmops_prop = importutils.import_class(
                'compute_hyperv.nova.vmops.VMOps')()
        return self._vmops_prop

    @property
    def _block_dev_man(self):
        if not self._block_dev_man_prop:
            self._block_dev_man_prop = importutils.import_class(
                'compute_hyperv.nova.block_device_manager.'
                'BlockDeviceInfoManager')()
        return self._block_dev_man_prop

    def _get_volume_driver(self, connection_info):
        driver_type = connection_info.get('driver_volume_type')
        if driver_type not in self.volume_drivers:
            raise exception.VolumeDriverNotFound(driver_type=driver_type)
        return self.volume_drivers[driver_type]

    def validate_host_configuration(self):
        for protocol, volume_driver in self.volume_drivers.items():
            try:
                volume_driver.validate_host_configuration()
            except exception.ValidationError as ex:
                LOG.warning(
                    "Volume driver %(protocol)s reported a validation "
                    "error. Attaching such volumes will probably fail. "
                    "Error message: %(err_msg)s.",
                    dict(protocol=protocol, err_msg=ex.message))

    def attach_volumes(self, context, volumes, instance):
        for vol in volumes:
            self.attach_volume(context, vol['connection_info'], instance)

    def disconnect_volumes(self, block_device_info):
        mapping = driver.block_device_info_get_mapping(block_device_info)
        for vol in mapping:
            self.disconnect_volume(vol['connection_info'])

    def attach_volume(self, context, connection_info, instance,
                      disk_bus=constants.CTRL_TYPE_SCSI,
                      update_device_metadata=False):
        tries_left = CONF.hyperv.volume_attach_retry_count + 1

        while tries_left:
            try:
                self._attach_volume(context,
                                    connection_info,
                                    instance,
                                    disk_bus,
                                    update_device_metadata)
                break
            except Exception as ex:
                tries_left -= 1
                if not tries_left:
                    LOG.exception(
                        "Failed to attach volume %(connection_info)s "
                        "to instance %(instance_name)s. ",
                        {'connection_info': strutils.mask_dict_password(
                             connection_info),
                         'instance_name': instance.name})

                    # We're requesting a detach as the disk may have
                    # been attached to the instance but one of the
                    # post-attach operations failed.
                    self.detach_volume(context,
                                       connection_info,
                                       instance,
                                       update_device_metadata)
                    raise exception.VolumeAttachFailed(
                        volume_id=connection_info['serial'],
                        reason=ex)
                else:
                    LOG.warning(
                        "Failed to attach volume %(connection_info)s "
                        "to instance %(instance_name)s. "
                        "Tries left: %(tries_left)s.",
                        {'connection_info': strutils.mask_dict_password(
                             connection_info),
                         'instance_name': instance.name,
                         'tries_left': tries_left})

                    time.sleep(CONF.hyperv.volume_attach_retry_interval)

    def _attach_volume(self, context, connection_info, instance,
                       disk_bus=constants.CTRL_TYPE_SCSI,
                       update_device_metadata=False):
        LOG.debug(
            "Attaching volume: %(connection_info)s to %(instance_name)s",
            {'connection_info': strutils.mask_dict_password(connection_info),
             'instance_name': instance.name})
        volume_driver = self._get_volume_driver(connection_info)
        volume_driver.attach_volume(connection_info,
                                    instance.name,
                                    disk_bus)

        if update_device_metadata:
            # When attaching volumes to already existing instances,
            # the connection info passed to the driver is not saved
            # yet within the BDM table.
            self._block_dev_man.set_volume_bdm_connection_info(
                context, instance, connection_info)
            self._vmops.update_device_metadata(
                context, instance)

        qos_specs = connection_info['data'].get('qos_specs') or {}
        if qos_specs:
            volume_driver.set_disk_qos_specs(connection_info,
                                             qos_specs)

    def disconnect_volume(self, connection_info):
        volume_driver = self._get_volume_driver(connection_info)
        volume_driver.disconnect_volume(connection_info)

    def detach_volume(self, context, connection_info, instance,
                      update_device_metadata=False):
        LOG.debug("Detaching volume: %(connection_info)s "
                  "from %(instance_name)s",
                  {'connection_info': strutils.mask_dict_password(
                      connection_info),
                   'instance_name': instance.name})
        volume_driver = self._get_volume_driver(connection_info)
        volume_driver.detach_volume(connection_info, instance.name)
        volume_driver.disconnect_volume(connection_info)

        if update_device_metadata:
            self._vmops.update_device_metadata(context, instance)

    def fix_instance_volume_disk_paths(self, instance_name, block_device_info):
        # Mapping containing the current disk paths for each volume.
        actual_disk_mapping = self.get_disk_path_mapping(block_device_info)
        if not actual_disk_mapping:
            return

        # Mapping containing virtual disk resource path and the physical
        # disk path for each volume serial number. The physical path
        # associated with this resource may not be the right one,
        # as physical disk paths can get swapped after host reboots.
        vm_disk_mapping = self._vmutils.get_vm_physical_disk_mapping(
            instance_name)

        for serial, vm_disk in vm_disk_mapping.items():
            actual_disk_path = actual_disk_mapping[serial]
            if vm_disk['mounted_disk_path'] != actual_disk_path:
                self._vmutils.set_disk_host_res(vm_disk['resource_path'],
                                                actual_disk_path)

    def get_volume_connector(self):
        # NOTE(lpetrut): the Windows os-brick connectors
        # do not use a root helper.
        conn = connector.get_connector_properties(
            root_helper=None,
            my_ip=CONF.my_block_storage_ip,
            multipath=CONF.hyperv.use_multipath_io,
            enforce_multipath=True,
            host=CONF.host)
        return conn

    def connect_volumes(self, block_device_info):
        mapping = driver.block_device_info_get_mapping(block_device_info)
        for vol in mapping:
            connection_info = vol['connection_info']
            volume_driver = self._get_volume_driver(connection_info)
            volume_driver.connect_volume(connection_info)

    def get_disk_path_mapping(self, block_device_info, block_dev_only=False):
        block_mapping = driver.block_device_info_get_mapping(block_device_info)
        disk_path_mapping = {}
        for vol in block_mapping:
            connection_info = vol['connection_info']
            disk_serial = connection_info['serial']

            volume_driver = self._get_volume_driver(connection_info)
            if block_dev_only and not volume_driver._is_block_dev:
                continue

            disk_path = volume_driver.get_disk_resource_path(connection_info)
            disk_path_mapping[disk_serial] = disk_path
        return disk_path_mapping

    def get_disk_resource_path(self, connection_info):
        volume_driver = self._get_volume_driver(connection_info)
        return volume_driver.get_disk_resource_path(connection_info)

    @staticmethod
    def bytes_per_sec_to_iops(no_bytes):
        # Hyper-v uses normalized IOPS (8 KB increments)
        # as IOPS allocation units.
        return (
            (no_bytes + constants.IOPS_BASE_SIZE - 1) //
            constants.IOPS_BASE_SIZE)

    @staticmethod
    def validate_qos_specs(qos_specs, supported_qos_specs):
        unsupported_specs = set(qos_specs.keys()).difference(
            supported_qos_specs)
        if unsupported_specs:
            LOG.warning('Got unsupported QoS specs: '
                        '%(unsupported_specs)s. '
                        'Supported qos specs: %(supported_qos_specs)s',
                        {'unsupported_specs': unsupported_specs,
                         'supported_qos_specs': supported_qos_specs})

    @volume_snapshot_lock
    def volume_snapshot_create(self, context, instance, volume_id,
                               create_info):
        LOG.debug("Creating snapshot for volume %(volume_id)s on instance "
                  "%(instance_name)s with create info %(create_info)s",
                  {"volume_id": volume_id,
                   "instance_name": instance.name,
                   "create_info": create_info})
        snapshot_id = create_info['snapshot_id']

        snapshot_failed = False
        try:
            instance.task_state = task_states.IMAGE_SNAPSHOT_PENDING
            instance.save(expected_task_state=[None])

            bdm = objects.BlockDeviceMapping.get_by_volume_and_instance(
                context, volume_id, instance.uuid)
            driver_bdm = driver_block_device.convert_volume(bdm)
            connection_info = driver_bdm['connection_info']

            volume_driver = self._get_volume_driver(connection_info)
            volume_driver.create_snapshot(connection_info, instance,
                                          create_info)

            # The volume driver is expected to
            # update the connection info.
            driver_bdm.save()
        except Exception:
            with excutils.save_and_reraise_exception():
                snapshot_failed = True

                err_msg = ('Error occurred while snapshotting volume. '
                           'sending error status to Cinder.')
                LOG.exception(err_msg,
                              instance=instance)
        finally:
            instance.task_state = None
            instance.save(
                expected_task_state=[task_states.IMAGE_SNAPSHOT_PENDING])

            snapshot_status = 'error' if snapshot_failed else 'creating'
            self._volume_api.update_snapshot_status(
                context, snapshot_id, snapshot_status)

    @volume_snapshot_lock
    def volume_snapshot_delete(self, context, instance, volume_id,
                               snapshot_id, delete_info):
        LOG.debug("Deleting snapshot for volume %(volume_id)s on instance "
                  "%(instance_name)s with delete info %(delete_info)s",
                  {"volume_id": volume_id,
                   "instance_name": instance.name,
                   "delete_info": delete_info})

        snapshot_delete_failed = False
        try:
            instance.task_state = task_states.IMAGE_SNAPSHOT_PENDING
            instance.save(expected_task_state=[None])

            bdm = objects.BlockDeviceMapping.get_by_volume_and_instance(
                context, volume_id, instance.uuid)
            driver_bdm = driver_block_device.convert_volume(bdm)
            connection_info = driver_bdm['connection_info']

            volume_driver = self._get_volume_driver(connection_info)
            volume_driver.delete_snapshot(connection_info, instance,
                                          delete_info)

            # The volume driver is expected to
            # update the connection info.
            driver_bdm.save()
        except Exception:
            with excutils.save_and_reraise_exception():
                snapshot_delete_failed = True

                err_msg = ('Error occurred while deleting volume '
                           'snapshot. Sending error status to Cinder.')
                LOG.exception(err_msg,
                              instance=instance)
        finally:
            instance.task_state = None
            instance.save(
                expected_task_state=[task_states.IMAGE_SNAPSHOT_PENDING])

            snapshot_status = ('error_deleting'
                               if snapshot_delete_failed else 'deleting')
            self._volume_api.update_snapshot_status(
                context, snapshot_id, snapshot_status)

    def get_disk_attachment_info(self, connection_info):
        volume_driver = self._get_volume_driver(connection_info)
        return volume_driver.get_disk_attachment_info(connection_info)

    def extend_volume(self, connection_info):
        volume_driver = self._get_volume_driver(connection_info)
        return volume_driver.extend_volume(connection_info)


class BaseVolumeDriver(object):
    _is_block_dev = True
    _protocol = None
    _extra_connector_args = {}

    def __init__(self):
        self._conn = None
        self._diskutils = utilsfactory.get_diskutils()
        self._vmutils = utilsfactory.get_vmutils()
        self._migrutils = utilsfactory.get_migrationutils()
        self._metricsutils = utilsfactory.get_metricsutils()

    @property
    def _connector(self):
        if not self._conn:
            scan_attempts = CONF.hyperv.mounted_disk_query_retry_count
            scan_interval = CONF.hyperv.mounted_disk_query_retry_interval

            self._conn = connector.InitiatorConnector.factory(
                protocol=self._protocol,
                root_helper=None,
                use_multipath=CONF.hyperv.use_multipath_io,
                device_scan_attempts=scan_attempts,
                device_scan_interval=scan_interval,
                **self._extra_connector_args)
        return self._conn

    def connect_volume(self, connection_info):
        return self._connector.connect_volume(connection_info['data'])

    def disconnect_volume(self, connection_info):
        self._connector.disconnect_volume(connection_info['data'])

    def get_disk_resource_path(self, connection_info):
        disk_paths = self._connector.get_volume_paths(connection_info['data'])
        if not disk_paths:
            vol_id = connection_info['serial']
            err_msg = _("Could not find disk path. Volume id: %s")
            raise exception.DiskNotFound(err_msg % vol_id)

        return self._get_disk_res_path(disk_paths[0])

    def validate_host_configuration(self):
        if self._is_block_dev:
            self._check_san_policy()

    def _get_disk_res_path(self, disk_path):
        if self._is_block_dev:
            # We need the Msvm_DiskDrive resource path as this
            # will be used when the disk is attached to an instance.
            disk_number = self._diskutils.get_device_number_from_device_name(
                disk_path)
            disk_res_path = self._vmutils.get_mounted_disk_by_drive_number(
                disk_number)
        else:
            disk_res_path = disk_path

        if not disk_res_path:
            err_msg = _("Could not find an attachable disk resource path "
                        "for disk: %s") % disk_path
            raise exception.DiskNotFound(err_msg)
        return disk_res_path

    def _check_san_policy(self):
        disk_policy = self._diskutils.get_new_disk_policy()

        accepted_policies = [os_win_const.DISK_POLICY_OFFLINE_SHARED,
                             os_win_const.DISK_POLICY_OFFLINE_ALL]

        if disk_policy not in accepted_policies:
            err_msg = _("Invalid SAN policy. The SAN policy "
                        "must be set to 'Offline Shared' or 'Offline All' "
                        "in order to attach passthrough disks to instances.")
            raise exception.ValidationError(message=err_msg)

    def attach_volume(self, connection_info, instance_name,
                      disk_bus=constants.CTRL_TYPE_SCSI):
        self.validate_host_configuration()

        dev_info = self.connect_volume(connection_info)

        serial = connection_info['serial']
        disk_path = self._get_disk_res_path(dev_info['path'])
        ctrller_path, slot = self._get_disk_ctrl_and_slot(instance_name,
                                                          disk_bus)
        if self._is_block_dev:
            # We need to tag physical disk resources with the volume
            # serial number, in order to be able to retrieve them
            # during live migration.
            self._vmutils.attach_volume_to_controller(instance_name,
                                                      ctrller_path,
                                                      slot,
                                                      disk_path,
                                                      serial=serial)
        else:
            self._vmutils.attach_drive(instance_name,
                                       disk_path,
                                       ctrller_path,
                                       slot)

        self._configure_disk_metrics(disk_path)

    def _configure_disk_metrics(self, disk_path):
        if not CONF.hyperv.enable_instance_metrics_collection:
            return

        if self._is_block_dev:
            LOG.warning("Hyper-V does not support collecting metrics for "
                        "passthrough disks (e.g. iSCSI/FC).")
            return

        LOG.debug("Enabling disk metrics: %s.", disk_path)
        self._metricsutils.enable_disk_metrics_collection(
            disk_path, is_physical=self._is_block_dev)

    def detach_volume(self, connection_info, instance_name):
        if self._migrutils.planned_vm_exists(instance_name):
            LOG.warning("Instance %s is a Planned VM, cannot detach "
                        "volumes from it.", instance_name)
            return
        # Retrieving the disk path can be a time consuming operation in
        # case of passthrough disks. As such disks attachments will be
        # tagged using the volume id, we'll just use that instead.
        #
        # Note that Hyper-V does not allow us to attach the same passthrough
        # disk to multiple instances, which means that we're safe to rely
        # on this tag.
        if not self._is_block_dev:
            disk_path = self.get_disk_resource_path(connection_info)
            # In this case, we're not tagging the disks, so we want os-win
            # to use the disk path to identify the attachment.
            serial = None
        else:
            disk_path = None
            serial = connection_info['serial']

        LOG.debug("Detaching disk from instance: %(instance_name)s. "
                  "Disk path: %(disk_path)s. Disk serial tag: %(serial)s.",
                  dict(disk_path=disk_path,
                       serial=serial,
                       instance_name=instance_name))
        self._vmutils.detach_vm_disk(instance_name, disk_path,
                                     is_physical=self._is_block_dev,
                                     serial=serial)

    def _get_disk_ctrl_and_slot(self, instance_name, disk_bus):
        if disk_bus == constants.CTRL_TYPE_IDE:
            # Find the IDE controller for the vm.
            ctrller_path = self._vmutils.get_vm_ide_controller(
                instance_name, 0)
            # Attaching to the first slot
            slot = 0
        else:
            # Find the SCSI controller for the vm
            ctrller_path = self._vmutils.get_vm_scsi_controller(
                instance_name)
            slot = self._vmutils.get_free_controller_slot(ctrller_path)
        return ctrller_path, slot

    def set_disk_qos_specs(self, connection_info, disk_qos_specs):
        LOG.info("The %(protocol)s Hyper-V volume driver "
                 "does not support QoS. Ignoring QoS specs.",
                 dict(protocol=self._protocol))

    def create_snapshot(self, connection_info, instance, create_info):
        raise NotImplementedError()

    def delete_snapshot(self, connection_info, instance, delete_info):
        raise NotImplementedError()

    def get_disk_attachment_info(self, connection_info):
        if self._is_block_dev:
            disk_path = None
            serial = connection_info['serial']
        else:
            disk_path = self.get_disk_resource_path(connection_info)
            serial = None

        return self._vmutils.get_disk_attachment_info(
            disk_path,
            is_physical=self._is_block_dev,
            serial=serial)

    def extend_volume(self, connection_info):
        # We're not actually extending the volume, we're just
        # refreshing cached information about an already extended volume.
        self._connector.extend_volume(connection_info['data'])


class ISCSIVolumeDriver(BaseVolumeDriver):
    _is_block_dev = True
    _protocol = constants.STORAGE_PROTOCOL_ISCSI

    def __init__(self, *args, **kwargs):
        self._extra_connector_args = dict(
            initiator_list=CONF.hyperv.iscsi_initiator_list)

        super(ISCSIVolumeDriver, self).__init__(*args, **kwargs)


class SMBFSVolumeDriver(BaseVolumeDriver):
    _is_block_dev = False
    _protocol = constants.STORAGE_PROTOCOL_SMBFS
    _extra_connector_args = dict(local_path_for_loopback=True)

    def __init__(self):
        self._vmops_prop = None
        self._pathutils = pathutils.PathUtils()
        self._vhdutils = utilsfactory.get_vhdutils()
        super(SMBFSVolumeDriver, self).__init__()

    @property
    def _vmops(self):
        # We have to avoid a circular dependency.
        if not self._vmops_prop:
            self._vmops_prop = importutils.import_class(
                'compute_hyperv.nova.vmops.VMOps')()
        return self._vmops_prop

    def export_path_synchronized(f):
        def wrapper(inst, connection_info, *args, **kwargs):
            export_path = inst._get_export_path(connection_info)

            @utils.synchronized(export_path)
            def inner():
                return f(inst, connection_info, *args, **kwargs)
            return inner()
        return wrapper

    def _get_export_path(self, connection_info):
        return connection_info['data']['export'].replace('/', '\\')

    @export_path_synchronized
    def attach_volume(self, *args, **kwargs):
        super(SMBFSVolumeDriver, self).attach_volume(*args, **kwargs)

    @export_path_synchronized
    def disconnect_volume(self, *args, **kwargs):
        # We synchronize those operations based on the share path in order to
        # avoid the situation when a SMB share is unmounted while a volume
        # exported by it is about to be attached to an instance.
        super(SMBFSVolumeDriver, self).disconnect_volume(*args, **kwargs)

    def set_disk_qos_specs(self, connection_info, qos_specs):
        supported_qos_specs = ['total_iops_sec', 'total_bytes_sec']
        VolumeOps.validate_qos_specs(qos_specs, supported_qos_specs)

        total_bytes_sec = int(qos_specs.get('total_bytes_sec') or 0)
        total_iops_sec = int(qos_specs.get('total_iops_sec') or
                             VolumeOps.bytes_per_sec_to_iops(
                                total_bytes_sec))

        if total_iops_sec:
            disk_path = self.get_disk_resource_path(connection_info)
            self._vmutils.set_disk_qos_specs(disk_path, total_iops_sec)

    def create_snapshot(self, connection_info, instance, create_info):
        attached_path = self.get_disk_resource_path(connection_info)
        # Cinder tells us the new differencing disk file name it expects.
        # The image does not exist yet, so we'll have to create it.
        new_path = os.path.join(os.path.dirname(attached_path),
                                create_info['new_file'])
        attachment_info = self._vmutils.get_disk_attachment_info(
            attached_path, is_physical=False)
        disk_ctrl_type = attachment_info['controller_type']

        if disk_ctrl_type == constants.CTRL_TYPE_SCSI:
            self._create_snapshot_scsi(instance, attachment_info,
                                       attached_path, new_path)
        else:
            # IDE disks cannot be hotplugged.
            self._create_snapshot_ide(instance, attached_path, new_path)

        connection_info['data']['name'] = create_info['new_file']

    def _create_snapshot_ide(self, instance, attached_path, new_path):
        with self._vmops.prepare_for_volume_snapshot(instance):
            self._vhdutils.create_differencing_vhd(new_path, attached_path)
            self._vmutils.update_vm_disk_path(attached_path, new_path,
                                              is_physical=False)

    def _create_snapshot_scsi(self, instance, attachment_info,
                              attached_path, new_path):
        with self._vmops.prepare_for_volume_snapshot(instance,
                                                     allow_paused=True):
            self._vmutils.detach_vm_disk(instance.name,
                                         attached_path,
                                         is_physical=False)
            self._vhdutils.create_differencing_vhd(new_path, attached_path)
            self._vmutils.attach_drive(instance.name,
                                       new_path,
                                       attachment_info['controller_path'],
                                       attachment_info['controller_slot'])

    def delete_snapshot(self, connection_info, instance, delete_info):
        attached_path = self.get_disk_resource_path(connection_info)
        attachment_info = self._vmutils.get_disk_attachment_info(
            attached_path, is_physical=False)
        disk_ctrl_type = attachment_info['controller_type']

        base_dir = os.path.dirname(attached_path)
        file_to_merge_name = delete_info['file_to_merge']
        file_to_merge = os.path.join(base_dir, file_to_merge_name)

        allow_paused = disk_ctrl_type == constants.CTRL_TYPE_SCSI
        with self._vmops.prepare_for_volume_snapshot(
                instance,
                allow_paused=allow_paused):
            curr_state = self._vmutils.get_vm_state(instance.name)
            # We need to detach the image in order to alter the vhd chain
            # while the instance is paused.
            needs_detach = curr_state == os_win_const.HYPERV_VM_STATE_PAUSED

            if needs_detach:
                self._vmutils.detach_vm_disk(instance.name,
                                             attached_path,
                                             is_physical=False)
            new_top_img_path = self._do_delete_snapshot(attached_path,
                                                        file_to_merge)
            attachment_changed = (attached_path.lower() !=
                                  new_top_img_path.lower())

            if needs_detach:
                self._vmutils.attach_drive(instance.name,
                                           new_top_img_path,
                                           attachment_info['controller_path'],
                                           attachment_info['controller_slot'])
            elif attachment_changed:
                # When merging the latest snapshot, we have to update
                # the attachment. Luckily, although we cannot detach
                # IDE disks, we can swap them.
                self._vmutils.update_vm_disk_path(attached_path,
                                                  new_top_img_path,
                                                  is_physical=False)

            connection_info['data']['name'] = os.path.basename(
                new_top_img_path)

    def _do_delete_snapshot(self, attached_path, file_to_merge):
        parent_path = self._vhdutils.get_vhd_parent_path(file_to_merge)
        path_to_reconnect = None

        merging_top_image = attached_path.lower() == file_to_merge.lower()
        if not merging_top_image:
            path_to_reconnect = self._get_higher_image_from_chain(
                file_to_merge, attached_path)

        # We'll let Cinder delete this image. At this point, Cinder may
        # safely query it, considering that it will no longer be in-use.
        self._vhdutils.merge_vhd(file_to_merge,
                                 delete_merged_image=False)

        if path_to_reconnect:
            self._vhdutils.reconnect_parent_vhd(path_to_reconnect,
                                                parent_path)

        new_top_img_path = (parent_path if merging_top_image
                            else attached_path)
        return new_top_img_path

    def _get_higher_image_from_chain(self, vhd_path, top_vhd_path):
        # We're searching for the child image of the specified vhd.
        # We start by looking at the top image, looping through the
        # parent images.
        current_path = top_vhd_path
        parent_path = self._vhdutils.get_vhd_parent_path(current_path)
        while parent_path:
            if parent_path.lower() == vhd_path.lower():
                return current_path

            current_path = parent_path
            parent_path = self._vhdutils.get_vhd_parent_path(current_path)

        err_msg = _("Could not find image %(vhd_path)s in the chain using "
                    "top level image %(top_vhd_path)s")
        raise exception.ImageNotFound(
            err_msg % dict(vhd_path=vhd_path, top_vhd_path=top_vhd_path))


class FCVolumeDriver(BaseVolumeDriver):
    _is_block_dev = True
    _protocol = constants.STORAGE_PROTOCOL_FC
