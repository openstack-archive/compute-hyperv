# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
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

"""Base classes for our unit tests.

Allows overriding of flags for use of fakes, and some black magic for
inline callbacks.

"""

import os

import eventlet
eventlet.monkey_patch(os=False)

import fixtures
import inspect
import mock
from nova.tests import fixtures as nova_fixtures
from nova.tests.unit import conf_fixture
from nova.tests.unit import policy_fixture
from oslo_log.fixture import logging_error as log_fixture
from oslo_log import log as logging
from oslotest import base
from oslotest import mock_fixture
import six

import compute_hyperv.nova.conf

CONF = compute_hyperv.nova.conf.CONF

logging.register_options(CONF)
CONF.set_override('use_stderr', False)

_TRUE_VALUES = ('True', 'true', '1', 'yes')


def _patch_mock_to_raise_for_invalid_assert_calls():
    def raise_for_invalid_assert_calls(wrapped):
        def wrapper(_self, name):
            valid_asserts = [
                'assert_called_with',
                'assert_called_once_with',
                'assert_has_calls',
                'assert_any_calls']

            if name.startswith('assert') and name not in valid_asserts:
                raise AttributeError('%s is not a valid mock assert method'
                                     % name)

            return wrapped(_self, name)
        return wrapper
    mock.Mock.__getattr__ = raise_for_invalid_assert_calls(
        mock.Mock.__getattr__)

# NOTE(gibi): needs to be called only once at import time
# to patch the mock lib
_patch_mock_to_raise_for_invalid_assert_calls()

# NOTE(claudiub): this needs to be called before any mock.patch calls are
# being done, and especially before any other test classes load. This fixes
# the mock.patch autospec issue:
# https://github.com/testing-cabal/mock/issues/396
mock_fixture.patch_mock_module()


class NoDBTestCase(base.BaseTestCase):
    """Test case base class for all unit tests.

    Due to the slowness of DB access, please consider deriving from
    `NoDBTestCase` first.
    """

    TIMEOUT_SCALING_FACTOR = 1
    MOCK_TOOZ = True

    def setUp(self):
        """Run before each test method to initialize test environment."""
        super(NoDBTestCase, self).setUp()
        self.useFixture(mock_fixture.MockAutospecFixture())
        self.useFixture(nova_fixtures.Timeout(
            os.environ.get('OS_TEST_TIMEOUT', 0),
            self.TIMEOUT_SCALING_FACTOR))

        self.useFixture(fixtures.NestedTempfile())
        self.useFixture(fixtures.TempHomeDir())
        self.useFixture(log_fixture.get_logging_handle_error_fixture())

        self.useFixture(nova_fixtures.OutputStreamCapture())
        self.useFixture(nova_fixtures.StandardLogging())
        self.useFixture(conf_fixture.ConfFixture(CONF))

        # NOTE(blk-u): WarningsFixture must be after the Database fixture
        # because sqlalchemy-migrate messes with the warnings filters.
        self.useFixture(nova_fixtures.WarningsFixture())

        self.addCleanup(self._clear_attrs)
        self.policy = self.useFixture(policy_fixture.PolicyFixture())

        self.useFixture(nova_fixtures.PoisonFunctions())

        if self.MOCK_TOOZ:
            self.patch('compute_hyperv.nova.coordination.Coordinator.start')
            self.patch('compute_hyperv.nova.coordination.Coordinator.stop')
            self.patch('compute_hyperv.nova.coordination.Coordinator.get_lock')

    def _clear_attrs(self):
        # Delete attributes that don't start with _ so they don't pin
        # memory around unnecessarily for the duration of the test
        # suite
        for key in [k for k in six.iterkeys(self.__dict__) if k[0] != '_']:
            del self.__dict__[key]

    def flags(self, **kw):
        """Override flag variables for a test."""
        group = kw.pop('group', None)
        for k, v in six.iteritems(kw):
            CONF.set_override(k, v, group)

    def patch(self, path, *args, **kwargs):
        patcher = mock.patch(path, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def assertPublicAPISignatures(self, baseinst, inst):
        def get_public_apis(inst):
            methods = {}
            for (name, value) in inspect.getmembers(inst, inspect.ismethod):
                if name.startswith("_"):
                    continue
                methods[name] = value
            return methods

        baseclass = baseinst.__class__.__name__
        basemethods = get_public_apis(baseinst)
        implmethods = get_public_apis(inst)

        extranames = []
        for name in sorted(implmethods.keys()):
            if name not in basemethods:
                extranames.append(name)

        self.assertEqual([], extranames,
                         "public APIs not listed in base class %s" %
                         baseclass)

        for name in sorted(implmethods.keys()):
            baseargs = inspect.getargspec(basemethods[name])
            implargs = inspect.getargspec(implmethods[name])

            self.assertEqual(baseargs, implargs,
                             "%s args don't match base class %s" %
                             (name, baseclass))
