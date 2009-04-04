# Copyright (C) 2005, 2007, 2008, 2009 Canonical Ltd
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

"""Black-box tests for bzr dpush."""

import os

from bzrlib.branch import Branch
from bzrlib.bzrdir import BzrDirFormat
from bzrlib.foreign import ForeignBranch, ForeignRepository
from bzrlib.repository import Repository
from bzrlib.tests.blackbox import ExternalBase
from bzrlib.tests.test_foreign import DummyForeignVcsDirFormat


class TestDpush(ExternalBase):

    def setUp(self):
        BzrDirFormat.register_control_format(DummyForeignVcsDirFormat)
        self.addCleanup(self.unregister_format)
        super(TestDpush, self).setUp()

    def unregister_format(self):
        try:
            BzrDirFormat.unregister_control_format(DummyForeignVcsDirFormat)
        except ValueError:
            pass

    def test_dpush_native(self):
        target_tree = self.make_branch_and_tree("dp")
        source_tree = self.make_branch_and_tree("dc")
        error = self.run_bzr("dpush -d dc dp", retcode=3)[1]
        self.assertContainsRe(error, 'not a foreign branch, use regular push')

    def test_dpush(self):
        tree = self.make_branch_and_tree("d", format=DummyForeignVcsDirFormat())
        self.build_tree(("d/foo", "bar"))
        tree.add("foo")
        tree.commit("msg")

        dc = tree.bzrdir.sprout('dc') 
        self.build_tree(("dc/foo", "blaaaa"))
        dc.open_workingtree().commit('msg')

        self.run_bzr("dpush -d dc d")
        self.check_output("", "status dc")

    def test_dpush_new(self):
        tree = self.make_branch_and_tree("d", format=DummyForeignVcsDirFormat())

        self.build_tree(("d/foo", "bar"))
        tree.add("foo")
        tree.commit("msg") # rev 1

        dc = tree.bzrdir.sprout('dc')
        self.build_tree(("dc/foofile", "blaaaa"))
        dc_tree = dc.open_workingtree()
        dc.add("foofile")
        dc.commit("msg")

        self.run_bzr("dpush -d dc d")
        self.check_output("2\n", "revno dc")
        self.check_output("", "status dc")

    def test_dpush_wt_diff(self):
        tree = self.make_branch_and_tree("d", format=DummyForeignVcsDirFormat())
        
        self.build_tree_contents([("d/foo", "bar")])
        tree.add(["foo"])
        tree.commit("msg")

        dc = tree.bzrdir.sprout('dc')
        self.build_tree_contents([("dc/foofile", "blaaaa")])
        dc_tree = dc.open_workingtree()
        dc.add("foofile")
        dc.commit('msg')

        self.build_tree_contents([("dc/foofile", "blaaaal")])
        self.run_bzr("dpush -d dc d")
        self.assertFileEqual("blaaaal", "dc/foofile")
        self.check_output('modified:\n  foofile\n', "status dc")

    def test_diverged(self):
        tree = self.make_branch_and_tree("d", format=DummyForeignVcsDirFormat())
        
        self.build_tree(["d/foo"])
        tree.add("foo")
        tree.commit("msg")

        dc = tree.bzrdir.sprout('dc')
        dc_tree = dc.open_workingtree()

        self.build_tree_contents([("dc/foo", "bar")])
        dc.commit('msg1')

        self.build_tree_contents([("d/foo", "blie")])
        tree.commit('msg2')

        error = self.run_bzr("dpush -d dc d", retcode=3)[1]
        self.assertContainsRe(error, "have diverged")
