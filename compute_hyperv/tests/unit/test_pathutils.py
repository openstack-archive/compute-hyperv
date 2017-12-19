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

import ddt
import mock
from nova import exception
from os_win import exceptions as os_win_exc
from oslo_utils import fileutils
from six.moves import builtins

from compute_hyperv.nova import constants
from compute_hyperv.nova import pathutils
from compute_hyperv.tests.unit import test_base


@ddt.ddt
class PathUtilsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V PathUtils class."""

    def setUp(self):
        super(PathUtilsTestCase, self).setUp()
        self.fake_instance_dir = os.path.join('C:', 'fake_instance_dir')
        self.fake_instance_name = 'fake_instance_name'

        self._pathutils = pathutils.PathUtils()

    @mock.patch.object(pathutils.PathUtils, 'copy')
    @mock.patch.object(os.path, 'isfile')
    @mock.patch.object(os, 'listdir')
    def test_copy_folder_files(self, mock_listdir, mock_isfile, mock_copy):
        src_dir = 'src'
        dest_dir = 'dest'
        fname = 'tmp_file.txt'
        subdir = 'tmp_folder'
        src_fname = os.path.join(src_dir, fname)
        dest_fname = os.path.join(dest_dir, fname)

        # making sure src_subdir is not copied.
        mock_listdir.return_value = [fname, subdir]
        mock_isfile.side_effect = [True, False]

        self._pathutils.copy_folder_files(src_dir, dest_dir)
        mock_copy.assert_called_once_with(src_fname, dest_fname)

    @ddt.data({'conf_instances_path': r'c:\inst_dir',
               'expected_dir': r'c:\inst_dir'},
              {'conf_instances_path': r'c:\inst_dir',
               'remote_server': 'fake_remote',
               'expected_dir': r'\\fake_remote\c$\inst_dir'},
              {'conf_instances_path': r'\\fake_share\fake_path',
               'remote_server': 'fake_remote',
               'expected_dir': r'\\fake_share\fake_path'},
              {'conf_instances_path_share': r'inst_share',
               'remote_server': 'fake_remote',
               'expected_dir': r'\\fake_remote\inst_share'})
    @ddt.unpack
    def test_get_instances_dir(self, expected_dir, remote_server=None,
                               conf_instances_path='',
                               conf_instances_path_share=''):
        self.flags(instances_path=conf_instances_path)
        self.flags(instances_path_share=conf_instances_path_share,
                   group='hyperv')

        instances_dir = self._pathutils.get_instances_dir(remote_server)

        self.assertEqual(expected_dir, instances_dir)

    def test_get_remote_path_share(self):
        fake_remote_path = '\\\\fake_path'

        actual_path = self._pathutils.get_remote_path(mock.sentinel.server,
                                                      fake_remote_path)
        self.assertEqual(fake_remote_path, actual_path)

    @mock.patch.object(pathutils.os, 'getenv')
    def test_get_remote_path_csv(self, mock_getenv):
        mock_getenv.return_value = 'C:'
        fake_server = 'fake_server'
        fake_remote_path = 'C:\\ClusterStorage\\Volume1\\fake_dir'

        actual_path = self._pathutils.get_remote_path(fake_server,
                                                      fake_remote_path)

        self.assertEqual(fake_remote_path, actual_path)
        mock_getenv.assert_called_once_with('SYSTEMDRIVE', 'C:')

    def test_get_remote_path_normal(self):
        fake_server = 'fake_server'
        fake_remote_path = 'C:\\fake_path'

        actual_path = self._pathutils.get_remote_path(fake_server,
                                                      fake_remote_path)

        expected_path = ('\\\\%(remote_server)s\\%(path)s' %
                         dict(remote_server=fake_server,
                              path=fake_remote_path.replace(':', '$')))
        self.assertEqual(expected_path, actual_path)

    @mock.patch.object(pathutils.PathUtils, 'get_instances_dir')
    @mock.patch.object(pathutils.PathUtils, 'check_dir')
    def test_get_instances_sub_dir(self, mock_check_dir,
                                   mock_get_instances_dir):
        fake_instances_dir = 'fake_instances_dir'
        mock_get_instances_dir.return_value = fake_instances_dir

        sub_dir = 'fake_subdir'
        expected_path = os.path.join(fake_instances_dir, sub_dir)

        path = self._pathutils._get_instances_sub_dir(
            sub_dir,
            remote_server=mock.sentinel.remote_server,
            create_dir=mock.sentinel.create_dir,
            remove_dir=mock.sentinel.remove_dir)

        self.assertEqual(expected_path, path)

        mock_get_instances_dir.assert_called_once_with(
            mock.sentinel.remote_server)
        mock_check_dir.assert_called_once_with(
            expected_path,
            create_dir=mock.sentinel.create_dir,
            remove_dir=mock.sentinel.remove_dir)

    @ddt.data({'create_dir': True, 'remove_dir': False},
              {'create_dir': False, 'remove_dir': True})
    @ddt.unpack
    @mock.patch.object(pathutils.PathUtils, 'check_create_dir')
    @mock.patch.object(pathutils.PathUtils, 'check_remove_dir')
    def test_check_dir(self, mock_check_remove_dir, mock_check_create_dir,
                       create_dir, remove_dir):
        self._pathutils.check_dir(
            mock.sentinel.dir, create_dir=create_dir, remove_dir=remove_dir)

        if create_dir:
            mock_check_create_dir.assert_called_once_with(mock.sentinel.dir)
        else:
            self.assertFalse(mock_check_create_dir.called)

        if remove_dir:
            mock_check_remove_dir.assert_called_once_with(mock.sentinel.dir)
        else:
            self.assertFalse(mock_check_remove_dir.called)

    @mock.patch.object(pathutils.PathUtils, 'check_create_dir')
    def test_check_dir_exc(self, mock_check_create_dir):

        class FakeWindowsError(Exception):
            def __init__(self, winerror=None):
                self.winerror = winerror

        mock_check_create_dir.side_effect = FakeWindowsError(
            pathutils.ERROR_INVALID_NAME)
        with mock.patch.object(builtins, 'WindowsError',
                               FakeWindowsError, create=True):
            self.assertRaises(exception.AdminRequired,
                              self._pathutils.check_dir,
                              mock.sentinel.dir_name,
                              create_dir=True)

    @mock.patch.object(pathutils.PathUtils, 'check_dir')
    def test_get_instance_migr_revert_dir(self, mock_check_dir):
        dir_name = 'fake_dir'
        expected_dir_name = '%s_revert' % dir_name

        revert_dir = self._pathutils.get_instance_migr_revert_dir(
            dir_name, create_dir=mock.sentinel.create_dir,
            remove_dir=mock.sentinel.remove_dir)

        self.assertEqual(expected_dir_name, revert_dir)
        mock_check_dir.assert_called_once_with(expected_dir_name,
                                               mock.sentinel.create_dir,
                                               mock.sentinel.remove_dir)

    @ddt.data({},
              {'configured_dir_exists': True},
              {'vm_exists': True},
              {'vm_exists': True,
               'remote_server': mock.sentinel.remote_server})
    @ddt.unpack
    @mock.patch.object(pathutils.PathUtils, '_get_instances_sub_dir')
    @mock.patch.object(pathutils.PathUtils, 'get_remote_path')
    @mock.patch.object(pathutils.PathUtils, 'check_dir')
    @mock.patch.object(pathutils.os.path, 'exists')
    @mock.patch('os_win.utilsfactory.get_vmutils')
    def test_get_instance_dir(self, mock_get_vmutils,
                              mock_exists,
                              mock_check_dir,
                              mock_get_remote_path,
                              mock_get_instances_sub_dir,
                              configured_dir_exists=False,
                              remote_server=None, vm_exists=False):
        mock_get_instances_sub_dir.return_value = mock.sentinel.configured_dir
        mock_exists.return_value = configured_dir_exists

        expected_vmutils = (self._pathutils._vmutils
                            if not remote_server
                            else mock_get_vmutils.return_value)
        mock_get_root_dir = expected_vmutils.get_vm_config_root_dir
        mock_get_root_dir.side_effect = (
            (mock.sentinel.config_root_dir,)
            if vm_exists
            else os_win_exc.HyperVVMNotFoundException(
                vm_name=mock.sentinel.instance_name))

        mock_get_remote_path.return_value = mock.sentinel.remote_root_dir

        instance_dir = self._pathutils.get_instance_dir(
            mock.sentinel.instance_name,
            remote_server=remote_server,
            create_dir=mock.sentinel.create_dir,
            remove_dir=mock.sentinel.remove_dir)

        if configured_dir_exists or not vm_exists:
            expected_instance_dir = mock.sentinel.configured_dir
        else:
            # In this case, we expect the instance location to be
            # retrieved from the vm itself.
            mock_get_root_dir.assert_called_once_with(
                mock.sentinel.instance_name)

            if remote_server:
                expected_instance_dir = mock.sentinel.remote_root_dir
                mock_get_remote_path.assert_called_once_with(
                    mock.sentinel.remote_server,
                    mock.sentinel.config_root_dir)
            else:
                expected_instance_dir = mock.sentinel.config_root_dir

        self.assertEqual(expected_instance_dir, instance_dir)

        mock_get_instances_sub_dir.assert_called_once_with(
            mock.sentinel.instance_name, remote_server,
            create_dir=False, remove_dir=False)
        mock_check_dir.assert_called_once_with(
            expected_instance_dir,
            create_dir=mock.sentinel.create_dir,
            remove_dir=mock.sentinel.remove_dir)

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

    @mock.patch.object(pathutils.PathUtils, 'check_dir')
    @mock.patch.object(pathutils.PathUtils, 'get_instance_dir')
    def test_export_dir(self, mock_get_instance_dir, mock_check_dir):
        mock_get_instance_dir.return_value = self.fake_instance_dir

        export_dir = self._pathutils.get_export_dir(
            mock.sentinel.instance_name, create_dir=mock.sentinel.create_dir,
            remove_dir=mock.sentinel.remove_dir)

        expected_dir = os.path.join(self.fake_instance_dir, 'export')
        self.assertEqual(expected_dir, export_dir)
        mock_get_instance_dir.assert_called_once_with(
            mock.sentinel.instance_name, create_dir=mock.sentinel.create_dir)
        mock_check_dir.assert_called_once_with(
            expected_dir, create_dir=mock.sentinel.create_dir,
            remove_dir=mock.sentinel.remove_dir)

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
    def _test_get_image_path(self, mock_exists, mock_get_base_vhd_dir,
                             found=True):
        fake_image_name = 'fake_image_name'
        if found:
            mock_exists.side_effect = [False, True]
            expected_path = os.path.join('fake_base_dir',
                                         'fake_image_name.vhdx')
        else:
            mock_exists.return_value = False
            expected_path = None
        mock_get_base_vhd_dir.return_value = 'fake_base_dir'

        res = self._pathutils.get_image_path(fake_image_name)

        mock_get_base_vhd_dir.assert_called_once_with()
        self.assertEqual(expected_path, res)

    def test_get_image_path(self):
        self._test_get_image_path()

    def test_get_image_path_not_found(self):
        self._test_get_image_path(found=False)

    @mock.patch('os.path.getmtime')
    @mock.patch.object(pathutils, 'time')
    def test_get_age_of_file(self, mock_time, mock_getmtime):
        mock_time.time.return_value = time.time()
        mock_getmtime.return_value = mock_time.time.return_value - 42

        actual_age = self._pathutils.get_age_of_file(mock.sentinel.filename)
        self.assertEqual(42, actual_age)
        mock_time.time.assert_called_once_with()
        mock_getmtime.assert_called_once_with(mock.sentinel.filename)

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

    @mock.patch.object(pathutils.PathUtils, 'check_dirs_shared_storage')
    @mock.patch.object(pathutils.PathUtils, 'get_instances_dir')
    def test_check_remote_instances_shared(self, mock_get_instances_dir,
                                           mock_check_dirs_shared_storage):
        mock_get_instances_dir.side_effect = [mock.sentinel.local_inst_dir,
                                              mock.sentinel.remote_inst_dir]

        shared_storage = self._pathutils.check_remote_instances_dir_shared(
            mock.sentinel.dest)

        self.assertEqual(mock_check_dirs_shared_storage.return_value,
                         shared_storage)
        mock_get_instances_dir.assert_has_calls(
            [mock.call(), mock.call(mock.sentinel.dest)])
        mock_check_dirs_shared_storage.assert_called_once_with(
            mock.sentinel.local_inst_dir, mock.sentinel.remote_inst_dir)

    @mock.patch.object(os, 'close')
    @mock.patch('tempfile.mkstemp')
    @mock.patch.object(pathutils.PathUtils, 'get_instance_dir')
    def test_check_instance_shared_storage_local(self, mock_get_instance_dir,
                                                 mock_mkstemp, mock_close):
        mock_instance = mock.Mock()
        mock_mkstemp.return_value = (mock.sentinel.tmp_fd,
                                     mock.sentinel.tmp_file)

        ret_val = self._pathutils.check_instance_shared_storage_local(
            mock_instance)
        exp_ret_val = {'filename': mock.sentinel.tmp_file}

        self.assertEqual(exp_ret_val, ret_val)
        mock_get_instance_dir.assert_called_once_with(mock_instance.name)
        mock_mkstemp.assert_called_once_with(
            dir=mock_get_instance_dir.return_value)
        mock_close.assert_called_once_with(mock.sentinel.tmp_fd)

    @mock.patch.object(os.path, 'exists')
    def test_check_instance_shared_storage_remote(self, mock_exists):
        check_data = dict(filename=mock.sentinel.filename)
        ret_val = self._pathutils.check_instance_shared_storage_remote(
            check_data)

        self.assertEqual(mock_exists.return_value, ret_val)

    @mock.patch.object(fileutils, 'delete_if_exists')
    def test_check_instance_shared_storage_cleanup(self,
                                                   mock_delete_if_exists):
        check_data = dict(filename=mock.sentinel.filename)
        self._pathutils.check_instance_shared_storage_cleanup(check_data)

        mock_delete_if_exists.assert_called_once_with(mock.sentinel.filename)

    @mock.patch.object(pathutils.PathUtils, 'get_instance_dir')
    def test_get_instance_snapshot_dir(self, mock_get_instance_dir):
        mock_get_instance_dir.return_value = self.fake_instance_dir
        response = self._pathutils.get_instance_snapshot_dir(
            self.fake_instance_name)

        expected_path = os.path.join(self.fake_instance_dir, 'Snapshots')
        self.assertEqual(expected_path, response)
        mock_get_instance_dir.assert_called_once_with(self.fake_instance_name,
                                                      create_dir=False)

    @mock.patch.object(pathutils.PathUtils, 'get_instance_dir')
    def test_get_instance_virtual_machines_dir(self, mock_get_instance_dir):
        mock_get_instance_dir.return_value = self.fake_instance_dir
        response = self._pathutils.get_instance_virtual_machines_dir(
            self.fake_instance_name)

        expected_path = os.path.join(self.fake_instance_dir,
                                     'Virtual Machines')
        self.assertEqual(expected_path, response)
        mock_get_instance_dir.assert_called_once_with(self.fake_instance_name,
                                                      create_dir=False)

    @mock.patch.object(pathutils.PathUtils, 'copy_folder_files')
    @mock.patch.object(pathutils.PathUtils,
                       'get_instance_virtual_machines_dir')
    def test_copy_vm_config_files(self, mock_get_inst_vm_dir, mock_copy_files):
        self._pathutils.copy_vm_config_files(mock.sentinel.instance_name,
                                             mock.sentinel.dest_dir)

        mock_get_inst_vm_dir.assert_called_once_with(
            mock.sentinel.instance_name)
        mock_copy_files.assert_called_once_with(
            mock_get_inst_vm_dir.return_value, mock.sentinel.dest_dir)

    @mock.patch('os.listdir')
    def test_get_vm_config_file(self, mock_listdir):
        config_file = '81027A62-7187-4EC4-AFF5-9CA853BF7C68.vmcx'
        mock_listdir.return_value = [config_file]

        response = self._pathutils.get_vm_config_file(self.fake_instance_dir)

        expected_path = os.path.join(self.fake_instance_dir, config_file)
        self.assertEqual(expected_path, response)
        mock_listdir.assert_called_once_with(self.fake_instance_dir)

    @mock.patch('os.listdir')
    def test_get_vm_config_file_exception(self, mock_listdir):
        mock_listdir.return_value = ['fake_file']

        self.assertRaises(exception.NotFound,
                          self._pathutils.get_vm_config_file,
                          mock.sentinel.instances_path)
