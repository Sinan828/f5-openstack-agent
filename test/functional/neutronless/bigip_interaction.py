#!/usr/bin/env python
# Copyright 2017 F5 Networks Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import datetime
import os
import pytest
import subprocess

from collections import namedtuple
from time import sleep

my_epoch = datetime.datetime.now().strftime('%Y%m%d%H%M%S')

"""Allows test interaction with the BIG-IP

    This module holds classes and tools used for the control of the BIG-IP
    config via backup and restore for test pollution prevention purposes.

    For use outside of testenv:
        In order to use this outside of testenv, the user should set the bash
        env variable:
            export ssh_cfg=/path/to/.ssh/config
        It should also be noted that this setup assumes a few things:
            1. That the handshakes and any key sharing is performed before
                runtime
            2. That the key-add to the shared key has already been performed

    Diff files will be generated by `which diff` linux-builtin app.  These diff
    results will be stored within /tmp/ under the format of:
        agent_only_bigip_<test>_<year><month><day><hour><minute><second>.diff
    Example:
        /tmp/agent_only_bigip_test_foo_20170703222353.diff
        for test_foo() at 2017/07/03 22:23:53 or 10:23:53 PM
    Time stamps for this are captured at compile time, not during runtime or
    at file generation time, so it is expected that the creation date may
    differ from the timestamp in the filename.  This was chosen due to wanting
    all files generated in a single test run to have the same timestamp.
"""


