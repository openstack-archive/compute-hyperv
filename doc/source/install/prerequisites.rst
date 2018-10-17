=============
Prerequisites
=============

Starting with Folsom, Hyper-V can be used as a compute node within OpenStack
deployments.

The Hyper-V versions that are currently supported are:

* (deprecated) Windows / Hyper-V Server 2012
* Windows / Hyper-V Server 2012 R2
* Windows / Hyper-V Server 2016

Newer Hyper-V versions come with an extended list of features, and can offer
better overall performance. Thus, Windows / Hyper-V Server 2016 is recommended
for the best experience.


Hardware requirements
---------------------

Although this document does not provide a complete list of Hyper-V compatible
hardware, the following items are necessary:

* 64-bit processor with Second Level Address Translation (SLAT).
* CPU support for VM Monitor Mode Extension (VT-c on Intel CPU's).
* Minimum of 4 GB memory. As virtual machines share memory with the Hyper-V
  host, you will need to provide enough memory to handle the expected virtual
  workload.
* Minimum 16-20 GB of disk space for the OS itself and updates.
* At least one NIC, but optimally two NICs: one connected to the management
  network, and one connected to the guest data network. If a single NIC is
  used, when creating the Hyper-V vSwitch, make sure the ``-AllowManagementOS``
  option is set to ``True``, otherwise you will lose connectivity to the host.

The following items will need to be enabled in the system BIOS:

* Virtualization Technology - may have a different label depending on
  motherboard manufacturer.
* Hardware Enforced Data Execution Prevention.

To check a host's Hyper-V compatibility, open up cmd or Powershell and run:

.. code-block:: bat

    systeminfo

The output will include the Hyper-V requirements and if the host meets them or
not. If all the requirements are met, the host is Hyper-V capable.


Storage considerations
----------------------

Instance files
~~~~~~~~~~~~~~

Nova will use a pre-configured directory for storing instance files such as:

* instance boot images and ``ephemeral`` disk images
* instance config files (config drive image and Hyper-V files)
* instance console log
* cached Glance images
* snapshot files

The following options are available for the instance directory:

* Local disk.
* SMB shares. Make sure that they are persistent.
* Cluster Shared Volumes (``CSV``)
    * Storage Spaces
    * Storage Spaces Direct (``S2D``)
    * SAN LUNs as underlying CSV storage

.. note::

    Ample storage may be required when using Nova "local" storage for the
    instance virtual disk images (as opposed to booting from Cinder volumes).

Compute nodes can be configured to use the same storage option. Doing so will
result in faster cold / live migration operations to other compute nodes using
the same storage, but there's a risk of disk overcommitment. Nova is not aware
of compute nodes sharing the same storage and because of this, the Nova
scheduler might pick a host it normally wouldn't.

For example, hosts A and B are configured to use a 100 GB SMB share. Both
compute nodes will report as having 100 GB storage available. Nova has to
spawn 2 instances requiring 80 GB storage each. Normally, Nova would be able
to spawn only one instance, but both will spawn on different hosts,
overcommiting the disk by 60 GB.


Cinder volumes
~~~~~~~~~~~~~~

The Nova Hyper-V driver can attach Cinder volumes exposed through the
following protocols:

* iSCSI
* Fibre Channel
* SMB - the volumes are stored as virtual disk images (e.g. VHD / VHDX)

.. note::

    The Nova Hyper-V Cluster driver only supports SMB backed volumes. The
    reason is that the volumes need to be available on the destination
    host side during an unexpected instance failover.

Before configuring Nova, you should ensure that the Hyper-V compute nodes
can properly access the storage backend used by Cinder.

The MSI installer can enable the Microsoft Software iSCSI initiator for you.
When using hardware iSCSI initiators or Fibre Channel, make sure that the HBAs
are properly configured and the drivers are up to date.

Please consult your storage vendor documentation to see if there are any other
special requirements (e.g. additional software to be installed, such as iSCSI
DSMs - Device Specific Modules).

Some Cinder backends require pre-configured information (specified via volume
types or Cinder Volume config file) about the hosts that are going to consume
the volumes (e.g. the operating system type), based on which the LUNs will be
created/exposed. The reason is that the supported SCSI command set may differ
based on the operating system. An incorrect LUN type may prevent Windows nodes
from accessing the volumes (although generic LUN types should be fine in most
cases).

Multipath IO
""""""""""""

You may setup multiple paths between your Windows hosts and the storage
backends in order to provide increased throughput and fault tolerance.

When using iSCSI or Fibre Channel, make sure to enable and configure the
MPIO service. MPIO is a service that manages available disk paths, performing
failover and load balancing based on pre-configured policies. It's extendable,
in the sense that Device Specific Modules may be imported.

The MPIO service will ensure that LUNs accessible through multiple paths are
exposed by the OS as a single disk drive.

.. warning::
    If multiple disk paths are available and the MPIO service is not
    configured properly, the same LUN can be exposed as multiple disk drives
    (one per available path). This must be addressed urgently as it can
    potentially lead to data corruption.

Run the following to enable the MPIO service:

.. code-block:: powershell

    Enable-WindowsOptionalFeature –Online –FeatureName MultiPathIO

    # Ensure that the "mpio" service is running
    Get-Service mpio

Once you have enabled MPIO, make sure to configure it to automatically
claim volumes exposed by the desired storage backend. If needed, import
vendor provided DSMs.

For more details about Windows MPIO, check the following `page`__.

__ https://docs.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-R2-and-2008/ee619734(v=ws.10)

SMB 3.0 and later also supports using multiple paths to a share (the UNC
path can be the same), leveraging ``SMB Direct`` and ``SMB Multichannel``.

By default, all available paths will be used when accessing SMB shares.
You can configure constraints in order to choose which adapters should
be used when connecting to SMB shares (for example, to avoid using a
management network for SMB traffic).

.. note::

    SMB does not require or interact in any way with the MPIO service.

For best performance, ``SMB Direct`` (RDMA) should also be used, if your
network cards support it.

For more details about ``SMB Multichannel``, check the following
`blog post`__.

__ https://blogs.technet.microsoft.com/josebda/2012/06/28/the-basics-of-smb-multichannel-a-feature-of-windows-server-2012-and-smb-3-0/


NTP configuration
-----------------

Network time services must be configured to ensure proper operation of the
OpenStack nodes. To set network time on your Windows host you must run the
following commands:

.. code-block:: bat

   net stop w32time
   w32tm /config /manualpeerlist:pool.ntp.org,0x8 /syncfromflags:MANUAL
   net start w32time

Keep in mind that the node will have to be time synchronized with the other
nodes of your OpenStack environment, so it is important to use the same NTP
server. Note that in case of an Active Directory environment, you may do this
only for the AD Domain Controller.


Live migration configuration
----------------------------

In order for the live migration feature to work on the Hyper-V compute nodes,
the following items are required:

* A Windows domain controller with the Hyper-V compute nodes as domain members.
* The ``nova-compute`` service must run with domain credentials. You can set
  the service credentials with:

.. code-block:: bat

   sc.exe config openstack-compute obj="DOMAIN\username" password="password"

`This guide`__ contains information on how to setup and configure live
migration on your Hyper-V compute nodes (authentication options, constrained
delegation, migration performance options, etc), and a few troubleshooting
tips.

__ https://docs.microsoft.com/en-us/windows-server/virtualization/hyper-v/manage/Use-live-migration-without-Failover-Clustering-to-move-a-virtual-machine


Hyper-V Cluster configuration
-----------------------------

compute-hyperv also offers a driver for Hyper-V Cluster nodes, which will be
able to create and manage highly available virtual machines. For the Hyper-V
Cluster Driver to be usable, the Hyper-V Cluster nodes will have to be joined
to an Active Directory and a Microsoft Failover Cluster. The nodes in a
Hyper-V Cluster must be identical.

In order to avoid race conditions, our driver relies on distributed locks. A
distributed lock backend such as etcd, mysql or a file share will have to be
configured.

For more details about available distributed lock backends, check the
`list of drivers supported by tooz`__.

__ https://docs.openstack.org/tooz/latest/user/drivers.html


Guarded Host configuration (Shielded VMs)
-----------------------------------------

Shielded VMs is a new feature introduced in Windows / Hyper-V Server 2016 and
can be used in order to have highly secure virtual machines that cannot be
read from, tampered with, or inspected by malware, or even malicious
administrators.

In order for a Hyper-V compute node to be able to spawn such VMs, it must be
configured as a Guarded Host.

For more information on how to configure your Active Directory, Host Guardian
Service, and compute node as a Guarded Host, you can read `this article`__.

__ https://cloudbase.it/hyperv-shielded-vms-part-1/


.. _numa_setup:

NUMA spanning configuration
---------------------------

Non-Uniform Memory Access (NUMA) is a computer system architecture that groups
processors and memory in NUMA nodes. Processor threads accessing data in the
same NUMA cell have lower memory access latencies and better overall
performance. Some applications are NUMA-aware, taking advantage of NUMA
performance optimizations.

Windows / Hyper-V Server 2012 introduced support for Virtual NUMA (vNUMA),
which can be exposed to the VMs, allowing them to benefit from the NUMA
performance optimizations.

By default, when Hyper-V starts a VM, it will try to fit all of its memory in
a single NUMA node, but it doesn't fit in only one, it will be spanned across
multiple NUMA nodes. This is called NUMA spanning, and it is enabled by
default. This allows Hyper-V to easily utilize the host's memory for VMs.

NUMA spanning can be disabled and VMs can be configured to span a specific
number of NUMA nodes (including 1), and have that NUMA topology exposed to
the guest. Keep in mind that if a VM's vNUMA topology doesn't fit in the
host's available NUMA topology, it won't be able to start, and as a side
effect, less memory can be utilized for VMs.

If a compute node only has 1 NUMA node, disabling NUMA spanning will have no
effect. To check how many NUMA node a host has, run the following powershell
command:

.. code-block:: powershell

    Get-VMHostNumaNode

The output will contain a list of NUMA nodes, their processors, total memory,
and used memory.

To disable NUMA spanning, run the following powershell commands:

.. code-block:: powershell

    Set-VMHost -NumaSpanningEnabled $false
    Restart-Service vmms

In order for the changes to take effect, the Hyper-V Virtual Machine Management
service (vmms) and the Hyper-V VMs have to be restarted.

For more details on vNUMA, you can read the `following documentation`__.

__ https://docs.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-R2-and-2012/dn282282(v=ws.11)


.. _pci_devices_setup:

PCI passthrough host configuration
----------------------------------

Starting with Windows / Hyper-V Server 2016, PCI devices can be directly
assigned to Hyper-V VMs.

In order to benefit from this feature, the host must support SR-IOV and
have assignable PCI devices. This can easily be checked by running the
following in powershell:

.. code-block:: powershell

    Start-BitsTransfer https://raw.githubusercontent.com/Microsoft/Virtualization-Documentation/master/hyperv-samples/benarm-powershell/DDA/survey-dda.ps1
    .\survey-dda.ps1

The script above will output if the host supports SR-IOV, a detailed list
of PCI devices and if they're assignable or not.

If all the conditions are met, the desired devices will have to be prepared to
be assigned to VMs. The `following article`__ contains a step-by-step guide on
how to prepare them and how to restore the configurations if needed.

__ https://blogs.technet.microsoft.com/heyscriptingguy/2016/07/14/passing-through-devices-to-hyper-v-vms-by-using-discrete-device-assignment/
