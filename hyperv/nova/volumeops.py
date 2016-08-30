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
import abc
import collections
import os
import platform
import re
import sys

import nova.conf
from nova import exception
from nova import utils
from nova.virt import driver
from os_win import exceptions as os_win_exc
from os_win import utilsfactory
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import excutils
from oslo_utils import units
import six

from hyperv.i18n import _, _LI, _LE, _LW
from hyperv.nova import constants
from hyperv.nova import pathutils

LOG = logging.getLogger(__name__)

hyper_volumeops_opts = [
    cfg.BoolOpt('use_multipath_io',
                default=False,
                help='Use multipath connections when attaching iSCSI or '
                     'FC disks. This requires the Multipath IO Windows '
                     'feature to be enabled. MPIO must be configured to '
                     'claim such devices.'),
    cfg.ListOpt('iscsi_initiator_list',
                default=[],
                help='List of iSCSI initiators that will be used for '
                     'estabilishing iSCSI sessions. If none is specified, '
                     'the Microsoft iSCSI initiator service will choose '
                     'the initiator.'),
]

CONF = nova.conf.CONF
CONF.register_opts(hyper_volumeops_opts, 'hyperv')


class VolumeOps(object):
    """Management class for Volume-related tasks
    """

    _SUPPORTED_QOS_SPECS = ['total_bytes_sec', 'min_bytes_sec',
                            'total_iops_sec', 'min_iops_sec']
    _IOPS_BASE_SIZE = 8 * units.Ki

    def __init__(self):
        self._default_root_device = 'vda'

        self._hostutils = utilsfactory.get_hostutils()
        self._vmutils = utilsfactory.get_vmutils()

        self._verify_setup()
        self.volume_drivers = {'smbfs': SMBFSVolumeDriver(),
                               'iscsi': ISCSIVolumeDriver(),
                               'fibre_channel': FCVolumeDriver()}

    def _verify_setup(self):
        if CONF.hyperv.use_multipath_io:
            mpio_enabled = self._hostutils.check_server_feature(
                self._hostutils.FEATURE_MPIO)
            if not mpio_enabled:
                err_msg = _LE(
                    "Using multipath connections for iSCSI and FC disks "
                    "requires the Multipath IO Windows feature to be "
                    "enabled. MPIO must be configured to claim such devices.")
                raise exception.ServiceUnavailable(err_msg)

    def _get_volume_driver(self, connection_info):
        driver_type = connection_info.get('driver_volume_type')
        if driver_type not in self.volume_drivers:
            raise exception.VolumeDriverNotFound(driver_type=driver_type)
        return self.volume_drivers[driver_type]

    def attach_volumes(self, volumes, instance_name):
        for vol in volumes:
            self.attach_volume(vol['connection_info'], instance_name)

    def attach_volume(self, connection_info, instance_name,
                      disk_bus=constants.CTRL_TYPE_SCSI):
        volume_driver = self._get_volume_driver(
            connection_info=connection_info)

        volume_connected = False
        try:
            volume_driver.connect_volume(connection_info)
            volume_connected = True

            volume_driver.attach_volume(connection_info, instance_name,
                                        disk_bus=disk_bus)

            qos_specs = connection_info['data'].get('qos_specs') or {}
            min_iops, max_iops = self.parse_disk_qos_specs(qos_specs)
            if min_iops or max_iops:
                volume_driver.set_disk_qos_specs(connection_info,
                                                 min_iops, max_iops)
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.exception(_LE('Unable to attach volume to instance %s'),
                              instance_name)
                # Even if the attach failed, some cleanup may be needed. If
                # the volume could not be connected, it surely is not attached.
                if volume_connected:
                    volume_driver.detach_volume(connection_info, instance_name)
                volume_driver.disconnect_volume(connection_info)

    def disconnect_volumes(self, block_device_info):
        mapping = driver.block_device_info_get_mapping(block_device_info)
        for volume in mapping:
            connection_info = volume['connection_info']
            volume_driver = self._get_volume_driver(connection_info)
            volume_driver.disconnect_volume(connection_info)

    def detach_volume(self, connection_info, instance_name):
        volume_driver = self._get_volume_driver(connection_info)
        volume_driver.detach_volume(connection_info, instance_name)
        volume_driver.disconnect_volume(connection_info)

    def fix_instance_volume_disk_paths(self, instance_name,
                                       block_device_info):
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
        connector = {
            'host': CONF.host,
            'ip': CONF.my_block_storage_ip,
            'multipath': CONF.hyperv.use_multipath_io,
            'os_type': sys.platform,
            'platform': platform.machine(),
        }
        for volume_driver_type, volume_driver in self.volume_drivers.items():
            connector_updates = volume_driver.get_volume_connector_props()
            connector.update(connector_updates)
        return connector

    def connect_volumes(self, block_device_info):
        mapping = driver.block_device_info_get_mapping(block_device_info)
        for vol in mapping:
            connection_info = vol['connection_info']
            volume_driver = self._get_volume_driver(connection_info)
            volume_driver.connect_volume(connection_info)

    def parse_disk_qos_specs(self, qos_specs):
        total_bytes_sec = int(qos_specs.get('total_bytes_sec', 0))
        min_bytes_sec = int(qos_specs.get('min_bytes_sec', 0))

        total_iops = int(qos_specs.get('total_iops_sec',
                                       self._bytes_per_sec_to_iops(
                                           total_bytes_sec)))
        min_iops = int(qos_specs.get('min_iops_sec',
                                     self._bytes_per_sec_to_iops(
                                         min_bytes_sec)))

        if total_iops and total_iops < min_iops:
            err_msg = (_("Invalid QoS specs: minimum IOPS cannot be greater "
                         "than maximum IOPS. "
                         "Requested minimum IOPS: %(min_iops)s "
                         "Requested maximum IOPS: %(total_iops)s.") %
                       {'min_iops': min_iops,
                        'total_iops': total_iops})
            raise exception.Invalid(err_msg)

        unsupported_specs = [spec for spec in qos_specs if
                             spec not in self._SUPPORTED_QOS_SPECS]
        if unsupported_specs:
            LOG.warning(_LW('Ignoring unsupported qos specs: '
                            '%(unsupported_specs)s. '
                            'Supported qos specs: %(supported_qos_speces)s'),
                        {'unsupported_specs': unsupported_specs,
                         'supported_qos_speces': self._SUPPORTED_QOS_SPECS})

        return min_iops, total_iops

    def _bytes_per_sec_to_iops(self, no_bytes):
        # Hyper-v uses normalized IOPS (8 KB increments)
        # as IOPS allocation units.
        return (no_bytes + self._IOPS_BASE_SIZE - 1) // self._IOPS_BASE_SIZE

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


