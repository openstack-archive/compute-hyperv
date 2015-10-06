# Copyright (c) 2015 Cloudbase Solutions Srl
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

from nova import block_device
from nova.virt import configdrive
from nova.virt import driver

from hyperv.i18n import _
from hyperv.nova import constants
from hyperv.nova import vmutils
from hyperv.nova import volumeops


class BlockDeviceInfoManager(object):

    _VALID_BUS = {constants.VM_GEN_1: (constants.CTRL_TYPE_IDE,
                                       constants.CTRL_TYPE_SCSI),
                  constants.VM_GEN_2: (constants.CTRL_TYPE_SCSI,)}

    _DEFAULT_BUS = constants.CTRL_TYPE_SCSI

    _TYPE_FOR_DISK_FORMAT = {'vhd': constants.DISK,
                             'iso': constants.DVD}

    _DEFAULT_ROOT_DEVICE = '/dev/sda'

    def __init__(self):
        self._volops = volumeops.VolumeOps()

    def _initialize_controller_slot_counter(self, instance, vm_gen):
        # we have 2 IDE controllers, for a total of 4 slots
        free_slots_by_device_type = {
            constants.CTRL_TYPE_IDE: [
                constants.IDE_CONTROLLER_SLOTS_NUMBER] * 2,
            constants.CTRL_TYPE_SCSI: [constants.SCSI_CONTROLLER_SLOTS_NUMBER]
            }
        if configdrive.required_by(instance):
            if vm_gen == constants.VM_GEN_1:
                # reserve one slot for the config drive on the second
                # controller in case of generation 1 virtual machines
                free_slots_by_device_type[constants.CTRL_TYPE_IDE][1] -= 1
            else:
                free_slots_by_device_type[constants.CTRL_TYPE_SCSI][0] -= 1
        return free_slots_by_device_type

    def validate_and_update_bdi(self, instance, image_meta, vm_gen,
                                block_device_info):
        slot_map = self._initialize_controller_slot_counter(instance, vm_gen)
        self._check_and_update_root_device(vm_gen, image_meta,
                                           block_device_info, slot_map)
        self._check_and_update_ephemerals(vm_gen, block_device_info, slot_map)
        self._check_and_update_volumes(vm_gen, block_device_info, slot_map)

    def _check_and_update_root_device(self, vm_gen, image_meta,
                                      block_device_info, slot_map):
        # either booting from volume, or booting from image/iso
        root_disk = {}

        root_device = (driver.block_device_info_get_root(block_device_info) or
                       self._DEFAULT_ROOT_DEVICE)

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
                msg = _("Hyper-V driver does not support image type "
                        "%s.") % image_meta['disk_format']
                raise vmutils.HyperVException(msg)
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
        max_slots = (constants.IDE_CONTROLLER_SLOTS_NUMBER if
                     controller_type == constants.CTRL_TYPE_IDE else
                     constants.SCSI_CONTROLLER_SLOTS_NUMBER)
        for idx, ctrl in enumerate(slot_map[controller_type]):
            if slot_map[controller_type][idx] >= 1:
                drive_addr = idx
                ctrl_disk_addr = max_slots - slot_map[controller_type][idx]
                slot_map[controller_type][idx] -= 1
                return (drive_addr, ctrl_disk_addr)

        msg = _("There are no more free slots on controller %s"
                ) % controller_type
        raise vmutils.HyperVException(msg)

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
            raise vmutils.HyperVException(msg)

        device_type = bdm.get('device_type')
        if not device_type:
            bdm['device_type'] = 'disk'
        elif device_type != 'disk':
            msg = _("Hyper-V does not support disk type %s for ephemerals "
                    "or volumes.") % device_type
            raise vmutils.HyperVException(msg)

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
            return [constants.BOOT_DEVICE_CDROM,
                    constants.BOOT_DEVICE_HARDDISK,
                    constants.BOOT_DEVICE_NETWORK,
                    constants.BOOT_DEVICE_FLOPPY]
        else:
            return [constants.BOOT_DEVICE_HARDDISK,
                    constants.BOOT_DEVICE_CDROM,
                    constants.BOOT_DEVICE_NETWORK,
                    constants.BOOT_DEVICE_FLOPPY]

    def _get_boot_order_gen2(self, block_device_info):
        boot_order = [block_device_info['root_disk']]
        boot_order += driver.block_device_info_get_ephemerals(
            block_device_info)
        boot_order += driver.block_device_info_get_mapping(block_device_info)

        self._sort_by_boot_order(boot_order)

        return [self._volops.get_mounted_disk_path_from_volume(
                boot_dev['connection_info']) if boot_dev.get('connection_info')
                else boot_dev['path'] for boot_dev in boot_order]
