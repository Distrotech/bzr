# Copyright (C) 2007 Canonical Ltd
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

from bzrlib import (
    bzrdir,
    errors,
    tests,
    workingtree,
    )


class TestReconfigure(tests.TestCaseWithTransport):

    def test_no_type(self):
        branch = self.make_branch('branch')
        self.run_bzr_error(['No target configuration specified'],
                           'reconfigure branch')

    def test_branch_to_tree(self):
        branch = self.make_branch('branch')
        self.run_bzr('reconfigure --tree branch')
        tree = workingtree.WorkingTree.open('branch')

    def test_tree_to_branch(self):
        tree = self.make_branch_and_tree('tree')
        self.run_bzr('reconfigure --branch tree')
        self.assertRaises(errors.NoWorkingTree,
                          workingtree.WorkingTree.open, 'tree')

    def test_branch_to_specified_checkout(self):
        branch = self.make_branch('branch')
        parent = self.make_branch('parent')
        self.run_bzr('reconfigure branch --checkout --bind-to parent')

    def test_force(self):
        tree = self.make_branch_and_tree('tree')
        self.build_tree(['tree/file'])
        tree.add('file')
        self.run_bzr_error(['Working tree ".*" has uncommitted changes'],
                            'reconfigure --branch tree')
        self.run_bzr('reconfigure --force --branch tree')

    def test_lightweight_checkout_to_checkout(self):
        branch = self.make_branch('branch')
        checkout = branch.create_checkout('checkout', lightweight=True)
        self.run_bzr('reconfigure --checkout checkout')

    def test_no_args(self):
        branch = self.make_branch('branch')
        self.run_bzr_error(['No target configuration specified'],
                           'reconfigure', working_dir='branch')

    def test_checkout_to_lightweight_checkout(self):
        branch = self.make_branch('branch')
        checkout = branch.create_checkout('checkout')
        self.run_bzr('reconfigure --lightweight-checkout checkout')

    def test_standalone_to_use_shared(self):
        self.build_tree(['repo/'])
        tree = self.make_branch_and_tree('repo/tree')
        repo = self.make_repository('repo', shared=True)
        self.run_bzr('reconfigure --use-shared', working_dir='repo/tree')
        tree = workingtree.WorkingTree.open('repo/tree')
        self.assertNotEqual(tree.bzrdir.root_transport.base,
            tree.branch.repository.bzrdir.root_transport.base)

    def test_use_shared_to_standalone(self):
        repo = self.make_repository('repo', shared=True)
        branch = bzrdir.BzrDir.create_branch_convenience('repo/tree')
        self.assertNotEqual(branch.bzrdir.root_transport.base,
            branch.repository.bzrdir.root_transport.base)
        self.run_bzr('reconfigure --standalone', working_dir='repo/tree')
        tree = workingtree.WorkingTree.open('repo/tree')
        self.assertEqual(tree.bzrdir.root_transport.base,
            tree.branch.repository.bzrdir.root_transport.base)

    def test_make_with_trees(self):
        repo = self.make_repository('repo', shared=True)
        repo.set_make_working_trees(False)
        self.run_bzr('reconfigure --with-trees', working_dir='repo')
        self.assertIs(True, repo.make_working_trees())

    def test_make_with_trees_already_trees(self):
        repo = self.make_repository('repo', shared=True)
        repo.set_make_working_trees(True)
        self.run_bzr_error([" already creates working trees"],
                            'reconfigure --with-trees repo')

    def test_make_without_trees(self):
        repo = self.make_repository('repo', shared=True)
        repo.set_make_working_trees(True)
        self.run_bzr('reconfigure --with-no-trees', working_dir='repo')
        self.assertIs(False, repo.make_working_trees())

    def test_make_without_trees_already_no_trees(self):
        repo = self.make_repository('repo', shared=True)
        repo.set_make_working_trees(False)
        self.run_bzr_error([" already doesn't create working trees"],
                            'reconfigure --with-no-trees repo')

    def test_make_with_trees_nonshared_repo(self):
        branch = self.make_branch('branch')
        self.run_bzr_error(
            ["Requested reconfiguration of '.*' is not supported"],
            'reconfigure --with-trees branch')

    def test_make_without_trees_leaves_tree_alone(self):
        repo = self.make_repository('repo', shared=True)
        branch = bzrdir.BzrDir.create_branch_convenience('repo/branch')
        tree = workingtree.WorkingTree.open('repo/branch')
        self.build_tree(['repo/branch/foo'])
        tree.add('foo')
        self.run_bzr('reconfigure --with-no-trees --force',
            working_dir='repo/branch')
        self.failUnlessExists('repo/branch/foo')
        tree = workingtree.WorkingTree.open('repo/branch')

    def test_shared_format_to_standalone(self, format=None):
        repo = self.make_repository('repo', shared=True, format=format)
        branch = bzrdir.BzrDir.create_branch_convenience('repo/tree')
        self.assertNotEqual(branch.bzrdir.root_transport.base,
            branch.repository.bzrdir.root_transport.base)
        tree = workingtree.WorkingTree.open('repo/tree')
        self.build_tree_contents([('repo/tree/file', 'foo\n')]);
        tree.add(['file'])
        tree.commit('added file')
        self.run_bzr('reconfigure --standalone', working_dir='repo/tree')
        tree = workingtree.WorkingTree.open('repo/tree')
        self.build_tree_contents([('repo/tree/file', 'bar\n')]);
        self.check_file_contents('repo/tree/file', 'bar\n')
        self.run_bzr('revert', working_dir='repo/tree')
        self.check_file_contents('repo/tree/file', 'foo\n')
        self.assertEqual(tree.bzrdir.root_transport.base,
            tree.branch.repository.bzrdir.root_transport.base)

    def test_shared_knit_to_standalone(self):
        self.test_shared_format_to_standalone('knit')

    def test_shared_pack092_to_standalone(self):
        self.test_shared_format_to_standalone('pack-0.92')

    def test_shared_rich_root_pack_to_standalone(self):
        self.test_shared_format_to_standalone('rich-root-pack')
