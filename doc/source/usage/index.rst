===========
Usage guide
===========

This section contains information on how to create Glance images for Hyper-V
compute nodes and how to use various Hyper-V features through image metadata
properties and Nova flavor extra specs.


Prepare images for use with Hyper-V
-----------------------------------

Hyper-V currently supports only the VHD and VHDx file formats for virtual
machines.

OpenStack Hyper-V images should have the following items installed:

* cloud-init (Linux) or cloudbase-init (Windows)
* Linux Integration Services (on Linux type OSes)

Images can be uploaded to `glance` using the `openstack` client:

.. code-block:: bash

    openstack image create --name "VM_IMAGE_NAME" --property hypervisor_type=hyperv --public \
        --container-format bare --disk-format vhd --file /path/to/image

.. note::

   VHD and VHDx files sizes can be bigger than their maximum internal size,
   as such you need to boot instances using a flavor with a slightly bigger
   disk size than the internal size of the disk files.


Generation 2 VM images
~~~~~~~~~~~~~~~~~~~~~~

Windows / Hyper-V Server 2012 R2 introduced a feature called
**Generation 2 VMs**, which adds the support for Secure Boot, UEFI,
reduced boot times, etc.

Starting with Kilo, the Hyper-V Driver supports Generation 2 VMs.

Check the `original spec`__ for more details on its features, how to prepare
and create the glance images, and restrictions.

Regarding restrictions, the original spec mentions that RemoteFX is not
supported with Generation 2 VMs, but starting with Windows /
Hyper-V Server 2016, this is a supported usecase.

.. important::

    The images must be prepared for Generation 2 VMs before uploading to glance
    (can be created and prepared in a Hyper-V Generation 2 VM). Generation 2
    VM images cannot be used in Generation 1 VMs and vice-versa. The instances
    will spawn and will be in the ``Running`` state, but they will **not** be
    usable.

__ https://specs.openstack.org/openstack/nova-specs/specs/kilo/implemented/hyper-v-generation-2-vms.html


UEFI Secure Boot
----------------

Secure Boot is a mechanism that starts the bootloader only if the bootloader's
signature has maintained integrity, assuring that only approved components are
allowed to run. This mechanism is dependent on UEFI.

As it requires UEFI, this feature is only available to Generation 2 VMs, and
the guest OS must be supported by Hyper-V. Newer Hyper-V versions supports
more OS types and versions, for example:

* Windows / Hyper-V Server 2012 R2 supports only Windows guests
* Windows / Hyper-V Server 2016 supports Windows and Linux guests

Check the following for a detailed list of supported
`Linux distributions and versions`__.

The Hyper-V Driver supports this feature starting with OpenStack Liberty.

.. important::
    The images must be prepared for Secure Boot before they're uploaded to
    glance. For example, the VM on which the image is prepared must be a
    Generation 2 VM with Secure Boot enabled. These images can be spawned
    with Secure Boot enabled or disabled, while other images can only be
    spawned with Secure Boot disabled. The instances will spawn and will be
    in the ``Running`` state, but they will **not** be usable.

