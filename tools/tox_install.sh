#!/bin/sh

# compute-hyperv repo depends on nova, which doesn't exist on pypi.

# This wrapper for tox's package installer will use the existing package
# if it exists, else use zuul-cloner if that program exists, else grab it
# from nova master via a hard-coded URL. That last case should only
# happen with devs running unit tests locally.

# From the tox.ini config page:
# install_command=ARGV
# default:
# pip install {opts} {packages}

ZUUL_CLONER=/usr/zuul-env/bin/zuul-cloner
nova_installed=$(echo "import nova" | python 2>/dev/null ; echo $?)

set -e

if [ $nova_installed -eq 0 ]; then
    echo "ALREADY INSTALLED" > /tmp/tox_install.txt
    echo "Nova already installed; using existing package"
elif [ -x "$ZUUL_CLONER" ]; then
    export ZUUL_BRANCH=${ZUUL_BRANCH-$BRANCH}
    echo "ZUUL CLONER" > /tmp/tox_install.txt
    cwd=$(/bin/pwd)
    cd /tmp
    $ZUUL_CLONER --cache-dir \
        /opt/git \
        git://git.openstack.org \
        openstack/nova
    cd openstack/nova
    pip install -e .
    cd "$cwd"
else
    echo "PIP HARDCODE" > /tmp/tox_install.txt
    pip install -U -egit+https://git.openstack.org/openstack/nova@stable/liberty#egg=nova
fi

pip install -U $*
exit $?
