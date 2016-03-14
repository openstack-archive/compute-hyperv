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

    _DEVICE_ID = "Microsoft:UUID\\0\\0"
    _NODE_ID = "Microsoft:PhysicalNode\\0"

    def setUp(self):
        super(HostUtilsV2TestCase, self).setUp()
        self._hostutils = hostutilsv2.HostUtilsV2()
        self._hostutils._conn_virt = mock.MagicMock()

    def _check_get_numa_nodes_missing_info(self):
        numa_node = mock.MagicMock()
        self._hostutils._conn_virt.Msvm_NumaNode.return_value = [
            numa_node, numa_node]

        nodes_info = self._hostutils.get_numa_nodes()
        self.assertEqual([], nodes_info)

    @mock.patch.object(hostutilsv2.HostUtilsV2, '_get_numa_memory_info')
    def test_get_numa_nodes_missing_memory_info(self, mock_get_memory_info):
        mock_get_memory_info.return_value = None
        self._check_get_numa_nodes_missing_info()

    @mock.patch.object(hostutilsv2.HostUtilsV2, '_get_numa_cpu_info')
    @mock.patch.object(hostutilsv2.HostUtilsV2, '_get_numa_memory_info')
    def test_get_numa_nodes_missing_cpu_info(self, mock_get_memory_info,
                                             mock_get_cpu_info):
        mock_get_cpu_info.return_value = None
        self._check_get_numa_nodes_missing_info()

    @mock.patch.object(hostutilsv2.HostUtilsV2, '_get_numa_cpu_info')
    @mock.patch.object(hostutilsv2.HostUtilsV2, '_get_numa_memory_info')
    def test_get_numa_nodes(self, mock_get_memory_info, mock_get_cpu_info):
        numa_memory = mock_get_memory_info.return_value
        host_cpu = mock.MagicMock(DeviceID=self._DEVICE_ID)
        mock_get_cpu_info.return_value = [host_cpu]
        numa_node = mock.MagicMock(NodeID=self._NODE_ID)
        self._hostutils._conn_virt.Msvm_NumaNode.return_value = [
            numa_node, numa_node]

        nodes_info = self._hostutils.get_numa_nodes()

        expected_info = {
            'id': self._DEVICE_ID.split('\\')[-1],
            'memory': numa_memory.NumberOfBlocks,
            'memory_usage': numa_node.CurrentlyConsumableMemoryBlocks,
            'cpuset': set([self._DEVICE_ID.split('\\')[-1]]),
            'cpu_usage': 0,
        }

        self.assertEqual([expected_info, expected_info], nodes_info)

    def test_get_numa_memory_info(self):
        host_memory = mock.MagicMock()
        host_memory.path_.return_value = 'fake_wmi_obj_path'
        vm_memory = mock.MagicMock()
        numa_node_assoc = mock.MagicMock()
        numa_node_assoc.path_.return_value = 'fake_wmi_obj_path'
        memory_info = self._hostutils._get_numa_memory_info(
            [numa_node_assoc], [host_memory, vm_memory])

        self.assertEqual(host_memory, memory_info)

    def test_get_numa_memory_info_not_found(self):
        other = mock.MagicMock()
        numa_node_assoc = []
        memory_info = self._hostutils._get_numa_memory_info(
            numa_node_assoc, [other])

        self.assertIsNone(memory_info)

    def test_get_numa_cpu_info(self):
        host_cpu = mock.MagicMock()
        host_cpu.path_.return_value = 'fake_wmi_obj_path'
        vm_cpu = mock.MagicMock()
        vm_cpu.path_return_value = 'fake_wmi_obj_path1'
        numa_node_proc = mock.MagicMock()
        numa_node_proc.path_.return_value = 'fake_wmi_obj_path'
        cpu_info = self._hostutils._get_numa_cpu_info([numa_node_proc],
                                                      [host_cpu, vm_cpu])
        self.assertEqual([host_cpu], cpu_info)

    def test_get_numa_cpu_info_not_found(self):
        other = mock.MagicMock()
        cpu_info = self._hostutils._get_numa_cpu_info([], [other])

        self.assertIsNone(cpu_info)

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