UEFI Secure Boot instances are created by specifying the ``os_secure_boot``
image metadata property, or the nova flavor extra spec ``os:secure_boot``
(the flavor extra spec's value takes precedence).

The ``os_secure_boot`` image metadata property acceptable values are:
``disabled, optional, required`` (``disabled`` by default). The ``optional``
value means that the image is capable of Secure Boot, but it will require the
flavor extra spec ``os:secure_boot`` to be ``required`` in order to use this
feature.

Additionally, the image metadata property ``os_type`` is mandatory when
enabling Secure Boot. Acceptable values: ``windows``, ``linux``.

Finally, in deployments with compute nodes with different Hyper-V versions,
the ``hypervisor_version_requires`` image metadata property should be set
in order to ensure proper scheduling. The correct values are:

* ``>=6.3`` for images targeting Windows / Hyper-V Server 2012 R2 or newer
* ``>=10.0`` for images targeting Windows / Hyper-V Server 2016 or newer
  (Linux guests)

Examples of how to create the glance image:

.. code-block:: bsah

    glance image-create --property hypervisor_type=hyperv \
        --property hw_machine_type="hyperv-gen2" \
        --property hypervisor_version_requires=">=6.3" \
        --property os_secure_boot=required --os-type=windows \
        --name win-secure --disk-format vhd --container-format bare \
        --file path/to/windows.vhdx

    glance image-update --property os_secure_boot=optional <linux-image-uuid>
    glance image-update --property hypervisor_version_requires=">=10.0" <linux-image-uuid>
    glance image-update --property os_type=linux

    nova flavor-key <flavor-name> set "os:secure_boot=required"

__ https://docs.microsoft.com/en-us/windows-server/virtualization/hyper-v/Supported-Linux-and-FreeBSD-virtual-machines-for-Hyper-V-on-Windows


Shielded VMs
------------

Introduced in Windows / Hyper-V Server 2016, shielded virtual machines are
Generation 2 VMs, with virtual TPMs, and encrypted using BitLocker (memory,
disks, VM state, video, etc.). These VMs can only run on healthy Guarded
Hosts. Because of this, the shielded VMs have better protection against
malware or even compromised administrators, as they cannot tamper with,
inspect, or steal data from these virtual machines.

This feature has been introduced in OpenStack in Newton.

In order to use this feature in OpenStack, the Hyper-V compute nodes must
be prepared and configured as a Guarded Host beforehand. Additionally, the
Shielded VM images must be prepared for this feature before uploading them
into Glance.

For information on how to create a Host Guardian Service and Guarded Host
setup, and how to create a Shielded VM template for Glance, you can check
`this article`__.

__ https://cloudbase.it/hyperv-shielded-vms-part-1/

Finally, after the Shielded VM template has been created, it will have to be
uploaded to Glance. After which, Shielded VM instances can be spawned through
Nova. You can read the `followup article`__ for details on how to do these
steps.

__ https://cloudbase.it/hyper-v-shielded-vms-part-2/


Setting Boot Order
------------------

Support for setting boot order for Hyper-V instances has been introduced in
Liberty, and it is only available for Generation 2 VMs. For Generation 1 VMs,
the spawned VM's boot order is changed only if the given image is an ISO,
booting from ISO first.

The boot order can be specified when creating a new instance:

.. code-block:: bash

    nova boot --flavor m1.tiny --nic --net-name=private --block-device \
        source=image,id=<image_id>,dest=volume,size=2,shutdown=remove,bootindex=0 \
        my-new-vm

For more details on block devices, including more details about setting the
the boot order, you can check the `block device mapping docs`__.

__ https://docs.openstack.org/nova/stein/user/block-device-mapping.html#block-device-mapping-v2


RemoteFX
--------

RemoteFX allows you to virtualize your GPUs and share them with Hyper-V VMs by
adding virtual graphics devices to them, especially useful for enhancing
GPU-intensive applications (CUDA, OpenCL, etc.) and a richer RDP experience.

We have added support for RemoteFX in OpenStack in Kilo.

Check `this article`__ for more details on RemoteFX's prerequisites, how to
configure the host and the ``nova-compute`` service, guest OS requirements,
and how to spawn RemoteFX instances in OpenStack.

RemoteFX can be enabled during spawn, or it can be enabled / disabled through
cold resize.

__ https://cloudbase.it/openstack-remotefx/


Hyper-V vNUMA instances
-----------------------

Hyper-V instances can have a vNUMA topology starting with Windows / Hyper-V
Server 2012. This feature improves the performance for instances with large
amounts of memory and for high-performance NUMA-aware applications.

Support for Hyper-V vNUMA instances has been added in Liberty.

Before spawning vNUMA instances, the Hyper-V host must be configured first. For
this, refer to :ref:`numa_setup`.

Hyper-V only supports symmetric NUMA topologies, and the Hyper-V Driver will
raise an exception if an asymmetric one is given.

Additionally, a Hyper-V VM cannot be configured with a NUMA topology and
Dynamic Memory at the same time. Because of this, the Hyper-V Driver will
always disable Dynamic Memory on VMs that require NUMA topology, even if the
configured ``dynamic_memory_ratio`` is higher than ``1.0``.

For more details on this feature and how to use it in OpenStack, check the
`original spec`__

**Note:** Since Hyper-V is responsible for fitting the instance's vNUMA
topologies in the host's NUMA topology, there's a slight risk of instances
not being to be started after they've been stopped for a while, because it
doesn't fit in the NUMA topology anymore. For example, let's consider the
following scenario:

Host A with 2 NUMA nodes (0, 1), 16 GB memory each. The host has the following
instances:

* **instance A:** 16 GB memory, spans 2 vNUMA nodes (8 each).
* **instances B, C:** 6 GB memory each, spans 1 vNUMA node.
* **instances D, E:** 2 GB memory each, spans 1 vNUMA node.

Topology-wise, they would fit as follows:

**NUMA node 0:** A(0), B, D
**NUMA node 1:** A(1), C, E

All instances are stopped, then the following instances are started in this
order: B, D, E, C. The topology would look something like this:

**NUMA node 0:** B
**NUMA node 1:** D, E, C

Starting A will fail, as the NUMA node 1 will have 10 GB memory used, and A
needs 8 GB on that node.

One way to mitigate this issue would be to segregate instances spanning
multiple NUMA nodes to different compute nodes / availability zones from the
regular instances.

__ https://specs.openstack.org/openstack/nova-specs/specs/ocata/implemented/hyper-v-vnuma-enable.html


Using Cinder Volumes
--------------------

Identifying disks
~~~~~~~~~~~~~~~~~

When attaching multiple volumes to an instance, it's important to have a way
in which you can safely identify them on the guest side.

While Libvirt exposes the Cinder volume id as disk serial id (visible in
/dev/disk/by-id/), this is not possible in case of Hyper-V.

The mountpoints exposed by Nova (e.g. /dev/sd*) are not a reliable source
either (which mostly stands for other Nova drivers as well).

Starting with Queens, the Hyper-V driver includes disk address information in
the instance metadata, accessible on the guest side through the metadata
service. This also applies to untagged volume attachments.

.. note::
    The config drive should not be relied upon when fetching disk metadata
    as it never gets updated after an instance is created.

Here's an example:

.. code-block:: bash

    nova volume-attach cirros 1517bb04-38ed-4b4a-bef3-21bec7d38792
    vm_fip="192.168.42.74"

    cmd="curl -s 169.254.169.254/openstack/latest/meta_data.json"
    ssh_opts=( -o "StrictHostKeyChecking no" -o "UserKnownHostsFile /dev/null" )
    metadata=`ssh "${ssh_opts[@]}" "cirros@$vm_fip" $cmd`
    echo $metadata | python -m json.tool

    # Sample output
    #
    # {
    #     "availability_zone": "nova",
    #     "devices": [
    #         {
    #             "address": "0:0:0:0",
    #             "bus": "scsi",
    #             "serial": "1517bb04-38ed-4b4a-bef3-21bec7d38792",
    #             "tags": [],
    #             "type": "disk"
    #         }
    #     ],
    #     "hostname": "cirros.novalocal",
    #     "launch_index": 0,
    #     "name": "cirros",
    #     "project_id": "3a8199184dfc4821ab01f9cbd72f905e",
    #     "uuid": "f0a09969-d477-4d2f-9ad3-3e561226d49d"
    # }

    # Now that we have the disk SCSI address, we may fetch its path.
    file `find /dev/disk/by-path  | grep "scsi-0:0:0:0"`

    # Sample output
    # /dev/disk/by-path/pci-0000:00:10.0-scsi-0:0:0:0: symbolic link to ../../sdb

The volumes may be identified in a similar way in case of Windows guests as
well.


Online volume extend
~~~~~~~~~~~~~~~~~~~~

The Hyper-V driver supports online Cinder volume resize. Still, there are a
few cases in which this feature is not available:

* SMB backed volumes
* Some iSCSI backends where the online resize operation impacts connected
  initiators. For example, when using the Cinder LVM driver and TGT, the
  iSCSI targets are actually recreated during the process. The MS iSCSI
  initiator will attempt to reconnect but TGT will report that the target
  does not exist, for which reason no reconnect attempts will be performed.


Disk QoS
--------

In terms of QoS, Hyper-V allows IOPS limits to be set on virtual disk images
preventing instances to exhaust the storage resources.

Support for setting disk IOPS limits in Hyper-V has been added in OpenStack
in Kilo.

The IOPS limits can be specified by number of IOPS, or number of bytes per
second (IOPS has precedence). Keep in mind that Hyper-V sets IOPS in normalized
IOPS allocation units (8 KB increments) and if the configured QoS policies are
not multiple of 8 KB, the Hyper-V Driver will round down to the nearest
multiple (minimum 1 IOPS).

QoS is set differently for Cinder volumes and Nova local disks.


Cinder Volumes
~~~~~~~~~~~~~~

Cinder QoS specs can be either front-end (enforced on the consumer side),
in this case Nova, or back-end (enforced on the Cinder side).

The Hyper-V driver only allows setting IOPS limits for volumes exposed by
Cinder SMB backends. For other Cinder backends (e.g. SANs exposing volumes
through iSCSI or FC), backend QoS specs must be used.

.. code-block:: bash

    # alternatively, total_iops_sec can be specified instead.
    cinder qos-create my-qos consumer=front-end total_bytes_sec=<number_of_bytes>
    cinder qos-associate my-qos <volume_type>

    cinder create <size> --volume-type <volume_type>

    # The QoS specs are applied when the volume is attached to a Hyper-V instance
    nova volume-attach <hyperv_instance_id> <volume_id>


Nova instance local disks
~~~~~~~~~~~~~~~~~~~~~~~~~

The QoS policy is set to all of the instance's disks (including ephemeral
disks), and can be enabled at spawn, or enabled / disabled through cold
resize.

