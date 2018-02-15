.. _next-steps:

Next steps
~~~~~~~~~~

Your OpenStack environment now includes the ``nova-compute`` service
installed and configured with the compute_hyperv driver.

If the OpenStack services are Running on the Hyper-V compute node, make sure
that they're reporting to the OpenStack controller and that they're alive by
running the following:

.. code-block:: bash

    neutron agent-list
    nova service-list

The output should contain the Hyper-V host's ``nova-compute`` service and
Neutron L2 agent (either a Neutron Hyper-V Agent, or a Neutron OVS Agent) as
alive / running.

Starting with Ocata, Nova cells became mandatory. Make sure that the newly
added Hyper-V compute node is mapped into a Nova cell, otherwise Nova will not
build any instances on it. In small deployments, two cells are enough:
``cell0`` and ``cell1``. ``cell0`` is a special cell, instances that are never
scheduled are relegated to the ``cell0`` database, which is effectively a
graveyard of instances that failed to start. All successful/running instances
are stored in ``cell1``.

You can check your Nova cells by running this on the Nova Controller:

.. code-block:: bash

    nova-manage cell_v2 list_cells

You should at least have 2 cells listed (``cell0`` and ``cell1``). If they're
not, or only ``cell0`` exists, you can simply run:

.. code-block:: bash

    nova-manage cell_v2 simple_cell_setup

If you have the 2 cells, in order to map the newly created compute nodes to
``cell1``, run:

.. code-block:: bash

    nova-manage cell_v2 discover_hosts
    nova-manage cell_v2 list_hosts

The ``list_hosts`` command should output a table with your compute nodes
mapped to the Nova cell. For more details on Nova cells, their benefits and
how to properly use them, check the `Nova cells documentation`__.

__ https://docs.openstack.org/nova/latest/user/cells.html

If Neutron Hyper-V Agent has been chosen as an L2 agent, make sure that the
Neutron Server meets the following requirements:

* ``networking-hyperv`` installed. To check if ``networking-hyperv`` is
  installed, run the following:

.. code-block:: bash

    pip freeze | grep networking-hyperv

  If there is no output, it can be installed by running the command:

.. code-block:: bash

    pip install networking-hyperv==VERSION

  The ``VERSION`` is dependent on your OpenStack deployment version. For
  example, for Queens, the ``VERSION`` is 6.0.0. For other release names and
  versions, you can look here:
  https://github.com/openstack/networking-hyperv/releases

* The Neutron Server has been configured to use the ``hyperv`` mechanism
  driver. The configuration option can be found in
  ``/etc/neutron/plugins/ml2/ml2_conf.ini``:

.. code-block:: ini

    [ml2]
    mechanism_drivers = openvswitch,hyperv

If the configuration file has been modified, or ``networking-hyperv`` has been
installed, the Neutron Server service will have to be restarted.

Additionally, keep in mind that the Neutron Hyper-V Agent only supports the
following network types: local, flat, VLAN. Ports with any other network
type will result in a PortBindingFailure exception. If tunneling is desired,
the Neutron OVS Agent should be used instead.
