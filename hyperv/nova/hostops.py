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
Management class for host operations.
"""
import datetime
import os
import platform
import six
import time


from nova.compute import api
from nova.compute import arch
from nova.compute import hv_type
from nova.compute import vm_mode
from nova.compute import vm_states
from nova import context
from nova import exception
from nova import objects
from oslo_config import cfg
from oslo_log import log as logging
from oslo_serialization import jsonutils
from oslo_utils import units

from hyperv.i18n import _, _LE, _LI
from hyperv.nova import constants
from hyperv.nova import utilsfactory
from hyperv.nova import vmops
from hyperv.nova import vmutils

hyper_host_opts = [
    cfg.IntOpt('evacuate_task_state_timeout',
               default=600,
               help='Number of seconds to wait for an instance to be '
                    'evacuated during host maintenance.'),
]

CONF = cfg.CONF
CONF.register_opts(hyper_host_opts, 'hyperv')
CONF.import_opt('my_ip', 'nova.netconf')
CONF.import_opt('enable_remotefx', 'hyperv.nova.vmops', 'hyperv')
LOG = logging.getLogger(__name__)


class HostOps(object):
    def __init__(self):
        self._hostutils = utilsfactory.get_hostutils()
        self._pathutils = utilsfactory.get_pathutils()
        self._vmutils = utilsfactory.get_vmutils()
        self._vmops = vmops.VMOps()
        self._api = api.API()

    def _get_cpu_info(self):
        """Get the CPU information.
        :returns: A dictionary containing the main properties
        of the central processor in the hypervisor.
        """
        cpu_info = dict()

        processors = self._hostutils.get_cpus_info()

        w32_arch_dict = constants.WMI_WIN32_PROCESSOR_ARCHITECTURE
        cpu_info['arch'] = w32_arch_dict.get(processors[0]['Architecture'],
                                             'Unknown')
        cpu_info['model'] = processors[0]['Name']
        cpu_info['vendor'] = processors[0]['Manufacturer']

        topology = dict()
        topology['sockets'] = len(processors)
        topology['cores'] = processors[0]['NumberOfCores']
        topology['threads'] = (processors[0]['NumberOfLogicalProcessors'] //
                               processors[0]['NumberOfCores'])
        cpu_info['topology'] = topology

        features = list()
        for fkey, fname in six.iteritems(constants.PROCESSOR_FEATURE):
            if self._hostutils.is_cpu_feature_present(fkey):
                features.append(fname)
        cpu_info['features'] = features

        return cpu_info

    def _get_memory_info(self):
        (total_mem_kb, free_mem_kb) = self._hostutils.get_memory_info()
        total_mem_mb = total_mem_kb // 1024
        free_mem_mb = free_mem_kb // 1024
        return (total_mem_mb, free_mem_mb, total_mem_mb - free_mem_mb)

    def _get_local_hdd_info_gb(self):
        drive = os.path.splitdrive(self._pathutils.get_instances_dir())[0]
        (size, free_space) = self._hostutils.get_volume_info(drive)

        total_gb = size // units.Gi
        free_gb = free_space // units.Gi
        used_gb = total_gb - free_gb
        return (total_gb, free_gb, used_gb)

    def _get_hypervisor_version(self):
        """Get hypervisor version.
        :returns: hypervisor version (ex. 6003)
        """

        # NOTE(claudiub): The hypervisor_version will be stored in the database
        # as an Integer and it will be used by the scheduler, if required by
        # the image property 'hypervisor_version_requires'.
        # The hypervisor_version will then be converted back to a version
        # by splitting the int in groups of 3 digits.
        # E.g.: hypervisor_version 6003 is converted to '6.3'.
        version = self._hostutils.get_windows_version().split('.')
        version = int(version[0]) * 1000 + int(version[1])
        LOG.debug('Windows version: %s ', version)
        return version

    def _get_host_numa_topology(self):
        numa_nodes = self._hostutils.get_numa_nodes()
        cells = []
        for numa_node in numa_nodes:
            numa_node['pinned_cpus'] = set([])
            numa_node['mempages'] = []
            numa_node['siblings'] = []
            cell = objects.NUMACell(**numa_node)
            cells.append(cell)

        return objects.NUMATopology(cells=cells)

    def _get_remotefx_gpu_info(self):
        remotefx_total_video_ram = 0
        remotefx_available_video_ram = 0

        if CONF.hyperv.enable_remotefx:
            gpus = self._hostutils.get_remotefx_gpu_info()
            for gpu in gpus:
                remotefx_total_video_ram += int(gpu['total_video_ram'])
                remotefx_available_video_ram += int(gpu['available_video_ram'])
        else:
            gpus = []

        return {'remotefx_total_video_ram': remotefx_total_video_ram,
                'remotefx_available_video_ram': remotefx_available_video_ram,
                'remotefx_gpu_info': jsonutils.dumps(gpus)}

    def get_available_resource(self):
        """Retrieve resource info.

        This method is called when nova-compute launches, and
        as part of a periodic task.

        :returns: dictionary describing resources

        """
        LOG.debug('get_available_resource called')

        (total_mem_mb,
         free_mem_mb,
         used_mem_mb) = self._get_memory_info()

        (total_hdd_gb,
         free_hdd_gb,
         used_hdd_gb) = self._get_local_hdd_info_gb()

        cpu_info = self._get_cpu_info()
        cpu_topology = cpu_info['topology']
        vcpus = (cpu_topology['sockets'] *
                 cpu_topology['cores'] *
                 cpu_topology['threads'])

        gpu_info = self._get_remotefx_gpu_info()

        dic = {'vcpus': vcpus,
               'memory_mb': total_mem_mb,
               'memory_mb_used': used_mem_mb,
               'local_gb': total_hdd_gb,
               'local_gb_used': used_hdd_gb,
               'hypervisor_type': "hyperv",
               'hypervisor_version': self._get_hypervisor_version(),
               'hypervisor_hostname': platform.node(),
               'vcpus_used': 0,
               'cpu_info': jsonutils.dumps(cpu_info),
               'supported_instances': jsonutils.dumps(
                   [(arch.I686, hv_type.HYPERV, vm_mode.HVM),
                    (arch.X86_64, hv_type.HYPERV, vm_mode.HVM)]),
               }
        dic.update(gpu_info)

        numa_topology = self._get_host_numa_topology()
        if numa_topology:
            dic['numa_topology'] = numa_topology._to_json()
        else:
            dic['numa_topology'] = None

        return dic

    def host_power_action(self, action):
        """Reboots, shuts down or powers up the host."""
        if action in [constants.HOST_POWER_ACTION_SHUTDOWN,
                      constants.HOST_POWER_ACTION_REBOOT]:
            self._hostutils.host_power_action(action)
        else:
            if action == constants.HOST_POWER_ACTION_STARTUP:
                raise NotImplementedError(
                    _("Host PowerOn is not supported by the Hyper-V driver"))

    def get_host_ip_addr(self):
        host_ip = CONF.my_ip
        if not host_ip:
            # Return the first available address
            host_ip = self._hostutils.get_local_ips()[0]
        LOG.debug("Host IP address is: %s", host_ip)
        return host_ip

    def get_host_uptime(self):
        """Returns the host uptime."""

        tick_count64 = self._hostutils.get_host_tick_count64()

        # format the string to match libvirt driver uptime
        # Libvirt uptime returns a combination of the following
        # - current host time
        # - time since host is up
        # - number of logged in users
        # - cpu load
        # Since the Windows function GetTickCount64 returns only
        # the time since the host is up, returning 0s for cpu load
        # and number of logged in users.
        # This is done to ensure the format of the returned
        # value is same as in libvirt
        return "%s up %s,  0 users,  load average: 0, 0, 0" % (
                   str(time.strftime("%H:%M:%S")),
                   str(datetime.timedelta(milliseconds=int(tick_count64))))

    def host_maintenance_mode(self, host, mode):
        """Starts/Stops host maintenance. On start, it triggers
        guest VMs evacuation.
        """
        ctxt = context.get_admin_context()

        if not mode:
            self._set_service_state(host=host, binary='nova-compute',
                                    is_disabled=False)
            LOG.info(_LI('Host is no longer under maintenance.'))
            return 'off_maintenance'

        self._set_service_state(host=host, binary='nova-compute',
                                is_disabled=True)
        vms_uuids = self._vmops.list_instance_uuids()
        for vm_uuid in vms_uuids:
            self._wait_for_instance_pending_task(ctxt, vm_uuid)

        vm_names = self._vmutils.list_instances()
        for vm_name in vm_names:
            self._migrate_vm(ctxt, vm_name, host)

        vms_uuid_after_migration = self._vmops.list_instance_uuids()
        remaining_vms = len(vms_uuid_after_migration)
        if remaining_vms == 0:
            LOG.info(_LI('All vms have been migrated successfully.'
                         'Host is down for maintenance'))
            return 'on_maintenance'
        raise vmutils.HyperVException(
            _('Not all vms have been migrated: %s remaining instances.')
            % remaining_vms)

    def _set_service_state(self, host, binary, is_disabled):
        "Enables/Disables service on host"

        ctxt = context.get_admin_context(read_deleted='no')
        service = objects.Service.get_by_args(ctxt, host, binary)
        service.disabled = is_disabled
        service.save()

    def _migrate_vm(self, ctxt, vm_name, host):
        try:
            instance_uuid = self._vmutils.get_instance_uuid(vm_name)
            if not instance_uuid:
                LOG.info(_LI('VM "%s" running on this host was not created by '
                             'nova. Skip migrating this vm to a new host.'),
                         vm_name)
                return
            instance = objects.Instance.get_by_uuid(ctxt, instance_uuid)
            if instance.vm_state == vm_states.ACTIVE:
                self._api.live_migrate(ctxt, instance, block_migration=False,
                                       disk_over_commit=False, host_name=None)
            else:
                self._api.resize(ctxt, instance, flavor_id=None,
                    clean_shutdown=True)
            self._wait_for_instance_pending_task(ctxt, instance_uuid)
        except Exception as e:
            LOG.error(_LE('Migrating vm failed with error: %s '), e)
            raise exception.MigrationError(reason='Unable to migrate %s.'
                                           % vm_name)

    def _wait_for_instance_pending_task(self, context, vm_uuid):
        instance = objects.Instance.get_by_uuid(context, vm_uuid)
        task_state_timeout = CONF.hyperv.evacuate_task_state_timeout
        while instance.task_state:
            LOG.debug("Waiting to evacuate instance %(instance_id)s. Current "
                      "task state: '%(task_state)s', Time remaining: "
                      "%(timeout)s.", {'instance_id': instance.id,
                                       'task_state': instance.task_state,
                                       'timeout': task_state_timeout})
            time.sleep(1)
            instance.refresh()
            task_state_timeout -= 1
            if task_state_timeout <= 0:
                err = (_("Timeout error. Instance %(instance)s hasn't changed "
                         "task_state %(task_state)s within %(timeout)s "
                         "seconds.") %
                         {'instance': instance.name,
                          'task_state': instance.task_state,
                          'timeout': CONF.hyperv.evacuate_task_state_timeout})
                raise exception.InternalError(message=err)
