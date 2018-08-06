# Copyright 2016 Cloudbase Solutions Srl
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

from barbicanclient import client as barbican_client
from keystoneauth1 import session
from nova import exception
from os_win._i18n import _


class PDK(object):

    def create_pdk(self, context, instance, image_meta, pdk_filepath):
        """Generates a pdk file using the barbican container referenced by
        the image metadata or instance metadata. A pdk file is a shielding
        data file which contains a RDP certificate, unattended file,
        volume signature catalogs and guardian metadata.
        """

        with open(pdk_filepath, 'wb') as pdk_file_handle:
            pdk_reference = self._get_pdk_reference(instance, image_meta)
            pdk_container = self._get_pdk_container(context, instance,
                                                    pdk_reference)
            pdk_data = self._get_pdk_data(pdk_container)
            pdk_file_handle.write(pdk_data)

    def _get_pdk_reference(self, instance, image_meta):
        image_pdk_ref = image_meta['properties'].get('img_pdk_reference')
        boot_metadata_pdk_ref = instance.metadata.get('img_pdk_reference')

        if not (image_pdk_ref or boot_metadata_pdk_ref):
            reason = _('A reference to a barbican container containing the '
                       'pdk file must be passed as an image property. This '
                       'is required in order to enable VTPM')
            raise exception.InstanceUnacceptable(instance_id=instance.uuid,
                                                 reason=reason)
        return boot_metadata_pdk_ref or image_pdk_ref

    def _get_pdk_container(self, context, instance, pdk_reference):
        """Retrieves the barbican container containing the pdk file.
        """

        auth = context.get_auth_plugin()
        sess = session.Session(auth=auth)
        brb_client = barbican_client.Client(session=sess)

        try:
            pdk_container = brb_client.containers.get(pdk_reference)
        except Exception as e:
            err_msg = _("Retrieving barbican container with reference "
                        "%(pdk_reference)s failed with error: %(error)s") % {
                        'pdk_reference': pdk_reference,
                        'error': e}
            raise exception.InvalidMetadata(instance_id=instance.uuid,
                                            reason=err_msg)
        return pdk_container

    def _get_pdk_data(self, pdk_container):
        """Return the data from all barbican container's secrets.
        """

        no_of_secrets = len(pdk_container.secrets)
        data = bytes()
        for index in range(no_of_secrets):
            current_secret = pdk_container.secrets[str(index + 1)]
            retrived_secret_data = current_secret.payload
            data += retrived_secret_data
        return data
