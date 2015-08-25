# Copyright 2014 Cloudbase Solutions SRL
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
Unit tests for the Hyper-V utils factory.
"""

import mock

from hyperv.nova import utilsfactory
from hyperv.nova import vmutils
from hyperv.nova import volumeutilsv2
from hyperv.tests import test


class TestHyperVUtilsFactory(test.NoDBTestCase):

    def test_get_class(self):
        expected_instance = volumeutilsv2.VolumeUtilsV2()
        utilsfactory.utils = mock.MagicMock()
        utilsfactory.utils.get_windows_version.return_value = '6.2'
        instance = utilsfactory._get_class('volumeutils')
        self.assertEqual(type(expected_instance), type(instance))

    def test_get_class_not_found(self):
        utilsfactory.utils = mock.MagicMock()
        utilsfactory.utils.get_windows_version.return_value = '5.2'
        self.assertRaises(vmutils.HyperVException, utilsfactory._get_class,
                          'hostutils')
