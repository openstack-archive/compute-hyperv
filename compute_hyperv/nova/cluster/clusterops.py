# Copyright 2016 Cloudbase Solutions Srl
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

"""Management class for Cluster VM operations."""

import functools

from nova.compute import power_state
from nova.compute import task_states
from nova.compute import vm_states
from nova import context
from nova import network
from nova import objects
from nova import utils
from nova.virt import block_device
from os_win import exceptions as os_win_exc
from os_win import utilsfactory
from oslo_log import log as logging

import compute_hyperv.nova.conf
from compute_hyperv.nova import hostops
from compute_hyperv.nova import serialconsoleops
from compute_hyperv.nova import vmops

LOG = logging.getLogger(__name__)
CONF = compute_hyperv.nova.conf.CONF


class ClusterOps(object):

    def __init__(self):
        self._clustutils = utilsfactory.get_clusterutils()
        self._vmutils = utilsfactory.get_vmutils()
        self._clustutils.check_cluster_state()
        self._instance_map = {}

        self._this_node = hostops.HostOps.get_hostname()

        self._context = context.get_admin_context()
        self._network_api = network.API()
        self._vmops = vmops.VMOps()
        self._serial_console_ops = serialconsoleops.SerialConsoleOps()

    def get_instance_host(self, instance):
        return self._clustutils.get_vm_host(instance.name)

    def add_to_cluster(self, instance):
        try:
            self._clustutils.add_vm_to_cluster(
                instance.name, CONF.hyperv.max_failover_count,
                CONF.hyperv.failover_period, CONF.hyperv.auto_failback)
            self._instance_map[instance.name] = instance.uuid
        except os_win_exc.HyperVClusterException:
            LOG.exception('Adding instance to cluster failed.',
                          instance=instance)

    def remove_from_cluster(self, instance):
        try:
            if self._clustutils.vm_exists(instance.name):
                self._clustutils.delete(instance.name)
            self._instance_map.pop(instance.name, None)
        except os_win_exc.HyperVClusterException:
            LOG.exception('Removing instance from cluster failed.',
                          instance=instance)

    def post_migration(self, instance):
        # update instance cache
        self._instance_map[instance.name] = instance.uuid

    def start_failover_listener_daemon(self):
        """Start the daemon failover listener."""

        listener = self._clustutils.get_vm_owner_change_listener()
        cbk = functools.partial(utils.spawn_n, self._failover_migrate)

        utils.spawn_n(listener, cbk)

    def reclaim_failovered_instances(self):
        # NOTE(claudiub): some instances might have failovered while the
        # nova-compute service was down. Those instances will have to be
        # reclaimed by this node.
        expected_attrs = ['id', 'uuid', 'name', 'host']
        host_instance_uuids = self._vmops.list_instance_uuids()
        nova_instances = self._get_nova_instances(expected_attrs,
                                                  host_instance_uuids)

        # filter out instances that are known to be on this host.
        nova_instances = [instance for instance in nova_instances if
                          self._this_node.upper() != instance.host.upper()]

        for instance in nova_instances:
            utils.spawn_n(self._failover_migrate,
                          instance.name, instance.host,
                          self._this_node)

    def _failover_migrate(self, instance_name, old_host, new_host):
        """This method will check if the generated event is a legitimate
        failover to this node. If it is, it will proceed to prepare the
        failovered VM if necessary and update the owner of the compute vm in
        nova and ports in neutron.
        """
        LOG.info('Checking instance failover %(instance)s to %(new_host)s '
                 'from host %(old_host)s.',
                 {'instance': instance_name,
                  'new_host': new_host,
                  'old_host': old_host})

        instance = self._get_instance_by_name(instance_name)
        if not instance:
            # Some instances on the hypervisor may not be tracked by nova
            LOG.debug('Instance %s does not exist in nova. Skipping.',
                      instance_name)
            return

        if instance.task_state == task_states.MIGRATING:
            # In case of live migration triggered by the user, we get the
            # event that the instance changed host but we do not want
            # to treat it as a failover.
            LOG.debug('Instance %s is live migrating.', instance_name)
            return

        nw_info = self._network_api.get_instance_nw_info(self._context,
                                                         instance)
        if old_host and old_host.upper() == self._this_node.upper():
            LOG.debug('Actions at source node.')
            self._vmops.unplug_vifs(instance, nw_info)
            return
        elif new_host.upper() != self._this_node.upper():
            LOG.debug('Instance %s did not failover to this node.',
                      instance_name)
            return

        LOG.info('Instance %(instance)s  failover to %(host)s.',
                 {'instance': instance_name,
                  'host': new_host})

        self._nova_failover_server(instance, new_host)
        self._failover_migrate_networks(instance, old_host)
        self._vmops.plug_vifs(instance, nw_info)
        self._serial_console_ops.start_console_handler(instance_name)

    def _failover_migrate_networks(self, instance, source):
        """This is called after a VM failovered to this node.
        This will change the owner of the neutron ports to this node.
        """
        migration = {'source_compute': source,
                     'dest_compute': self._this_node, }

        self._network_api.setup_networks_on_host(
            self._context, instance, self._this_node)
        self._network_api.migrate_instance_start(
            self._context, instance, migration)
        self._network_api.setup_networks_on_host(
            self._context, instance, self._this_node)
        self._network_api.migrate_instance_finish(
            self._context, instance, migration)
        self._network_api.setup_networks_on_host(
            self._context, instance, self._this_node)
        self._network_api.setup_networks_on_host(
            self._context, instance, source, teardown=True)

    def _get_instance_by_name(self, instance_name):
        # Since from a failover we only get the instance name
        # we need to find it's uuid so we can retrieve the instance
        # object from nova. We keep a map from the instance name to the
        # instance uuid. First we try to get the vm uuid from that map
        # if it's not there, we try to get it from the instance notes,
        # this may fail (during a failover for example, the vm will not
        # be at the source node anymore) and lastly we try and get the
        # vm uuid from the database.
        vm_uuid = self._instance_map.get(instance_name)
        if not vm_uuid:
            try:
                vm_uuid = self._vmutils.get_instance_uuid(instance_name)
                self._instance_map[instance_name] = vm_uuid
            except os_win_exc.HyperVVMNotFoundException:
                pass

        if not vm_uuid:
            self._update_instance_map()
            vm_uuid = self._instance_map.get(instance_name)

        if not vm_uuid:
            LOG.debug("Instance %s cannot be found in Nova.", instance_name)
            return

        return objects.Instance.get_by_uuid(self._context, vm_uuid)

    def _update_instance_map(self):
        for server in self._get_nova_instances():
            self._instance_map[server.name] = server.uuid

    def _get_nova_instances(self, expected_attrs=None, instance_uuids=None):
        if not expected_attrs:
            expected_attrs = ['id', 'uuid', 'name']

        filters = {'deleted': False}
        if instance_uuids is not None:
            filters['uuid'] = instance_uuids

        return objects.InstanceList.get_by_filters(
            self._context, filters, expected_attrs=expected_attrs)

    def _get_instance_block_device_mappings(self, instance):
        """Transform block devices to the driver block_device format."""
        bdms = objects.BlockDeviceMappingList.get_by_instance_uuid(
            self._context, instance.uuid)
        return [block_device.DriverVolumeBlockDevice(bdm) for bdm in bdms]

    def _nova_failover_server(self, instance, new_host):
        if instance.vm_state == vm_states.ERROR:
            # Sometimes during a failover nova can set the instance state
            # to error depending on how much time the failover takes.
            instance.vm_state = vm_states.ACTIVE
        if instance.power_state == power_state.NOSTATE:
            instance.power_state = power_state.RUNNING

        instance.host = new_host
        instance.node = new_host
        instance.save(expected_task_state=[None])
