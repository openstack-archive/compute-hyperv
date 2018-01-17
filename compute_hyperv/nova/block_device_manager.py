# Copyright (c) 2016 Cloudbase Solutions Srl
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
Handling of block device information and mapping

Module contains helper methods for dealing with block device information
"""

import os

from nova import block_device
from nova import exception
from nova import objects
from nova.virt import block_device as driver_block_device
from nova.virt import configdrive
from nova.virt import driver
from os_win import constants as os_win_const
from os_win import exceptions as os_win_exc
from os_win import utilsfactory
from oslo_log import log as logging
from oslo_serialization import jsonutils

from compute_hyperv.i18n import _
from compute_hyperv.nova import constants
from compute_hyperv.nova import pathutils
from compute_hyperv.nova import volumeops

LOG = logging.getLogger(__name__)


class BlockDeviceInfoManager(object):

    _VALID_BUS = {constants.VM_GEN_1: (constants.CTRL_TYPE_IDE,
                                       constants.CTRL_TYPE_SCSI),
                  constants.VM_GEN_2: (constants.CTRL_TYPE_SCSI,)}

    _DEFAULT_BUS = constants.CTRL_TYPE_SCSI

    _TYPE_FOR_DISK_FORMAT = {'vhd': constants.DISK,
                             'vhdx': constants.DISK,
                             'iso': constants.DVD}

    _DEFAULT_ROOT_DEVICE = '/dev/sda'

    def __init__(self):
        self._volops = volumeops.VolumeOps()
        self._pathutils = pathutils.PathUtils()

        self._vmutils = utilsfactory.get_vmutils()

    @staticmethod
    def _get_device_bus(ctrl_type, ctrl_addr, ctrl_slot):
        """Determines the device bus and it's hypervisor assigned address.
        """
        if ctrl_type == constants.CTRL_TYPE_SCSI:
            address = ':'.join(map(str, [0, 0, ctrl_addr, ctrl_slot]))
            return objects.SCSIDeviceBus(address=address)
        elif ctrl_type == constants.CTRL_TYPE_IDE:
            address = ':'.join(map(str, [ctrl_addr, ctrl_slot]))
            return objects.IDEDeviceBus(address=address)

    def _get_vol_bdm_attachment_info(self, bdm):
        drv_vol_bdm = driver_block_device.convert_volume(bdm)
        if not drv_vol_bdm:
            return

        connection_info = drv_vol_bdm['connection_info']
        if not connection_info:
            LOG.warning("Missing connection info for volume %s.",
                        bdm.volume_id)
            return

        attachment_info = self._volops.get_disk_attachment_info(
            connection_info)
        attachment_info['serial'] = connection_info['serial']
        return attachment_info

    def _get_eph_bdm_attachment_info(self, instance, bdm):
        # When attaching ephemeral disks, we're setting this field so that
        # we can map them with bdm objects.
        connection_info = self.get_bdm_connection_info(bdm)
        eph_filename = connection_info.get("eph_filename")
        if not eph_filename:
            LOG.warning("Missing ephemeral disk filename in "
                        "BDM connection info. BDM: %s", bdm)
            return

        eph_path = os.path.join(
            self._pathutils.get_instance_dir(instance.name), eph_filename)
        if not os.path.exists(eph_path):
            LOG.warning("Could not find ephemeral disk %s.", eph_path)
            return

        return self._vmutils.get_disk_attachment_info(eph_path,
                                                      is_physical=False)

    def _get_disk_metadata(self, instance, bdm):
        attachment_info = None
        if bdm.is_volume:
            attachment_info = self._get_vol_bdm_attachment_info(bdm)
        elif block_device.new_format_is_ephemeral(bdm):
            attachment_info = self._get_eph_bdm_attachment_info(
                instance, bdm)

        if not attachment_info:
            LOG.debug("No attachment info retrieved for bdm %s.", bdm)
            return

        tags = [bdm.tag] if bdm.tag else []
        bus = self._get_device_bus(
            attachment_info['controller_type'],
            attachment_info['controller_addr'],
            attachment_info['controller_slot'])
        serial = attachment_info.get('serial')

        return objects.DiskMetadata(bus=bus,
                                    tags=tags,
                                    serial=serial)

    def get_bdm_metadata(self, context, instance):
        """Builds a metadata object for instance devices, that maps the user
           provided tag to the hypervisor assigned device address.
        """
        bdms = objects.BlockDeviceMappingList.get_by_instance_uuid(
            context, instance.uuid)

        bdm_metadata = []
        for bdm in bdms:
            try:
                device_metadata = self._get_disk_metadata(instance, bdm)
                if device_metadata:
                    bdm_metadata.append(device_metadata)
            except (exception.DiskNotFound, os_win_exc.DiskNotFound):
                LOG.debug("Could not find disk attachment while "
                          "updating device metadata. It may have been "
                          "detached. BDM: %s", bdm)

        return bdm_metadata

    def set_volume_bdm_connection_info(self, context, instance,
                                       connection_info):
        # When attaching volumes to already existing instances, the connection
        # info passed to the driver is not saved yet within the BDM table.
        #
        # Nova sets the volume id within the connection info using the
        # 'serial' key.
        volume_id = connection_info['serial']
        bdm = objects.BlockDeviceMapping.get_by_volume_and_instance(
            context, volume_id, instance.uuid)
        bdm.connection_info = jsonutils.dumps(connection_info)
        bdm.save()

    @staticmethod
    def get_bdm_connection_info(bdm):
        # We're using the BDM 'connection_info' field to store ephemeral
        # image information so that we can map them. In order to do so,
        # we're using this helper.
        # The ephemeral bdm object wrapper does not currently expose this
        # field.
        try:
            conn_info = jsonutils.loads(bdm.connection_info)
        except TypeError:
            conn_info = {}

        return conn_info

    @staticmethod
    def update_bdm_connection_info(bdm, **kwargs):
        conn_info = BlockDeviceInfoManager.get_bdm_connection_info(bdm)
        conn_info.update(**kwargs)
        bdm.connection_info = jsonutils.dumps(conn_info)
        bdm.save()

    def _initialize_controller_slot_counter(self, instance, vm_gen):
        # we have 2 IDE controllers, for a total of 4 slots
        free_slots_by_device_type = {
            constants.CTRL_TYPE_IDE: [
                os_win_const.IDE_CONTROLLER_SLOTS_NUMBER] * 2,
            constants.CTRL_TYPE_SCSI: [
                os_win_const.SCSI_CONTROLLER_SLOTS_NUMBER]
            }
        if configdrive.required_by(instance):
            if vm_gen == constants.VM_GEN_1:
                # reserve one slot for the config drive on the second
                # controller in case of generation 1 virtual machines
                free_slots_by_device_type[constants.CTRL_TYPE_IDE][1] -= 1
        return free_slots_by_device_type

    def validate_and_update_bdi(self, instance, image_meta, vm_gen,
                                block_device_info):
        slot_map = self._initialize_controller_slot_counter(instance, vm_gen)
        self._check_and_update_root_device(vm_gen, image_meta,
                                           block_device_info, slot_map)
        self._check_and_update_ephemerals(vm_gen, block_device_info, slot_map)
        self._check_and_update_volumes(vm_gen, block_device_info, slot_map)

        if vm_gen == constants.VM_GEN_2 and configdrive.required_by(instance):
            # for Generation 2 VMs, the configdrive is attached to the SCSI
            # controller. Check that there is still a slot available for it.
            if slot_map[constants.CTRL_TYPE_SCSI][0] == 0:
                msg = _("There are no more free slots on controller %s for "
                        "configdrive.") % constants.CTRL_TYPE_SCSI
                raise exception.InvalidBDMFormat(details=msg)

    def _check_and_update_root_device(self, vm_gen, image_meta,
                                      block_device_info, slot_map):
        # either booting from volume, or booting from image/iso
        root_disk = {}

        root_device = driver.block_device_info_get_root_device(
                block_device_info)
        root_device = root_device or self._DEFAULT_ROOT_DEVICE

        if self.is_boot_from_volume(block_device_info):
            root_volume = self._get_root_device_bdm(
                block_device_info, root_device)
            root_disk['type'] = constants.VOLUME
            root_disk['path'] = None
            root_disk['connection_info'] = root_volume['connection_info']
        else:
            root_disk['type'] = self._TYPE_FOR_DISK_FORMAT.get(
                image_meta['disk_format'])
            if root_disk['type'] is None:
                raise exception.InvalidImageFormat(
                    format=image_meta['disk_format'])
            root_disk['path'] = None
            root_disk['connection_info'] = None

        root_disk['disk_bus'] = (constants.CTRL_TYPE_IDE if
            vm_gen == constants.VM_GEN_1 else constants.CTRL_TYPE_SCSI)
        (root_disk['drive_addr'],
         root_disk['ctrl_disk_addr']) = self._get_available_controller_slot(
            root_disk['disk_bus'], slot_map)
        root_disk['boot_index'] = 0
        root_disk['mount_device'] = root_device

        block_device_info['root_disk'] = root_disk

    def _get_available_controller_slot(self, controller_type, slot_map):
        max_slots = (os_win_const.IDE_CONTROLLER_SLOTS_NUMBER if
                     controller_type == constants.CTRL_TYPE_IDE else
                     os_win_const.SCSI_CONTROLLER_SLOTS_NUMBER)
        for idx, ctrl in enumerate(slot_map[controller_type]):
            if slot_map[controller_type][idx] >= 1:
                drive_addr = idx
                ctrl_disk_addr = max_slots - slot_map[controller_type][idx]
                slot_map[controller_type][idx] -= 1
                return (drive_addr, ctrl_disk_addr)

        msg = _("There are no more free slots on controller %s"
                ) % controller_type
        raise exception.InvalidBDMFormat(details=msg)

    def is_boot_from_volume(self, block_device_info):
        if block_device_info:
            root_device = block_device_info.get('root_device_name')
            if not root_device:
                root_device = self._DEFAULT_ROOT_DEVICE

            return block_device.volume_in_mapping(root_device,
                                                  block_device_info)

    def _get_root_device_bdm(self, block_device_info, mount_device=None):
        for mapping in driver.block_device_info_get_mapping(block_device_info):
            if mapping['mount_device'] == mount_device:
                return mapping

    def _check_and_update_ephemerals(self, vm_gen, block_device_info,
                                     slot_map):
        ephemerals = driver.block_device_info_get_ephemerals(block_device_info)
        for eph in ephemerals:
            self._check_and_update_bdm(slot_map, vm_gen, eph)

    def _check_and_update_volumes(self, vm_gen, block_device_info, slot_map):
        volumes = driver.block_device_info_get_mapping(block_device_info)
        root_device_name = block_device_info['root_disk']['mount_device']
        root_bdm = self._get_root_device_bdm(block_device_info,
                                             root_device_name)
        if root_bdm:
            volumes.remove(root_bdm)
        for vol in volumes:
            self._check_and_update_bdm(slot_map, vm_gen, vol)

    def _check_and_update_bdm(self, slot_map, vm_gen, bdm):
        disk_bus = bdm.get('disk_bus')
        if not disk_bus:
            bdm['disk_bus'] = self._DEFAULT_BUS
        elif disk_bus not in self._VALID_BUS[vm_gen]:
            msg = _("Hyper-V does not support bus type %(disk_bus)s "
                    "for generation %(vm_gen)s instances."
                    ) % {'disk_bus': disk_bus,
                         'vm_gen': vm_gen}
            raise exception.InvalidDiskInfo(reason=msg)

        device_type = bdm.get('device_type')
        if not device_type:
            bdm['device_type'] = 'disk'
        elif device_type != 'disk':
            msg = _("Hyper-V does not support disk type %s for ephemerals "
                    "or volumes.") % device_type
            raise exception.InvalidDiskInfo(reason=msg)

        (bdm['drive_addr'],
         bdm['ctrl_disk_addr']) = self._get_available_controller_slot(
            bdm['disk_bus'], slot_map)

        # make sure that boot_index is set.
        bdm['boot_index'] = bdm.get('boot_index')

    def _sort_by_boot_order(self, bd_list):
        # we sort the block devices by boot_index leaving the ones that don't
        # have a specified boot_index at the end
        bd_list.sort(key=lambda x: (x['boot_index'] is None, x['boot_index']))

    def get_boot_order(self, vm_gen, block_device_info):
        if vm_gen == constants.VM_GEN_1:
            return self._get_boot_order_gen1(block_device_info)
        else:
            return self._get_boot_order_gen2(block_device_info)

    def _get_boot_order_gen1(self, block_device_info):
        if block_device_info['root_disk']['type'] == 'iso':
            return [os_win_const.BOOT_DEVICE_CDROM,
                    os_win_const.BOOT_DEVICE_HARDDISK,
                    os_win_const.BOOT_DEVICE_NETWORK,
                    os_win_const.BOOT_DEVICE_FLOPPY]
        else:
            return [os_win_const.BOOT_DEVICE_HARDDISK,
                    os_win_const.BOOT_DEVICE_CDROM,
                    os_win_const.BOOT_DEVICE_NETWORK,
                    os_win_const.BOOT_DEVICE_FLOPPY]

    def _get_boot_order_gen2(self, block_device_info):
        devices = [block_device_info['root_disk']]
        devices += driver.block_device_info_get_ephemerals(
            block_device_info)
        devices += driver.block_device_info_get_mapping(block_device_info)

        self._sort_by_boot_order(devices)

        boot_order = []
        for dev in devices:
            if dev.get('connection_info'):
                dev_path = self._volops.get_disk_resource_path(
                    dev['connection_info'])
                boot_order.append(dev_path)
            else:
                boot_order.append(dev['path'])

        return boot_order
