# Copyright 2018 Cloudbase Solutions Srl
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
from nova import context
from nova import exception
from nova import objects
from nova.tests.unit import fake_requests
from oslo_serialization import jsonutils

from compute_hyperv.nova.utils import placement as placement
from compute_hyperv.tests import fake_instance
from compute_hyperv.tests.unit import test_base


@ddt.ddt
class PlacementUtilsTestCase(test_base.HyperVBaseTestCase):
    _autospec_classes = [
        placement.report.SchedulerReportClient
    ]

    _FAKE_PROVIDER = 'fdb5c6d0-e0e9-4411-b952-fb05d6133718'
    _FAKE_RESOURCES = {'VCPU': 1, 'MEMORY_MB': 512, 'DISK_GB': 1}
    _FAKE_ALLOCATIONS = {
        _FAKE_PROVIDER: {'resources': _FAKE_RESOURCES}
    }

    def setUp(self):
        super(PlacementUtilsTestCase, self).setUp()
        self.context = context.get_admin_context()
        self.instance = fake_instance.fake_instance_obj(self.context)

        self.placement = placement.PlacementUtils()
        self.client = self.placement.reportclient

    @mock.patch.object(objects.ComputeNode, 'get_by_host_and_nodename')
    @mock.patch.object(placement.PlacementUtils, 'move_allocations')
    def test_move_compute_node_allocations(self, mock_move_alloc,
                                           mock_get_comp_node):
        mock_get_comp_node.side_effect = [
            mock.Mock(uuid=uuid) for uuid in [mock.sentinel.old_host_uuid,
                                              mock.sentinel.new_host_uuid]]

        self.placement.move_compute_node_allocations(
            self.context, self.instance, mock.sentinel.old_host,
            mock.sentinel.new_host,
            merge_existing=mock.sentinel.merge_existing)

        mock_move_alloc.assert_called_once_with(
            self.context, self.instance.uuid,
            mock.sentinel.old_host_uuid,
            mock.sentinel.new_host_uuid,
            merge_existing=mock.sentinel.merge_existing)
        mock_get_comp_node.assert_has_calls(
            mock.call(self.context, host, host) for host in
            [mock.sentinel.old_host, mock.sentinel.new_host])

    @ddt.data({},  # provider did not change
              {'old_rp': 'fake_rp'})  # provider not included in allocations
    @ddt.unpack
    @mock.patch.object(placement.PlacementUtils, '_get_allocs_for_consumer')
    @mock.patch.object(placement.PlacementUtils, '_put_allocs')
    def test_move_allocations_noop(self, mock_put, mock_get_allocs,
                                   old_rp=_FAKE_PROVIDER,
                                   new_rp=_FAKE_PROVIDER):
        mock_get_allocs.return_value = {'allocations': self._FAKE_ALLOCATIONS}

        self.placement.move_allocations(
            self.context, mock.sentinel.consumer, old_rp, new_rp)

        mock_get_allocs.assert_called_once_with(
            self.context, mock.sentinel.consumer,
            version=placement.CONSUMER_GENERATION_VERSION)
        mock_put.assert_not_called()

    @ddt.data(True, False)
    @mock.patch.object(placement.PlacementUtils, '_get_allocs_for_consumer')
    @mock.patch.object(placement.PlacementUtils, '_put_allocs')
    def test_merge_allocations(self, merge_existing,
                               mock_put, mock_get_allocs):
        old_rp = self._FAKE_PROVIDER
        new_rp = 'new_rp'
        allocs = self._FAKE_ALLOCATIONS.copy()
        allocs[new_rp] = {'resources': self._FAKE_RESOURCES.copy()}

        mock_get_allocs.return_value = {'allocations': allocs}

        if merge_existing:
            exp_resources = {'VCPU': 2, 'MEMORY_MB': 1024, 'DISK_GB': 2}
        else:
            exp_resources = self._FAKE_RESOURCES
        exp_allocs = {new_rp: {'resources': exp_resources}}

        self.placement.move_allocations(
            self.context, mock.sentinel.consumer, old_rp, new_rp,
            merge_existing=merge_existing)

        mock_put.assert_called_once_with(
            self.context, mock.sentinel.consumer,
            {'allocations': exp_allocs},
            version=placement.CONSUMER_GENERATION_VERSION)

    @ddt.data({},  # no errors
              {'status_code': 409,
               'errors': [{'code': 'placement.concurrent_update'}],
               'expected_exc': placement.report.Retry},
              {'status_code': 500,
               'expected_exc': exception.AllocationUpdateFailed})
    @ddt.unpack
    def test_put_allocs(self, status_code=204, expected_exc=None, errors=None):
        response = fake_requests.FakeResponse(
            status_code,
            content=jsonutils.dumps({'errors': errors}))
        self.client.put.return_value = response

        args = (self.context, mock.sentinel.consumer, mock.sentinel.allocs,
                mock.sentinel.version)
        if expected_exc:
            self.assertRaises(expected_exc, self.placement._put_allocs, *args)
        else:
            self.placement._put_allocs(*args)

        self.client.put.assert_called_once_with(
            '/allocations/%s' % mock.sentinel.consumer,
            mock.sentinel.allocs,
            version=mock.sentinel.version,
            global_request_id=self.context.global_id)

    def test_get_allocs(self):
        ret_val = self.placement._get_allocs_for_consumer(
            self.context, mock.sentinel.consumer, mock.sentinel.version)
        exp_val = self.client.get.return_value.json.return_value
        self.assertEqual(exp_val, ret_val)

        self.client.get.assert_called_once_with(
            '/allocations/%s' % mock.sentinel.consumer,
            version=mock.sentinel.version,
            global_request_id=self.context.global_id)

    def test_get_allocs_missing(self):
        self.client.get.return_value = fake_requests.FakeResponse(500)
        self.assertRaises(
            exception.ConsumerAllocationRetrievalFailed,
            self.placement._get_allocs_for_consumer,
            self.context, mock.sentinel.consumer, mock.sentinel.version)

    def test_merge_resources(self):
        resources = {
            'VCPU': 1, 'MEMORY_MB': 1024,
        }
        new_resources = {
            'VCPU': 2, 'MEMORY_MB': 2048, 'CUSTOM_FOO': 1,
        }
        doubled = {
            'VCPU': 3, 'MEMORY_MB': 3072, 'CUSTOM_FOO': 1,
        }
        saved_orig = dict(resources)
        self.placement.merge_resources(resources, new_resources)
        # Check to see that we've doubled our resources
        self.assertEqual(doubled, resources)
        # and then removed those doubled resources
        self.placement.merge_resources(resources, saved_orig, -1)
        self.assertEqual(new_resources, resources)

    def test_merge_resources_zero(self):
        # Test 0 value resources are ignored.
        resources = {
            'VCPU': 1, 'MEMORY_MB': 1024,
        }
        new_resources = {
            'VCPU': 2, 'MEMORY_MB': 2048, 'DISK_GB': 0,
        }
        # The result should not include the zero valued resource.
        doubled = {
            'VCPU': 3, 'MEMORY_MB': 3072,
        }
        self.placement.merge_resources(resources, new_resources)
        self.assertEqual(doubled, resources)

    def test_merge_resources_original_zeroes(self):
        # Confirm that merging that result in a zero in the original
        # excludes the zeroed resource class.
        resources = {
            'VCPU': 3, 'MEMORY_MB': 1023, 'DISK_GB': 1,
        }
        new_resources = {
            'VCPU': 1, 'MEMORY_MB': 512, 'DISK_GB': 1,
        }
        merged = {
            'VCPU': 2, 'MEMORY_MB': 511,
        }
        self.placement.merge_resources(resources, new_resources, -1)
        self.assertEqual(merged, resources)
