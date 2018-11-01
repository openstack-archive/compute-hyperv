# Copyright 2015 Cloudbase Solutions Srl
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

import ddt
import mock
from nova import utils
from os_win import constants

from compute_hyperv.nova import eventhandler
from compute_hyperv.nova import vmops
from compute_hyperv.tests.unit import test_base


@ddt.ddt
class EventHandlerTestCase(test_base.HyperVBaseTestCase):
    _FAKE_POLLING_INTERVAL = 3
    _FAKE_EVENT_CHECK_TIMEFRAME = 15

    def setUp(self):
        super(EventHandlerTestCase, self).setUp()

        self.flags(
            power_state_check_timeframe=self._FAKE_EVENT_CHECK_TIMEFRAME,
            group='hyperv')
        self.flags(
            power_state_event_polling_interval=self._FAKE_POLLING_INTERVAL,
            group='hyperv')

        self._event_handler = eventhandler.InstanceEventHandler()

    @ddt.data(True, False)
    @mock.patch.object(vmops.VMOps, 'get_instance_uuid')
    @mock.patch.object(eventhandler.InstanceEventHandler, '_emit_event')
    def test_handle_event(self, missing_uuid, mock_emit_event, mock_get_uuid):
        mock_get_uuid.return_value = (
            mock.sentinel.instance_uuid if not missing_uuid else None)
        self._event_handler._vmutils.get_vm_power_state.return_value = (
            mock.sentinel.power_state)

        self._event_handler._handle_event(mock.sentinel.instance_name,
                                          mock.sentinel.power_state)

        if not missing_uuid:
            mock_emit_event.assert_called_once_with(
                mock.sentinel.instance_name,
                mock.sentinel.instance_uuid,
                mock.sentinel.power_state)
        else:
            self.assertFalse(mock_emit_event.called)

    @mock.patch.object(eventhandler.InstanceEventHandler, '_get_virt_event')
    @mock.patch.object(utils, 'spawn_n',
                       lambda f, *args, **kwargs: f(*args, **kwargs))
    def test_emit_event(self, mock_get_event):
        state = constants.HYPERV_VM_STATE_ENABLED
        callbacks = [mock.Mock(), mock.Mock()]

        for cbk in callbacks:
            self._event_handler.add_callback(cbk)

        self._event_handler._emit_event(mock.sentinel.instance_name,
                                        mock.sentinel.instance_uuid,
                                        state)

        for cbk in callbacks:
            cbk.assert_called_once_with(mock_get_event.return_value)

    def test_get_virt_event(self):
        instance_state = constants.HYPERV_VM_STATE_ENABLED
        expected_transition = self._event_handler._TRANSITION_MAP[
            instance_state]

        virt_event = self._event_handler._get_virt_event(
            mock.sentinel.instance_uuid,
            mock.sentinel.instance_name,
            instance_state)

        self.assertEqual(mock.sentinel.instance_name, virt_event.name)
        self.assertEqual(mock.sentinel.instance_uuid, virt_event.uuid)
        self.assertEqual(expected_transition, virt_event.transition)
