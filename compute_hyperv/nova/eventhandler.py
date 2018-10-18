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

from nova import utils
from nova.virt import event as virtevent
from os_win import constants
from os_win import utilsfactory
from oslo_log import log as logging

import compute_hyperv.nova.conf
from compute_hyperv.nova import vmops

LOG = logging.getLogger(__name__)

CONF = compute_hyperv.nova.conf.CONF


class HyperVLifecycleEvent(virtevent.LifecycleEvent):
    def __init__(self, uuid, name, transition, timestamp=None):
        super(HyperVLifecycleEvent, self).__init__(uuid, transition, timestamp)

        self.name = name


class InstanceEventHandler(object):
    _TRANSITION_MAP = {
        constants.HYPERV_VM_STATE_ENABLED: virtevent.EVENT_LIFECYCLE_STARTED,
        constants.HYPERV_VM_STATE_DISABLED: virtevent.EVENT_LIFECYCLE_STOPPED,
        constants.HYPERV_VM_STATE_PAUSED: virtevent.EVENT_LIFECYCLE_PAUSED,
        constants.HYPERV_VM_STATE_SUSPENDED:
            virtevent.EVENT_LIFECYCLE_SUSPENDED
    }

    def __init__(self):
        self._vmutils = utilsfactory.get_vmutils()
        self._listener = self._vmutils.get_vm_power_state_change_listener(
            timeframe=CONF.hyperv.power_state_check_timeframe,
            event_timeout=CONF.hyperv.power_state_event_polling_interval,
            filtered_states=list(self._TRANSITION_MAP.keys()),
            get_handler=True)

        self._vmops = vmops.VMOps()

        self._callbacks = []

    def add_callback(self, callback):
        self._callbacks.append(callback)

    def start_listener(self):
        utils.spawn_n(self._listener, self._handle_event)

    def _handle_event(self, instance_name, instance_power_state):
        # Instance uuid set by Nova. If this is missing, we assume that
        # the instance was not created by Nova and ignore the event.
        instance_uuid = self._vmops.get_instance_uuid(instance_name)
        if instance_uuid:
            self._emit_event(instance_name,
                             instance_uuid,
                             instance_power_state)
        else:
            LOG.debug("Instance uuid could not be retrieved for instance "
                      "%(instance_name)s. Instance state change event will "
                      "be ignored. Current power state: %(power_state)s.",
                      dict(instance_name=instance_name,
                           power_state=instance_power_state))

    def _emit_event(self, instance_name, instance_uuid, instance_state):
        virt_event = self._get_virt_event(instance_uuid,
                                          instance_name,
                                          instance_state)

        for callback in self._callbacks:
            utils.spawn_n(callback, virt_event)

    def _get_virt_event(self, instance_uuid, instance_name, instance_state):
        transition = self._TRANSITION_MAP[instance_state]
        return HyperVLifecycleEvent(
            uuid=instance_uuid,
            name=instance_name,
            transition=transition)