.. code-block:: bash

    # alternatively, quota:disk_total_iops_sec can be used instead.
    nova flavor-key <my_flavor> set quota:disk_total_bytes_sec=<number_of_bytes>


PCI devices
-----------

Windows / Hyper-V Server 2016 introduced Discrete Device Assignment, which
allows users to attach PCI devices directly to Hyper-V VMs. The Hyper-V host
must have SR-IOV support and have the PCI devices prepared before assignment.

The Hyper-V Driver added support for this feature in OpenStack in Ocata.

For preparing the PCI devices for assignment, refer to :ref:`pci_devices_setup`.

The PCI devices must be whitelisted before being able to assign them. For this,
refer to :ref:`pci_devices_config`.

PCI devices can be attached to Hyper-V instances at spawn, or attached /
detached through cold resize through nova flavor extra specs:

.. code-block:: bash

    nova flavor-key <my_flavor> set "pci_passthrough:alias"="alias:num_pci_devices"


Serial port configuration
-------------------------

Serial ports are used to interact with an instance's console and / or read its
output. This feature has been introduced for the Hyper-V Drvier in Kilo.

For Hyper-V, the serial ports can be configured to be Read Only or Read / Write.
This can be specified through the image metadata properties:

* ``interactive_serial_port``: configure the given port as Read / Write.
* ``logging_serial_port``: configure the given port as Read Only.

