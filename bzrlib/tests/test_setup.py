# Copyright (C) 2005, 2006, 2008-2011 Canonical Ltd
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

"""Test for setup.py build process"""

import os
import sys
import subprocess

import bzrlib
from bzrlib import tests

# TODO: Run bzr from the installed copy to see if it works.  Really we need to
# run something that exercises every module, just starting it may not detect
# some missing modules.
#
# TODO: Check that the version numbers are in sync.  (Or avoid this...)

class TestSetup(tests.TestCaseInTempDir):

    def test_build_and_install(self):
        """ test cmd `python setup.py build`

        This tests that the build process and man generator run correctly.
        It also can catch new subdirectories that weren't added to setup.py.
        """
        # setup.py must be run from the root source directory, but the tests
        # are not necessarily invoked from there
        self.source_dir = os.path.dirname(os.path.dirname(bzrlib.__file__))
        if not os.path.isfile(os.path.join(self.source_dir, 'setup.py')):
            self.skip(
                'There is no setup.py file adjacent to the bzrlib directory')
        try:
            import distutils.sysconfig
            makefile_path = distutils.sysconfig.get_makefile_filename()
            if not os.path.exists(makefile_path):
                self.skip(
                    'You must have the python Makefile installed to run this'
                    ' test. Usually this can be found by installing'
                    ' "python-dev"')
        except ImportError:
            self.skip(
                'You must have distutils installed to run this test.'
                ' Usually this can be found by installing "python-dev"')
        self.log('test_build running from %s' % self.source_dir)
        build_dir = os.path.join(self.test_dir, "build")
        install_dir = os.path.join(self.test_dir, "install")
        self.run_setup([
            'build', '-b', build_dir,
            'install', '--root', install_dir])
        # Install layout is platform dependant
        self.assertPathExists(install_dir)
        self.run_setup(['clean', '-b', build_dir])

    def run_setup(self, args):
        args = [sys.executable, './setup.py', ] + args
        self.log('source base directory: %s', self.source_dir)
        self.log('args: %r', args)
        p = subprocess.Popen(args,
                             cwd=self.source_dir,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             )
        stdout, stderr = p.communicate()
        self.log('stdout: %r', stdout)
        self.log('stderr: %r', stderr)
        self.assertEqual(0, p.returncode,
                         'invocation of %r failed' % args)
