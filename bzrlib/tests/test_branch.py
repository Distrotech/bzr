# Copyright (C) 2005, 2006 Canonical Ltd
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

"""Tests for the Branch facility that are not interface  tests.

For interface tests see tests/branch_implementations/*.py.

For concrete class tests see this file, and for meta-branch tests
also see this file.
"""

from StringIO import StringIO

from bzrlib import branch
from bzrlib import bzrdir
import bzrlib.branch
from bzrlib.branch import (BzrBranch5, 
                           BzrBranchFormat5)
from bzrlib.bzrdir import (BzrDirMetaFormat1, BzrDirMeta1, 
                           BzrDir, BzrDirFormat)
from bzrlib.errors import (NotBranchError,
                           UnknownFormatError,
                           UnsupportedFormatError,
                           )

from bzrlib.tests import TestCase, TestCaseWithTransport
from bzrlib.transport import get_transport

class TestDefaultFormat(TestCase):

    def test_get_set_default_format(self):
        old_format = bzrlib.branch.BranchFormat.get_default_format()
        # default is 5
        self.assertTrue(isinstance(old_format, bzrlib.branch.BzrBranchFormat5))
        bzrlib.branch.BranchFormat.set_default_format(SampleBranchFormat())
        try:
            # the default branch format is used by the meta dir format
            # which is not the default bzrdir format at this point
            dir = BzrDirMetaFormat1().initialize('memory:///')
            result = dir.create_branch()
            self.assertEqual(result, 'A branch')
        finally:
            bzrlib.branch.BranchFormat.set_default_format(old_format)
        self.assertEqual(old_format, bzrlib.branch.BranchFormat.get_default_format())


class TestBranchFormat5(TestCaseWithTransport):
    """Tests specific to branch format 5"""

    def test_branch_format_5_uses_lockdir(self):
        url = self.get_url()
        bzrdir = BzrDirMetaFormat1().initialize(url)
        bzrdir.create_repository()
        branch = bzrdir.create_branch()
        t = self.get_transport()
        self.log("branch instance is %r" % branch)
        self.assert_(isinstance(branch, BzrBranch5))
        self.assertIsDirectory('.', t)
        self.assertIsDirectory('.bzr/branch', t)
        self.assertIsDirectory('.bzr/branch/lock', t)
        branch.lock_write()
        try:
            self.assertIsDirectory('.bzr/branch/lock/held', t)
        finally:
            branch.unlock()


class SampleBranchFormat(bzrlib.branch.BranchFormat):
    """A sample format

    this format is initializable, unsupported to aid in testing the 
    open and open_downlevel routines.
    """

    def get_format_string(self):
        """See BzrBranchFormat.get_format_string()."""
        return "Sample branch format."

    def initialize(self, a_bzrdir):
        """Format 4 branches cannot be created."""
        t = a_bzrdir.get_branch_transport(self)
        t.put_bytes('format', self.get_format_string())
        return 'A branch'

    def is_supported(self):
        return False

    def open(self, transport, _found=False):
        return "opened branch."


class TestBzrBranchFormat(TestCaseWithTransport):
    """Tests for the BzrBranchFormat facility."""

    def test_find_format(self):
        # is the right format object found for a branch?
        # create a branch with a few known format objects.
        # this is not quite the same as 
        self.build_tree(["foo/", "bar/"])
        def check_format(format, url):
            dir = format._matchingbzrdir.initialize(url)
            dir.create_repository()
            format.initialize(dir)
            found_format = bzrlib.branch.BranchFormat.find_format(dir)
            self.failUnless(isinstance(found_format, format.__class__))
        check_format(bzrlib.branch.BzrBranchFormat5(), "bar")
        
    def test_find_format_not_branch(self):
        dir = bzrdir.BzrDirMetaFormat1().initialize(self.get_url())
        self.assertRaises(NotBranchError,
                          bzrlib.branch.BranchFormat.find_format,
                          dir)

    def test_find_format_unknown_format(self):
        dir = bzrdir.BzrDirMetaFormat1().initialize(self.get_url())
        SampleBranchFormat().initialize(dir)
        self.assertRaises(UnknownFormatError,
                          bzrlib.branch.BranchFormat.find_format,
                          dir)

    def test_register_unregister_format(self):
        format = SampleBranchFormat()
        # make a control dir
        dir = bzrdir.BzrDirMetaFormat1().initialize(self.get_url())
        # make a branch
        format.initialize(dir)
        # register a format for it.
        bzrlib.branch.BranchFormat.register_format(format)
        # which branch.Open will refuse (not supported)
        self.assertRaises(UnsupportedFormatError, bzrlib.branch.Branch.open, self.get_url())
        # but open_downlevel will work
        self.assertEqual(format.open(dir), bzrdir.BzrDir.open(self.get_url()).open_branch(unsupported=True))
        # unregister the format
        bzrlib.branch.BranchFormat.unregister_format(format)

    def do_checkout_test(self, lightweight=False):
        tree = self.make_branch_and_tree('source', format='experimental-knit3')
        subtree = self.make_branch_and_tree('source/subtree', 
                                            format='experimental-knit3')
        subsubtree = self.make_branch_and_tree('source/subtree/subsubtree',
                                               format='experimental-knit3')
        self.build_tree(['source/subtree/file',
                         'source/subtree/subsubtree/file'])
        subsubtree.add('file')
        subtree.add('file')
        subtree.add_reference(subsubtree)
        tree.add_reference(subtree)
        tree.commit('a revision')
        subtree.commit('a subtree file')
        subsubtree.commit('a subsubtree file')
        tree.branch.create_checkout('target', lightweight=lightweight)
        self.failUnlessExists('target')
        self.failUnlessExists('target/subtree')
        self.failUnlessExists('target/subtree/file')
        self.failUnlessExists('target/subtree/subsubtree/file')
        subbranch = branch.Branch.open('target/subtree/subsubtree')
        if lightweight:
            self.assertEndsWith(subbranch.base, 'source/subtree/subsubtree/')
        else:
            self.assertEndsWith(subbranch.base, 'target/subtree/subsubtree/')


    def test_checkout_with_references(self):
        self.do_checkout_test()

    def test_light_checkout_with_references(self):
        self.do_checkout_test(lightweight=True)

class TestBranchReference(TestCaseWithTransport):
    """Tests for the branch reference facility."""

    def test_create_open_reference(self):
        bzrdirformat = bzrdir.BzrDirMetaFormat1()
        t = get_transport(self.get_url('.'))
        t.mkdir('repo')
        dir = bzrdirformat.initialize(self.get_url('repo'))
        dir.create_repository()
        target_branch = dir.create_branch()
        t.mkdir('branch')
        branch_dir = bzrdirformat.initialize(self.get_url('branch'))
        made_branch = bzrlib.branch.BranchReferenceFormat().initialize(branch_dir, target_branch)
        self.assertEqual(made_branch.base, target_branch.base)
        opened_branch = branch_dir.open_branch()
        self.assertEqual(opened_branch.base, target_branch.base)