@six.add_metaclass(abc.ABCMeta)
class BaseVolumeDriver(object):

    _is_block_dev = True

    def __init__(self):
        self._vmutils = utilsfactory.get_vmutils()
        self._diskutils = utilsfactory.get_diskutils()

    def connect_volume(self, connection_info):
        pass

    def disconnect_volume(self, connection_info):
        pass

    def get_volume_connector_props(self):
        return {}

    @abc.abstractmethod
    def get_disk_resource_path(connection_info):
        pass

    def attach_volume(self, connection_info, instance_name,
                      disk_bus=constants.CTRL_TYPE_SCSI):
        """Attach a volume to the SCSI controller or to the IDE controller if
        ebs_root is True
        """
        serial = connection_info['serial']
        # Getting the mounted disk
        mounted_disk_path = self.get_disk_resource_path(connection_info)

        ctrller_path, slot = self._get_disk_ctrl_and_slot(instance_name,
                                                          disk_bus)
        if self._is_block_dev:
            # We need to tag physical disk resources with the volume
            # serial number, in order to be able to retrieve them
            # during live migration.
            self._vmutils.attach_volume_to_controller(instance_name,
                                                      ctrller_path,
                                                      slot,
                                                      mounted_disk_path,
                                                      serial=serial)
        else:
            self._vmutils.attach_drive(instance_name,
                                       mounted_disk_path,
                                       ctrller_path,
                                       slot)

    def detach_volume(self, connection_info, instance_name):
        mounted_disk_path = self.get_disk_resource_path(connection_info)

        LOG.debug("Detaching disk %(disk_path)s "
                  "from instance: %(instance_name)s",
                  dict(disk_path=mounted_disk_path,
                       instance_name=instance_name))
        self._vmutils.detach_vm_disk(instance_name, mounted_disk_path,
                                     is_physical=self._is_block_dev)

    def _get_disk_ctrl_and_slot(self, instance_name, disk_bus):
        if disk_bus == constants.CTRL_TYPE_IDE:
            # Find the IDE controller for the vm.
            ctrller_path = self._vmutils.get_vm_ide_controller(
                instance_name, 0)
            # Attaching to the first slot
            slot = 0
        elif disk_bus == constants.CTRL_TYPE_SCSI:
            # Find the SCSI controller for the vm
            ctrller_path = self._vmutils.get_vm_scsi_controller(
                instance_name)
            slot = self._vmutils.get_free_controller_slot(ctrller_path)
        else:
            err_msg = _("Unsupported disk bus requested: %s")
            raise exception.Invalid(err_msg % disk_bus)
        return ctrller_path, slot

    def set_disk_qos_specs(self, connection_info, min_iops, max_iops):
        volume_type = connection_info.get('driver_volume_type', '')
        LOG.warning(_LW("The %s Hyper-V volume driver does not support QoS. "
                        "Ignoring QoS specs."), volume_type)

    def _check_device_paths(self, device_paths):
        if len(device_paths) > 1:
            err_msg = _("Multiple disk paths were found: %s. This can "
                        "occur if multipath is used and MPIO is not "
                        "properly configured, thus not claiming the device "
                        "paths. This issue must be addressed urgently as "
                        "it can lead to data corruption.")
            raise exception.InvalidDevicePath(err_msg % device_paths)
        elif not device_paths:
            err_msg = _("Could not find the physical disk "
                        "path for the requested volume.")
            raise exception.DiskNotFound(err_msg)

    def _get_mounted_disk_path_by_dev_name(self, device_name):
        device_number = self._diskutils.get_device_number_from_device_name(
            device_name)
        mounted_disk_path = self._vmutils.get_mounted_disk_by_drive_number(
            device_number)
        return mounted_disk_path


