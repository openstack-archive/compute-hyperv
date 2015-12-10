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

import mock
from oslo_config import cfg

from hyperv.nova import livemigrationops
from hyperv.nova import vmutils
from hyperv.tests import fake_instance
from hyperv.tests.unit import test_base

CONF = cfg.CONF


class LiveMigrationOpsTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V LiveMigrationOps class."""

    def setUp(self):
        super(LiveMigrationOpsTestCase, self).setUp()
        self.context = 'fake_context'
        self._livemigrops = livemigrationops.LiveMigrationOps()
        self._livemigrops._block_dev_man = mock.MagicMock()

    @mock.patch('hyperv.nova.serialconsoleops.SerialConsoleOps.'
                'stop_console_handler')
    @mock.patch('hyperv.nova.vmops.VMOps.copy_vm_dvd_disks')
    def _test_live_migration(self, mock_get_vm_dvd_paths,
                             mock_stop_console_handler, side_effect):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_post = mock.MagicMock()
        mock_recover = mock.MagicMock()
        fake_dest = mock.sentinel.DESTINATION
        self._livemigrops._livemigrutils.live_migrate_vm.side_effect = [
            side_effect]
        if side_effect is vmutils.HyperVException:
            self.assertRaises(vmutils.HyperVException,
                              self._livemigrops.live_migration,
                              self.context, mock_instance, fake_dest,
                              mock_post, mock_recover, False, None)
            mock_recover.assert_called_once_with(self.context, mock_instance,
                                                 fake_dest, False)
        else:
            self._livemigrops.live_migration(context=self.context,
                                             instance_ref=mock_instance,
                                             dest=fake_dest,
                                             post_method=mock_post,
                                             recover_method=mock_recover)

            mock_stop_console_handler.assert_called_once_with(
                mock_instance.name)
            mock_copy_logs = self._livemigrops._pathutils.copy_vm_console_logs
            mock_copy_logs.assert_called_once_with(mock_instance.name,
                                                   fake_dest)
            mock_live_migr = self._livemigrops._livemigrutils.live_migrate_vm
            mock_live_migr.assert_called_once_with(mock_instance.name,
                                                   fake_dest)
            mock_post.assert_called_once_with(self.context, mock_instance,
                                              fake_dest, False)

    def test_live_migration(self):
        self._test_live_migration(side_effect=None)

    def test_live_migration_exception(self):
        self._test_live_migration(side_effect=vmutils.HyperVException)

    def test_live_migration_wrong_os_version(self):
        self._livemigrops._livemigrutils = None
        self.assertRaises(NotImplementedError,
                          self._livemigrops.live_migration, self.context,
                          instance_ref=mock.DEFAULT,
                          dest=mock.sentinel.DESTINATION,
                          post_method=mock.DEFAULT,
                          recover_method=mock.DEFAULT)

    @mock.patch('hyperv.nova.imagecache.ImageCache.get_cached_image')
    @mock.patch('hyperv.nova.volumeops.VolumeOps'
                '.initialize_volumes_connection')
    def test_pre_live_migration(self, mock_initialize_connection,
                                mock_get_cached_image):
        mock_instance = fake_instance.fake_instance_obj(self.context)
        mock_instance.image_ref = "fake_image_ref"
        bdman = self._livemigrops._block_dev_man
        mock_is_boot_from_vol = bdman.is_boot_from_volume
        mock_is_boot_from_vol.return_value = None
        CONF.set_override('use_cow_images', True)
        self._livemigrops.pre_live_migration(
            self.context, mock_instance,
            block_device_info=mock.sentinel.BLOCK_INFO,
            network_info=mock.sentinel.NET_INFO)

        check_config = (
            self._livemigrops._livemigrutils.check_live_migration_config)
        check_config.assert_called_once_with()
        mock_is_boot_from_vol.assert_called_once_with(
            mock.sentinel.BLOCK_INFO)
        mock_get_cached_image.assert_called_once_with(self.context,
                                                      mock_instance)
        mock_initialize_connection.assert_called_once_with(
            mock.sentinel.BLOCK_INFO)

    @mock.patch('hyperv.nova.volumeops.VolumeOps.disconnect_volumes')
    def test_post_live_migration(self, mock_disconnect_volumes):
        self._livemigrops.post_live_migration(
            self.context, mock.sentinel.instance,
            mock.sentinel.block_device_info)
        mock_disconnect_volumes.assert_called_once_with(
            mock.sentinel.block_device_info)
        self._livemigrops._pathutils.get_instance_dir.assert_called_once_with(
            mock.sentinel.instance.name, create_dir=False, remove_dir=True)

    @mock.patch.object(livemigrationops.vmops.VMOps, 'post_start_vifs')
    def test_post_live_migration_at_destination(self, mock_post_start_vifs):
        self._livemigrops.post_live_migration_at_destination(
            mock.sentinel.context, mock.sentinel.instance,
            mock.sentinel.network_info, mock.sentinel.block_migration)

        mock_post_start_vifs.assert_called_once_with(
            mock.sentinel.instance, mock.sentinel.network_info)
