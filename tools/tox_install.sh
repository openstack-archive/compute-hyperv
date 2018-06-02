#!/usr/bin/env bash

# This repo depends on nova, but it does not exist on pypi.

# This wrapper for tox's package installer will use the existing package
# if it exists, else use zuul-cloner if that program exists, else grab it
# from nova master via a hard-coded URL. That last case should only
# happen with devs running unit tests locally.

# From the tox.ini config page:
# install_command=ARGV
# default:
# pip install {opts} {packages}

ZUUL_CLONER=/usr/zuul-env/bin/zuul-cloner
BRANCH_NAME=master
GIT_BASE=${GIT_BASE:-https://git.openstack.org/}

install_project() {
    local project=$1
    local branch=${2:-$BRANCH_NAME}

    if [ -x "$ZUUL_CLONER" ]; then
        echo "ZUUL CLONER" > /tmp/tox_install.txt
        # Make this relative to current working directory so that
        # git clean can remove it. We cannot remove the directory directly
        # since it is referenced after $install_cmd -e
        mkdir -p .tmp
        PROJECT_DIR=$(/bin/mktemp -d -p $(pwd)/.tmp)
        pushd $PROJECT_DIR
        $ZUUL_CLONER --cache-dir \
            /opt/git \
            --branch $branch \
            http://git.openstack.org \
            openstack/$project
        cd openstack/$project
        $install_cmd -e .
        popd
    else
        echo "PIP HARDCODE" > /tmp/tox_install.txt
        local GIT_REPO="$GIT_BASE/openstack/$project"
        SRC_DIR="$VIRTUAL_ENV/src/$project"
        git clone --depth 1 --branch $branch $GIT_REPO $SRC_DIR
        $install_cmd -U -e $SRC_DIR
    fi
}

set -e

install_cmd="pip install -c$1"
shift

install_project nova

$install_cmd -U .
exit $?
