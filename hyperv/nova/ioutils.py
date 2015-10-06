# Copyright 2014 Cloudbase Solutions Srl
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

import ctypes
import six
import struct
import sys

from eventlet import patcher
from nova.i18n import _
from oslo_log import log as logging
from oslo_utils import units

from hyperv.nova import constants
from hyperv.nova import vmutils

LOG = logging.getLogger(__name__)

# Avoid using six.moves.queue as we need a non monkey patched class
if sys.version_info > (3, 0):
    Queue = patcher.original('queue')
else:
    Queue = patcher.original('Queue')

if sys.platform == 'win32':
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ('Internal', wintypes.ULONG),
            ('InternalHigh', wintypes.ULONG),
            ('Offset', wintypes.DWORD),
            ('OffsetHigh', wintypes.DWORD),
            ('hEvent', wintypes.HANDLE)
        ]

        def __init__(self):
            self.Offset = 0
            self.OffsetHigh = 0

    LPOVERLAPPED = ctypes.POINTER(OVERLAPPED)
    LPOVERLAPPED_COMPLETION_ROUTINE = ctypes.WINFUNCTYPE(
        None, wintypes.DWORD, wintypes.DWORD, LPOVERLAPPED)

    kernel32.ReadFileEx.argtypes = [
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        LPOVERLAPPED, LPOVERLAPPED_COMPLETION_ROUTINE]
    kernel32.WriteFileEx.argtypes = [
        wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
        LPOVERLAPPED, LPOVERLAPPED_COMPLETION_ROUTINE]


FILE_FLAG_OVERLAPPED = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3

FORMAT_MESSAGE_FROM_SYSTEM = 0x00001000
FORMAT_MESSAGE_ALLOCATE_BUFFER = 0x00000100
FORMAT_MESSAGE_IGNORE_INSERTS = 0x00000200

INVALID_HANDLE_VALUE = -1
WAIT_FAILED = 0xFFFFFFFF
WAIT_FINISHED = 0
ERROR_PIPE_BUSY = 231
ERROR_PIPE_NOT_CONNECTED = 233
ERROR_NOT_FOUND = 1168

WAIT_PIPE_DEFAULT_TIMEOUT = 5  # seconds
WAIT_IO_COMPLETION_TIMEOUT = 2 * units.k
WAIT_INFINITE_TIMEOUT = 0xFFFFFFFF

IO_QUEUE_TIMEOUT = 2
IO_QUEUE_BURST_TIMEOUT = 0.05


class HyperVIOError(vmutils.HyperVException):
    msg_fmt = _("IO operation failed while executing "
                "Win32 API function %(func_name)s. "
                "Error code: %(error_code)s. "
                "Error message %(error_message)s.")

    def __init__(self, error_code=None, error_message=None,
                 func_name=None):
        self.error_code = error_code
        message = self.msg_fmt % {'func_name': func_name,
                                  'error_code': error_code,
                                  'error_message': error_message}
        super(HyperVIOError, self).__init__(message)