class BigIpInteraction(object):
    """Class of simple class methods that open interaction with BIG-IP

    This class assumes that you are in testenv and that there is a BIG-IP
    configured for test runner's use.
    """
    __current_test = ''
    diff_file = '/tmp/agent_only_bigip_{}_{}.diff'
    dirty_file = '/tmp/agent_only_bigip_dirty_{}_{}.cfg'
    config_file = '/tmp/agent_only_bigip_{}.cfg'
    _lbs_to_delete = []
    ssh_cmd = ''
    __extract_cmd = '''{} << EOF
tmsh -c \"cd /;
list sys folder recursive one-line" | cut -d " " -f3 |
while read f; do echo \"====================\";
echo \"Folder $f\"; tmsh -c "cd /$f; list\"; done;
exit
EOF'''
    __ucs_cmd_fmt = "{} tmsh {} /sys ucs /tmp/backup.ucs"

    @staticmethod
    def __exec_shell(stdin, shell=False):
        """Protected method for internal use"""
        Result = namedtuple('Result', 'stdout, stdin, stderr, exit_status')
        try:
            stdout = subprocess.check_output(stdin, shell=shell)
            stderr = ''
            exit_status = 0
        except subprocess.CalledProcessError as error:
            stderr = str(error)
            stdout = error.output
            exit_status = error.returncode

        return Result(stdout, stdin, stderr, exit_status)

    @staticmethod
    def __check_results(results):
        if results.exit_status:
            raise RuntimeError(
                "Could not extract bigip data!\nstderr:'{}'"
                ";stdout'{}' ({})".format(results.stderr, results.stdout,
                                          results.exit_status))

    @classmethod
    def _get_current_bigip_cfg(cls):
        """Get and return the current BIG-IP Config

        This method will perform the action of collecting BIG-IP config data.
        """
        results = cls.__exec_shell(
            cls.__extract_cmd.format(cls.ssh_cmd), shell=True)
        cls.__check_results(results)
        return results.stdout

    @classmethod
    def _get_existing_bigip_cfg(cls):
        """Extracts the BIG-IP config and stores it within instance

        This method will hold a copy of the existing BIG-IP config for later
        comparison.
        """
        result = cls._get_current_bigip_cfg()
        with open(cls.config_file.format(my_epoch), 'w') as fh:
            fh.write(result)

    @classmethod
    def __restore_from_backup(cls):
        cmd = cls.__ucs_cmd_fmt.format(cls.ssh_cmd, 'load')
        result = cls.__exec_shell(cmd, True)
        cls.__check_results(result)

    @classmethod
    def _resulting_bigip_cfg(cls, test_method):
        """Checks the resulting BIG-IP config as it stands against snap shot

        This method will raise upon discovery of a polluted config against snap
        shot.  Upon a raise, it will also:
            * restore from backup
            * Sleep 5 seconds to asssure the BIG-IP is ready for REST cmds
            * Generate a diff file against the polluted config
        """
        try:
            diff_file = cls.__collect_diff(test_method)
            os.remove(diff_file)
        except AssertionError as err:
            cls.__restore_from_backup()
            sleep(5)  # after nuke, BIG-IP needs a delay...
            raise AssertionError(
                "BIG-IP cfg was polluted by test!! (diff: {})".format(err))

    @classmethod
    def _collect_diff(cls):
        """An accessible diff collection without a frame.

        This method can force the collection of a diff at any time during the
        testing process and does not necessarily require a difference between
        snapshot and current BIG-IP config.
        """
        result = cls._get_current_bigip_cfg()
        try:
            diff_file = cls.__collect_diff(result)
        except AssertionError as err:
            diff_file = str(err)
        return diff_file

    @classmethod
    def __collect_diff(cls, test_method):
        """An internal method"""
        dirty_file = cls.dirty_file.format(my_epoch, test_method)
        session_file = cls.config_file.format(my_epoch)
        dirty_content = cls._get_current_bigip_cfg()
        with open(dirty_file, 'w') as fh:
            fh.write(dirty_content)
        diff_file = cls.diff_file.format(test_method, my_epoch)
        cmd = "diff -u {} {} > {}".format(
            session_file, dirty_file, diff_file)
        result = cls.__exec_shell(cmd, True)
        try:
            cls.__check_results(result)
        except RuntimeError:
            if os.path.getsize(diff_file) != os.path.getsize(session_file):
                raise AssertionError(diff_file)
        os.remove(dirty_file)
        return diff_file

    @classmethod
    def check_resulting_cfg(cls, test_name=None):
        """Check the current BIG-IP cfg agianst previous Reset upon Error

        This classmethod will check the current BIG-IP config and raise if
        there are any changes from the previous snap-shot.  Upon raise, the
        method will attempt to clear the BIG-IP back to the previous config

        test_name := the name of the test currently in tearDown
        """
        if test_name:
            cls.__current_test = test_name
        else:
            test_name = cls.__current_test
        if not hasattr(pytest.symbols, 'no_bigip_tracking'):
            cls._resulting_bigip_cfg(test_name)

    @classmethod
    def backup_bigip_cfg(cls, test_name):
        """Performs a config backup of the BIG-IP's configuration

        This method will store a backup of the BIG-IP's configuration on the
        BIG-IP for later restoration.
        """
        cls.__current_test = test_name
        if hasattr(pytest.symbols, 'no_bigip_tracking'):
            pass
        elif not os.path.isfile(cls.config_file.format(my_epoch)):
            cls.__exec_shell(
                cls.__ucs_cmd_fmt.format(cls.ssh_cmd, 'save'), True)
            cls._get_existing_bigip_cfg()


def begin():
    """Performs library's initial, imported setup

    This setup function will perform basic consolidation in meaning provided in
    environment variables.

    User Notes: if it is found that bigip config tracking is unnecessary and is
    reducing or impacting production time, an added 'no_bigip_tracker' variable
    can be added to the --symbols <file>.  In this instance, this library will
    not execute, and all fixtures that use this library will not perform
    bigip config tracking.

    WARNING: Nightly will perform this tracking; thus, a user does themselves
    no justice if lingering config is later discovered.  It is; therefore,
    still recommended to run this as a last step to assure proper config
    tracking is assured.
    """
    ssh_options = """ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    {}"""
    ssh_host_specific_fmt = "{}@{}"
    hostname = ''
    username = ''
    if hasattr(pytest.symbols, 'bigip_mgmt_ip_public') and \
            hasattr(pytest.symbols, 'bigip_ssh_username'):
        hostname = pytest.symbols.bigip_mgmt_ip_public
        username = pytest.symbols.bigip_ssh_username
    else:
        raise EnvironmentError("Cannot perform tests without symbols!")
    ssh_cmd = ssh_options.format(
        ssh_host_specific_fmt.format(username, hostname))
    BigIpInteraction.ssh_cmd = ssh_cmd


begin()
