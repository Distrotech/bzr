# Copyright (C) 2010 Canonical Ltd
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

"""Tests for bazaar control directories that do not support colocated branches.

Colocated branch support is optional, and when it is not supported the methods 
and attributes colocated branch support added should fail in known ways.
"""

from bzrlib import (
    errors,
    tests,
    transport,
    )
from bzrlib.tests import (
    per_controldir,
    )


class TestNoColocatedSupport(per_controldir.TestCaseWithControlDir):

    def make_bzrdir_with_repo(self):
        # a bzrdir can construct a branch and repository for itself.
        if not self.bzrdir_format.is_supported():
            # unsupported formats are not loopback testable
            # because the default open will not open them and
            # they may not be initializable.
            raise tests.TestNotApplicable('Control dir format not supported')
        t = self.get_transport()
        made_control = self.bzrdir_format.initialize(t.base)
        made_repo = made_control.create_repository()
        return made_control

    def test_destroy_colocated_branch(self):
        branch = self.make_branch('branch')
        # Colocated branches should not be supported *or* 
        # destroy_branch should not be supported at all
        self.assertRaises(
            (errors.NoColocatedBranchSupport, errors.UnsupportedOperation),
            branch.bzrdir.destroy_branch, 'colo')

    def test_create_colo_branch(self):
        made_control = self.make_bzrdir_with_repo()
        self.assertRaises(errors.NoColocatedBranchSupport, 
            made_control.create_branch, "colo")

    def test_open_branch(self):
        made_control = self.make_bzrdir_with_repo()
        self.assertRaises(errors.NoColocatedBranchSupport,
            made_control.open_branch, name="colo")

    def test_get_branch_reference(self):
        made_control = self.make_bzrdir_with_repo()
        self.assertRaises(errors.NoColocatedBranchSupport,
            made_control.get_branch_reference, "colo")
