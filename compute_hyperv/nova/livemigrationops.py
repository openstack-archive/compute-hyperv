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
Management class for live migration VM operations.
"""

from nova import exception
from nova.objects import migrate_data as migrate_data_obj
from os_win import utilsfactory
from oslo_log import log as logging

from compute_hyperv.i18n import _
from compute_hyperv.nova import block_device_manager
import compute_hyperv.nova.conf
from compute_hyperv.nova import imagecache
from compute_hyperv.nova import pathutils
from compute_hyperv.nova import serialconsoleops
from compute_hyperv.nova import vmops
from compute_hyperv.nova import volumeops

LOG = logging.getLogger(__name__)
CONF = compute_hyperv.nova.conf.CONF


class LiveMigrationOps(object):
    def __init__(self):
        self._livemigrutils = utilsfactory.get_livemigrationutils()
        self._pathutils = pathutils.PathUtils()
        self._vmops = vmops.VMOps()
        self._volumeops = volumeops.VolumeOps()
        self._serial_console_ops = serialconsoleops.SerialConsoleOps()
        self._imagecache = imagecache.ImageCache()
        self._block_dev_man = block_device_manager.BlockDeviceInfoManager()

    def live_migration(self, context, instance_ref, dest, post_method,
                       recover_method, block_migration=False,
                       migrate_data=None):
        LOG.debug("live_migration called", instance=instance_ref)
        instance_name = instance_ref["name"]

        if migrate_data and 'is_shared_instance_path' in migrate_data:
            shared_storage = migrate_data.is_shared_instance_path
        else:
            shared_storage = (
                self._pathutils.check_remote_instances_dir_shared(dest))
            if migrate_data:
                migrate_data.is_shared_instance_path = shared_storage
            else:
                migrate_data = migrate_data_obj.HyperVLiveMigrateData(
                    is_shared_instance_path=shared_storage)

        try:
            # We must make sure that the console log workers are stopped,
            # otherwise we won't be able to delete / move VM log files.
            self._serial_console_ops.stop_console_handler(instance_name)

            if not shared_storage:
                self._pathutils.copy_vm_console_logs(instance_name, dest)
                self._vmops.copy_vm_dvd_disks(instance_name, dest)

            self._livemigrutils.live_migrate_vm(
                instance_name,
                dest,
                migrate_disks=not shared_storage)
        except Exception:
            # The recover method will update the migration state.
            # We won't error out if we manage to recover the instance,
            # which would otherwise end up in error state.
            LOG.exception("Live migration failed. Attempting rollback.",
                          instance=instance_ref)
            recover_method(context, instance_ref, dest, migrate_data)
            return

        LOG.debug("Calling live migration post_method for instance: %s",
                  instance_name)
        post_method(context, instance_ref, dest,
                    block_migration, migrate_data)

    def pre_live_migration(self, context, instance, block_device_info,
                           network_info):
        LOG.debug("pre_live_migration called", instance=instance)
        self._livemigrutils.check_live_migration_config()

        if CONF.use_cow_images:
            boot_from_volume = self._block_dev_man.is_boot_from_volume(
                block_device_info)
            if not boot_from_volume and instance.image_ref:
                self._imagecache.get_cached_image(context, instance)

        self._volumeops.connect_volumes(block_device_info)

        # A planned VM with updated disk paths is needed only in case of
        # passthrough disks, in which case this will ensure that the volumes
        # remain attached after the VM is migrated.
        disk_path_mapping = self._volumeops.get_disk_path_mapping(
            block_device_info, block_dev_only=True)
        if disk_path_mapping:
            # We create a planned VM, ensuring that volumes will remain
            # attached after the VM is migrated.
            self._livemigrutils.create_planned_vm(instance.name,
                                                  instance.host,
                                                  disk_path_mapping)

    def post_live_migration(self, context, instance, block_device_info,
                            migrate_data):
        self._volumeops.disconnect_volumes(block_device_info)

        if not migrate_data.is_shared_instance_path:
            self._pathutils.get_instance_dir(instance.name,
                                             create_dir=False,
                                             remove_dir=True)

    def post_live_migration_at_destination(self, ctxt, instance_ref,
                                           network_info, block_migration):
        LOG.debug("post_live_migration_at_destination called",
                  instance=instance_ref)
        self._vmops.plug_vifs(instance_ref, network_info)
        self._vmops.configure_instance_metrics(instance_ref.name)

    def check_can_live_migrate_destination(self, ctxt, instance_ref,
                                           src_compute_info, dst_compute_info,
                                           block_migration=False,
                                           disk_over_commit=False):
        LOG.debug("check_can_live_migrate_destination called",
                  instance=instance_ref)

        migrate_data = migrate_data_obj.HyperVLiveMigrateData()
        try:
            migrate_data.is_shared_instance_path = (
                self._pathutils.check_remote_instances_dir_shared(
                    instance_ref.host))
        except OSError as e:
            reason = _('Unavailable instance location. Exception: %s') % e
            raise exception.MigrationPreCheckError(reason=reason)

        return migrate_data

    def cleanup_live_migration_destination_check(self, ctxt, dest_check_data):
        LOG.debug("cleanup_live_migration_destination_check called")

    def check_can_live_migrate_source(self, ctxt, instance_ref,
                                      dest_check_data):
        LOG.debug("check_can_live_migrate_source called",
                  instance=instance_ref)
        return dest_check_data
