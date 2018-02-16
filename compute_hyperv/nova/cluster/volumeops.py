# Copyright 2018 Cloudbase Solutions Srl
#
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

from nova import exception
from oslo_log import log as logging

from compute_hyperv.nova import constants
from compute_hyperv.nova import volumeops

LOG = logging.getLogger(__name__)


class ClusterVolumeOps(volumeops.VolumeOps):
    def _load_volume_drivers(self):
        self.volume_drivers = {
            constants.STORAGE_PROTOCOL_SMBFS: volumeops.SMBFSVolumeDriver()
        }

    def _get_volume_driver(self, connection_info):
        driver_type = connection_info.get('driver_volume_type')
        if driver_type in [constants.STORAGE_PROTOCOL_ISCSI,
                           constants.STORAGE_PROTOCOL_FC]:
            err_msg = (
                "The Hyper-V Cluster driver does not currently support "
                "passthrough disks (e.g. iSCSI/FC disks). The reason is "
                "that the volumes need to be available on the destination "
                "host side during an unexpected instance failover. In order "
                "to leverage your storage backend, you may either use the "
                "*standard* Nova Hyper-V driver or use the Cinder SMB volume "
                "driver (which may imply deploying CSVs on top of LUNs "
                "exposed by your storage backend).")
            LOG.error(err_msg)
            raise exception.VolumeDriverNotFound(driver_type=driver_type)

        return super(ClusterVolumeOps, self)._get_volume_driver(
            connection_info)
