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

from compute_hyperv.nova.cluster import volumeops
from compute_hyperv.nova import constants
from compute_hyperv.nova import volumeops as base_volumeops
from compute_hyperv.tests.unit import test_base


class ClusterVolumeOpsTestCase(test_base.HyperVBaseTestCase):
    _autospec_classes = [
        base_volumeops.cinder.API,
    ]

    def setUp(self):
        super(ClusterVolumeOpsTestCase, self).setUp()
        self._volumeops = volumeops.ClusterVolumeOps()

    def test_loaded_volume_drivers(self):
        self.assertEqual(set([constants.STORAGE_PROTOCOL_SMBFS]),
                         set(self._volumeops.volume_drivers.keys()))

    def test_get_blacklisted_volume_driver(self):
        conn_info = dict(driver_volume_type=constants.STORAGE_PROTOCOL_ISCSI)

        self.assertRaises(
            exception.VolumeDriverNotFound,
            self._volumeops._get_volume_driver,
            conn_info)

    def test_get_supported_volume_driver(self):
        conn_info = dict(driver_volume_type=constants.STORAGE_PROTOCOL_SMBFS)
        drv = self._volumeops._get_volume_driver(conn_info)

        self.assertIsInstance(drv, base_volumeops.SMBFSVolumeDriver)
