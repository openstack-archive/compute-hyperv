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

The Hyper-V compute nodes needs to have ample storage for storing the virtual
machine images running on the compute nodes (for boot-from-image instances).

For Hyper-V compute nodes, the following storage options are available:

* Local disk.
* SMB shares. Make sure that they are persistent.
* Cluster Shared Volumes (``CSV``)
    * Storage Spaces
    * Storage Spaces Direct (``S2D``)
    * SAN LUNs as underlying CSV storage

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
