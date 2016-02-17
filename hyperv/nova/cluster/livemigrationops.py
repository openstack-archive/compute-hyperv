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

"""Management class for cluster live migration VM operations."""

from os_win import exceptions as os_win_exc
from os_win import utilsfactory
from oslo_log import log as logging
from oslo_utils import excutils

from hyperv.nova import livemigrationops

LOG = logging.getLogger(__name__)


class ClusterLiveMigrationOps(livemigrationops.LiveMigrationOps):
    def __init__(self):
        super(ClusterLiveMigrationOps, self).__init__()
        self._clustutils = utilsfactory.get_clusterutils()

    def _is_instance_clustered(self, instance_name):
        return self._clustutils.vm_exists(instance_name)

    def live_migration(self, context, instance_ref, dest, post_method,
                       recover_method, block_migration=False,
                       migrate_data=None):
        LOG.debug("live_migration called.", instance=instance_ref)
        instance_name = instance_ref.name
        clustered = self._is_instance_clustered(instance_name)
        node_names = [node.upper() for node in
                      self._clustutils.get_cluster_node_names()]

        if dest.upper() not in node_names or not clustered:
            # destination is not in same cluster or instance not clustered.
            # do a normal live migration.
            if clustered:
                # remove VM from cluster before proceding to a normal live
                # migration.
                self._clustutils.delete(instance_name)
            super(ClusterLiveMigrationOps, self).live_migration(
                context, instance_ref, dest, post_method, recover_method,
                block_migration, migrate_data)
            return
        elif self._clustutils.get_vm_host(
                instance_name).upper() == dest.upper():
            # VM is already migrated. Do nothing.
            # this can happen when the VM has been failovered.
            return

        # destination is in the same cluster.
        # perform a clustered live migration.
        try:
            self._clustutils.live_migrate_vm(instance_name, dest)
        except os_win_exc.HyperVVMNotFoundException:
            with excutils.save_and_reraise_exception():
                LOG.debug("Calling live migration recover_method "
                          "for instance.", instance=instance_ref)
                recover_method(context, instance_ref, dest, block_migration)

        LOG.debug("Calling live migration post_method for instance.",
                  instance=instance_ref)
        post_method(context, instance_ref, dest, block_migration)

    def pre_live_migration(self, context, instance, block_device_info,
                           network_info):
        if self._is_instance_clustered(instance.name):
            self._volumeops.connect_volumes(block_device_info)
        else:
            super(ClusterLiveMigrationOps, self).pre_live_migration(
                context, instance, block_device_info, network_info)

    def post_live_migration(self, context, instance, block_device_info):
        if not self._is_instance_clustered(instance.name):
            super(ClusterLiveMigrationOps, self).post_live_migration(
                context, instance, block_device_info)
