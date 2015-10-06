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
"""
Image caching and management.
"""
import os
import re

from nova import utils
from nova.virt import imagecache
from nova.virt import images
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import excutils
from oslo_utils import units
from oslo_utils import uuidutils

from hyperv.i18n import _
from hyperv.nova import utilsfactory
from hyperv.nova import vmutils

LOG = logging.getLogger(__name__)

CONF = cfg.CONF
CONF.import_opt('use_cow_images', 'nova.virt.driver')
CONF.import_opt('instances_path', 'nova.compute.manager')
CONF.import_opt('remove_unused_original_minimum_age_seconds',
                'nova.virt.imagecache')


def synchronize_with_path(f):
    def wrapper(self, image_path):

        @utils.synchronized(image_path)
        def inner():
            return f(self, image_path)
        return inner()

    return wrapper


class ImageCache(imagecache.ImageCacheManager):
    def __init__(self):
        super(ImageCache, self).__init__()
        self._pathutils = utilsfactory.get_pathutils()
        self._vhdutils = utilsfactory.get_vhdutils()
        self.used_images = []
        self.unexplained_images = []
        self.originals = []

    def _get_root_vhd_size_gb(self, instance):
        if instance.old_flavor:
            return instance.old_flavor.root_gb
        else:
            return instance.root_gb

    def _resize_and_cache_vhd(self, instance, vhd_path):
        vhd_info = self._vhdutils.get_vhd_info(vhd_path)
        vhd_size = vhd_info['MaxInternalSize']

        root_vhd_size_gb = self._get_root_vhd_size_gb(instance)
        root_vhd_size = root_vhd_size_gb * units.Gi

        root_vhd_internal_size = (
                self._vhdutils.get_internal_vhd_size_by_file_size(
                    vhd_path, root_vhd_size))

        if root_vhd_internal_size < vhd_size:
            raise vmutils.HyperVException(
                _("Cannot resize the image to a size smaller than the VHD "
                  "max. internal size: %(vhd_size)s. Requested disk size: "
                  "%(root_vhd_size)s") %
                {'vhd_size': vhd_size, 'root_vhd_size': root_vhd_size}
            )
        if root_vhd_internal_size > vhd_size:
            path_parts = os.path.splitext(vhd_path)
            resized_vhd_path = '%s_%s%s' % (path_parts[0],
                                            root_vhd_size_gb,
                                            path_parts[1])

            @utils.synchronized(resized_vhd_path)
            def copy_and_resize_vhd():
                if not self._pathutils.exists(resized_vhd_path):
                    try:
                        LOG.debug("Copying VHD %(vhd_path)s to "
                                  "%(resized_vhd_path)s",
                                  {'vhd_path': vhd_path,
                                   'resized_vhd_path': resized_vhd_path})
                        self._pathutils.copyfile(vhd_path, resized_vhd_path)
                        LOG.debug("Resizing VHD %(resized_vhd_path)s to new "
                                  "size %(root_vhd_size)s",
                                  {'resized_vhd_path': resized_vhd_path,
                                   'root_vhd_size': root_vhd_size})
                        self._vhdutils.resize_vhd(resized_vhd_path,
                                                  root_vhd_internal_size,
                                                  is_file_max_size=False)
                    except Exception:
                        with excutils.save_and_reraise_exception():
                            if self._pathutils.exists(resized_vhd_path):
                                self._pathutils.remove(resized_vhd_path)

            copy_and_resize_vhd()
            return resized_vhd_path

    def get_cached_image(self, context, instance, rescue_image_id=None):
        image_id = rescue_image_id or instance.image_ref
        image_type = instance.system_metadata['image_disk_format']

        base_image_dir = self._pathutils.get_base_vhd_dir()
        base_image_path = os.path.join(base_image_dir, image_id)

        @utils.synchronized(base_image_path)
        def fetch_image_if_not_existing():
            image_path = None
            for format_ext in ['vhd', 'vhdx', 'iso']:
                test_path = base_image_path + '.' + format_ext
                if self._pathutils.exists(test_path):
                    image_path = test_path
                    self._update_image_timestamp(image_id)
                    break

            if not image_path:
                try:
                    images.fetch(context, image_id, base_image_path,
                                 instance.user_id,
                                 instance.project_id)
                    if image_type == 'iso':
                        format_ext = 'iso'
                    else:
                        format_ext = self._vhdutils.get_vhd_format(
                            base_image_path)
                    image_path = base_image_path + '.' + format_ext.lower()
                    self._pathutils.rename(base_image_path, image_path)
                except Exception:
                    with excutils.save_and_reraise_exception():
                        if self._pathutils.exists(base_image_path):
                            self._pathutils.remove(base_image_path)

            return image_path

        image_path = fetch_image_if_not_existing()

        # Note: rescue images are not resized.
        is_vhd = image_path.split('.')[-1].lower() == 'vhd'
        if (CONF.use_cow_images and is_vhd and not rescue_image_id):
            # Resize the base VHD image as it's not possible to resize a
            # differencing VHD. This does not apply to VHDX images.
            resized_image_path = self._resize_and_cache_vhd(instance,
                                                            image_path)
            if resized_image_path:
                return resized_image_path

        if rescue_image_id:
            self._verify_rescue_image(instance, rescue_image_id, image_path)

        return image_path

    def _verify_rescue_image(self, instance, rescue_image_id,
                             rescue_image_path):
        rescue_image_info = self._vhdutils.get_vhd_info(rescue_image_path)
        rescue_image_size = rescue_image_info['MaxInternalSize']
        flavor_disk_size = instance.root_gb * units.Gi

        if rescue_image_size > flavor_disk_size:
            err_msg = _('Using a rescue image bigger than the instance '
                        'flavor disk size is not allowed. '
                        'Rescue image size: %(rescue_image_size)s. '
                        'Flavor disk size:%(flavor_disk_size)s. '
                        'Rescue image id %(rescue_image_id)s.')
            raise vmutils.HyperVException(err_msg %
                {'rescue_image_size': rescue_image_size,
                 'flavor_disk_size': flavor_disk_size,
                 'rescue_image_id': rescue_image_id})

    def get_image_details(self, context, instance):
        image_id = instance.image_ref
        return images.get_info(context, image_id)

    def _age_and_verify_cached_images(self, context, all_instances, base_dir):
        for img in self.originals:
            if img in self.used_images:
                # change the timestamp on the image so as to reflect the last
                # time it was used
                self._update_image_timestamp(img)
            else:
                self._remove_if_old_image(img)

    def _update_image_timestamp(self, image_name):
        backing_files = self._get_image_backing_files(image_name)
        for img in backing_files:
            os.utime(img, None)

    def _get_image_backing_files(self, image_name):
        backing_files = [self._pathutils.lookup_image_basepath(image_name)]
        resize_re = re.compile('%s_[0-9]+$' % image_name)
        for img in self.unexplained_images:
            match = resize_re.match(img)
            if match:
                backing_files.append(
                    self._pathutils.lookup_image_basepath(img))

        return backing_files

    def _remove_if_old_image(self, image_name):
        max_age_seconds = CONF.remove_unused_original_minimum_age_seconds
        backing_files = self._get_image_backing_files(image_name)
        for img in backing_files:
            age_seconds = self._pathutils.get_age_of_file(img)
            if age_seconds > max_age_seconds:
                self.remove_old_image(img)

    @synchronize_with_path
    def remove_old_image(self, img):
        self._pathutils.remove(img)

    def update(self, context, all_instances):
        base_vhd_dir = self._pathutils.get_base_vhd_dir()

        running = self._list_running_instances(context, all_instances)
        self.used_images = running['used_images'].keys()
        all_files = self.list_base_images(base_vhd_dir)
        self.originals = all_files['originals']
        self.unexplained_images = all_files['unexplained_images']

        self._age_and_verify_cached_images(context, all_instances,
                                           base_vhd_dir)

    def list_base_images(self, base_dir):
        unexplained_images = []
        originals = []

        for entry in os.listdir(base_dir):
            # remove file extension
            file_name = os.path.splitext(entry)[0]
            if uuidutils.is_uuid_like(file_name):
                originals.append(file_name)
            else:
                unexplained_images.append(file_name)

        return {'unexplained_images': unexplained_images,
                'originals': originals}
