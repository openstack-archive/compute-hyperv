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
from six.moves import builtins

from hyperv.nova import constants
from hyperv.nova import pathutils
from hyperv.nova import vmutils
from hyperv.tests.unit import test_base


class PathUtilsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V PathUtils class."""

    def setUp(self):
        super(PathUtilsTestCase, self).setUp()
        self.fake_instance_dir = os.path.join('C:', 'fake_instance_dir')
        self.fake_instance_name = 'fake_instance_name'

        self._pathutils = pathutils.PathUtils()
        self._pathutils._smb_conn_attr = mock.MagicMock()

    @mock.patch.object(pathutils, 'wmi', create=True)
    def _test_smb_conn(self, mock_wmi, smb_available=True):
        mock_wmi.x_wmi = Exception
        mock_wmi.WMI.side_effect = None if smb_available else Exception

        self._pathutils._set_smb_conn()

        if smb_available:
            expected_conn = mock_wmi.WMI.return_value
            self.assertEqual(expected_conn, self._pathutils._smb_conn)
        else:
            self.assertRaises(vmutils.HyperVException,
                              getattr,
                              self._pathutils, '_smb_conn')

    def test_smb_conn_available(self):
        self._test_smb_conn()

    def test_smb_conn_unavailable(self):
        self._test_smb_conn(smb_available=False)

    @mock.patch.object(pathutils.PathUtils, 'rename')
    @mock.patch.object(os.path, 'isfile')
    @mock.patch.object(os, 'listdir')
    def test_move_folder_files(self, mock_listdir, mock_isfile, mock_rename):
        src_dir = 'src'
        dest_dir = 'dest'
        fname = 'tmp_file.txt'
        subdir = 'tmp_folder'
        src_fname = os.path.join(src_dir, fname)
        dest_fname = os.path.join(dest_dir, fname)

        # making sure src_subdir is not moved.
        mock_listdir.return_value = [fname, subdir]
        mock_isfile.side_effect = [True, False]

        self._pathutils.move_folder_files(src_dir, dest_dir)
        mock_rename.assert_called_once_with(src_fname, dest_fname)

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

    @mock.patch.object(pathutils.PathUtils, 'unmount_smb_share')
    @mock.patch('os.path.exists')
    def _test_check_smb_mapping(self, mock_exists, mock_unmount_smb_share,
                                existing_mappings=True, share_available=False):
        mock_exists.return_value = share_available

        fake_mappings = (
            [mock.sentinel.smb_mapping] if existing_mappings else [])

        self._pathutils._smb_conn.Msft_SmbMapping.return_value = (
            fake_mappings)

        ret_val = self._pathutils.check_smb_mapping(
            mock.sentinel.share_path)

        self.assertEqual(existing_mappings and share_available, ret_val)
        if existing_mappings and not share_available:
            mock_unmount_smb_share.assert_called_once_with(
                mock.sentinel.share_path, force=True)

    def test_check_mapping(self):
        self._test_check_smb_mapping()

    def test_remake_unavailable_mapping(self):
        self._test_check_smb_mapping(existing_mappings=True,
                                     share_available=False)

    def test_available_mapping(self):
        self._test_check_smb_mapping(existing_mappings=True,
                                     share_available=True)

    def test_mount_smb_share(self):
        fake_create = self._pathutils._smb_conn.Msft_SmbMapping.Create
        self._pathutils.mount_smb_share(mock.sentinel.share_path,
                                        mock.sentinel.username,
                                        mock.sentinel.password)
        fake_create.assert_called_once_with(
            RemotePath=mock.sentinel.share_path,
            UserName=mock.sentinel.username,
            Password=mock.sentinel.password)

    def _test_unmount_smb_share(self, force=False):
        fake_mapping = mock.Mock()
        smb_mapping_class = self._pathutils._smb_conn.Msft_SmbMapping
        smb_mapping_class.return_value = [fake_mapping]

        self._pathutils.unmount_smb_share(mock.sentinel.share_path,
                                          force)

        smb_mapping_class.assert_called_once_with(
            RemotePath=mock.sentinel.share_path)
        fake_mapping.Remove.assert_called_once_with(Force=force)

    def test_soft_unmount_smb_share(self):
        self._test_unmount_smb_share()

    def test_force_unmount_smb_share(self):
        self._test_unmount_smb_share(force=True)

    @mock.patch('shutil.rmtree')
    def test_rmtree(self, mock_rmtree):
        class WindowsError(Exception):
            def __init__(self, winerror=None):
                self.winerror = winerror

        mock_rmtree.side_effect = [WindowsError(
            pathutils.ERROR_DIR_IS_NOT_EMPTY), True]
        fake_windows_error = WindowsError
        with mock.patch.object(builtins, 'WindowsError',
                               fake_windows_error, create=True):
            self._pathutils.rmtree(mock.sentinel.FAKE_PATH)

        mock_rmtree.assert_has_calls([mock.call(mock.sentinel.FAKE_PATH),
                                      mock.call(mock.sentinel.FAKE_PATH)])

    def test_get_instances_sub_dir(self):

        class WindowsError(Exception):
            def __init__(self, winerror=None):
                self.winerror = winerror

        fake_dir_name = "fake_dir_name"
        fake_windows_error = WindowsError
        self._pathutils._check_create_dir = mock.MagicMock(
            side_effect=WindowsError(pathutils.ERROR_INVALID_NAME))
        with mock.patch.object(builtins, 'WindowsError',
                               fake_windows_error, create=True):
            self.assertRaises(vmutils.HyperVException,
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
