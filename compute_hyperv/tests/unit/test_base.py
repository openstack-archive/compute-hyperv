# Copyright 2014 Cloudbase Solutions Srl
#
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

import eventlet.hubs as hubs
import mock
import monotonic
from os_win import utilsfactory

from compute_hyperv.tests import test


class HyperVBaseTestCase(test.NoDBTestCase):
    _autospec_classes = []

    def setUp(self):
        super(HyperVBaseTestCase, self).setUp()

        utilsfactory_patcher = mock.patch.object(utilsfactory, '_get_class')
        utilsfactory_patcher.start()

        self._patch_autospec_classes()
        self.addCleanup(mock.patch.stopall)

    def _patch_autospec_classes(self):
        for class_type in self._autospec_classes:
            mocked_class = mock.MagicMock(autospec=class_type)
            patcher = mock.patch(
                '.'.join([class_type.__module__, class_type.__name__]),
                mocked_class)
            patcher.start()


class MonotonicTestCase(test.NoDBTestCase):
    def test_monotonic(self):
        import nova  # noqa

        hub = hubs.get_hub()
        self.assertEqual(monotonic.monotonic, hub.clock)
