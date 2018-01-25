.. _verify:

Verify operation
~~~~~~~~~~~~~~~~

Verify that instances can be created on the Hyper-V compute node through
nova. If spawning fails, check the nova compute log file on the Hyper-V
compute node for relevant information (by default, it can be found in
``C:\OpenStack\Log\``). Additionally, setting the ``debug`` configuration
option in ``nova.conf`` will help troubleshoot the issue.

If there is no relevant information in the compute node's logs, check the
Nova controller's logs.