class ISCSIVolumeDriver(BaseVolumeDriver):
    def __init__(self):
        super(ISCSIVolumeDriver, self).__init__()
        self._iscsi_utils = utilsfactory.get_iscsi_initiator_utils()
        self._initiator_node_name = self._iscsi_utils.get_iscsi_initiator()

        self.validate_initiators()

    def get_volume_connector_props(self):
        props = {'initiator': self._initiator_node_name}
        return props

    def validate_initiators(self):
        # The MS iSCSI initiator service can manage the software iSCSI
        # initiator as well as hardware initiators.
        initiator_list = CONF.hyperv.iscsi_initiator_list
        valid_initiators = True

        if not initiator_list:
            LOG.info(_LI("No iSCSI initiator was explicitly requested. "
                         "The Microsoft iSCSI initiator will choose the "
                         "initiator when estabilishing sessions."))
        else:
            available_initiators = self._iscsi_utils.get_iscsi_initiators()
            for initiator in initiator_list:
                if initiator not in available_initiators:
                    valid_initiators = False
                    msg = _LW("The requested initiator %(req_initiator)s "
                              "is not in the list of available initiators: "
                              "%(avail_initiators)s.")
                    LOG.warning(msg,
                                dict(req_initiator=initiator,
                                     avail_initiators=available_initiators))

        return valid_initiators

    def _get_all_targets(self, connection_properties):
        if all([key in connection_properties for key in ('target_portals',
                                                         'target_iqns',
                                                         'target_luns')]):
            return zip(connection_properties['target_portals'],
                       connection_properties['target_iqns'],
                       connection_properties['target_luns'])

        return [(connection_properties['target_portal'],
                 connection_properties['target_iqn'],
                 connection_properties.get('target_lun', 0))]

    def _get_all_paths(self, connection_properties):
        initiator_list = CONF.hyperv.iscsi_initiator_list or [None]
        all_targets = self._get_all_targets(connection_properties)
        paths = [(initiator_name, target_portal, target_iqn, target_lun)
                 for target_portal, target_iqn, target_lun in all_targets
                 for initiator_name in initiator_list]
        return paths

    def connect_volume(self, connection_info):
        connection_properties = connection_info['data']
        auth_method = connection_properties.get('auth_method')

        if auth_method and auth_method.upper() != 'CHAP':
            LOG.error(_LE("Unsupported iSCSI authentication "
                          "method: %(auth_method)s."),
                      dict(auth_method=auth_method))
            raise exception.UnsupportedBDMVolumeAuthMethod(
                auth_method=auth_method)

        volume_connected = False
        for (initiator_name,
             target_portal,
             target_iqn,
             target_lun) in self._get_all_paths(connection_properties):
            try:
                msg = _LI("Attempting to estabilish an iSCSI session to "
                          "target %(target_iqn)s on portal %(target_portal)s "
                          "acessing LUN %(target_lun)s using initiator "
                          "%(initiator_name)s.")
                LOG.info(msg, dict(target_portal=target_portal,
                                   target_iqn=target_iqn,
                                   target_lun=target_lun,
                                   initiator_name=initiator_name))
                self._iscsi_utils.login_storage_target(
                    target_lun=target_lun,
                    target_iqn=target_iqn,
                    target_portal=target_portal,
                    auth_username=connection_properties.get('auth_username'),
                    auth_password=connection_properties.get('auth_password'),
                    mpio_enabled=CONF.hyperv.use_multipath_io,
                    initiator_name=initiator_name)

                volume_connected = True
                if not CONF.hyperv.use_multipath_io:
                    break
            except os_win_exc.OSWinException:
                LOG.exception(_LE("Could not connect iSCSI target %s."),
                              target_iqn)

        if not volume_connected:
            raise exception.VolumeAttachFailed(
                _("Could not connect volume %s.") %
                connection_properties['volume_id'])

    def disconnect_volume(self, connection_info):
        # We want to refresh the cached information first.
        self._diskutils.rescan_disks()

        for (target_portal,
             target_iqn,
             target_lun) in self._get_all_targets(connection_info['data']):

            luns = self._iscsi_utils.get_target_luns(target_iqn)
            # We disconnect the target only if it does not expose other
            # luns which may be in use.
            if not luns or luns == [target_lun]:
                self._iscsi_utils.logout_storage_target(target_iqn)

    def get_disk_resource_path(self, connection_info):
        device_paths = set()
        connection_properties = connection_info['data']

        for (target_portal,
             target_iqn,
             target_lun) in self._get_all_targets(connection_properties):

            (device_number,
             device_path) = self._iscsi_utils.get_device_number_and_path(
                target_iqn, target_lun)
            if device_path:
                device_paths.add(device_path)

        self._check_device_paths(device_paths)
        disk_path = list(device_paths)[0]
        return self._get_mounted_disk_path_by_dev_name(disk_path)


