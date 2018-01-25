==================
Installation guide
==================

The compute-hyperv project offers two Nova Hyper-V drivers, providing
additional features and bug fixes compared to the in-tree Nova
Hyper-V driver:

* ``compute_hyperv.driver.HyperVDriver``
* ``compute_hyperv.cluster.driver.HyperVClusterDriver``

These drivers receive the same degree of testing (if not even more) as the
upstream driver, being covered by a range of official OpenStack Continuous
Integration (CI) systems.

Most production Hyper-V based OpenStack deployments use the compute-hyperv
drivers.

The ``HyperVClusterDriver`` can be used on Hyper-V Cluster compute nodes and
will create and manage highly available clustered virtual machines.

This chapter assumes a working setup of OpenStack following the
`OpenStack Installation Tutorial
<https://docs.openstack.org/install-guide/>`_.


.. toctree::
   :maxdepth: 2

   prerequisites.rst
   install.rst
   next-steps.rst
   verify.rst
