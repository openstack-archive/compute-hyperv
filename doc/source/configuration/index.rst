.. _config_index:

=============
Configuration
=============

In addition to the Nova config options, compute-hyperv has a few extra
configuration options. For a sample configuration file, refer to
:ref:`config_sample`.


Driver configuration
--------------------

In order to use the compute-hyperv Nova driver, the following configuration
option will have to be set in the ``nova.conf`` file:

  .. code-block:: ini

    [DEFAULT]
    compute_driver = compute_hyperv.driver.HyperVDriver

And for Hyper-V Clusters, the following:

  .. code-block:: ini

    [DEFAULT]
    compute_driver = compute_hyperv.cluster.driver.HyperVClusterDriver
    instances_path = path\to\cluster\wide\storage\location
    sync_power_state_interval = -1

    [workarounds]
    handle_virt_lifecycle_events = False

By default, the OpenStack Hyper-V installer will configure the ``nova-compute``
service to use the ``compute_hyperv.driver.HyperVDriver`` driver.


Storage configuration
---------------------

When spawning instances, ``nova-compute`` will create the VM related files (
VM configuration file, ephemerals, configdrive, console.log, etc.) in the
location specified by the ``instances_path`` configuration option, even if
the instance is volume-backed.

It is not recommended for Nova and Cinder to use the same storage location, as
that can create scheduling and disk overcommitment issues.


Nova instance files location
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, the OpenStack Hyper-V installer will configure ``nova-compute`` to
use the following path as the ``instances_path``:

  .. code-block:: ini

    [DEFAULT]
    instances_path = C:\OpenStack\Instances

``instances_path`` can be set to an SMB share, mounted or unmounted:

  .. code-block:: ini

    [DEFAULT]
    # in this case, X is a persistently mounted SMB share.
    instances_path = X:\OpenStack\Instances

    # or
    instances_path = \\SMB_SERVER\share_name\OpenStack\Instances

Alternatively, CSVs can be used:

  .. code-block:: ini

    [DEFAULT]
    instances_path = C:\ClusterStorage\Volume1\OpenStack\Instances


Block Storage (Cinder) configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

TODO


Live migration configuration
----------------------------

For live migrating virtual machines to hosts with different CPU features the
following configuration option must be set in the compute node's ``nova.conf``
file:

  .. code-block:: ini

    [hyperv]
    limit_cpu_features = True

Keep in mind that changing this configuration option will not affect the
instances that are already spawned, meaning that instances spawned with this
flag set to False will not be able to live migrate to hosts with different CPU
features, and that they will have to be shut down and rebuilt, or have the
setting manually set.


Configuration options
---------------------

.. toctree::
   :maxdepth: 1

   config
   sample_config
