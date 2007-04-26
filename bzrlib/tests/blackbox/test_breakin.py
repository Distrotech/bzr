# Copyright (C) 2006, 2007 Canonical Ltd
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""Blackbox tests for debugger breakin"""

import os
import signal
import subprocess
import sys
import time

from bzrlib.tests import TestCase, TestSkipped


class TestBreakin(TestCase):
    # FIXME: If something is broken, these tests may just hang indefinitely in
    # wait() waiting for the child to exit when it's not going to.

    def setUp(self):
        if sys.platform == 'win32':
            raise TestSkipped('breakin signal not tested on win32')
        super(TestBreakin, self).setUp()

    # port 0 means to allocate any port
    _test_process_args = ['serve', '--port', 'localhost:0']

    def test_breakin(self):
        # Break in to a debugger while bzr is running
        # we need to test against a command that will wait for 
        # a while -- bzr serve should do
        proc = self.start_bzr_subprocess(self._test_process_args,
                env_changes=dict(BZR_SIGQUIT_PDB=None))
        # wait for it to get started, and print the 'listening' line
        proc.stdout.readline()
        # first sigquit pops into debugger
        os.kill(proc.pid, signal.SIGQUIT)
        proc.stdin.write("q\n")
        time.sleep(.5)
        err = proc.stderr.readline()
        self.assertContainsRe(err, r'entering debugger')

    def test_breakin_harder(self):
        proc = self.start_bzr_subprocess(self._test_process_args,
                env_changes=dict(BZR_SIGQUIT_PDB=None))
        # wait for it to get started, and print the 'listening' line
        proc.stdout.readline()
        # another hit gives the default behaviour of terminating it
        os.kill(proc.pid, signal.SIGQUIT)
        # wait for it to go into pdb
        time.sleep(.5)
        os.kill(proc.pid, signal.SIGQUIT)
        proc.wait()

    def test_breakin_disabled(self):
        proc = self.start_bzr_subprocess(self._test_process_args,
                env_changes=dict(BZR_SIGQUIT_PDB='0'))
        # wait for it to get started, and print the 'listening' line
        proc.stdout.readline()
        # first hit should just kill it
        os.kill(proc.pid, signal.SIGQUIT)
        proc.wait()
