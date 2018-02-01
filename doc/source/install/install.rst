.. _install:

Install
~~~~~~~

This section describes how to install a Hyper-V nova compute node into an
OpenStack deployment. For details about configuration, refer to
:ref:`config_index`.

This section assumes that you already have a working OpenStack environment.

The easiest way to install and configure the ``nova-compute`` service is to use
an MSI, which can be freely downloaded from:
https://cloudbase.it/openstack-hyperv-driver/

The MSI can optionally include the installation and / or configuration of:

* Neutron L2 agents: Neutron Hyper-V Agent, Neutron OVS Agent (if OVS is
  installed on the compute node).
* Ceilometer Polling Agent.
* Windows Services for the mentioned agents.
* Live migration feature (if the compute node is joined in an AD).
* OVS vSwitch extension, OVS bridge, OVS tunnel IP (if OVS is installed, and
  Neutron OVS Agent is used).
* Free RDP
* iSCSI Initiator

MSIs can be installed normally through its GUI, or can be installed in an
unattended mode (useful for automation). In order to do so, the following
command has to be executed:

.. code-block:: bat

    msiexec /i \path\to\the\HyperVNovaCompute.msi /qn /l*v log.txt

The command above will install the given MSI in the quiet, no UI mode, and
will output its verbose logs into the given ``log.txt`` file. Additional
key-value arguments can be given to the MSI for configuration. Some of the
configurations are:

* ADDLOCAL: Comma separated list of features to install. Acceptable values:
  ``HyperVNovaCompute,NeutronHyperVAgent,iSCSISWInitiator,FreeRDP``
* INSTALLDIR: The location where the OpenStack services and their
  configuration files are installed. By default, they are installed in:
  ``%ProgramFiles%\Cloudbase Solutions\OpenStack\Nova``
* SKIPNOVACONF: Installs the MSI without doing any of the other actions:
  creating configuration files, services, vSwitches, OVS bridges, etc.

Example:

.. code-block:: bat

    msiexec /i HyperVNovaCompute.msi /qn /l*v log.txt `
        ADDLOCAL="HyperVNovaCompute,NeutronHyperVAgent,iSCSISWInitiator,FreeRDP"

After installing the OpenStack services on the Hyper-V compute node, check that
they are up and running:

.. code-block:: powershell

    Get-Service nova-compute
    Get-Service neutron-*
    Get-Service ceilometer-*  # if the Ceilometer Polling Agent has been installed.

All the listed services must have the ``Running`` status. If not, refer to the
:ref:`troubleshooting`.
