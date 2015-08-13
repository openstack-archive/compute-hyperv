# Copyright 2015 Cloudbase Solutions Srl
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


import mock

from hyperv.nova import hostutilsv2
from hyperv.tests.unit import test_base


class HostUtilsV2TestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V hostutilsv2 class."""

    def setUp(self):
        super(HostUtilsV2TestCase, self).setUp()
        self._hostutils = hostutilsv2.HostUtilsV2()
        self._hostutils._conn_virt = mock.MagicMock()

    def test_get_remotefx_gpu_info(self):
        fake_gpu = mock.MagicMock()
        fake_gpu.Name = mock.sentinel.Fake_gpu_name
        fake_gpu.TotalVideoMemory = mock.sentinel.Fake_gpu_total_memory
        fake_gpu.AvailableVideoMemory = mock.sentinel.Fake_gpu_available_memory
        fake_gpu.DirectXVersion = mock.sentinel.Fake_gpu_directx
        fake_gpu.DriverVersion = mock.sentinel.Fake_gpu_driver_version

        mock_phys_3d_proc = (
            self._hostutils._conn_virt.Msvm_Physical3dGraphicsProcessor)
        mock_phys_3d_proc.return_value = [fake_gpu]

        return_gpus = self._hostutils.get_remotefx_gpu_info()
        self.assertEqual(mock.sentinel.Fake_gpu_name, return_gpus[0]['name'])
        self.assertEqual(mock.sentinel.Fake_gpu_driver_version,
            return_gpus[0]['driver_version'])
        self.assertEqual(mock.sentinel.Fake_gpu_total_memory,
            return_gpus[0]['total_video_ram'])
        self.assertEqual(mock.sentinel.Fake_gpu_available_memory,
            return_gpus[0]['available_video_ram'])
        self.assertEqual(mock.sentinel.Fake_gpu_directx,
            return_gpus[0]['directx_version'])
