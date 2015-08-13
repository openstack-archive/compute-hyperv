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

import sys

if sys.platform == 'win32':
    import wmi

from hyperv.nova import hostutils


class HostUtilsV2(hostutils.HostUtils):

    def __init__(self):
        super(HostUtilsV2, self).__init__()
        self._init_wmi_virt_conn()

    def _init_wmi_virt_conn(self):
        if sys.platform == 'win32':
            self._conn_virt = wmi.WMI(moniker='//./root/virtualization/v2')

    def get_remotefx_gpu_info(self):
        gpus = []
        all_gpus = self._conn_virt.Msvm_Physical3dGraphicsProcessor(
            EnabledForVirtualization=True)
        for gpu in all_gpus:
            gpus.append({'name': gpu.Name,
                         'driver_version': gpu.DriverVersion,
                         'total_video_ram': gpu.TotalVideoMemory,
                         'available_video_ram': gpu.AvailableVideoMemory,
                         'directx_version': gpu.DirectXVersion})
        return gpus
