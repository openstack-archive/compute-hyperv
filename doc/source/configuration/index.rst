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

When the compute hosts are using different CSVs, Nova must be configured not
to delete unused images since its image caching mechanism can't properly track
the image file usage in this case.

.. code-block:: ini

    [image_cache]
    remove_unused_base_images = False


Block Storage (Cinder) configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This section describes Nova configuration options that handle the way in which
Cinder volumes are consumed.

When having multiple paths connecting the host to the storage backend,
make sure to enable the following config option:

.. code-block:: ini

    [hyperv]
    use_multipath_io = True

This will ensure that the available paths are actually leveraged. Also, before
attempting any volume connection, it will ensure that the MPIO service is
enabled and that passthrough block devices (iSCSI / FC) are claimed by MPIO.
SMB backed volumes are not affected by this option.

In some cases, Nova may fail to attach volumes due to transient connectivity
issues. The following options specify how many and how often retries should be
performed.

.. code-block:: ini

    [hyperv]
    # Those are the default values.
    volume_attach_retry_count = 10
    volume_attach_retry_interval = 5

    # The following options only apply to disk scan retries.
    mounted_disk_query_retry_count = 10
    mounted_disk_query_retry_interval = 5

When having one or more hardware iSCSI initiators, you may use the following
config option, explicitly telling Nova which iSCSI initiator to use:

.. code-block:: ini

    [hyperv]
    iscsi_initiator_list = PCI\VEN_1077&DEV_2031&SUBSYS_17E8103C&REV_02\\4&257301f0&0&0010_0, PCI\VEN_1077&DEV_2031&SUBSYS_17E8103C&REV_02\4&257301f0&0&0010_1

The list of available initiators may be retrieved using:

.. code-block:: powershell

    Get-InitiatorPort

If no iSCSI initiator is specified, the MS iSCSI Initiator service will only
pick one of the available ones when establishing iSCSI sessions.


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


.. _pci_devices_config:

Whitelisting PCI devices
------------------------

After the assignable PCI devices have been prepared for Hyper-V
(:ref:`pci_devices_setup`), the next step is whitelist them in the compute
node's ``nova.conf``.

.. code-block:: ini

    [pci]
    # this is a list of dictionaries, more dictionaries can be added.
    passthrough_whitelist = [{"vendor_id": "<dev_vendor_id>", "product_id": "<dev_product_id>"}]

The ``vendor_id`` and ``product_id`` necessary for the ``passthrough_whitelist``
can be obtained from assignable PCI device's ``InstanceId``:

.. code-block:: powershell

    Get-VMHostAssignableDevice

The ``InstanceId`` should have the following format:

.. code-block:: none

    PCIP\VEN_<vendor_id>&DEV_<product_id>

The ``<vendor_id>`` and ``<product_id>`` can be extracted and used in the
``nova.conf`` file. After the configuration file has been changed, the
``nova-compute`` service will have to be restarted.

Afterwards, the ``nova-api`` and ``nova-scheduler`` services will have to be
configured. For this, check the `nova PCI passthrough configuration guide`__.

__ https://docs.openstack.org/nova/queens/admin/pci-passthrough.html


Distributed locking configuration
---------------------------------

In order to avoid race conditions, our driver relies on distributed locks. A
distributed lock backend such as etcd, mysql or a file share will have to be
configured.

The following configuration will use etcd 3 as a lock backend:

.. code-block:: ini

    [coordination]
    backend_url = etcd3+http://etcd_address:2379

.. note::

   The ``etcd3gw`` python package is required when using etcd 3. This does not
   apply to the v2 etcd API, which may be requested through
   ``etcd://etcd_address:2379``.

In order to use a file share, set the following:

.. code-block:: ini

    [coordination]
    backend_url = file:////share_addr/share_name


Configuration options
---------------------

.. toctree::
   :maxdepth: 1

   config
   sample_config
