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
build any instances on it.

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