class SMBFSVolumeDriver(BaseVolumeDriver):

    _is_block_dev = False

    def __init__(self):
        self._pathutils = pathutils.PathUtils()
        self._smbutils = utilsfactory.get_smbutils()
        self._username_regex = re.compile(r'user(?:name)?=([^, ]+)')
        self._password_regex = re.compile(r'pass(?:word)?=([^, ]+)')
        super(SMBFSVolumeDriver, self).__init__()

    def export_path_synchronized(f):
        def wrapper(inst, connection_info, *args, **kwargs):
            export_path = inst._get_export_path(connection_info)

            @utils.synchronized(export_path)
            def inner():
                return f(inst, connection_info, *args, **kwargs)
            return inner()
        return wrapper

    def get_disk_resource_path(self, connection_info):
        return self._get_disk_path(connection_info)

    @export_path_synchronized
    def attach_volume(self, connection_info, instance_name,
                      disk_bus=constants.CTRL_TYPE_SCSI):
        super(SMBFSVolumeDriver, self).attach_volume(
            connection_info, instance_name, disk_bus)

    @export_path_synchronized
    def disconnect_volume(self, connection_info):
        # We synchronize share unmount and volume attach operations based on
        # the share path in order to avoid the situation when a SMB share is
        # unmounted while a volume exported by it is about to be attached to
        # an instance.
        export_path = self._get_export_path(connection_info)
        self._smbutils.unmount_smb_share(export_path)

    def _get_export_path(self, connection_info):
        return connection_info[
            'data']['export'].replace('/', '\\').rstrip('\\')

    def _get_disk_path(self, connection_info):
        share_addr = self._get_export_path(connection_info)
        disk_dir = share_addr

        if self._smbutils.is_local_share(share_addr):
            share_name = share_addr.lstrip('\\').split('\\')[1]
            disk_dir = self._smbutils.get_smb_share_path(share_name)
            if not disk_dir:
                err_msg = _("Could not find the local share path for %s.")
                raise exception.DiskNotFound(err_msg % share_addr)

        disk_name = connection_info['data']['name']
        disk_path = os.path.join(disk_dir, disk_name)
        return disk_path

    def ensure_share_mounted(self, connection_info):
        export_path = self._get_export_path(connection_info)

        if self._smbutils.is_local_share(export_path):
            LOG.info(_LI("Skipping mounting share %s, "
                         "using local path instead."),
                     export_path)
        elif not self._smbutils.check_smb_mapping(export_path):
            opts_str = connection_info['data'].get('options') or ''
            username, password = self._parse_credentials(opts_str)
            self._smbutils.mount_smb_share(export_path,
                                           username=username,
                                           password=password)

    def _parse_credentials(self, opts_str):
        match = self._username_regex.findall(opts_str)
        username = match[0] if match and match[0] != 'guest' else None

        match = self._password_regex.findall(opts_str)
        password = match[0] if match else None

        return username, password

    def connect_volume(self, connection_info):
        self.ensure_share_mounted(connection_info)

    def set_disk_qos_specs(self, connection_info, min_iops, max_iops):
        disk_path = self._get_disk_path(connection_info)
        self._vmutils.set_disk_qos_specs(disk_path, min_iops, max_iops)