class IOUtils(object):
    """Asyncronous IO helper class."""

    def _run_and_check_output(self, func, *args, **kwargs):
        """Convenience helper method for running Win32 API methods."""
        # A list of return values signaling that the operation failed.
        error_codes = kwargs.pop('error_codes', [0])
        ignored_error_codes = kwargs.pop('ignored_error_codes', None)

        ret_val = func(*args, **kwargs)

        if ret_val in error_codes:
            func_name = func.__name__
            self.handle_last_error(func_name=func_name,
                                   ignored_error_codes=ignored_error_codes)
        return ret_val

    def handle_last_error(self, func_name=None, ignored_error_codes=None):
        error_code = kernel32.GetLastError()
        kernel32.SetLastError(0)

        if ignored_error_codes and error_code in ignored_error_codes:
            return

        message_buffer = ctypes.c_char_p()
        kernel32.FormatMessageA(
            FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_ALLOCATE_BUFFER |
            FORMAT_MESSAGE_IGNORE_INSERTS,
            None, error_code, 0, ctypes.byref(message_buffer), 0, None)

        error_message = message_buffer.value
        kernel32.LocalFree(message_buffer)

        raise HyperVIOError(error_code=error_code,
                            error_message=error_message,
                            func_name=func_name)

    def wait_named_pipe(self, pipe_name, timeout=WAIT_PIPE_DEFAULT_TIMEOUT):
        """Wait a given ammount of time for a pipe to become available."""
        self._run_and_check_output(kernel32.WaitNamedPipeW,
                                   ctypes.c_wchar_p(pipe_name),
                                   timeout * units.k)

    def open(self, path, desired_access=None, share_mode=None,
             creation_disposition=None, flags_and_attributes=None):
        error_codes = [INVALID_HANDLE_VALUE]
        handle = self._run_and_check_output(kernel32.CreateFileW,
                                            ctypes.c_wchar_p(path),
                                            desired_access,
                                            share_mode,
                                            None,
                                            creation_disposition,
                                            flags_and_attributes,
                                            None,
                                            error_codes=error_codes)
        return handle

    def cancel_io(self, handle, overlapped_structure=None):
        """Cancels pending IO on specified handle."""
        # Ignore errors thrown when there are no requests
        # to be canceled.
        ignored_error_codes = [ERROR_NOT_FOUND]
        self._run_and_check_output(kernel32.CancelIoEx,
                                   handle,
                                   overlapped_structure,
                                   ignored_error_codes=ignored_error_codes)

    def close_handle(self, handle):
        self._run_and_check_output(kernel32.CloseHandle, handle)

    def _wait_io_completion(self, event):
        self._run_and_check_output(kernel32.WaitForSingleObjectEx,
                                   event, WAIT_INFINITE_TIMEOUT,
                                   True, error_codes=[WAIT_FAILED])

    def set_event(self, event):
        self._run_and_check_output(kernel32.SetEvent, event)

    def _reset_event(self, event):
        self._run_and_check_output(kernel32.ResetEvent, event)

    def _create_event(self, event_attributes=None, manual_reset=True,
                      initial_state=False, name=None):
        return self._run_and_check_output(kernel32.CreateEventW,
                                          event_attributes, manual_reset,
                                          initial_state, name,
                                          error_codes=[None])

    def get_completion_routine(self, callback=None):
        def _completion_routine(error_code, num_bytes, lpOverLapped):
            """Sets the completion event and executes callback, if passed."""
            overlapped = ctypes.cast(lpOverLapped, LPOVERLAPPED).contents
            self.set_event(overlapped.hEvent)

            if callback:
                callback(num_bytes)

        return LPOVERLAPPED_COMPLETION_ROUTINE(_completion_routine)

    def get_new_overlapped_structure(self):
        """Structure used for asyncronous IO operations."""
        # Event used for signaling IO completion
        hEvent = self._create_event()

        overlapped_structure = OVERLAPPED()
        overlapped_structure.hEvent = hEvent
        return overlapped_structure

    def read(self, handle, buff, num_bytes,
             overlapped_structure, completion_routine):
        self._reset_event(overlapped_structure.hEvent)
        self._run_and_check_output(kernel32.ReadFileEx,
                                   handle, buff, num_bytes,
                                   ctypes.byref(overlapped_structure),
                                   completion_routine)
        self._wait_io_completion(overlapped_structure.hEvent)

    def write(self, handle, buff, num_bytes,
              overlapped_structure, completion_routine):
        self._reset_event(overlapped_structure.hEvent)
        self._run_and_check_output(kernel32.WriteFileEx,
                                   handle, buff, num_bytes,
                                   ctypes.byref(overlapped_structure),
                                   completion_routine)
        self._wait_io_completion(overlapped_structure.hEvent)

    def get_buffer(self, buff_size):
        return (ctypes.c_ubyte * buff_size)()

    def get_buffer_data(self, buff, num_bytes):
        return bytes(bytearray(buff[:num_bytes]))

    def write_buffer_data(self, buff, data):
        for i, c in enumerate(data):
            buff[i] = struct.unpack('B', six.b(c))[0]


class IOQueue(Queue.Queue):
    def __init__(self, client_connected):
        Queue.Queue.__init__(self)
        self._client_connected = client_connected

    def get(self, timeout=IO_QUEUE_TIMEOUT, continue_on_timeout=True):
        while self._client_connected.isSet():
            try:
                return Queue.Queue.get(self, timeout=timeout)
            except Queue.Empty:
                if continue_on_timeout:
                    continue
                else:
                    break

    def put(self, item, timeout=IO_QUEUE_TIMEOUT):
        while self._client_connected.isSet():
            try:
                return Queue.Queue.put(self, item, timeout=timeout)
            except Queue.Full:
                continue

    def get_burst(self, timeout=IO_QUEUE_TIMEOUT,
                  burst_timeout=IO_QUEUE_BURST_TIMEOUT,
                  max_size=constants.SERIAL_CONSOLE_BUFFER_SIZE):
        # Get as much data as possible from the queue
        # to avoid sending small chunks.
        data = self.get(timeout=timeout)

        while data and not (len(data) > max_size):
            chunk = self.get(timeout=burst_timeout,
                             continue_on_timeout=False)
            if chunk:
                data += chunk
            else:
                break
        return data
