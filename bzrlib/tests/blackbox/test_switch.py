# Copyright (C) 2007-2012 Canonical Ltd
# -*- coding: utf-8 -*-
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


"""Tests for the switch command of bzr."""

import os

from bzrlib.controldir import ControlDir
from bzrlib import (
    osutils,
    urlutils,
    branch,
    )
from bzrlib.workingtree import WorkingTree
from bzrlib.tests import (
    TestCaseWithTransport,
    script,
    )
from bzrlib.tests.features import UnicodeFilenameFeature
from bzrlib.directory_service import directories

from bzrlib.tests.matchers import ContainsNoVfsCalls


class TestSwitch(TestCaseWithTransport):

    def _create_sample_tree(self):
        tree = self.make_branch_and_tree('branch-1')
        self.build_tree(['branch-1/file-1', 'branch-1/file-2'])
        tree.add('file-1')
        tree.commit('rev1')
        tree.add('file-2')
        tree.commit('rev2')
        return tree

    def test_switch_up_to_date_light_checkout(self):
        self.make_branch_and_tree('branch')
        self.run_bzr('branch branch branch2')
        self.run_bzr('checkout --lightweight branch checkout')
        os.chdir('checkout')
        out, err = self.run_bzr('switch ../branch2')
        self.assertContainsRe(err, 'Tree is up to date at revision 0.\n')
        self.assertContainsRe(err, 'Switched to branch: .*/branch2.\n')
        self.assertEqual('', out)

    def test_switch_out_of_date_light_checkout(self):
        self.make_branch_and_tree('branch')
        self.run_bzr('branch branch branch2')
        self.build_tree(['branch2/file'])
        self.run_bzr('add branch2/file')
        self.run_bzr('commit -m add-file branch2')
        self.run_bzr('checkout --lightweight branch checkout')
        os.chdir('checkout')
        out, err = self.run_bzr('switch ../branch2')
        #self.assertContainsRe(err, '\+N  file')
        self.assertContainsRe(err, 'Updated to revision 1.\n')
        self.assertContainsRe(err, 'Switched to branch: .*/branch2.\n')
        self.assertEqual('', out)

    def _test_switch_nick(self, lightweight):
        """Check that the nick gets switched too."""
        tree1 = self.make_branch_and_tree('branch1')
        tree2 = self.make_branch_and_tree('branch2')
        tree2.pull(tree1.branch)
        checkout =  tree1.branch.create_checkout('checkout',
            lightweight=lightweight)
        self.assertEqual(checkout.branch.nick, tree1.branch.nick)
        self.assertEqual(checkout.branch.get_config().has_explicit_nickname(),
            False)
        self.run_bzr('switch branch2', working_dir='checkout')

        # we need to get the tree again, otherwise we don't get the new branch
        checkout = WorkingTree.open('checkout')
        self.assertEqual(checkout.branch.nick, tree2.branch.nick)
        self.assertEqual(checkout.branch.get_config().has_explicit_nickname(),
            False)

    def test_switch_nick(self):
        self._test_switch_nick(lightweight=False)

    def test_switch_nick_lightweight(self):
        self._test_switch_nick(lightweight=True)

    def _test_switch_explicit_nick(self, lightweight):
        """Check that the nick gets switched too."""
        tree1 = self.make_branch_and_tree('branch1')
        tree2 = self.make_branch_and_tree('branch2')
        tree2.pull(tree1.branch)
        checkout =  tree1.branch.create_checkout('checkout',
            lightweight=lightweight)
        self.assertEqual(checkout.branch.nick, tree1.branch.nick)
        checkout.branch.nick = "explicit_nick"
        self.assertEqual(checkout.branch.nick, "explicit_nick")
        self.assertEqual(checkout.branch.get_config()._get_explicit_nickname(),
            "explicit_nick")
        self.run_bzr('switch branch2', working_dir='checkout')

        # we need to get the tree again, otherwise we don't get the new branch
        checkout = WorkingTree.open('checkout')
        self.assertEqual(checkout.branch.nick, tree2.branch.nick)
        self.assertEqual(checkout.branch.get_config()._get_explicit_nickname(),
            tree2.branch.nick)

    def test_switch_explicit_nick(self):
        self._test_switch_explicit_nick(lightweight=False)

    def test_switch_explicit_nick_lightweight(self):
        self._test_switch_explicit_nick(lightweight=True)

    def test_switch_finds_relative_branch(self):
        """Switch will find 'foo' relative to the branch the checkout is of."""
        self.build_tree(['repo/'])
        tree1 = self.make_branch_and_tree('repo/brancha')
        tree1.commit('foo')
        tree2 = self.make_branch_and_tree('repo/branchb')
        tree2.pull(tree1.branch)
        branchb_id = tree2.commit('bar')
        checkout =  tree1.branch.create_checkout('checkout', lightweight=True)
        self.run_bzr(['switch', 'branchb'], working_dir='checkout')
        self.assertEqual(branchb_id, checkout.last_revision())
        checkout = checkout.bzrdir.open_workingtree()
        self.assertEqual(tree2.branch.base, checkout.branch.base)

    def test_switch_finds_relative_bound_branch(self):
        """Using switch on a heavy checkout should find master sibling

        The behaviour of lighweight and heavy checkouts should be
        consistent when using the convenient "switch to sibling" feature
        Both should switch to a sibling of the branch
        they are bound to, and not a sibling of themself"""

        self.build_tree(['repo/',
                         'heavyco/'])
        tree1 = self.make_branch_and_tree('repo/brancha')
        tree1.commit('foo')
        tree2 = self.make_branch_and_tree('repo/branchb')
        tree2.pull(tree1.branch)
        branchb_id = tree2.commit('bar')
        checkout = tree1.branch.create_checkout('heavyco/a', lightweight=False)
        self.run_bzr(['switch', 'branchb'], working_dir='heavyco/a')
        # Refresh checkout as 'switch' modified it
        checkout = checkout.bzrdir.open_workingtree()
        self.assertEqual(branchb_id, checkout.last_revision())
        self.assertEqual(tree2.branch.base,
                         checkout.branch.get_bound_location())

    def test_switch_finds_relative_unicode_branch(self):
        """Switch will find 'foo' relative to the branch the checkout is of."""
        self.requireFeature(UnicodeFilenameFeature)
        self.build_tree(['repo/'])
        tree1 = self.make_branch_and_tree('repo/brancha')
        tree1.commit('foo')
        tree2 = self.make_branch_and_tree(u'repo/branch\xe9')
        tree2.pull(tree1.branch)
        branchb_id = tree2.commit('bar')
        checkout =  tree1.branch.create_checkout('checkout', lightweight=True)
        self.run_bzr(['switch', u'branch\xe9'], working_dir='checkout')
        self.assertEqual(branchb_id, checkout.last_revision())
        checkout = checkout.bzrdir.open_workingtree()
        self.assertEqual(tree2.branch.base, checkout.branch.base)

    def test_switch_finds_relative_unicode_branch(self):
        """Switch will find 'foo' relative to the branch the checkout is of."""
        self.requireFeature(UnicodeFilenameFeature)
        self.build_tree(['repo/'])
        tree1 = self.make_branch_and_tree('repo/brancha')
        tree1.commit('foo')
        tree2 = self.make_branch_and_tree(u'repo/branch\xe9')
        tree2.pull(tree1.branch)
        branchb_id = tree2.commit('bar')
        checkout =  tree1.branch.create_checkout('checkout', lightweight=True)
        self.run_bzr(['switch', u'branch\xe9'], working_dir='checkout')
        self.assertEqual(branchb_id, checkout.last_revision())
        checkout = checkout.bzrdir.open_workingtree()
        self.assertEqual(tree2.branch.base, checkout.branch.base)

    def test_switch_revision(self):
        tree = self._create_sample_tree()
        checkout = tree.branch.create_checkout('checkout', lightweight=True)
        self.run_bzr(['switch', 'branch-1', '-r1'], working_dir='checkout')
        self.assertPathExists('checkout/file-1')
        self.assertPathDoesNotExist('checkout/file-2')

    def test_switch_into_colocated(self):
        # Create a new colocated branch from an existing non-colocated branch.
        tree = self.make_branch_and_tree('.', format='development-colo')
        self.build_tree(['file-1', 'file-2'])
        tree.add('file-1')
        revid1 = tree.commit('rev1')
        tree.add('file-2')
        revid2 = tree.commit('rev2')
        self.run_bzr(['switch', '-b', 'anotherbranch'])
        self.assertEquals(
            set(['', 'anotherbranch']),
            set(tree.branch.bzrdir.get_branches().keys()))

    def test_switch_into_unrelated_colocated(self):
        # Create a new colocated branch from an existing non-colocated branch.
        tree = self.make_branch_and_tree('.', format='development-colo')
        self.build_tree(['file-1', 'file-2'])
        tree.add('file-1')
        revid1 = tree.commit('rev1')
        tree.add('file-2')
        revid2 = tree.commit('rev2')
        tree.bzrdir.create_branch(name='foo')
        self.run_bzr_error(['Cannot switch a branch, only a checkout.'],
            'switch foo')
        self.run_bzr(['switch', '--force', 'foo'])

    def test_switch_existing_colocated(self):
        # Create a branch branch-1 that initially is a checkout of 'foo'
        # Use switch to change it to 'anotherbranch'
        repo = self.make_repository('branch-1', format='development-colo')
        target_branch = repo.bzrdir.create_branch(name='foo')
        repo.bzrdir.set_branch_reference(target_branch)
        tree = repo.bzrdir.create_workingtree()
        self.build_tree(['branch-1/file-1', 'branch-1/file-2'])
        tree.add('file-1')
        revid1 = tree.commit('rev1')
        tree.add('file-2')
        revid2 = tree.commit('rev2')
        otherbranch = tree.bzrdir.create_branch(name='anotherbranch')
        otherbranch.generate_revision_history(revid1)
        self.run_bzr(['switch', 'anotherbranch'], working_dir='branch-1')
        tree = WorkingTree.open("branch-1")
        self.assertEquals(tree.last_revision(), revid1)
        self.assertEquals(tree.branch.control_url, otherbranch.control_url)

    def test_switch_new_colocated(self):
        # Create a branch branch-1 that initially is a checkout of 'foo'
        # Use switch to create 'anotherbranch' which derives from that
        repo = self.make_repository('branch-1', format='development-colo')
        target_branch = repo.bzrdir.create_branch(name='foo')
        repo.bzrdir.set_branch_reference(target_branch)
        tree = repo.bzrdir.create_workingtree()
        self.build_tree(['branch-1/file-1', 'branch-1/file-2'])
        tree.add('file-1')
        revid1 = tree.commit('rev1')
        self.run_bzr(['switch', '-b', 'anotherbranch'], working_dir='branch-1')
        bzrdir = ControlDir.open("branch-1")
        self.assertEquals(
            set([b.name for b in bzrdir.list_branches()]),
            set(["foo", "anotherbranch"]))
        self.assertEquals(bzrdir.open_branch().name, "anotherbranch")
        self.assertEquals(bzrdir.open_branch().last_revision(), revid1)

    def test_switch_new_colocated_unicode(self):
        # Create a branch branch-1 that initially is a checkout of 'foo'
        # Use switch to create 'branch\xe9' which derives from that
        self.requireFeature(UnicodeFilenameFeature)
        repo = self.make_repository('branch-1', format='development-colo')
        target_branch = repo.bzrdir.create_branch(name='foo')
        repo.bzrdir.set_branch_reference(target_branch)
        tree = repo.bzrdir.create_workingtree()
        self.build_tree(['branch-1/file-1', 'branch-1/file-2'])
        tree.add('file-1')
        revid1 = tree.commit('rev1')
        self.run_bzr(['switch', '-b', u'branch\xe9'], working_dir='branch-1')
        bzrdir = ControlDir.open("branch-1")
        self.assertEquals(
            set([b.name for b in bzrdir.list_branches()]),
            set(["foo", u"branch\xe9"]))
        self.assertEquals(bzrdir.open_branch().name, u"branch\xe9")
        self.assertEquals(bzrdir.open_branch().last_revision(), revid1)

    def test_switch_only_revision(self):
        tree = self._create_sample_tree()
        checkout = tree.branch.create_checkout('checkout', lightweight=True)
        self.assertPathExists('checkout/file-1')
        self.assertPathExists('checkout/file-2')
        self.run_bzr(['switch', '-r1'], working_dir='checkout')
        self.assertPathExists('checkout/file-1')
        self.assertPathDoesNotExist('checkout/file-2')
        # Check that we don't accept a range
        self.run_bzr_error(
            ['bzr switch --revision takes exactly one revision identifier'],
            ['switch', '-r0..2'], working_dir='checkout')

    def prepare_lightweight_switch(self):
        branch = self.make_branch('branch')
        branch.create_checkout('tree', lightweight=True)
        osutils.rename('branch', 'branch1')

    def test_switch_lightweight_after_branch_moved(self):
        self.prepare_lightweight_switch()
        self.run_bzr('switch --force ../branch1', working_dir='tree')
        branch_location = WorkingTree.open('tree').branch.base
        self.assertEndsWith(branch_location, 'branch1/')

    def test_switch_lightweight_after_branch_moved_relative(self):
        self.prepare_lightweight_switch()
        self.run_bzr('switch --force branch1', working_dir='tree')
        branch_location = WorkingTree.open('tree').branch.base
        self.assertEndsWith(branch_location, 'branch1/')

    def test_create_branch_no_branch(self):
        self.prepare_lightweight_switch()
        self.run_bzr_error(['cannot create branch without source branch'],
            'switch --create-branch ../branch2', working_dir='tree')

    def test_create_branch(self):
        branch = self.make_branch('branch')
        tree = branch.create_checkout('tree', lightweight=True)
        tree.commit('one', rev_id='rev-1')
        self.run_bzr('switch --create-branch ../branch2', working_dir='tree')
        tree = WorkingTree.open('tree')
        self.assertEndsWith(tree.branch.base, '/branch2/')

    def test_create_branch_local(self):
        branch = self.make_branch('branch')
        tree = branch.create_checkout('tree', lightweight=True)
        tree.commit('one', rev_id='rev-1')
        self.run_bzr('switch --create-branch branch2', working_dir='tree')
        tree = WorkingTree.open('tree')
        # The new branch should have been created at the same level as
        # 'branch', because we did not have a '/' segment
        self.assertEqual(branch.base[:-1] + '2/', tree.branch.base)

    def test_create_branch_short_name(self):
        branch = self.make_branch('branch')
        tree = branch.create_checkout('tree', lightweight=True)
        tree.commit('one', rev_id='rev-1')
        self.run_bzr('switch -b branch2', working_dir='tree')
        tree = WorkingTree.open('tree')
        # The new branch should have been created at the same level as
        # 'branch', because we did not have a '/' segment
        self.assertEqual(branch.base[:-1] + '2/', tree.branch.base)

    def test_create_branch_directory_services(self):
        branch = self.make_branch('branch')
        tree = branch.create_checkout('tree', lightweight=True)
        class FooLookup(object):
            def look_up(self, name, url):
                return 'foo-'+name
        directories.register('foo:', FooLookup, 'Create branches named foo-')
        self.addCleanup(directories.remove, 'foo:')
        self.run_bzr('switch -b foo:branch2', working_dir='tree')
        tree = WorkingTree.open('tree')
        self.assertEndsWith(tree.branch.base, 'foo-branch2/')

    def test_switch_with_post_switch_hook(self):
        from bzrlib import branch as _mod_branch
        calls = []
        _mod_branch.Branch.hooks.install_named_hook('post_switch',
            calls.append, None)
        self.make_branch_and_tree('branch')
        self.run_bzr('branch branch branch2')
        self.run_bzr('checkout branch checkout')
        os.chdir('checkout')
        self.assertLength(0, calls)
        out, err = self.run_bzr('switch ../branch2')
        self.assertLength(1, calls)

    def test_switch_lightweight_co_with_post_switch_hook(self):
        from bzrlib import branch as _mod_branch
        calls = []
        _mod_branch.Branch.hooks.install_named_hook('post_switch',
            calls.append, None)
        self.make_branch_and_tree('branch')
        self.run_bzr('branch branch branch2')
        self.run_bzr('checkout --lightweight branch checkout')
        os.chdir('checkout')
        self.assertLength(0, calls)
        out, err = self.run_bzr('switch ../branch2')
        self.assertLength(1, calls)

    def test_switch_lightweight_directory(self):
        """Test --directory option"""

        # create a source branch
        a_tree = self.make_branch_and_tree('a')
        self.build_tree_contents([('a/a', 'initial\n')])
        a_tree.add('a')
        a_tree.commit(message='initial')

        # clone and add a differing revision
        b_tree = a_tree.bzrdir.sprout('b').open_workingtree()
        self.build_tree_contents([('b/a', 'initial\nmore\n')])
        b_tree.commit(message='more')

        self.run_bzr('checkout --lightweight a checkout')
        self.run_bzr('switch --directory checkout b')
        self.assertFileEqual('initial\nmore\n', 'checkout/a')