class FCVolumeDriver(BaseVolumeDriver):
    _MAX_RESCAN_COUNT = 10

    def __init__(self):
        self._fc_utils = utilsfactory.get_fc_utils()
        super(FCVolumeDriver, self).__init__()

    def get_volume_connector_props(self):
        props = {}

        self._fc_utils.refresh_hba_configuration()
        fc_hba_ports = self._fc_utils.get_fc_hba_ports()

        if fc_hba_ports:
            wwnns = []
            wwpns = []
            for port in fc_hba_ports:
                wwnns.append(port['node_name'])
                wwpns.append(port['port_name'])
            props['wwpns'] = wwpns
            props['wwnns'] = list(set(wwnns))
        return props

    def connect_volume(self, connection_info):
        self.get_disk_resource_path(connection_info)

    def get_disk_resource_path(self, connection_info):
        for attempt in range(self._MAX_RESCAN_COUNT):
            disk_paths = set()

            self._diskutils.rescan_disks()
            volume_mappings = self._get_fc_volume_mappings(connection_info)

            LOG.debug("Retrieved volume mappings %(vol_mappings)s "
                      "for volume %(conn_info)s",
                      dict(vol_mappings=volume_mappings,
                           conn_info=connection_info))

            # Because of MPIO, we may not be able to get the device name
            # from a specific mapping if the disk was accessed through
            # an other HBA at that moment. In that case, the device name
            # will show up as an empty string.
            for mapping in volume_mappings:
                device_name = mapping['device_name']
                if device_name:
                    disk_paths.add(device_name)

            if disk_paths:
                self._check_device_paths(disk_paths)
                disk_path = list(disk_paths)[0]
                return self._get_mounted_disk_path_by_dev_name(
                    disk_path)

        err_msg = _("Could not find the physical disk "
                    "path for the requested volume.")
        raise exception.DiskNotFound(err_msg)

    def _get_fc_volume_mappings(self, connection_info):
        # Note(lpetrut): All the WWNs returned by os-win are upper case.
        target_wwpns = [wwpn.upper()
                        for wwpn in connection_info['data']['target_wwn']]
        target_lun = connection_info['data']['target_lun']

        volume_mappings = []
        hba_mapping = self._get_fc_hba_mapping()
        for node_name, hba_ports in hba_mapping.items():
            target_mappings = self._fc_utils.get_fc_target_mappings(node_name)
            for mapping in target_mappings:
                if (mapping['port_name'] in target_wwpns
                        and mapping['lun'] == target_lun):
                    volume_mappings.append(mapping)

        return volume_mappings

    def _get_fc_hba_mapping(self):
        mapping = collections.defaultdict(list)
        fc_hba_ports = self._fc_utils.get_fc_hba_ports()
        for port in fc_hba_ports:
            mapping[port['node_name']].append(port['port_name'])
        return mapping
