#    Copyright 2013 IBM Corp.
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

import datetime
import uuid

from nova import objects
from nova.objects import fields
import six


def fake_db_instance(**updates):
    flavorinfo = None
    db_instance = {
        'id': 1,
        'deleted': False,
        'uuid': str(uuid.uuid4()),
        'user_id': 'fake-user',
        'project_id': 'fake-project',
        'host': 'fake-host',
        'created_at': datetime.datetime(1955, 11, 5),
        'pci_devices': [],
        'security_groups': [],
        'metadata': {},
        'system_metadata': {},
        'root_gb': 0,
        'ephemeral_gb': 0,
        'extra': {'pci_requests': None,
                  'flavor': flavorinfo,
                  'numa_topology': None,
                  'vcpu_model': None,
                  'trusted_certs': None,
                 },
        'tags': [],
        'services': []
        }

    for name, field in six.iteritems(objects.Instance.fields):
        if name in db_instance:
            continue
        if field.nullable:
            db_instance[name] = None
        elif field.default != fields.UnspecifiedDefault:
            db_instance[name] = field.default
        elif name in ['flavor', 'ec2_ids', 'keypairs']:
            pass
        else:
            raise Exception('fake_db_instance needs help with %s' % name)

    if updates:
        db_instance.update(updates)
    return db_instance


def fake_instance_obj(context='fake-context', **updates):
    expected_attrs = updates.pop('expected_attrs', None)
    flavor = objects.Flavor(id=1, name='flavor1',
                            memory_mb=256, vcpus=1,
                            root_gb=1, ephemeral_gb=1,
                            flavorid='1',
                            swap=0, rxtx_factor=1.0,
                            vcpu_weight=1,
                            disabled=False,
                            is_public=True,
                            extra_specs={},
                            projects=[])
    flavor.obj_reset_changes()
    inst = objects.Instance._from_db_object(context,
               objects.Instance(), fake_db_instance(**updates),
               expected_attrs=expected_attrs)
    inst.flavor = flavor
    inst.old_flavor = flavor.obj_clone()
    inst.obj_reset_changes()
    return inst
