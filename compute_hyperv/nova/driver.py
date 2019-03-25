# Copyright (c) 2010 Cloud.com, Inc
# Copyright (c) 2012 Cloudbase Solutions Srl
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
A Hyper-V Nova Compute driver.
"""

import functools
import platform
import sys

from nova import context as nova_context
from nova import exception
from nova import image
from nova.virt import driver
from os_win import exceptions as os_win_exc
from os_win import utilsfactory
from oslo_log import log as logging
import six

from compute_hyperv.nova import coordination
from compute_hyperv.nova import eventhandler
from compute_hyperv.nova import hostops
from compute_hyperv.nova import imagecache
from compute_hyperv.nova import livemigrationops
from compute_hyperv.nova import migrationops
from compute_hyperv.nova import pathutils
from compute_hyperv.nova import rdpconsoleops
from compute_hyperv.nova import serialconsoleops
from compute_hyperv.nova import snapshotops
from compute_hyperv.nova import vmops
from compute_hyperv.nova import volumeops

LOG = logging.getLogger(__name__)


def convert_exceptions(function, exception_map):
    expected_exceptions = tuple(exception_map.keys())

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except expected_exceptions as ex:
            raised_exception = exception_map.get(type(ex))
            if not raised_exception:
                # exception might be a subclass of an expected exception.
                for expected in expected_exceptions:
                    if isinstance(ex, expected):
                        raised_exception = exception_map[expected]
                        break

            exc_info = sys.exc_info()
            # NOTE(claudiub): Python 3 raises the exception object given as
            # the second argument in six.reraise.
            # The original message will be maintained by passing the original
            # exception.
            exc = raised_exception(six.text_type(exc_info[1]))
            six.reraise(raised_exception, exc, exc_info[2])
    return wrapper


def decorate_all_methods(decorator, *args, **kwargs):
    def decorate(cls):
        for attr in cls.__dict__:
            class_member = getattr(cls, attr)
            if callable(class_member):
                setattr(cls, attr, decorator(class_member, *args, **kwargs))
        return cls

    return decorate


exception_conversion_map = {
    # expected_exception: converted_exception
    os_win_exc.OSWinException: exception.NovaException,
    os_win_exc.HyperVVMNotFoundException: exception.InstanceNotFound,
}

# NOTE(claudiub): the purpose of the decorator below is to prevent any
# os_win exceptions (subclasses of OSWinException) to leak outside of the
# HyperVDriver.


@decorate_all_methods(convert_exceptions, exception_conversion_map)
class HyperVDriver(driver.ComputeDriver):
    capabilities = {
        "has_imagecache": True,
        "supports_evacuate": True,
        "supports_migrate_to_same_host": False,
        "supports_attach_interface": True,
        "supports_device_tagging": True,
        "supports_tagged_attach_interface": True,
        "supports_tagged_attach_volume": True,
        "supports_extend_volume": True,
        "supports_multiattach": False,
        "supports_trusted_certs": True,
    }

    use_coordination = False

    def __init__(self, virtapi):
        # check if the current version of Windows is supported before any
        # further driver initialisation.
        self._check_minimum_windows_version()

        super(HyperVDriver, self).__init__(virtapi)

        self._hostops = hostops.HostOps()
        self._volumeops = volumeops.VolumeOps()
        self._vmops = vmops.VMOps(virtapi)
        self._snapshotops = snapshotops.SnapshotOps()
        self._livemigrationops = livemigrationops.LiveMigrationOps()
        self._migrationops = migrationops.MigrationOps()
        self._rdpconsoleops = rdpconsoleops.RDPConsoleOps()
        self._serialconsoleops = serialconsoleops.SerialConsoleOps()
        self._imagecache = imagecache.ImageCache()
        self._image_api = image.API()
        self._pathutils = pathutils.PathUtils()
        self._event_handler = eventhandler.InstanceEventHandler()

    def _check_minimum_windows_version(self):
        hostutils = utilsfactory.get_hostutils()
        if not hostutils.check_min_windows_version(6, 2):
            # the version is of Windows is older than Windows Server 2012 R2.
            # Log an error, letting users know that this version is not
            # supported any longer.
            LOG.error('You are running nova-compute on an unsupported '
                      'version of Windows (older than Windows / Hyper-V '
                      'Server 2012). The support for this version of '
                      'Windows has been removed in Mitaka.')
            raise exception.HypervisorTooOld(version='6.2')
        elif not hostutils.check_min_windows_version(6, 3):
            # TODO(claudiub): replace the warning with an exception in Rocky.
            LOG.warning('You are running nova-compute on Windows / Hyper-V '
                        'Server 2012. The support for this version of Windows '
                        'has been deprecated In Queens, and will be removed '
                        'in Rocky.')

    @property
    def need_legacy_block_device_info(self):
        return False

    def init_host(self, host):
        if self.use_coordination:
            coordination.COORDINATOR.start()

        self._serialconsoleops.start_console_handlers()

        self._set_event_handler_callbacks()
        self._event_handler.start_listener()

        instance_path = self._pathutils.get_instances_dir()
        self._pathutils.check_create_dir(instance_path)

    def _set_event_handler_callbacks(self):
        # Subclasses may override this.
        self._event_handler.add_callback(self.emit_event)
        self._event_handler.add_callback(
            self._vmops.instance_state_change_callback)

    def list_instance_uuids(self):
        return self._vmops.list_instance_uuids()

    def list_instances(self):
        return self._vmops.list_instances()

    def estimate_instance_overhead(self, instance_info):
        return self._vmops.estimate_instance_overhead(instance_info)

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, allocations, network_info=None,
              block_device_info=None):
        image_meta = self._recreate_image_meta(context, instance, image_meta)
        self._vmops.spawn(context, instance, image_meta, injected_files,
                          admin_password, network_info, block_device_info)

    def reboot(self, context, instance, network_info, reboot_type,
               block_device_info=None, bad_volumes_callback=None):
        self._vmops.reboot(instance, network_info, reboot_type)

    def destroy(self, context, instance, network_info, block_device_info=None,
                destroy_disks=True):
        self._vmops.destroy(instance, network_info, block_device_info,
                            destroy_disks)

    def cleanup(self, context, instance, network_info, block_device_info=None,
                destroy_disks=True, migrate_data=None, destroy_vifs=True):
        """Cleanup after instance being destroyed by Hypervisor."""
        self.unplug_vifs(instance, network_info)

    def get_info(self, instance, use_cache=True):
        return self._vmops.get_info(instance)

    def attach_volume(self, context, connection_info, instance, mountpoint,
                      disk_bus=None, device_type=None, encryption=None):
        self._volumeops.attach_volume(context,
                                      connection_info,
                                      instance,
                                      update_device_metadata=True)

    def detach_volume(self, context, connection_info, instance, mountpoint,
                      encryption=None):
        context = nova_context.get_admin_context()
        # The nova compute manager only updates the device metadata in
        # case of tagged devices. We're including untagged devices as well.
        self._volumeops.detach_volume(context,
                                      connection_info,
                                      instance,
                                      update_device_metadata=True)

    def extend_volume(self, connection_info, instance, requested_size):
        self._volumeops.extend_volume(connection_info)

    def get_volume_connector(self, instance):
        return self._volumeops.get_volume_connector()

    def get_available_resource(self, nodename):
        return self._hostops.get_available_resource()

    def get_available_nodes(self, refresh=False):
        return [platform.node()]

    def host_power_action(self, action):
        return self._hostops.host_power_action(action)

    def snapshot(self, context, instance, image_id, update_task_state):
        self._snapshotops.snapshot(context, instance, image_id,
                                   update_task_state)

    def volume_snapshot_create(self, context, instance, volume_id,
                               create_info):
        self._volumeops.volume_snapshot_create(context, instance, volume_id,
                                               create_info)

    def volume_snapshot_delete(self, context, instance, volume_id,
                               snapshot_id, delete_info):
        self._volumeops.volume_snapshot_delete(context, instance, volume_id,
                                               snapshot_id, delete_info)

    def pause(self, instance):
        self._vmops.pause(instance)

    def unpause(self, instance):
        self._vmops.unpause(instance)

    def suspend(self, context, instance):
        self._vmops.suspend(instance)

    def resume(self, context, instance, network_info, block_device_info=None):
        self._vmops.resume(instance)

    def power_off(self, instance, timeout=0, retry_interval=0):
        self._vmops.power_off(instance, timeout, retry_interval)

    def power_on(self, context, instance, network_info,
                 block_device_info=None):
        self._vmops.power_on(instance, block_device_info, network_info)

    def resume_state_on_host_boot(self, context, instance, network_info,
                                  block_device_info=None):
        """Resume guest state when a host is booted."""
        self._vmops.resume_state_on_host_boot(context, instance, network_info,
                                              block_device_info)

    def live_migration(self, context, instance, dest, post_method,
                       recover_method, block_migration=False,
                       migrate_data=None):
        self._livemigrationops.live_migration(context, instance, dest,
                                              post_method, recover_method,
                                              block_migration, migrate_data)

    def rollback_live_migration_at_destination(self, context, instance,
                                               network_info,
                                               block_device_info,
                                               destroy_disks=True,
                                               migrate_data=None):
        self.destroy(context, instance, network_info, block_device_info,
                     destroy_disks=destroy_disks)

    def pre_live_migration(self, context, instance, block_device_info,
                           network_info, disk_info, migrate_data):
        self._livemigrationops.pre_live_migration(context, instance,
                                                  block_device_info,
                                                  network_info)
        return migrate_data

    def post_live_migration(self, context, instance, block_device_info,
                            migrate_data=None):
        self._livemigrationops.post_live_migration(context, instance,
                                                   block_device_info,
                                                   migrate_data)

    def post_live_migration_at_source(self, context, instance, network_info):
        """Unplug VIFs from networks at source."""
        self._vmops.unplug_vifs(instance, network_info)

    def post_live_migration_at_destination(self, context, instance,
                                           network_info,
                                           block_migration=False,
                                           block_device_info=None):
        self._livemigrationops.post_live_migration_at_destination(
            context,
            instance,
            network_info,
            block_migration)

    def check_can_live_migrate_destination(self, context, instance,
                                           src_compute_info, dst_compute_info,
                                           block_migration=False,
                                           disk_over_commit=False):
        return self._livemigrationops.check_can_live_migrate_destination(
            context, instance, src_compute_info, dst_compute_info,
            block_migration, disk_over_commit)

    def cleanup_live_migration_destination_check(self, context,
                                                 dest_check_data):
        self._livemigrationops.cleanup_live_migration_destination_check(
            context, dest_check_data)

    def check_can_live_migrate_source(self, context, instance,
                                      dest_check_data, block_device_info=None):
        return self._livemigrationops.check_can_live_migrate_source(
            context, instance, dest_check_data)

    def get_instance_disk_info(self, instance, block_device_info=None):
        pass

    def plug_vifs(self, instance, network_info):
        """Plug VIFs into networks."""
        self._vmops.plug_vifs(instance, network_info)

    def unplug_vifs(self, instance, network_info):
        """Unplug VIFs from networks."""
        self._vmops.unplug_vifs(instance, network_info)

    def ensure_filtering_rules_for_instance(self, instance, network_info):
        LOG.debug("ensure_filtering_rules_for_instance called",
                  instance=instance)

    def unfilter_instance(self, instance, network_info):
        LOG.debug("unfilter_instance called", instance=instance)

    def migrate_disk_and_power_off(self, context, instance, dest,
                                   flavor, network_info,
                                   block_device_info=None,
                                   timeout=0, retry_interval=0):
        return self._migrationops.migrate_disk_and_power_off(context,
                                                             instance, dest,
                                                             flavor,
                                                             network_info,
                                                             block_device_info,
                                                             timeout,
                                                             retry_interval)

    def confirm_migration(self, context, migration, instance, network_info):
        self._migrationops.confirm_migration(context, migration,
                                             instance, network_info)

    def finish_revert_migration(self, context, instance, network_info,
                                block_device_info=None, power_on=True):
        self._migrationops.finish_revert_migration(context, instance,
                                                   network_info,
                                                   block_device_info, power_on)

    def finish_migration(self, context, migration, instance, disk_info,
                         network_info, image_meta, resize_instance,
                         block_device_info=None, power_on=True):
        image_meta = self._recreate_image_meta(context, instance, image_meta)
        self._migrationops.finish_migration(context, migration, instance,
                                            disk_info, network_info,
                                            image_meta, resize_instance,
                                            block_device_info, power_on)

    def get_host_ip_addr(self):
        return self._hostops.get_host_ip_addr()

    def get_host_uptime(self):
        return self._hostops.get_host_uptime()

    def get_rdp_console(self, context, instance):
        return self._rdpconsoleops.get_rdp_console(instance)

    def get_serial_console(self, context, instance):
        return self._serialconsoleops.get_serial_console(instance.name)

    def get_console_output(self, context, instance):
        return self._serialconsoleops.get_console_output(instance.name)

    def manage_image_cache(self, context, all_instances):
        self._imagecache.update(context, all_instances)

    def attach_interface(self, context, instance, image_meta, vif):
        self._vmops.attach_interface(context, instance, vif)

    def detach_interface(self, context, instance, vif):
        # The device metadata gets updated outside the driver.
        return self._vmops.detach_interface(instance, vif)

    def rescue(self, context, instance, network_info, image_meta,
               rescue_password):
        image_meta = self._recreate_image_meta(context, instance, image_meta)
        self._vmops.rescue_instance(context, instance, network_info,
                                    image_meta, rescue_password)

    def unrescue(self, instance, network_info):
        self._vmops.unrescue_instance(instance)

    def host_maintenance_mode(self, host, mode):
        return self._hostops.host_maintenance_mode(host, mode)

    def _recreate_image_meta(self, context, instance, image_meta):
        # TODO(claudiub): Cleanup this method. instance.system_metadata might
        # already contain all the image metadata properties we need anyways.
        if image_meta.obj_attr_is_set("id"):
            image_ref = image_meta.id
        else:
            image_ref = instance.system_metadata['image_base_image_ref']

        if image_ref:
            image_meta = self._image_api.get(context, image_ref)
        else:
            # boot from volume does not have an image_ref.
            image_meta = image_meta.obj_to_primitive()['nova_object.data']
            image_meta['properties'] = {k.replace('image_', '', 1): v for k, v
                                        in instance.system_metadata.items()}
        image_meta["id"] = image_ref
        return image_meta

    def check_instance_shared_storage_local(self, context, instance):
        """Check if instance files located on shared storage.

        This runs check on the destination host, and then calls
        back to the source host to check the results.

        :param context: security context
        :param instance: nova.objects.instance.Instance object
        :returns: A dict containing the tempfile info.
        """
        return self._pathutils.check_instance_shared_storage_local(instance)

    def check_instance_shared_storage_remote(self, context, data):
        return self._pathutils.check_instance_shared_storage_remote(data)

    def check_instance_shared_storage_cleanup(self, context, data):
        return self._pathutils.check_instance_shared_storage_cleanup(data)
