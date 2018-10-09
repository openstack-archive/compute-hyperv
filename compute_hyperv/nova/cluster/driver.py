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

"""A Hyper-V Cluster Nova Compute driver."""

from compute_hyperv.nova.cluster import clusterops
from compute_hyperv.nova.cluster import livemigrationops
from compute_hyperv.nova.cluster import volumeops
from compute_hyperv.nova import driver


class HyperVClusterDriver(driver.HyperVDriver):
    use_coordination = True

    def __init__(self, virtapi):
        super(HyperVClusterDriver, self).__init__(virtapi)

        self._clops = clusterops.ClusterOps()
        self._livemigrationops = livemigrationops.ClusterLiveMigrationOps()
        self._volumeops = volumeops.ClusterVolumeOps()

        self._clops.start_failover_listener_daemon()
        self._clops.reclaim_failovered_instances()

    def _set_event_handler_callbacks(self):
        super(HyperVClusterDriver, self)._set_event_handler_callbacks()

        self._event_handler.add_callback(
            self._clops.instance_state_change_callback)

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, allocations, network_info=None,
              block_device_info=None):
        super(HyperVClusterDriver, self).spawn(
            context, instance, image_meta, injected_files, admin_password,
            allocations, network_info, block_device_info)
        self._clops.add_to_cluster(instance)

    def destroy(self, context, instance, network_info, block_device_info=None,
                destroy_disks=True):
        self._clops.remove_from_cluster(instance)
        super(HyperVClusterDriver, self).destroy(
            context, instance, network_info, block_device_info,
            destroy_disks)

    def migrate_disk_and_power_off(self, context, instance, dest,
                                   flavor, network_info,
                                   block_device_info=None,
                                   timeout=0, retry_interval=0):
        self._clops.remove_from_cluster(instance)
        return super(HyperVClusterDriver, self).migrate_disk_and_power_off(
            context, instance, dest, flavor, network_info,
            block_device_info, timeout, retry_interval)

    def finish_migration(self, context, migration, instance, disk_info,
                         network_info, image_meta, resize_instance,
                         block_device_info=None, power_on=True):
        super(HyperVClusterDriver, self).finish_migration(
            context, migration, instance, disk_info, network_info,
            image_meta, resize_instance, block_device_info, power_on)
        self._clops.add_to_cluster(instance)

    def finish_revert_migration(self, context, instance, network_info,
                                block_device_info=None, power_on=True):
        super(HyperVClusterDriver, self).finish_revert_migration(
            context, instance, network_info, block_device_info, power_on)
        self._clops.add_to_cluster(instance)

    def rollback_live_migration_at_destination(self, context, instance,
                                               network_info,
                                               block_device_info,
                                               destroy_disks=True,
                                               migrate_data=None):
        if self._livemigrationops.is_instance_clustered(instance.name):
            self.unplug_vifs(instance, network_info)
        else:
            super(HyperVClusterDriver,
                  self).rollback_live_migration_at_destination(
                    context, instance, network_info, block_device_info,
                    destroy_disks, migrate_data)

    def post_live_migration_at_destination(self, context, instance,
                                           network_info,
                                           block_migration=False,
                                           block_device_info=None):
        self._clops.post_migration(instance)
        super(HyperVClusterDriver, self).post_live_migration_at_destination(
            context, instance, network_info,
            block_migration, block_device_info)