class TestSwitchParentLocationBase(TestCaseWithTransport):

    def setUp(self):
        """Set up a repository and branch ready for testing."""
        super(TestSwitchParentLocationBase, self).setUp()
        self.script_runner = script.ScriptRunner()
        self.script_runner.run_script(self, '''
                $ bzr init-repo --no-trees repo
                Shared repository...
                Location:
                  shared repository: repo
                $ bzr init repo/trunk
                Created a repository branch...
                Using shared repository: ...
                ''')

    def assertParent(self, expected_parent, branch):
        """Verify that the parent is not None and is set correctly."""
        actual_parent = branch.get_parent()
        self.assertIsSameRealPath(urlutils.local_path_to_url(expected_parent),
                                  branch.get_parent())


class TestSwitchParentLocation(TestSwitchParentLocationBase):

    def _checkout_and_switch(self, option=''):
        self.script_runner.run_script(self, '''
                $ bzr checkout %(option)s repo/trunk checkout
                $ cd checkout
                $ bzr switch --create-branch switched
                2>Tree is up to date at revision 0.
                2>Switched to branch:...switched...
                $ cd ..
                ''' % locals())
        bound_branch = branch.Branch.open_containing('checkout')[0]
        master_branch = branch.Branch.open_containing('repo/switched')[0]
        return (bound_branch, master_branch)

    def test_switch_parent_lightweight(self):
        """Lightweight checkout using bzr switch."""
        bb, mb = self._checkout_and_switch(option='--lightweight')
        self.assertParent('repo/trunk', bb)
        self.assertParent('repo/trunk', mb)

    def test_switch_parent_heavyweight(self):
        """Heavyweight checkout using bzr switch."""
        bb, mb = self._checkout_and_switch()
        self.assertParent('repo/trunk', bb)
        self.assertParent('repo/trunk', mb)


