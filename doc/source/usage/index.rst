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
