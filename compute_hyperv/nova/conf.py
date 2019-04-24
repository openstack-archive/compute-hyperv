# Copyright 2017 Cloudbase Solutions Srl
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

from oslo_config import cfg

import nova.conf

hyperv_opts = [
    cfg.IntOpt('evacuate_task_state_timeout',
               default=600,
               help='Number of seconds to wait for an instance to be '
                    'evacuated during host maintenance.'),
    cfg.IntOpt('cluster_event_check_interval',
               deprecated_for_removal=True,
               deprecated_since="5.0.1",
               default=2),
    cfg.BoolOpt('instance_automatic_shutdown',
                default=False,
                help='Automatically shutdown instances when the host is '
                     'shutdown. By default, instances will be saved, which '
                     'adds a disk overhead. Changing this option will not '
                     'affect existing instances.'),
    cfg.IntOpt('instance_live_migration_timeout',
               default=300,
               min=0,
               help='Number of seconds to wait for an instance to be '
                    'live migrated (Only applies to clustered instances '
                    'for the moment).'),
    cfg.IntOpt('max_failover_count',
               default=1,
               min=1,
               help="The maximum number of failovers that can occur in the "
                    "failover_period timeframe per VM. Once a VM's number "
                    "failover reaches this number, the VM will simply end up "
                    "in a Failed state."),
    cfg.IntOpt('failover_period',
               default=6,
               min=1,
               help="The number of hours in which the max_failover_count "
                    "number of failovers can occur."),
    cfg.BoolOpt('auto_failback',
                default=True,
                help="Allow the VM the failback to its original host once it "
                     "is available."),
    cfg.BoolOpt('force_destroy_instances',
                default=False,
                help="If this option is enabled, instance destroy requests "
                     "are executed immediately, regardless of instance "
                     "pending tasks. In some situations, the destroy "
                     "operation will fail (e.g. due to file locks), "
                     "requiring subsequent retries."),
    cfg.BoolOpt('move_disks_on_cold_migration',
                default=True,
                help="Move the instance files to the instance dir configured "
                     "on the destination host. You may consider disabling "
                     "this when using multiple CSVs or shares and you wish "
                     "the source location to be preserved."),
]

coordination_opts = [
    cfg.StrOpt('backend_url',
               default='file:///C:/OpenStack/Lock',
               help='The backend URL to use for distributed coordination.'),
]

CONF = nova.conf.CONF
CONF.register_opts(coordination_opts, 'coordination')
CONF.register_opts(hyperv_opts, 'hyperv')


def list_opts():
    return [('coordination', coordination_opts),
            ('hyperv', hyperv_opts)]
