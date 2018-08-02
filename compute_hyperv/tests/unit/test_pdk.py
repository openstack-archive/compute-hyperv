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

import mock
from nova import exception

from compute_hyperv.nova import pdk
from compute_hyperv.tests.unit import test_base
from six.moves import builtins


class PDKTestCase(test_base.HyperVBaseTestCase):

    _FAKE_PDK_FILE_PATH = 'C:\\path\\to\\fakepdk.pdk'

    def setUp(self):
        super(PDKTestCase, self).setUp()
        self._pdk = pdk.PDK()

    @mock.patch.object(builtins, 'open')
    @mock.patch.object(pdk.PDK, '_get_pdk_data')
    @mock.patch.object(pdk.PDK, '_get_pdk_container')
    @mock.patch.object(pdk.PDK, '_get_pdk_reference')
    def test_create_pdk(self, mock_get_pdk_reference, mock_get_pdk_container,
                        mock_get_pdk_data, mock_open):
        mock_instance = mock.MagicMock()
        pdk_file_handle = mock_open.return_value.__enter__.return_value

        pdk_reference = mock_get_pdk_reference.return_value
        pdk_container = mock_get_pdk_container.return_value

        self._pdk.create_pdk(mock.sentinel.context,
                             mock_instance,
                             mock.sentinel.image_meta,
                             self._FAKE_PDK_FILE_PATH)
        mock_get_pdk_reference.assert_called_once_with(
            mock_instance, mock.sentinel.image_meta)
        mock_get_pdk_container.assert_called_once_with(mock.sentinel.context,
                                                       mock_instance,
                                                       pdk_reference)
        mock_get_pdk_data.assert_called_once_with(pdk_container)
        pdk_file_handle.write.assert_called_once_with(
            mock_get_pdk_data.return_value)

    def _test_get_pdk_reference(self, pdk_reference=None,
                                image_meta_pdk_ref=None):
        mock_instance = mock.MagicMock(
            metadata={'img_pdk_reference': image_meta_pdk_ref})
        image_meta = {
            'properties': {'img_pdk_reference': pdk_reference}}

        expected_result = image_meta_pdk_ref or pdk_reference
        result = self._pdk._get_pdk_reference(mock_instance,
                                              image_meta)
        self.assertEqual(expected_result, result)

    def test_get_pdk_boot_reference(self):
        self._test_get_pdk_reference(
            image_meta_pdk_ref=mock.sentinel.image_meta_pdk_ref)

    def test_get_pdk_image_reference(self):
        self._test_get_pdk_reference(pdk_reference=mock.sentinel.pdk_reference)

    def test_get_pdk_no_reference(self):
        image_meta = {'properties': {}}
        mock_instance = mock.MagicMock(metadata={})

        self.assertRaises(exception.InstanceUnacceptable,
                          self._pdk._get_pdk_reference,
                          mock_instance, image_meta)

    @mock.patch('barbicanclient.client.Client')
    @mock.patch('keystoneauth1.session.Session')
    def test_get_pdk_container(self, mock_session, mock_barbican_client):
        instance = mock.MagicMock()
        context = mock.MagicMock()
        auth = context.get_auth_plugin.return_value
        sess = mock_session.return_value
        barbican_client = mock_barbican_client.return_value
        barbican_client.containers.get.return_value = (
            mock.sentinel.pdk_container)

        result = self._pdk._get_pdk_container(context, instance,
                                              mock.sentinel.pdk_reference)

        self.assertEqual(mock.sentinel.pdk_container, result)
        mock_session.assert_called_once_with(auth=auth)
        mock_barbican_client.assert_called_once_with(session=sess)

    @mock.patch('barbicanclient.client.Client')
    @mock.patch('keystoneauth1.session.Session')
    def test_get_pdk_container_exception(self, mock_session,
                                         mock_barbican_client):
        instance = mock.MagicMock()
        context = mock.MagicMock()
        auth = context.get_auth_plugin.return_value
        sess = mock_session.return_value

        barbican_client = mock_barbican_client.return_value
        barbican_client.containers.get.side_effect = [
            exception.InvalidMetadata]

        self.assertRaises(exception.InvalidMetadata,
                          self._pdk._get_pdk_container,
                          context,
                          instance,
                          mock.sentinel.pdk_reference)
        mock_session.assert_called_once_with(auth=auth)
        mock_barbican_client.assert_called_once_with(session=sess)

    def test_get_pdk_data(self):
        pdk_container = mock.MagicMock()
        pdk_container.secrets = {'1': mock.MagicMock(payload=b'fake_secret1'),
                                 '2': mock.MagicMock(payload=b'fake_secret2')}

        response = self._pdk._get_pdk_data(pdk_container)
        expected_result = b'fake_secret1fake_secret2'
        self.assertEqual(expected_result, response)
