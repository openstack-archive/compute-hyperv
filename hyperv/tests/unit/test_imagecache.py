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

import os
import uuid

import mock
from nova import exception
from nova import objects
from nova.tests.unit.objects import test_flavor
from oslo_config import cfg
from oslo_utils import units

from hyperv.nova import constants
from hyperv.nova import imagecache
from hyperv.tests import fake_instance
from hyperv.tests.unit import test_base

CONF = cfg.CONF


class ImageCacheTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V ImageCache class."""

    FAKE_BASE_DIR = 'fake/base/dir'
    FAKE_FORMAT = 'fake_format'
    FAKE_IMAGE_REF = 'fake_image_ref'
    FAKE_VHD_SIZE_GB = 1

    def setUp(self):
        super(ImageCacheTestCase, self).setUp()

        self.context = 'fake-context'
        self.instance = fake_instance.fake_instance_obj(self.context)

        self.imagecache = imagecache.ImageCache()
        self.imagecache._pathutils = mock.MagicMock()
        self.imagecache._vhdutils = mock.MagicMock()

    def _test_get_root_vhd_size_gb(self, old_flavor=True):
        if old_flavor:
            mock_flavor = objects.Flavor(**test_flavor.fake_flavor)
            self.instance.old_flavor = mock_flavor
        else:
            self.instance.old_flavor = None
        return self.imagecache._get_root_vhd_size_gb(self.instance)

    def test_get_root_vhd_size_gb_old_flavor(self):
        ret_val = self._test_get_root_vhd_size_gb()
        self.assertEqual(test_flavor.fake_flavor['root_gb'], ret_val)

    def test_get_root_vhd_size_gb(self):
        ret_val = self._test_get_root_vhd_size_gb(old_flavor=False)
        self.assertEqual(self.instance.root_gb, ret_val)

    @mock.patch.object(imagecache.ImageCache, '_get_root_vhd_size_gb')
    def test_resize_and_cache_vhd_smaller(self, mock_get_vhd_size_gb):
        self.imagecache._vhdutils.get_vhd_size.return_value = {
            'VirtualSize': (self.FAKE_VHD_SIZE_GB + 1) * units.Gi
        }
        mock_get_vhd_size_gb.return_value = self.FAKE_VHD_SIZE_GB
        mock_internal_vhd_size = (
            self.imagecache._vhdutils.get_internal_vhd_size_by_file_size)
        mock_internal_vhd_size.return_value = self.FAKE_VHD_SIZE_GB * units.Gi

        self.assertRaises(exception.FlavorDiskSmallerThanImage,
                          self.imagecache._resize_and_cache_vhd,
                          mock.sentinel.instance,
                          mock.sentinel.vhd_path)

        self.imagecache._vhdutils.get_vhd_size.assert_called_once_with(
            mock.sentinel.vhd_path)
        mock_get_vhd_size_gb.assert_called_once_with(mock.sentinel.instance)
        mock_internal_vhd_size.assert_called_once_with(
            mock.sentinel.vhd_path, self.FAKE_VHD_SIZE_GB * units.Gi)

    def _prepare_get_cached_image(self, path_exists=False, use_cow=False,
                                  rescue_image_id=None,
                                  image_format=constants.DISK_FORMAT_VHD):
        self.instance.image_ref = self.FAKE_IMAGE_REF
        self.instance.system_metadata = {'image_disk_format': image_format}
        self.imagecache._pathutils.get_base_vhd_dir.return_value = (
            self.FAKE_BASE_DIR)
        self.imagecache._pathutils.exists.return_value = path_exists
        self.imagecache._vhdutils.get_vhd_format.return_value = (
            constants.DISK_FORMAT_VHD)

        CONF.set_override('use_cow_images', use_cow)

        image_file_name = rescue_image_id or self.FAKE_IMAGE_REF
        expected_path = os.path.join(self.FAKE_BASE_DIR,
                                     image_file_name)
        expected_vhd_path = "%s.%s" % (expected_path,
                                       constants.DISK_FORMAT_VHD.lower())
        return (expected_path, expected_vhd_path)

    @mock.patch.object(imagecache.images, 'fetch')
    def test_get_cached_image_with_fetch(self, mock_fetch):
        (expected_path,
         expected_image_path) = self._prepare_get_cached_image(False, False)

        result = self.imagecache.get_cached_image(self.context, self.instance)
        self.assertEqual(expected_image_path, result)

        mock_fetch.assert_called_once_with(self.context, self.FAKE_IMAGE_REF,
                                           expected_path,
                                           self.instance['user_id'],
                                           self.instance['project_id'])
        self.imagecache._vhdutils.get_vhd_format.assert_called_once_with(
            expected_path)
        self.imagecache._pathutils.rename.assert_called_once_with(
            expected_path, expected_image_path)

    @mock.patch.object(imagecache.images, 'fetch')
    def test_get_cached_image_with_fetch_exception(self, mock_fetch):
        (expected_path,
         expected_image_path) = self._prepare_get_cached_image(False, False)

        # path doesn't exist until fetched.
        self.imagecache._pathutils.exists.side_effect = [False, False, False,
            True]
        mock_fetch.side_effect = exception.InvalidImageRef(
            image_href=self.FAKE_IMAGE_REF)

        self.assertRaises(exception.InvalidImageRef,
                          self.imagecache.get_cached_image,
                          self.context, self.instance)

        self.imagecache._pathutils.remove.assert_called_once_with(
            expected_path)

    @mock.patch.object(imagecache.ImageCache, '_resize_and_cache_vhd')
    @mock.patch.object(imagecache.ImageCache, '_update_image_timestamp')
    def test_get_cached_image_use_cow(self, mock_update_img_timestamp,
                                      mock_resize):
        (expected_path,
         expected_image_path) = self._prepare_get_cached_image(True, True)

        expected_resized_image_path = expected_image_path + 'x'
        mock_resize.return_value = expected_resized_image_path

        result = self.imagecache.get_cached_image(self.context, self.instance)
        self.assertEqual(expected_resized_image_path, result)

        mock_resize.assert_called_once_with(self.instance, expected_image_path)
        mock_update_img_timestamp.assert_called_once_with(
            self.instance.image_ref)

    @mock.patch.object(imagecache.images, 'fetch')
    def test_cache_rescue_image_bigger_than_flavor(self, mock_fetch):
        fake_rescue_image_id = 'fake_rescue_image_id'

        self.imagecache._vhdutils.get_vhd_info.return_value = {
            'VirtualSize': self.instance.root_gb + 1}
        (expected_path,
         expected_vhd_path) = self._prepare_get_cached_image(
            rescue_image_id=fake_rescue_image_id)

        self.assertRaises(exception.ImageUnacceptable,
                          self.imagecache.get_cached_image,
                          self.context, self.instance,
                          fake_rescue_image_id)

        mock_fetch.assert_called_once_with(self.context,
                                           fake_rescue_image_id,
                                           expected_path,
                                           self.instance.user_id,
                                           self.instance.project_id)
        self.imagecache._vhdutils.get_vhd_info.assert_called_once_with(
            expected_vhd_path)

    def test_age_and_verify_cached_images(self):
        fake_images = [mock.sentinel.FAKE_IMG1, mock.sentinel.FAKE_IMG2]
        fake_used_images = [mock.sentinel.FAKE_IMG1]

        self.imagecache.originals = fake_images
        self.imagecache.used_images = fake_used_images

        self.imagecache._update_image_timestamp = mock.Mock()
        self.imagecache._remove_if_old_image = mock.Mock()

        self.imagecache._age_and_verify_cached_images(
            mock.sentinel.FAKE_CONTEXT,
            mock.sentinel.all_instances,
            mock.sentinel.FAKE_BASE_DIR)

        self.imagecache._update_image_timestamp.assert_called_once_with(
            mock.sentinel.FAKE_IMG1)
        self.imagecache._remove_if_old_image.assert_called_once_with(
            mock.sentinel.FAKE_IMG2)

    @mock.patch.object(imagecache.os, 'utime')
    @mock.patch.object(imagecache.ImageCache, '_get_image_backing_files')
    def test_update_image_timestamp(self, mock_get_backing_files, mock_utime):
        mock_get_backing_files.return_value = [mock.sentinel.backing_file,
                                               mock.sentinel.resized_file]

        self.imagecache._update_image_timestamp(mock.sentinel.image)

        mock_get_backing_files.assert_called_once_with(mock.sentinel.image)
        mock_utime.assert_has_calls([
            mock.call(mock.sentinel.backing_file, None),
            mock.call(mock.sentinel.resized_file, None)])

    def test_get_image_backing_files(self):
        image = 'fake-img'
        self.imagecache.unexplained_images = ['%s_42' % image,
                                              'unexplained-img']
        self.imagecache._pathutils.get_image_path.side_effect = [
            mock.sentinel.base_file, mock.sentinel.resized_file]

        backing_files = self.imagecache._get_image_backing_files(image)

        self.assertEqual([mock.sentinel.base_file, mock.sentinel.resized_file],
                         backing_files)
        self.imagecache._pathutils.get_image_path.assert_has_calls(
            [mock.call(image), mock.call('%s_42' % image)])

    @mock.patch.object(imagecache.ImageCache, '_get_image_backing_files')
    def test_remove_if_old_image(self, mock_get_backing_files):
        mock_get_backing_files.return_value = [mock.sentinel.backing_file,
                                               mock.sentinel.resized_file]
        self.imagecache._pathutils.get_age_of_file.return_value = 3600

        self.imagecache._remove_if_old_image(mock.sentinel.image)

        calls = [mock.call(mock.sentinel.backing_file),
                 mock.call(mock.sentinel.resized_file)]
        self.imagecache._pathutils.get_age_of_file.assert_has_calls(calls)
        mock_get_backing_files.assert_called_once_with(mock.sentinel.image)

    def test_remove_old_image(self):
        fake_img_path = os.path.join(self.FAKE_BASE_DIR,
                                     self.FAKE_IMAGE_REF)
        self.imagecache._remove_old_image(fake_img_path)
        self.imagecache._pathutils.remove.assert_called_once_with(
            fake_img_path)

    @mock.patch.object(imagecache.ImageCache, '_age_and_verify_cached_images')
    @mock.patch.object(imagecache.ImageCache, '_list_base_images')
    @mock.patch.object(imagecache.ImageCache, '_list_running_instances')
    def test_update(self, mock_list_instances, mock_list_images,
                    mock_age_cached_images):
        base_vhd_dir = self.imagecache._pathutils.get_base_vhd_dir.return_value
        mock_list_instances.return_value = {
            'used_images': {mock.sentinel.image: mock.sentinel.instances}}
        mock_list_images.return_value = {
            'originals': [mock.sentinel.original_image],
            'unexplained_images': [mock.sentinel.unexplained_image]}

        self.imagecache.update(mock.sentinel.context,
                               mock.sentinel.all_instances)

        self.assertEqual([mock.sentinel.image],
                         list(self.imagecache.used_images))
        self.assertEqual([mock.sentinel.original_image],
                         self.imagecache.originals)
        self.assertEqual([mock.sentinel.unexplained_image],
                         self.imagecache.unexplained_images)
        mock_list_instances.assert_called_once_with(
            mock.sentinel.context, mock.sentinel.all_instances)
        mock_list_images.assert_called_once_with(base_vhd_dir)
        mock_age_cached_images.assert_called_once_with(
            mock.sentinel.context, mock.sentinel.all_instances, base_vhd_dir)

    @mock.patch.object(imagecache.os, 'listdir')
    def test_list_base_images(self, mock_listdir):
        original_image = str(uuid.uuid4())
        unexplained_image = 'just-an-image'
        ignored_file = 'foo.bar'
        mock_listdir.return_value = ['%s.VHD' % original_image,
                                     '%s.vhdx' % unexplained_image,
                                     ignored_file]

        images = self.imagecache._list_base_images(mock.sentinel.base_dir)

        self.assertEqual([original_image], images['originals'])
        self.assertEqual([unexplained_image], images['unexplained_images'])
        mock_listdir.assert_called_once_with(mock.sentinel.base_dir)
