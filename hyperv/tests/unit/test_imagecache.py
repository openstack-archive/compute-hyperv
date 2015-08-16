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

import mock
from nova import exception
from nova import objects
from nova.tests.unit.objects import test_flavor
from oslo_config import cfg

from hyperv.nova import constants
from hyperv.nova import imagecache
from hyperv.nova import vmutils
from hyperv.tests import fake_instance
from hyperv.tests.unit import test_base

CONF = cfg.CONF


class ImageCacheTestCase(test_base.HyperVBaseTestCase):
    """Unit tests for the Hyper-V ImageCache class."""

    FAKE_BASE_DIR = 'fake/base/dir'
    FAKE_FORMAT = 'fake_format'
    FAKE_IMAGE_REF = 'fake_image_ref'

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
            'MaxInternalSize': self.instance.root_gb + 1}
        (expected_path,
         expected_vhd_path) = self._prepare_get_cached_image(
            rescue_image_id=fake_rescue_image_id,
            image_format=constants.DISK_FORMAT_VHD)
        self.assertRaises(vmutils.HyperVException,
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

    @mock.patch.object(imagecache.ImageCache, '_update_image_timestamp')
    @mock.patch.object(imagecache.ImageCache, '_remove_if_old_image')
    def test_age_and_verify_cached_images(self, mock_rem_if_old_img,
                                          mock_update_img_timestamp):
        fake_images = [mock.sentinel.FAKE_IMG1, mock.sentinel.FAKE_IMG2]
        fake_used_images = [mock.sentinel.FAKE_IMG1]
        self.imagecache.originals = fake_images
        self.imagecache.used_images = fake_used_images

        self.imagecache._age_and_verify_cached_images(
            mock.sentinel.FAKE_CONTEXT,
            mock.sentinel.all_instances,
            mock.sentinel.FAKE_BASE_DIR)

        mock_update_img_timestamp.assert_called_once_with(
            mock.sentinel.FAKE_IMG1)
        mock_rem_if_old_img.assert_called_once_with(
            mock.sentinel.FAKE_IMG2)

    @mock.patch.object(imagecache.ImageCache, '_get_image_backing_files')
    @mock.patch.object(os, 'utime')
    def test_update_image_timestamp(self, mock_utime,
                                    mock_get_img_backing_file):
        fake_backing_files = [mock.sentinel.IMG_PATH1, mock.sentinel.IMG_PATH2,
                              mock.sentinel.IMG_PATH3]
        mock_get_img_backing_file.return_value = fake_backing_files

        self.imagecache._update_image_timestamp(mock.sentinel.IMG)

        mock_get_img_backing_file.assert_called_once_with(mock.sentinel.IMG)
        mock_utime.assert_has_calls([mock.call(mock.sentinel.IMG_PATH1, None),
                                     mock.call(mock.sentinel.IMG_PATH2, None),
                                     mock.call(mock.sentinel.IMG_PATH3, None)])

    def test_get_image_backing_files(self):
        mock_lookup_img_basepath = (
            self.imagecache._pathutils.lookup_image_basepath)
        fake_image_name = 'fake_image_name'
        resized_image1 = '%s_1' % fake_image_name
        resized_image5 = '%s_5' % fake_image_name
        self.imagecache.unexplained_images = [resized_image1,
                                              resized_image5]
        fake_backing_files = [mock.sentinel.BACKING_FILE,
                              mock.sentinel.RESIZED_FILE1,
                              mock.sentinel.RESIZED_FILE2]
        mock_lookup_img_basepath.side_effect = fake_backing_files

        ret = self.imagecache._get_image_backing_files(fake_image_name)

        self.assertEqual(ret, fake_backing_files)

    @mock.patch.object(imagecache.ImageCache, '_get_image_backing_files')
    @mock.patch.object(imagecache.ImageCache, 'remove_old_image')
    def test_remove_if_old_image(self, mock_remove_old_image,
                                  mock_get_img_backing_file):
        self.flags(remove_unused_original_minimum_age_seconds=3000)
        fake_backing_files = [mock.sentinel.BACKING_FILE,
                              mock.sentinel.RESIZED_FILE1,
                              mock.sentinel.RESIZED_FILE2]
        mock_get_img_backing_file.return_value = fake_backing_files
        self.imagecache._pathutils.get_age_of_file.side_effect = [3600, 2400,
                                                                  3600]

        self.imagecache._remove_if_old_image(mock.sentinel.FAKE_IMAGE_FILE)

        calls = [mock.call(mock.sentinel.BACKING_FILE),
                 mock.call(mock.sentinel.RESIZED_FILE1),
                 mock.call(mock.sentinel.RESIZED_FILE2)]
        self.imagecache._pathutils.get_age_of_file.assert_has_calls(calls)
        mock_remove_old_image.assert_has_calls([
            mock.call(mock.sentinel.BACKING_FILE),
            mock.call(mock.sentinel.RESIZED_FILE2)])

    def test_remove_old_images(self):
        self.imagecache.remove_old_image(mock.sentinel.img_file)

        self.imagecache._pathutils.remove.assert_called_once_with(
            mock.sentinel.img_file)

    @mock.patch.object(imagecache.ImageCache, '_list_running_instances')
    @mock.patch.object(imagecache.ImageCache, 'list_base_images')
    @mock.patch.object(imagecache.ImageCache, '_age_and_verify_cached_images')
    def test_update(self, mock_age_and_verify_cached_images,
                    mock_list_base_images, mock_list_running_instances):
        mock_get_base_vhd_dir = self.imagecache._pathutils.get_base_vhd_dir
        mock_get_base_vhd_dir.return_value = mock.sentinel.base_vhd_dir

        fake_used_images = mock.MagicMock()
        fake_used_images.keys.return_value = mock.sentinel.used_images
        fake_running = {'used_images': fake_used_images}
        fake_all_files = {'originals': mock.sentinel.originals,
                          'unexplained_images': mock.sentinel.unexplained
                         }
        mock_list_running_instances.return_value = fake_running
        mock_list_base_images.return_value = fake_all_files

        self.imagecache.update(mock.sentinel.FAKE_CONTEXT,
                               mock.sentinel.all_instances)

        mock_get_base_vhd_dir.assert_called_once_with()
        mock_list_running_instances.assert_called_once_with(
            mock.sentinel.FAKE_CONTEXT, mock.sentinel.all_instances)
        mock_list_base_images.assert_called_once_with(
            mock.sentinel.base_vhd_dir)
        mock_age_and_verify_cached_images.assert_called_once_with(
            mock.sentinel.FAKE_CONTEXT, mock.sentinel.all_instances,
            mock.sentinel.base_vhd_dir)
        self.assertEqual(mock.sentinel.used_images,
                         self.imagecache.used_images)
        self.assertEqual(mock.sentinel.originals, self.imagecache.originals)
        self.assertEqual(mock.sentinel.unexplained,
                         self.imagecache.unexplained_images)

    @mock.patch.object(os, 'listdir')
    def test_list_base_images(self, mock_list_dir):
        fake_file1 = 'fake_file'
        fake_file2 = '5a51f1c5-fbc2-4e26-906c-759d45168ecb'
        fake_file3 = '5a51f1c5-fbc2-4e26-906c-759d45168ecb_5'
        mock_list_dir.return_value = [fake_file1, fake_file2, fake_file3]

        ret = self.imagecache.list_base_images(mock.sentinel.base_vhd_dir)

        self.assertEqual([fake_file1, fake_file3], ret['unexplained_images'])
        self.assertEqual([fake_file2], ret['originals'])