class TestSwitchDoesntOpenMasterBranch(TestCaseWithTransport):
    # See https://bugs.launchpad.net/bzr/+bug/812285
    # "bzr switch --create-branch" can point the new branch's parent to the
    # master branch, but it doesn't have to open it to do so.

    def test_switch_create_doesnt_open_master_branch(self):
        master = self.make_branch_and_tree('master')
        master.commit('one')
        # Note: not a lightweight checkout
        checkout = master.branch.create_checkout('checkout')
        opened = []
        def open_hook(branch):
            # Just append the final directory of the branch
            name = branch.base.rstrip('/').rsplit('/', 1)[1]
            opened.append(name)
        branch.Branch.hooks.install_named_hook('open', open_hook,
                                               'open_hook_logger')
        self.run_bzr('switch --create-branch -d checkout feature')
        # We only open the master branch 1 time.
        # This test should be cleaner to write, but see bug:
        #  https://bugs.launchpad.net/bzr/+bug/812295
        self.assertEqual(1, opened.count('master'))


class TestSmartServerSwitch(TestCaseWithTransport):

    def test_switch_lightweight(self):
        self.setup_smart_server_with_call_log()
        t = self.make_branch_and_tree('from')
        for count in range(9):
            t.commit(message='commit %d' % count)
        out, err = self.run_bzr(['checkout', '--lightweight', self.get_url('from'),
            'target'])
        self.reset_smart_call_log()
        self.run_bzr(['switch', self.get_url('from')], working_dir='target')
        # This figure represent the amount of work to perform this use case. It
        # is entirely ok to reduce this number if a test fails due to rpc_count
        # being too low. If rpc_count increases, more network roundtrips have
        # become necessary for this use case. Please do not adjust this number
        # upwards without agreement from bzr's network support maintainers.
        self.assertLength(24, self.hpss_calls)
        self.assertLength(4, self.hpss_connections)
        self.assertThat(self.hpss_calls, ContainsNoVfsCalls)
