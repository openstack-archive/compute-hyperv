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

import mock

from hyperv.nova import constants
from hyperv.nova import ioutils
from hyperv.tests.unit import test_base


class IOUtilsTestCase(test_base.HyperVBaseTestCase):
    def setUp(self):
        super(IOUtilsTestCase, self).setUp()

        self._fake_kernel32 = mock.Mock()
        kernel32_patcher = mock.patch.object(
            ioutils, 'kernel32', new=self._fake_kernel32, create=True)
        self.addCleanup(kernel32_patcher.stop)
        kernel32_patcher.start()

        self._ioutils = ioutils.IOUtils()

    @mock.patch.object(ioutils.IOUtils, 'handle_last_error')
    def test_run_and_check_output(self, mock_handle_last_error):
        mock_func = mock.Mock()
        mock_func.__name__ = mock.sentinel.__name__

        ret_val = self._ioutils._run_and_check_output(
            mock_func, error_codes=[mock_func()],
            ignored_error_codes=mock.sentinel.ignored_error_codes)

        mock_handle_last_error.assert_called_once_with(
            func_name=mock.sentinel.__name__,
            ignored_error_codes=mock.sentinel.ignored_error_codes)
        self.assertEqual(mock_func(), ret_val)

    @mock.patch.object(ioutils, 'ctypes')
    def test_handle_last_error(self, mock_ctypes):
        self.assertRaises(ioutils.HyperVIOError,
                          self._ioutils.handle_last_error)

        last_error_code = self._fake_kernel32.GetLastError()
        fake_message_buffer = mock_ctypes.c_char_p()

        self._fake_kernel32.SetLastError.assert_called_once_with(0)
        self._fake_kernel32.LocalFree.assert_called_once_with(
            fake_message_buffer)

        expected_flags = (ioutils.FORMAT_MESSAGE_FROM_SYSTEM |
                          ioutils.FORMAT_MESSAGE_ALLOCATE_BUFFER |
                          ioutils.FORMAT_MESSAGE_IGNORE_INSERTS)
        self._fake_kernel32.FormatMessageA.assert_called_once_with(
            expected_flags, None, last_error_code, 0,
            mock_ctypes.byref(fake_message_buffer), 0, None)

    def test_get_write_buffer_data(self):
        fake_data = 'fake data'
        fake_buffer = (ctypes.c_ubyte * len(fake_data))()

        self._ioutils.write_buffer_data(fake_buffer, fake_data)
        buff_data = self._ioutils.get_buffer_data(fake_buffer, len(fake_data))

        self.assertEqual(six.b(fake_data), buff_data)


class IOQueueTestCase(test_base.HyperVBaseTestCase):
    def setUp(self):
        super(IOQueueTestCase, self).setUp()

        self._mock_queue = mock.Mock()
        queue_patcher = mock.patch.object(ioutils.Queue, 'Queue',
                                          new=self._mock_queue)
        queue_patcher.start()
        self.addCleanup(queue_patcher.stop)

        self._mock_client_connected = mock.Mock()
        self._ioqueue = ioutils.IOQueue(self._mock_client_connected)

    def test_get(self):
        self._mock_client_connected.isSet.return_value = True
        self._mock_queue.get.return_value = mock.sentinel.item

        queue_item = self._ioqueue.get(timeout=mock.sentinel.timeout)

        self._mock_queue.get.assert_called_once_with(
            self._ioqueue, timeout=mock.sentinel.timeout)
        self.assertEqual(mock.sentinel.item, queue_item)

    def _test_get_timeout(self, continue_on_timeout=True):
        self._mock_client_connected.isSet.side_effect = [True, True, False]
        self._mock_queue.get.side_effect = ioutils.Queue.Empty

        queue_item = self._ioqueue.get(timeout=mock.sentinel.timeout,
                                       continue_on_timeout=continue_on_timeout)

        expected_calls_number = 2 if continue_on_timeout else 1
        self._mock_queue.get.assert_has_calls(
            [mock.call(self._ioqueue, timeout=mock.sentinel.timeout)] *
            expected_calls_number)
        self.assertIsNone(queue_item)

    def test_get_continue_on_timeout(self):
        # Test that the queue blocks as long
        # as the client connected event is set.
        self._test_get_timeout()

    def test_get_break_on_timeout(self):
        self._test_get_timeout(continue_on_timeout=False)

    def test_put(self):
        self._mock_client_connected.isSet.side_effect = [True, True, False]
        self._mock_queue.put.side_effect = ioutils.Queue.Full

        self._ioqueue.put(mock.sentinel.item,
                          timeout=mock.sentinel.timeout)

        self._mock_queue.put.assert_has_calls(
            [mock.call(self._ioqueue, mock.sentinel.item,
                       timeout=mock.sentinel.timeout)] * 2)

    @mock.patch.object(ioutils.IOQueue, 'get')
    def _test_get_burst(self, mock_get,
                        exceeded_max_size=False):
        fake_data = 'fake_data'

        mock_get.side_effect = [fake_data, fake_data, None]

        if exceeded_max_size:
            max_size = 0
        else:
            max_size = constants.SERIAL_CONSOLE_BUFFER_SIZE

        ret_val = self._ioqueue.get_burst(
            timeout=mock.sentinel.timeout,
            burst_timeout=mock.sentinel.burst_timeout,
            max_size=max_size)

        expected_calls = [mock.call(timeout=mock.sentinel.timeout)]
        expected_ret_val = fake_data

        if not exceeded_max_size:
            expected_calls.append(
                mock.call(timeout=mock.sentinel.burst_timeout,
                          continue_on_timeout=False))
            expected_ret_val += fake_data

        mock_get.assert_has_calls(expected_calls)
        self.assertEqual(expected_ret_val, ret_val)

    def test_get_burst(self):
        self._test_get_burst()

    def test_get_burst_exceeded_size(self):
        self._test_get_burst(exceeded_max_size=True)
