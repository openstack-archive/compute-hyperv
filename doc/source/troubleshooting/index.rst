.. _troubleshooting:

=====================
Troubleshooting guide
=====================

This section contains a few tips and tricks which can help you troubleshoot
and solve your Hyper-V compute node's potential issues.


OpenStack Services not running
------------------------------

You can check if the OpenStack services are up by running:

.. code-block:: powershell

    Get-Service nova-compute
    Get-Service neutron-*

All the listed services must have the ``Running`` status. If not, check their
logs, which can typically be found in ``C:\OpenStack\Log\``. If there are no
logs, try to run the services manually. To see how to run ``nova-compute``
manually, run the following command:

.. code-block:: powershell

    sc.exe qc nova-compute

The output will contain the ``BINARY_PATH_NAME`` with the service's command.
The command will contain the path to the ``nova-compute.exe`` executable and
its configuration file path. Edit the configuration file and add the
following:

.. code-block:: ini

    [DEFAULT]
    debug = True
    use_stderr = True

This will help troubleshoot the service's issues. Next, run ``nova-compute``
in PowerShell manually:

.. code-block:: powershell

    &"C:\Program Files\Cloudbase Solutions\OpenStack\Nova\Python27\Scripts\nova-compute.exe" `
        --config-file "C:\Program Files\Cloudbase Solutions\OpenStack\Nova\etc\nova.conf"

The reason why the service could not be started should be visible in the
output.


Live migration
--------------

`This guide`__ offers a few tips for troubleshooting live migration issues.

If live migration fails because the nodes have incompatible hardware, refer to
refer to :ref:`config_index`.

__ https://docs.microsoft.com/en-us/windows-server/virtualization/hyper-v/manage/Use-live-migration-without-Failover-Clustering-to-move-a-virtual-machine


How to restart a service on Hyper-V
-----------------------------------

Restarting a service on OpenStack can easily be done through Powershell:

.. code-block:: powershell

     Restart-Service service-name

or through cmd:

.. code-block:: bat

     net stop service_name && net start service_name

For example, the following command will restart the iSCSI initiator service:

.. code-block:: powershell

     Restart-Service msiscsi
