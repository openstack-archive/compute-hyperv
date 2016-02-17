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

from hyperv.nova.cluster import clusterops
from hyperv.nova.cluster import livemigrationops
from hyperv.nova import driver


class HyperVClusterDriver(driver.HyperVDriver):
    def __init__(self, virtapi):
        super(HyperVClusterDriver, self).__init__(virtapi)

        self._clops = clusterops.ClusterOps()
        self._livemigrationops = livemigrationops.ClusterLiveMigrationOps()

        self._clops.start_failover_listener_daemon()

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info=None, block_device_info=None):
        super(HyperVClusterDriver, self).spawn(
            context, instance, image_meta, injected_files, admin_password,
            network_info, block_device_info)
        self._clops.add_to_cluster(instance)

    def destroy(self, context, instance, network_info, block_device_info=None,
                destroy_disks=True, migrate_data=None):
        self._clops.remove_from_cluster(instance)
        super(HyperVClusterDriver, self).destroy(
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
