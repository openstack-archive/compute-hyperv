# Copyright 2013 Cloudbase Solutions Srl
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

import os
import tempfile
import time

from nova import exception
from os_win import exceptions as os_win_exc
from os_win.utils import pathutils
from os_win import utilsfactory
from oslo_log import log as logging
from oslo_utils import fileutils
from oslo_utils import uuidutils

from compute_hyperv.i18n import _
import compute_hyperv.nova.conf
from compute_hyperv.nova import constants

LOG = logging.getLogger(__name__)

CONF = compute_hyperv.nova.conf.CONF

ERROR_INVALID_NAME = 123

# NOTE(claudiub): part of the pre-existing PathUtils is nova-specific and
# it does not belong in the os-win library. In order to ensure the same
# functionality with the least amount of changes necessary, adding as a mixin
# the os_win.pathutils.PathUtils class into this PathUtils.


class PathUtils(pathutils.PathUtils):

    _CSV_FOLDER = 'ClusterStorage\\'

    def __init__(self):
        super(PathUtils, self).__init__()
        self._vmutils = utilsfactory.get_vmutils()

    def copy_folder_files(self, src_dir, dest_dir):
        """Copies the files of the given src_dir to dest_dir.

        It will ignore any nested folders.

        :param src_dir: Given folder from which to copy files.
        :param dest_dir: Folder to which to copy files.
        """

        # NOTE(claudiub): this will have to be moved to os-win.

        for fname in os.listdir(src_dir):
            src = os.path.join(src_dir, fname)
            # ignore subdirs.
            if os.path.isfile(src):
                self.copy(src, os.path.join(dest_dir, fname))

    def get_instances_dir(self, remote_server=None):
        local_instance_path = os.path.normpath(CONF.instances_path)

        if remote_server and not local_instance_path.startswith(r'\\'):
            if CONF.hyperv.instances_path_share:
                path = CONF.hyperv.instances_path_share
            else:
                # In this case, we expect the instance dir to have the same
                # location on the remote server.
                path = local_instance_path
            return self.get_remote_path(remote_server, path)
        else:
            return local_instance_path

    def get_remote_path(self, remote_server, remote_path):
        if remote_path.startswith('\\\\'):
            return remote_path

        # Use an administrative share
        remote_unc_path = ('\\\\%(remote_server)s\\%(path)s' %
                           dict(remote_server=remote_server,
                                path=remote_path.replace(':', '$')))

        csv_location = '\\'.join([os.getenv('SYSTEMDRIVE', 'C:'),
                                  self._CSV_FOLDER])
        if remote_path.lower().startswith(csv_location.lower()):
            # the given remote_path is a CSV path.
            # Return remote_path as the local path.
            LOG.debug("Remote path %s is on a CSV. Returning as a local path.",
                      remote_path)
            return remote_path

        LOG.debug('Returning UNC path %(unc_path)s for host %(host)s.',
                  dict(unc_path=remote_unc_path, host=remote_server))
        return remote_unc_path

    def _get_instances_sub_dir(self, dir_name, remote_server=None,
                               create_dir=True, remove_dir=False):
        instances_path = self.get_instances_dir(remote_server)
        path = os.path.join(instances_path, dir_name)
        self.check_dir(path, create_dir=create_dir, remove_dir=remove_dir)

        return path

    def check_dir(self, path, create_dir=False, remove_dir=False):
        try:
            if remove_dir:
                self.check_remove_dir(path)
            if create_dir:
                self.check_create_dir(path)
        except WindowsError as ex:
            if ex.winerror == ERROR_INVALID_NAME:
                raise exception.AdminRequired(_(
                    "Cannot access \"%(path)s\", make sure the "
                    "path exists and that you have the proper permissions. "
                    "In particular Nova-Compute must not be executed with the "
                    "builtin SYSTEM account or other accounts unable to "
                    "authenticate on a remote host.") % {'path': path})
            raise

    def get_instance_migr_revert_dir(self, instance_path, create_dir=False,
                                     remove_dir=False):
        dir_name = '%s_revert' % instance_path
        self.check_dir(dir_name, create_dir, remove_dir)
        return dir_name

    def get_instance_dir(self, instance_name, remote_server=None,
                         create_dir=True, remove_dir=False):
        instance_dir = self._get_instances_sub_dir(
            instance_name, remote_server,
            create_dir=False, remove_dir=False)

        # In some situations, the instance files may reside at a different
        # location than the configured one.
        if not os.path.exists(instance_dir):
            vmutils = (self._vmutils if not remote_server
                       else utilsfactory.get_vmutils(remote_server))
            try:
                instance_dir = vmutils.get_vm_config_root_dir(
                    instance_name)
                if remote_server:
                    instance_dir = self.get_remote_path(remote_server,
                                                        instance_dir)
                LOG.info("Found instance dir at non-default location: %s",
                         instance_dir)
            except os_win_exc.HyperVVMNotFoundException:
                pass

        self.check_dir(instance_dir,
                       create_dir=create_dir,
                       remove_dir=remove_dir)
        return instance_dir

    def _lookup_vhd_path(self, instance_name, vhd_path_func,
                         *args, **kwargs):
        vhd_path = None
        for format_ext in ['vhd', 'vhdx']:
            test_path = vhd_path_func(instance_name, format_ext,
                                      *args, **kwargs)
            if self.exists(test_path):
                vhd_path = test_path
                break
        return vhd_path

    def lookup_root_vhd_path(self, instance_name, rescue=False):
        return self._lookup_vhd_path(instance_name, self.get_root_vhd_path,
                                     rescue)

    def lookup_configdrive_path(self, instance_name, rescue=False):
        configdrive_path = None
        for format_ext in constants.DISK_FORMAT_MAP:
            test_path = self.get_configdrive_path(instance_name, format_ext,
                                                  rescue=rescue)
            if self.exists(test_path):
                configdrive_path = test_path
                break
        return configdrive_path

    def lookup_ephemeral_vhd_path(self, instance_name, eph_name):
        return self._lookup_vhd_path(instance_name,
                                     self.get_ephemeral_vhd_path,
                                     eph_name)

    def get_root_vhd_path(self, instance_name, format_ext, rescue=False):
        instance_path = self.get_instance_dir(instance_name)
        image_name = 'root'
        if rescue:
            image_name += '-rescue'
        return os.path.join(instance_path,
                            image_name + '.' + format_ext.lower())

    def get_configdrive_path(self, instance_name, format_ext,
                             remote_server=None, rescue=False):
        instance_path = self.get_instance_dir(instance_name, remote_server)
        configdrive_image_name = 'configdrive'
        if rescue:
            configdrive_image_name += '-rescue'
        return os.path.join(instance_path,
                            configdrive_image_name + '.' + format_ext.lower())

    def get_ephemeral_vhd_path(self, instance_name, format_ext, eph_name):
        instance_path = self.get_instance_dir(instance_name)
        return os.path.join(instance_path, eph_name + '.' + format_ext.lower())

    def get_base_vhd_dir(self):
        return self._get_instances_sub_dir('_base')

    def get_export_dir(self, instance_name=None, instance_dir=None,
                       create_dir=False, remove_dir=False):
        if not instance_dir:
            instance_dir = self.get_instance_dir(instance_name,
                                                 create_dir=create_dir)

        export_dir = os.path.join(instance_dir, 'export')
        self.check_dir(export_dir, create_dir=create_dir,
                       remove_dir=remove_dir)
        return export_dir

    def get_vm_console_log_paths(self, instance_name, remote_server=None):
        instance_dir = self.get_instance_dir(instance_name,
                                             remote_server)
        console_log_path = os.path.join(instance_dir, 'console.log')
        return console_log_path, console_log_path + '.1'

    def copy_vm_console_logs(self, instance_name, dest_host):
        local_log_paths = self.get_vm_console_log_paths(
            instance_name)
        remote_log_paths = self.get_vm_console_log_paths(
            instance_name, remote_server=dest_host)

        for local_log_path, remote_log_path in zip(local_log_paths,
                                                   remote_log_paths):
            if self.exists(local_log_path):
                self.copy(local_log_path, remote_log_path)

    def get_image_path(self, image_name):
        # Note: it is possible that the path doesn't exist
        base_dir = self.get_base_vhd_dir()
        for ext in ['vhd', 'vhdx', 'iso']:
            file_path = os.path.join(base_dir,
                                     image_name + '.' + ext.lower())
            if self.exists(file_path):
                return file_path
        return None

    def get_age_of_file(self, file_name):
        return time.time() - os.path.getmtime(file_name)

    def check_dirs_shared_storage(self, src_dir, dest_dir):
        # Check if shared storage is being used by creating a temporary
        # file at the destination path and checking if it exists at the
        # source path.
        LOG.debug("Checking if %(src_dir)s and %(dest_dir)s point "
                  "to the same location.",
                  dict(src_dir=src_dir, dest_dir=dest_dir))
        with tempfile.NamedTemporaryFile(dir=dest_dir) as tmp_file:
            src_path = os.path.join(src_dir,
                                    os.path.basename(tmp_file.name))

            shared_storage = os.path.exists(src_path)
        return shared_storage

    def check_remote_instances_dir_shared(self, dest):
        # Checks if the instances dir from a remote host points
        # to the same storage location as the local instances dir.
        local_inst_dir = self.get_instances_dir()
        remote_inst_dir = self.get_instances_dir(dest)
        return self.check_dirs_shared_storage(local_inst_dir,
                                              remote_inst_dir)

    def check_instance_shared_storage_local(self, instance):
        instance_dir = self.get_instance_dir(instance.name)

        fd, tmp_file = tempfile.mkstemp(dir=instance_dir)
        LOG.debug("Creating tmpfile %s to verify with other "
                  "compute node that the instance is on "
                  "the same shared storage.",
                  tmp_file, instance=instance)
        os.close(fd)
        # We're sticking with the same dict key as the libvirt driver.
        # At some point, this may become a versioned object.
        return {"filename": tmp_file}

    def check_instance_shared_storage_remote(self, data):
        return os.path.exists(data['filename'])

    def check_instance_shared_storage_cleanup(self, data):
        fileutils.delete_if_exists(data["filename"])

    def get_instance_snapshot_dir(self, instance_name=None, instance_dir=None):
        if instance_name:
            instance_dir = self.get_instance_dir(instance_name,
                                                 create_dir=False)
        return os.path.join(instance_dir, 'Snapshots')

    def get_instance_virtual_machines_dir(self, instance_name=None,
                                          instance_dir=None):
        if instance_name:
            instance_dir = self.get_instance_dir(instance_name,
                                                 create_dir=False)
        return os.path.join(instance_dir, "Virtual Machines")

    def copy_vm_config_files(self, instance_name, dest_dir):
        """Copies the VM configuration files to the given destination folder.

        :param instance_name: the given instance's name.
        :param dest_dir: the location where the VM configuration files are
            copied to.
        """
        src_dir = self.get_instance_virtual_machines_dir(instance_name)
        self.copy_folder_files(src_dir, dest_dir)

    def get_vm_config_file(self, path):
        for dir_file in os.listdir(path):
            file_name, file_ext = os.path.splitext(dir_file)
            if (file_ext.lower() in ['.vmcx', '.xml'] and
                    uuidutils.is_uuid_like(file_name)):

                config_file = os.path.join(path, dir_file)
                LOG.debug("Found VM config file: %s", config_file)
                return config_file

        raise exception.NotFound(
            _("Folder %s does not contain any VM config data file.") % path)