Valid values: ``1,2``

One port will always be configured as Read / Write, and by default, that port
is ``1``.


Hyper-V VM vNIC attach / detach
-------------------------------

When creating a new instance, users can specify how many NICs the instance will
have, and to which neutron networks / ports they will be connected to. But
starting with Kilo, additional NICs can be added to Hyper-V VMs after they have
been created. This can be done through the command:

.. code-block:: bash

    # alternatively, --port_id <port_id> can be specified.
    nova interface-attach --net-id <net_id> <instance>

However, there are a few restrictions that have to be taken into account in
order for the operation to be successful. When attaching a new vNIC to an
instance, the instance must be turned off, unless all the following conditions
are met:

* The compute node hosting the VM is a Windows / Hyper-V Server 2016 or newer.
* The instance is a Generation 2 VM.

If the conditions are met, the vNIC can be hot-plugged and the instance does
not have to be turned off.

The same restrictions apply when detaching a vNIC from a Hyper-V instance.
Detaching interfaces can be done through the command:

.. code-block:: bash

    nova interface-detach <instance> <port_id>


Nested virtualization
---------------------

Nested virtualization has been introduced in Windows / Hyper-V Server 2016 and
support for it has been added to OpenStack in Pike. This feature will allow you
to create Hyper-V instances which will be able to create nested VMs of their own.

In order to use this feature, the compute nodes must have the latest updates
installed.

At the moment, only Windows / Hyper-V Server 2016 or Windows 10 guests can
benefit from this feature.

Dynamic Memory is not supported for instances with nested virtualization enabled,
thus, the Hyper-V Driver will always spawn such instances with Dynamic Memory
disabled, even if the configured ``dynamic_memory_ratio`` is higher than 1.0.

Disabling the security groups associated with instance's neutron ports will
enable MAC spoofing for instance's NICs (Queens or newer, if ``neutron-hyperv-agent``
is used), which is necessary if the nested VMs needs access to the tenant or
external network.

Instances with nested virtualization enabled can be spawned by adding ``vmx`` to
the image metadata property ``hw_cpu_features`` or the nova flavor extra spec
``hw:cpu_features``.

.. important::

    This feature will not work on clustered compute nodes.
