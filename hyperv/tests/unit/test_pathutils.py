#  Copyright 2014 IBM Corp.
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

import os
import time

import mock
from nova import exception
from six.moves import builtins

from hyperv.nova import constants
from hyperv.nova import pathutils
from hyperv.tests.unit import test_base


class PathUtilsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V PathUtils class."""

    def setUp(self):
        super(PathUtilsTestCase, self).setUp()
        self.fake_instance_dir = os.path.join('C:', 'fake_instance_dir')
        self.fake_instance_name = 'fake_instance_name'

        self._pathutils = pathutils.PathUtils()
        self._pathutils._smb_conn_attr = mock.MagicMock()

    def _mock_lookup_configdrive_path(self, ext, rescue=False):
        self._pathutils.get_instance_dir = mock.MagicMock(
            return_value=self.fake_instance_dir)

        def mock_exists(*args, **kwargs):
            path = args[0]
            return True if path[(path.rfind('.') + 1):] == ext else False
        self._pathutils.exists = mock_exists
        configdrive_path = self._pathutils.lookup_configdrive_path(
            self.fake_instance_name, rescue)
        return configdrive_path

    def _test_lookup_configdrive_path(self, rescue=False):
        configdrive_name = 'configdrive'
        if rescue:
            configdrive_name += '-rescue'

        for format_ext in constants.DISK_FORMAT_MAP:
            configdrive_path = self._mock_lookup_configdrive_path(format_ext,
                                                                  rescue)
            expected_path = os.path.join(self.fake_instance_dir,
                                         configdrive_name + '.' + format_ext)
            self.assertEqual(expected_path, configdrive_path)

    def test_lookup_configdrive_path(self):
        self._test_lookup_configdrive_path()

    def test_lookup_rescue_configdrive_path(self):
        self._test_lookup_configdrive_path(rescue=True)

    def test_lookup_configdrive_path_non_exist(self):
        self._pathutils.get_instance_dir = mock.MagicMock(
            return_value=self.fake_instance_dir)
        self._pathutils.exists = mock.MagicMock(return_value=False)
        configdrive_path = self._pathutils.lookup_configdrive_path(
            self.fake_instance_name)
        self.assertIsNone(configdrive_path)

    @mock.patch('os.path.join')
    def test_get_instances_sub_dir(self, fake_path_join):

        class WindowsError(Exception):
            def __init__(self, winerror=None):
                self.winerror = winerror

        fake_dir_name = "fake_dir_name"
        fake_windows_error = WindowsError
        self._pathutils.check_create_dir = mock.MagicMock(
            side_effect=WindowsError(pathutils.ERROR_INVALID_NAME))
        with mock.patch.object(builtins, 'WindowsError',
                               fake_windows_error, create=True):
            self.assertRaises(exception.AdminRequired,
                              self._pathutils._get_instances_sub_dir,
                              fake_dir_name)

    def test_copy_vm_console_logs(self):
        fake_local_logs = [mock.sentinel.log_path,
                           mock.sentinel.archived_log_path]
        fake_remote_logs = [mock.sentinel.remote_log_path,
                            mock.sentinel.remote_archived_log_path]

        self._pathutils.exists = mock.Mock(return_value=True)
        self._pathutils.copy = mock.Mock()
        self._pathutils.get_vm_console_log_paths = mock.Mock(
            side_effect=[fake_local_logs, fake_remote_logs])

        self._pathutils.copy_vm_console_logs(mock.sentinel.instance_name,
                                            mock.sentinel.dest_host)

        self._pathutils.get_vm_console_log_paths.assert_has_calls(
            [mock.call(mock.sentinel.instance_name),
             mock.call(mock.sentinel.instance_name,
                       remote_server=mock.sentinel.dest_host)])
        self._pathutils.copy.assert_has_calls([
            mock.call(mock.sentinel.log_path,
                      mock.sentinel.remote_log_path),
            mock.call(mock.sentinel.archived_log_path,
                      mock.sentinel.remote_archived_log_path)])

    @mock.patch.object(pathutils.PathUtils, 'get_base_vhd_dir')
    @mock.patch.object(pathutils.PathUtils, 'exists')
    def _test_lookup_image_basepath(self, mock_exists,
                                    mock_get_base_vhd_dir, found=True):
        fake_image_name = 'fake_image_name'
        if found:
            mock_exists.side_effect = [False, True]
        else:
            mock_exists.return_value = False
        mock_get_base_vhd_dir.return_value = 'fake_base_dir'

        res = self._pathutils.lookup_image_basepath(fake_image_name)

        mock_get_base_vhd_dir.assert_called_once_with()
        if found:
            self.assertEqual(
                res, os.path.join('fake_base_dir', 'fake_image_name.vhdx'))
        else:
            self.assertIsNone(res)

    def test_lookup_image_basepath(self):
        self._test_lookup_image_basepath()

    def test_lookup_image_basepath_not_found(self):
        self._test_lookup_image_basepath(found=False)

    def test_get_age_of_file(self):
        current_time = time.time()
        self._check_get_age_of_file(current_time=current_time)

    @mock.patch.object(os.path, 'getmtime')
    @mock.patch('time.time')
    def _check_get_age_of_file(self, mock_time, mock_getmtime, current_time):
        mock_time.return_value = current_time
        mock_getmtime.return_value = current_time - 5

        ret = self._pathutils.get_age_of_file(mock.sentinel.file_name)

        self.assertEqual(5, ret)
        mock_getmtime.assert_called_once_with(mock.sentinel.file_name)

    @mock.patch('os.path.exists')
    @mock.patch('tempfile.NamedTemporaryFile')
    def test_check_dirs_shared_storage(self, mock_named_tempfile,
                                       mock_exists):
        fake_src_dir = 'fake_src_dir'
        fake_dest_dir = 'fake_dest_dir'

        mock_exists.return_value = True
        mock_tmpfile = mock_named_tempfile.return_value.__enter__.return_value
        mock_tmpfile.name = 'fake_tmp_fname'
        expected_src_tmp_path = os.path.join(fake_src_dir,
                                             mock_tmpfile.name)

        self._pathutils.check_dirs_shared_storage(
            fake_src_dir, fake_dest_dir)

        mock_named_tempfile.assert_called_once_with(dir=fake_dest_dir)
        mock_exists.assert_called_once_with(expected_src_tmp_path)
