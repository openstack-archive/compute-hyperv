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
Management class for migration / resize operations.
"""
import os
import re

from nova import block_device
import nova.conf
from nova import exception
from nova.virt import configdrive
from nova.virt import driver
from os_win import utilsfactory
from oslo_log import log as logging
from oslo_utils import excutils
from oslo_utils import units

from compute_hyperv.i18n import _
from compute_hyperv.nova import block_device_manager
from compute_hyperv.nova import constants
from compute_hyperv.nova import imagecache
from compute_hyperv.nova import pathutils
from compute_hyperv.nova import vmops
from compute_hyperv.nova import volumeops

LOG = logging.getLogger(__name__)
CONF = nova.conf.CONF


class MigrationOps(object):

    _ADMINISTRATIVE_SHARE_RE = re.compile(r'\\\\.*\\[a-zA-Z]\$\\.*')

    def __init__(self):
        self._vmutils = utilsfactory.get_vmutils()
        self._vhdutils = utilsfactory.get_vhdutils()
        self._pathutils = pathutils.PathUtils()
        self._volumeops = volumeops.VolumeOps()
        self._vmops = vmops.VMOps()
        self._imagecache = imagecache.ImageCache()
        self._block_dev_man = block_device_manager.BlockDeviceInfoManager()
        self._migrationutils = utilsfactory.get_migrationutils()
        self._metricsutils = utilsfactory.get_metricsutils()

    def _move_vm_files(self, instance):
        instance_path = self._pathutils.get_instance_dir(instance.name)
        revert_path = self._pathutils.get_instance_migr_revert_dir(
            instance_path, remove_dir=True, create_dir=True)
        export_path = self._pathutils.get_export_dir(
            instance_dir=revert_path, create_dir=True)

        # copy the given instance's files to a _revert folder, as backup.
        LOG.debug("Moving instance files to a revert path: %s",
                  revert_path, instance=instance)
        self._pathutils.move_folder_files(instance_path, revert_path)
        self._pathutils.copy_vm_config_files(instance.name, export_path)

        return revert_path

    def _check_target_flavor(self, instance, flavor, block_device_info):
        ephemerals = driver.block_device_info_get_ephemerals(block_device_info)
        eph_size = (block_device.get_bdm_ephemeral_disk_size(ephemerals) or
                    instance.flavor.ephemeral_gb)

        new_root_gb = flavor.root_gb
        curr_root_gb = instance.flavor.root_gb
        new_eph_size = flavor.ephemeral_gb

        root_down = new_root_gb < curr_root_gb
        ephemeral_down = new_eph_size < eph_size
        booted_from_volume = self._block_dev_man.is_boot_from_volume(
            block_device_info)

        if root_down and not booted_from_volume:
            raise exception.InstanceFaultRollback(
                exception.CannotResizeDisk(
                    reason=_("Cannot resize the root disk to a smaller size. "
                             "Current size: %(curr_root_gb)s GB. Requested "
                             "size: %(new_root_gb)s GB.") % {
                                 'curr_root_gb': curr_root_gb,
                                 'new_root_gb': new_root_gb}))
        # We allow having a new flavor with no ephemeral storage, in which
        # case we'll just remove all the ephemeral disks.
        elif ephemeral_down and new_eph_size:
            reason = (_("The new flavor ephemeral size (%(flavor_eph)s) is "
                       "smaller than the current total ephemeral disk size: "
                       "%(current_eph)s.") %
                      dict(flavor_eph=flavor.ephemeral_gb,
                           current_eph=eph_size))
            raise exception.InstanceFaultRollback(
                exception.CannotResizeDisk(reason=reason))

    def migrate_disk_and_power_off(self, context, instance, dest,
                                   flavor, network_info,
                                   block_device_info=None, timeout=0,
                                   retry_interval=0):
        LOG.debug("migrate_disk_and_power_off called", instance=instance)

        self._check_target_flavor(instance, flavor, block_device_info)

        self._vmops.power_off(instance, timeout, retry_interval)
        instance_path = self._move_vm_files(instance)

        instance.system_metadata['backup_location'] = instance_path
        instance.save()

        self._vmops.destroy(instance, network_info,
                            block_device_info, destroy_disks=True,
                            cleanup_migration_files=False)

        # return the instance's path location.
        return instance_path

    def confirm_migration(self, context, migration, instance, network_info):
        LOG.debug("confirm_migration called", instance=instance)
        revert_path = instance.system_metadata['backup_location']
        export_path = self._pathutils.get_export_dir(instance_dir=revert_path)
        self._pathutils.check_dir(export_path, remove_dir=True)
        self._pathutils.check_dir(revert_path, remove_dir=True)

    def _revert_migration_files(self, instance):
        revert_path = instance.system_metadata['backup_location']
        instance_path = revert_path.rstrip('_revert')

        # the instance dir might still exist, if the destination node kept
        # the files on the original node.
        self._pathutils.check_dir(instance_path, remove_dir=True)
        self._pathutils.rename(revert_path, instance_path)
        return instance_path

    def _check_and_attach_config_drive(self, instance, vm_gen):
        if configdrive.required_by(instance):
            configdrive_path = self._pathutils.lookup_configdrive_path(
                instance.name)
            if configdrive_path:
                self._vmops.attach_config_drive(instance, configdrive_path,
                                                vm_gen)
            else:
                raise exception.ConfigDriveNotFound(
                    instance_uuid=instance.uuid)

    def finish_revert_migration(self, context, instance, network_info,
                                block_device_info=None, power_on=True):
        LOG.debug("finish_revert_migration called", instance=instance)
        instance_path = self._revert_migration_files(instance)

        image_meta = self._imagecache.get_image_details(context, instance)
        self._import_and_setup_vm(context, instance, instance_path, image_meta,
                                  block_device_info)

        if power_on:
            self._vmops.power_on(instance, network_info=network_info)

    def _merge_base_vhd(self, diff_vhd_path, base_vhd_path):
        base_vhd_copy_path = os.path.join(os.path.dirname(diff_vhd_path),
                                          os.path.basename(base_vhd_path))
        try:
            LOG.debug('Copying base disk %(base_vhd_path)s to '
                      '%(base_vhd_copy_path)s',
                      {'base_vhd_path': base_vhd_path,
                       'base_vhd_copy_path': base_vhd_copy_path})
            self._pathutils.copyfile(base_vhd_path, base_vhd_copy_path)

            LOG.debug("Reconnecting copied base VHD "
                      "%(base_vhd_copy_path)s and diff "
                      "VHD %(diff_vhd_path)s",
                      {'base_vhd_copy_path': base_vhd_copy_path,
                       'diff_vhd_path': diff_vhd_path})
            self._vhdutils.reconnect_parent_vhd(diff_vhd_path,
                                                base_vhd_copy_path)

            LOG.debug("Merging differential disk %s into its parent.",
                      diff_vhd_path)
            self._vhdutils.merge_vhd(diff_vhd_path)

            # Replace the differential VHD with the merged one
            self._pathutils.rename(base_vhd_copy_path, diff_vhd_path)
        except Exception:
            with excutils.save_and_reraise_exception():
                if self._pathutils.exists(base_vhd_copy_path):
                    self._pathutils.remove(base_vhd_copy_path)

    def _check_resize_vhd(self, vhd_path, vhd_info, new_size):
        curr_size = vhd_info['VirtualSize']
        if new_size < curr_size:
            raise exception.CannotResizeDisk(
                reason=_("Cannot resize the root disk to a smaller size. "
                         "Current size: %(curr_root_gb)s GB. Requested "
                         "size: %(new_root_gb)s GB.") % {
                             'curr_root_gb': curr_size / units.Gi,
                             'new_root_gb': new_size / units.Gi})
        elif new_size > curr_size:
            self._resize_vhd(vhd_path, new_size)

    def _resize_vhd(self, vhd_path, new_size):
        if vhd_path.split('.')[-1].lower() == "vhd":
            LOG.debug("Getting parent disk info for disk: %s", vhd_path)
            base_disk_path = self._vhdutils.get_vhd_parent_path(vhd_path)
            if base_disk_path:
                # A differential VHD cannot be resized. This limitation
                # does not apply to the VHDX format.
                self._merge_base_vhd(vhd_path, base_disk_path)
        LOG.debug("Resizing disk \"%(vhd_path)s\" to new max "
                  "size %(new_size)s",
                  {'vhd_path': vhd_path, 'new_size': new_size})
        self._vhdutils.resize_vhd(vhd_path, new_size)

    def _check_base_disk(self, context, instance, diff_vhd_path,
                         src_base_disk_path):
        base_vhd_path = self._imagecache.get_cached_image(context, instance)

        # If the location of the base host differs between source
        # and target hosts we need to reconnect the base disk
        if src_base_disk_path.lower() != base_vhd_path.lower():
            LOG.debug("Reconnecting copied base VHD "
                      "%(base_vhd_path)s and diff "
                      "VHD %(diff_vhd_path)s",
                      {'base_vhd_path': base_vhd_path,
                       'diff_vhd_path': diff_vhd_path})
            self._vhdutils.reconnect_parent_vhd(diff_vhd_path,
                                                base_vhd_path)

    def _migrate_disks_from_source(self, migration, instance,
                                   source_inst_dir):
        source_inst_dir = self._pathutils.get_remote_path(
            migration.source_compute, source_inst_dir)
        source_export_path = self._pathutils.get_export_dir(
            instance_dir=source_inst_dir)

        if CONF.hyperv.move_disks_on_cold_migration:
            # copy the files from the source node to this node's configured
            # location.
            inst_dir = self._pathutils.get_instance_dir(
                instance.name, create_dir=True, remove_dir=True)
        elif self._ADMINISTRATIVE_SHARE_RE.match(source_inst_dir):
            # make sure that the source is not a remote local path.
            # e.g.: \\win-srv\\C$\OpenStack\Instances\..
            # CSVs, local paths, and shares are fine.
            # NOTE(claudiub): get rid of the final _revert part of the path.
            # rstrip can remove more than _revert, which is not desired.
            inst_dir = source_inst_dir.rsplit('_revert', 1)[0]
            LOG.warning(
                'Host is configured not to copy disks on cold migration, but '
                'the instance will not be able to start with the remote path: '
                '"%s". Only local, share, or CSV paths are acceptable.',
                inst_dir)
            inst_dir = self._pathutils.get_instance_dir(
                instance.name, create_dir=True, remove_dir=True)
        else:
            # make a copy on the source node's configured location.
            # strip the _revert from the source backup dir.
            inst_dir = source_inst_dir.rsplit('_revert', 1)[0]
            self._pathutils.check_dir(inst_dir, create_dir=True)

        export_path = self._pathutils.get_export_dir(
            instance_dir=inst_dir)

        self._pathutils.copy_folder_files(source_inst_dir, inst_dir)
        self._pathutils.copy_dir(source_export_path, export_path)
        return inst_dir

    def finish_migration(self, context, migration, instance, disk_info,
                         network_info, image_meta, resize_instance=False,
                         block_device_info=None, power_on=True):
        LOG.debug("finish_migration called", instance=instance)
        instance_dir = self._migrate_disks_from_source(migration, instance,
                                                       disk_info)

        # NOTE(claudiub): nova compute manager only takes into account disk
        # flavor changes when passing to the driver resize_instance=True.
        # we need to take into account flavor extra_specs as well.
        resize_instance = (
            migration.old_instance_type_id != migration.new_instance_type_id)

        self._import_and_setup_vm(context, instance, instance_dir, image_meta,
                                  block_device_info, resize_instance)

        if power_on:
            self._vmops.power_on(instance, network_info=network_info)

    def _import_and_setup_vm(self, context, instance, instance_dir, image_meta,
                             block_device_info, resize_instance=False):
        vm_gen = self._vmops.get_image_vm_generation(instance.uuid, image_meta)
        self._import_vm(instance_dir)
        self._vmops.update_vm_resources(instance, vm_gen, image_meta,
                                        instance_dir, resize_instance)

        self._volumeops.connect_volumes(block_device_info)
        self._update_disk_image_paths(instance, instance_dir)
        self._check_and_update_disks(context, instance, vm_gen, image_meta,
                                     block_device_info,
                                     resize_instance=resize_instance)
        self._volumeops.fix_instance_volume_disk_paths(
            instance.name, block_device_info)

        self._migrationutils.realize_vm(instance.name)

        # During a resize, ephemeral disks may be removed. We cannot remove
        # disks from a planned vm, for which reason we have to do this after
        # *realizing* it. At the same time, we cannot realize a VM before
        # updating disks to use the destination paths.
        ephemerals = block_device_info['ephemerals']
        self._check_ephemeral_disks(instance, ephemerals, resize_instance)

        self._vmops.configure_remotefx(instance, vm_gen, resize_instance)
        self._vmops.configure_instance_metrics(instance.name)

    def _import_vm(self, instance_dir):
        snapshot_dir = self._pathutils.get_instance_snapshot_dir(
            instance_dir=instance_dir)
        export_dir = self._pathutils.get_export_dir(instance_dir=instance_dir)
        vm_config_file_path = self._pathutils.get_vm_config_file(export_dir)

        self._migrationutils.import_vm_definition(vm_config_file_path,
                                                  snapshot_dir)

        # NOTE(claudiub): after the VM was imported, the VM config files are
        # not necessary anymore.
        self._pathutils.get_export_dir(instance_dir=instance_dir,
                                       remove_dir=True)

    def _update_disk_image_paths(self, instance, instance_path):
        """Checks if disk images have the correct path and updates them if not.

        When resizing an instance, the vm is imported on the destination node
        and the disk files are copied from source node. If the hosts have
        different instance_path config options set, the disks are migrated to
        the correct paths, but vm disk resources are not updated to point to
        the new location.
        """
        (disk_files, volume_drives) = self._vmutils.get_vm_storage_paths(
            instance.name)

        pattern = re.compile('configdrive|eph|root')
        for disk_file in disk_files:
            disk_name = os.path.basename(disk_file)
            if not pattern.match(disk_name):
                # skip files that do not match the pattern.
                continue

            expected_disk_path = os.path.join(instance_path, disk_name)
            if not os.path.exists(expected_disk_path):
                raise exception.DiskNotFound(location=expected_disk_path)

            if expected_disk_path != disk_file:
                LOG.debug("Updating VM disk location from %(src)s to %(dest)s",
                          {'src': disk_file, 'dest': expected_disk_path,
                           'instance': instance})
                self._vmutils.update_vm_disk_path(disk_file,
                                                  expected_disk_path,
                                                  is_physical=False)

    def _check_and_update_disks(self, context, instance, vm_gen, image_meta,
                                block_device_info, resize_instance=False):
        self._block_dev_man.validate_and_update_bdi(instance, image_meta,
                                                    vm_gen, block_device_info)
        root_device = block_device_info['root_disk']

        if root_device['type'] == constants.DISK:
            root_vhd_path = self._pathutils.lookup_root_vhd_path(instance.name)
            root_device['path'] = root_vhd_path
            if not root_vhd_path:
                base_vhd_path = self._pathutils.get_instance_dir(instance.name)
                raise exception.DiskNotFound(location=base_vhd_path)

            root_vhd_info = self._vhdutils.get_vhd_info(root_vhd_path)
            src_base_disk_path = root_vhd_info.get("ParentPath")
            if src_base_disk_path:
                self._check_base_disk(context, instance, root_vhd_path,
                                      src_base_disk_path)

            if resize_instance:
                new_size = instance.flavor.root_gb * units.Gi
                self._check_resize_vhd(root_vhd_path, root_vhd_info, new_size)

    def _check_ephemeral_disks(self, instance, ephemerals,
                               resize_instance=False):
        instance_name = instance.name
        new_eph_gb = instance.get('ephemeral_gb', 0)
        ephemerals_to_remove = set()

        if not ephemerals and new_eph_gb:
            # No explicit ephemeral disk bdm was retrieved, yet the flavor
            # provides ephemeral storage, for which reason we're adding a
            # default ephemeral disk.
            eph = dict(device_type='disk',
                       drive_addr=0,
                       size=new_eph_gb)
            ephemerals.append(eph)

        if len(ephemerals) == 1:
            # NOTE(claudiub): Resize only if there is one ephemeral. If there
            # are more than 1, resizing them can be problematic. This behaviour
            # also exists in the libvirt driver and it has to be addressed in
            # the future.
            ephemerals[0]['size'] = new_eph_gb
        elif new_eph_gb and sum(
                eph['size'] for eph in ephemerals) != new_eph_gb:
            # New ephemeral size is different from the original ephemeral size
            # and there are multiple ephemerals.
            LOG.warning("Cannot resize multiple ephemeral disks for instance.",
                        instance=instance)

        for index, eph in enumerate(ephemerals):
            eph_name = "eph%s" % index
            existing_eph_path = self._pathutils.lookup_ephemeral_vhd_path(
                instance_name, eph_name)

            if not existing_eph_path and eph['size']:
                eph['format'] = self._vhdutils.get_best_supported_vhd_format()
                eph['path'] = self._pathutils.get_ephemeral_vhd_path(
                    instance_name, eph['format'], eph_name)
                if not resize_instance:
                    # ephemerals should have existed.
                    raise exception.DiskNotFound(location=eph['path'])

                # We cannot rely on the BlockDeviceInfoManager class to
                # provide us a disk slot as it's only usable when creating
                # new instances (it's not aware of the current disk address
                # layout).
                # There's no way in which IDE may be requested for new
                # ephemeral disks (after a resize), so we'll just enforce
                # SCSI for now. os-win does not currently allow retrieving
                # free IDE slots.
                ctrller_path = self._vmutils.get_vm_scsi_controller(
                    instance.name)
                ctrl_addr = self._vmutils.get_free_controller_slot(
                    ctrller_path)
                eph['disk_bus'] = constants.CTRL_TYPE_SCSI
                eph['ctrl_disk_addr'] = ctrl_addr

                # create ephemerals
                self._vmops.create_ephemeral_disk(instance.name, eph)
                self._vmops.attach_ephemerals(instance_name, [eph])
            elif eph['size'] > 0:
                # ephemerals exist. resize them.
                eph['path'] = existing_eph_path
                eph_vhd_info = self._vhdutils.get_vhd_info(eph['path'])
                self._check_resize_vhd(
                    eph['path'], eph_vhd_info, eph['size'] * units.Gi)
            else:
                eph['path'] = None
                # ephemeral new size is 0, remove it.
                ephemerals_to_remove.add(existing_eph_path)

        if not new_eph_gb:
            # The new flavor does not provide any ephemeral storage. We'll
            # remove any existing ephemeral disk (default ones included).
            attached_ephemerals = self._vmops.get_attached_ephemeral_disks(
                instance.name)
            ephemerals_to_remove |= set(attached_ephemerals)

        for eph_path in ephemerals_to_remove:
            self._vmutils.detach_vm_disk(instance_name, eph_path,
                                         is_physical=False)
            self._pathutils.remove(eph_path)
