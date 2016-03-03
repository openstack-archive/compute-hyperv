# Copyright 2012 Cloudbase Solutions Srl
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

"""
Constants used in ops classes
"""

from nova.compute import arch
from nova.compute import power_state
from os_win import constants
from oslo_utils import units

HYPERV_POWER_STATE = {
    constants.HYPERV_VM_STATE_DISABLED: power_state.SHUTDOWN,
    constants.HYPERV_VM_STATE_SHUTTING_DOWN: power_state.SHUTDOWN,
    constants.HYPERV_VM_STATE_ENABLED: power_state.RUNNING,
    constants.HYPERV_VM_STATE_PAUSED: power_state.PAUSED,
    constants.HYPERV_VM_STATE_SUSPENDED: power_state.SUSPENDED
}

WMI_WIN32_PROCESSOR_ARCHITECTURE = {
    constants.ARCH_I686: arch.I686,
    constants.ARCH_MIPS: arch.MIPS,
    constants.ARCH_ALPHA: arch.ALPHA,
    constants.ARCH_PPC: arch.PPC,
    constants.ARCH_ARMV7: arch.ARMV7,
    constants.ARCH_IA64: arch.IA64,
    constants.ARCH_X86_64: arch.X86_64,
}


CTRL_TYPE_IDE = "IDE"
CTRL_TYPE_SCSI = "SCSI"

DISK = "VHD"
DISK_FORMAT = DISK
DVD = "DVD"
DVD_FORMAT = "ISO"
VOLUME = "VOLUME"

DISK_FORMAT_MAP = {
    DISK_FORMAT.lower(): DISK,
    DVD_FORMAT.lower(): DVD
}

DISK_FORMAT_VHD = "VHD"
DISK_FORMAT_VHDX = "VHDX"

HOST_POWER_ACTION_SHUTDOWN = "shutdown"
HOST_POWER_ACTION_REBOOT = "reboot"
HOST_POWER_ACTION_STARTUP = "startup"

IMAGE_PROP_VM_GEN = "hw_machine_type"
IMAGE_PROP_VM_GEN_1 = "hyperv-gen1"
IMAGE_PROP_VM_GEN_2 = "hyperv-gen2"

VM_GEN_1 = 1
VM_GEN_2 = 2

REMOTEFX_MAX_RES_1024x768 = "1024x768"
REMOTEFX_MAX_RES_1280x1024 = "1280x1024"
REMOTEFX_MAX_RES_1600x1200 = "1600x1200"
REMOTEFX_MAX_RES_1920x1200 = "1920x1200"
REMOTEFX_MAX_RES_2560x1600 = "2560x1600"
REMOTEFX_MAX_RES_3840x2160 = "3840x2160"


FLAVOR_REMOTE_FX_EXTRA_SPEC_KEY = "hyperv:remotefx"

IMAGE_PROP_INTERACTIVE_SERIAL_PORT = "interactive_serial_port"
IMAGE_PROP_LOGGING_SERIAL_PORT = "logging_serial_port"

SERIAL_PORT_TYPE_RO = 'ro'
SERIAL_PORT_TYPE_RW = 'rw'

SERIAL_PORT_TYPES = {
    IMAGE_PROP_LOGGING_SERIAL_PORT: SERIAL_PORT_TYPE_RO,
    IMAGE_PROP_INTERACTIVE_SERIAL_PORT: SERIAL_PORT_TYPE_RW
}

# The default serial console port number used for
# logging and interactive sessions.
DEFAULT_SERIAL_CONSOLE_PORT = 1

SERIAL_CONSOLE_BUFFER_SIZE = 4 * units.Ki
MAX_CONSOLE_LOG_FILE_SIZE = units.Mi // 2

IMAGE_PROP_SECURE_BOOT = "os_secure_boot"
FLAVOR_SPEC_SECURE_BOOT = "os:secure_boot"
REQUIRED = "required"
DISABLED = "disabled"
OPTIONAL = "optional"

BOOT_DEVICE_FLOPPY = 0
BOOT_DEVICE_CDROM = 1
BOOT_DEVICE_HARDDISK = 2
BOOT_DEVICE_NETWORK = 3

_BDI_DEVICE_TYPE_TO_DRIVE_TYPE = {'disk': DISK,
                                  'cdrom': DVD}
