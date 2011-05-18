# Copyright (C) 2007-2011 Canonical Ltd
# Authors:  Robert Collins <robert.collins@canonical.com>
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

"""Tests for WorkingTreeFormat4"""

import os
import time

from bzrlib import (
    bzrdir,
    dirstate,
    errors,
    inventory,
    osutils,
    workingtree_4,
    )
from bzrlib.lockdir import LockDir
from bzrlib.tests import TestCaseWithTransport, TestSkipped
from bzrlib.tree import InterTree


class TestWorkingTreeFormat4(TestCaseWithTransport):
    """Tests specific to WorkingTreeFormat4."""

    def test_disk_layout(self):
        control = bzrdir.BzrDirMetaFormat1().initialize(self.get_url())
        control.create_repository()
        control.create_branch()
        tree = workingtree_4.WorkingTreeFormat4().initialize(control)
        # we want:
        # format 'Bazaar Working Tree format 4'
        # stat-cache = ??
        t = control.get_workingtree_transport(None)
        self.assertEqualDiff('Bazaar Working Tree Format 4 (bzr 0.15)\n',
                             t.get('format').read())
        self.assertFalse(t.has('inventory.basis'))
        # no last-revision file means 'None' or 'NULLREVISION'
        self.assertFalse(t.has('last-revision'))
        state = dirstate.DirState.on_file(t.local_abspath('dirstate'))
        state.lock_read()
        try:
            self.assertEqual([], state.get_parent_ids())
        finally:
            state.unlock()

    def test_uses_lockdir(self):
        """WorkingTreeFormat4 uses its own LockDir:

            - lock is a directory
            - when the WorkingTree is locked, LockDir can see that
        """
        # this test could be factored into a subclass of tests common to both
        # format 3 and 4, but for now its not much of an issue as there is only
        # one in common.
        t = self.get_transport()
        tree = self.make_workingtree()
        self.assertIsDirectory('.bzr', t)
        self.assertIsDirectory('.bzr/checkout', t)
        self.assertIsDirectory('.bzr/checkout/lock', t)
        our_lock = LockDir(t, '.bzr/checkout/lock')
        self.assertEquals(our_lock.peek(), None)
        tree.lock_write()
        self.assertTrue(our_lock.peek())
        tree.unlock()
        self.assertEquals(our_lock.peek(), None)

    def make_workingtree(self, relpath=''):
        url = self.get_url(relpath)
        if relpath:
            self.build_tree([relpath + '/'])
        dir = bzrdir.BzrDirMetaFormat1().initialize(url)
        repo = dir.create_repository()
        branch = dir.create_branch()
        try:
            return workingtree_4.WorkingTreeFormat4().initialize(dir)
        except errors.NotLocalUrl:
            raise TestSkipped('Not a local URL')

    def test_dirstate_stores_all_parent_inventories(self):
        tree = self.make_workingtree()

        # We're going to build in tree a working tree
        # with three parent trees, with some files in common.

        # We really don't want to do commit or merge in the new dirstate-based
        # tree, because that might not work yet.  So instead we build
        # revisions elsewhere and pull them across, doing by hand part of the
        # work that merge would do.

        subtree = self.make_branch_and_tree('subdir')
        # writelock the tree so its repository doesn't get readlocked by
        # the revision tree locks. This works around the bug where we dont
        # permit lock upgrading.
        subtree.lock_write()
        self.addCleanup(subtree.unlock)
        self.build_tree(['subdir/file-a',])
        subtree.add(['file-a'], ['id-a'])
        rev1 = subtree.commit('commit in subdir')

        subtree2 = subtree.bzrdir.sprout('subdir2').open_workingtree()
        self.build_tree(['subdir2/file-b'])
        subtree2.add(['file-b'], ['id-b'])
        rev2 = subtree2.commit('commit in subdir2')

        subtree.flush()
        subtree3 = subtree.bzrdir.sprout('subdir3').open_workingtree()
        rev3 = subtree3.commit('merge from subdir2')

        repo = tree.branch.repository
        repo.fetch(subtree.branch.repository, rev1)
        repo.fetch(subtree2.branch.repository, rev2)
        repo.fetch(subtree3.branch.repository, rev3)
        # will also pull the others...

        # create repository based revision trees
        rev1_revtree = repo.revision_tree(rev1)
        rev2_revtree = repo.revision_tree(rev2)
        rev3_revtree = repo.revision_tree(rev3)
        # tree doesn't contain a text merge yet but we'll just
        # set the parents as if a merge had taken place.
        # this should cause the tree data to be folded into the
        # dirstate.
        tree.set_parent_trees([
            (rev1, rev1_revtree),
            (rev2, rev2_revtree),
            (rev3, rev3_revtree), ])

        # create tree-sourced revision trees
        rev1_tree = tree.revision_tree(rev1)
        rev1_tree.lock_read()
        self.addCleanup(rev1_tree.unlock)
        rev2_tree = tree.revision_tree(rev2)
        rev2_tree.lock_read()
        self.addCleanup(rev2_tree.unlock)
        rev3_tree = tree.revision_tree(rev3)
        rev3_tree.lock_read()
        self.addCleanup(rev3_tree.unlock)

        # now we should be able to get them back out
        self.assertTreesEqual(rev1_revtree, rev1_tree)
        self.assertTreesEqual(rev2_revtree, rev2_tree)
        self.assertTreesEqual(rev3_revtree, rev3_tree)

    def test_dirstate_doesnt_read_parents_from_repo_when_setting(self):
        """Setting parent trees on a dirstate working tree takes
        the trees it's given and doesn't need to read them from the
        repository.
        """
        tree = self.make_workingtree()

        subtree = self.make_branch_and_tree('subdir')
        rev1 = subtree.commit('commit in subdir')
        rev1_tree = subtree.basis_tree()
        rev1_tree.lock_read()
        self.addCleanup(rev1_tree.unlock)

        tree.branch.pull(subtree.branch)

        # break the repository's legs to make sure it only uses the trees
        # it's given; any calls to forbidden methods will raise an
        # AssertionError
        repo = tree.branch.repository
        self.overrideAttr(repo, "get_revision", self.fail)
        self.overrideAttr(repo, "get_inventory", self.fail)
        self.overrideAttr(repo, "_get_inventory_xml", self.fail)
        # try to set the parent trees.
        tree.set_parent_trees([(rev1, rev1_tree)])

    def test_dirstate_doesnt_read_from_repo_when_returning_cache_tree(self):
        """Getting parent trees from a dirstate tree does not read from the
        repos inventory store. This is an important part of the dirstate
        performance optimisation work.
        """
        tree = self.make_workingtree()

        subtree = self.make_branch_and_tree('subdir')
        # writelock the tree so its repository doesn't get readlocked by
        # the revision tree locks. This works around the bug where we dont
        # permit lock upgrading.
        subtree.lock_write()
        self.addCleanup(subtree.unlock)
        rev1 = subtree.commit('commit in subdir')
        rev1_tree = subtree.basis_tree()
        rev1_tree.lock_read()
        rev1_tree.inventory
        self.addCleanup(rev1_tree.unlock)
        rev2 = subtree.commit('second commit in subdir', allow_pointless=True)
        rev2_tree = subtree.basis_tree()
        rev2_tree.lock_read()
        rev2_tree.inventory
        self.addCleanup(rev2_tree.unlock)

        tree.branch.pull(subtree.branch)

        # break the repository's legs to make sure it only uses the trees
        # it's given; any calls to forbidden methods will raise an
        # AssertionError
        repo = tree.branch.repository
        # dont uncomment this: the revision object must be accessed to
        # answer 'get_parent_ids' for the revision tree- dirstate does not
        # cache the parents of a parent tree at this point.
        #repo.get_revision = self.fail
        self.overrideAttr(repo, "get_inventory", self.fail)
        self.overrideAttr(repo, "_get_inventory_xml", self.fail)
        # set the parent trees.
        tree.set_parent_trees([(rev1, rev1_tree), (rev2, rev2_tree)])
        # read the first tree
        result_rev1_tree = tree.revision_tree(rev1)
        # read the second
        result_rev2_tree = tree.revision_tree(rev2)
        # compare - there should be no differences between the handed and
        # returned trees
        self.assertTreesEqual(rev1_tree, result_rev1_tree)
        self.assertTreesEqual(rev2_tree, result_rev2_tree)

    def test_dirstate_doesnt_cache_non_parent_trees(self):
        """Getting parent trees from a dirstate tree does not read from the
        repos inventory store. This is an important part of the dirstate
        performance optimisation work.
        """
        tree = self.make_workingtree()

        # make a tree that we can try for, which is able to be returned but
        # must not be
        subtree = self.make_branch_and_tree('subdir')
        rev1 = subtree.commit('commit in subdir')
        tree.branch.pull(subtree.branch)
        # check it fails
        self.assertRaises(errors.NoSuchRevision, tree.revision_tree, rev1)

    def test_no_dirstate_outside_lock(self):
        # temporary test until the code is mature enough to test from outside.
        """Getting a dirstate object fails if there is no lock."""
        def lock_and_call_current_dirstate(tree, lock_method):
            getattr(tree, lock_method)()
            tree.current_dirstate()
            tree.unlock()
        tree = self.make_workingtree()
        self.assertRaises(errors.ObjectNotLocked, tree.current_dirstate)
        lock_and_call_current_dirstate(tree, 'lock_read')
        self.assertRaises(errors.ObjectNotLocked, tree.current_dirstate)
        lock_and_call_current_dirstate(tree, 'lock_write')
        self.assertRaises(errors.ObjectNotLocked, tree.current_dirstate)
        lock_and_call_current_dirstate(tree, 'lock_tree_write')
        self.assertRaises(errors.ObjectNotLocked, tree.current_dirstate)

    def test_new_dirstate_on_new_lock(self):
        # until we have detection for when a dirstate can be reused, we
        # want to reparse dirstate on every new lock.
        known_dirstates = set()
        def lock_and_compare_all_current_dirstate(tree, lock_method):
            getattr(tree, lock_method)()
            state = tree.current_dirstate()
            self.assertFalse(state in known_dirstates)
            known_dirstates.add(state)
            tree.unlock()
        tree = self.make_workingtree()
        # lock twice with each type to prevent silly per-lock-type bugs.
        # each lock and compare looks for a unique state object.
        lock_and_compare_all_current_dirstate(tree, 'lock_read')
        lock_and_compare_all_current_dirstate(tree, 'lock_read')
        lock_and_compare_all_current_dirstate(tree, 'lock_tree_write')
        lock_and_compare_all_current_dirstate(tree, 'lock_tree_write')
        lock_and_compare_all_current_dirstate(tree, 'lock_write')
        lock_and_compare_all_current_dirstate(tree, 'lock_write')

    def test_constructing_invalid_interdirstate_raises(self):
        tree = self.make_workingtree()
        rev_id = tree.commit('first post')
        rev_id2 = tree.commit('second post')
        rev_tree = tree.branch.repository.revision_tree(rev_id)
        # Exception is not a great thing to raise, but this test is
        # very short, and code is used to sanity check other tests, so
        # a full error object is YAGNI.
        self.assertRaises(
            Exception, workingtree_4.InterDirStateTree, rev_tree, tree)
        self.assertRaises(
            Exception, workingtree_4.InterDirStateTree, tree, rev_tree)

    def test_revtree_to_revtree_not_interdirstate(self):
        # we should not get a dirstate optimiser for two repository sourced
        # revtrees. we can't prove a negative, so we dont do exhaustive tests
        # of all formats; though that could be written in the future it doesn't
        # seem well worth it.
        tree = self.make_workingtree()
        rev_id = tree.commit('first post')
        rev_id2 = tree.commit('second post')
        rev_tree = tree.branch.repository.revision_tree(rev_id)
        rev_tree2 = tree.branch.repository.revision_tree(rev_id2)
        optimiser = InterTree.get(rev_tree, rev_tree2)
        self.assertIsInstance(optimiser, InterTree)
        self.assertFalse(isinstance(optimiser, workingtree_4.InterDirStateTree))
        optimiser = InterTree.get(rev_tree2, rev_tree)
        self.assertIsInstance(optimiser, InterTree)
        self.assertFalse(isinstance(optimiser, workingtree_4.InterDirStateTree))

    def test_revtree_not_in_dirstate_to_dirstate_not_interdirstate(self):
        # we should not get a dirstate optimiser when the revision id for of
        # the source is not in the dirstate of the target.
        tree = self.make_workingtree()
        rev_id = tree.commit('first post')
        rev_id2 = tree.commit('second post')
        rev_tree = tree.branch.repository.revision_tree(rev_id)
        tree.lock_read()
        optimiser = InterTree.get(rev_tree, tree)
        self.assertIsInstance(optimiser, InterTree)
        self.assertFalse(isinstance(optimiser, workingtree_4.InterDirStateTree))
        optimiser = InterTree.get(tree, rev_tree)
        self.assertIsInstance(optimiser, InterTree)
        self.assertFalse(isinstance(optimiser, workingtree_4.InterDirStateTree))
        tree.unlock()

    def test_empty_basis_to_dirstate_tree(self):
        # we should get a InterDirStateTree for doing
        # 'changes_from' from the first basis dirstate revision tree to a
        # WorkingTree4.
        tree = self.make_workingtree()
        tree.lock_read()
        basis_tree = tree.basis_tree()
        basis_tree.lock_read()
        optimiser = InterTree.get(basis_tree, tree)
        tree.unlock()
        basis_tree.unlock()
        self.assertIsInstance(optimiser, workingtree_4.InterDirStateTree)

    def test_nonempty_basis_to_dirstate_tree(self):
        # we should get a InterDirStateTree for doing
        # 'changes_from' from a non-null basis dirstate revision tree to a
        # WorkingTree4.
        tree = self.make_workingtree()
        tree.commit('first post')
        tree.lock_read()
        basis_tree = tree.basis_tree()
        basis_tree.lock_read()
        optimiser = InterTree.get(basis_tree, tree)
        tree.unlock()
        basis_tree.unlock()
        self.assertIsInstance(optimiser, workingtree_4.InterDirStateTree)

    def test_empty_basis_revtree_to_dirstate_tree(self):
        # we should get a InterDirStateTree for doing
        # 'changes_from' from an empty repository based rev tree to a
        # WorkingTree4.
        tree = self.make_workingtree()
        tree.lock_read()
        basis_tree = tree.branch.repository.revision_tree(tree.last_revision())
        basis_tree.lock_read()
        optimiser = InterTree.get(basis_tree, tree)
        tree.unlock()
        basis_tree.unlock()
        self.assertIsInstance(optimiser, workingtree_4.InterDirStateTree)

    def test_nonempty_basis_revtree_to_dirstate_tree(self):
        # we should get a InterDirStateTree for doing
        # 'changes_from' from a non-null repository based rev tree to a
        # WorkingTree4.
        tree = self.make_workingtree()
        tree.commit('first post')
        tree.lock_read()
        basis_tree = tree.branch.repository.revision_tree(tree.last_revision())
        basis_tree.lock_read()
        optimiser = InterTree.get(basis_tree, tree)
        tree.unlock()
        basis_tree.unlock()
        self.assertIsInstance(optimiser, workingtree_4.InterDirStateTree)

    def test_tree_to_basis_in_other_tree(self):
        # we should get a InterDirStateTree when
        # the source revid is in the dirstate object of the target and
        # the dirstates are different. This is largely covered by testing
        # with repository revtrees, so is just for extra confidence.
        tree = self.make_workingtree('a')
        tree.commit('first post')
        tree2 = self.make_workingtree('b')
        tree2.pull(tree.branch)
        basis_tree = tree.basis_tree()
        tree2.lock_read()
        basis_tree.lock_read()
        optimiser = InterTree.get(basis_tree, tree2)
        tree2.unlock()
        basis_tree.unlock()
        self.assertIsInstance(optimiser, workingtree_4.InterDirStateTree)

    def test_merged_revtree_to_tree(self):
        # we should get a InterDirStateTree when
        # the source tree is a merged tree present in the dirstate of target.
        tree = self.make_workingtree('a')
        tree.commit('first post')
        tree.commit('tree 1 commit 2')
        tree2 = self.make_workingtree('b')
        tree2.pull(tree.branch)
        tree2.commit('tree 2 commit 2')
        tree.merge_from_branch(tree2.branch)
        second_parent_tree = tree.revision_tree(tree.get_parent_ids()[1])
        second_parent_tree.lock_read()
        tree.lock_read()
        optimiser = InterTree.get(second_parent_tree, tree)
        tree.unlock()
        second_parent_tree.unlock()
        self.assertIsInstance(optimiser, workingtree_4.InterDirStateTree)

    def test_id2path(self):
        tree = self.make_workingtree('tree')
        self.build_tree(['tree/a', 'tree/b'])
        tree.add(['a'], ['a-id'])
        self.assertEqual(u'a', tree.id2path('a-id'))
        self.assertRaises(errors.NoSuchId, tree.id2path, 'a')
        tree.commit('a')
        tree.add(['b'], ['b-id'])

        try:
            new_path = u'b\u03bcrry'
            tree.rename_one('a', new_path)
        except UnicodeEncodeError:
            # support running the test on non-unicode platforms
            new_path = 'c'
            tree.rename_one('a', new_path)
        self.assertEqual(new_path, tree.id2path('a-id'))
        tree.commit(u'b\xb5rry')
        tree.unversion(['a-id'])
        self.assertRaises(errors.NoSuchId, tree.id2path, 'a-id')
        self.assertEqual('b', tree.id2path('b-id'))
        self.assertRaises(errors.NoSuchId, tree.id2path, 'c-id')

    def test_unique_root_id_per_tree(self):
        # each time you initialize a new tree, it gets a different root id
        format_name = 'dirstate-with-subtree'
        tree1 = self.make_branch_and_tree('tree1',
            format=format_name)
        tree2 = self.make_branch_and_tree('tree2',
            format=format_name)
        self.assertNotEqual(tree1.get_root_id(), tree2.get_root_id())
        # when you branch, it inherits the same root id
        rev1 = tree1.commit('first post')
        tree3 = tree1.bzrdir.sprout('tree3').open_workingtree()
        self.assertEqual(tree3.get_root_id(), tree1.get_root_id())

    def test_set_root_id(self):
        # similar to some code that fails in the dirstate-plus-subtree branch
        # -- setting the root id while adding a parent seems to scramble the
        # dirstate invariants. -- mbp 20070303
        def validate():
            wt.lock_read()
            try:
                wt.current_dirstate()._validate()
            finally:
                wt.unlock()
        wt = self.make_workingtree('tree')
        wt.set_root_id('TREE-ROOTID')
        validate()
        wt.commit('somenthing')
        validate()
        # now switch and commit again
        wt.set_root_id('tree-rootid')
        validate()
        wt.commit('again')
        validate()

    def test_default_root_id(self):
        tree = self.make_branch_and_tree('tag', format='dirstate-tags')
        self.assertEqual(inventory.ROOT_ID, tree.get_root_id())
        tree = self.make_branch_and_tree('subtree',
                                         format='dirstate-with-subtree')
        self.assertNotEqual(inventory.ROOT_ID, tree.get_root_id())

    def test_non_subtree_with_nested_trees(self):
        # prior to dirstate, st/diff/commit ignored nested trees.
        # dirstate, as opposed to dirstate-with-subtree, should
        # behave the same way.
        tree = self.make_branch_and_tree('.', format='dirstate')
        self.assertFalse(tree.supports_tree_reference())
        self.build_tree(['dir/'])
        # for testing easily.
        tree.set_root_id('root')
        tree.add(['dir'], ['dir-id'])
        subtree = self.make_branch_and_tree('dir')
        # the most primitive operation: kind
        self.assertEqual('directory', tree.kind('dir-id'))
        # a diff against the basis should give us a directory and the root (as
        # the root is new too).
        tree.lock_read()
        expected = [('dir-id',
            (None, u'dir'),
            True,
            (False, True),
            (None, 'root'),
            (None, u'dir'),
            (None, 'directory'),
            (None, False)),
            ('root', (None, u''), True, (False, True), (None, None),
            (None, u''), (None, 'directory'), (None, 0))]
        self.assertEqual(expected, list(tree.iter_changes(tree.basis_tree(),
            specific_files=['dir'])))
        tree.unlock()
        # do a commit, we want to trigger the dirstate fast-path too
        tree.commit('first post')
        # change the path for the subdir, which will trigger getting all
        # its data:
        os.rename('dir', 'also-dir')
        # now the diff will use the fast path
        tree.lock_read()
        expected = [('dir-id',
            (u'dir', u'dir'),
            True,
            (True, True),
            ('root', 'root'),
            ('dir', 'dir'),
            ('directory', None),
            (False, False))]
        self.assertEqual(expected, list(tree.iter_changes(tree.basis_tree())))
        tree.unlock()

    def test_with_subtree_supports_tree_references(self):
        # dirstate-with-subtree should support tree-references.
        tree = self.make_branch_and_tree('.', format='dirstate-with-subtree')
        self.assertTrue(tree.supports_tree_reference())
        # having checked this is on, the tree interface, and intertree
        # interface tests, will proceed to test the subtree support of
        # workingtree_4.

    def test_iter_changes_ignores_unversioned_dirs(self):
        """iter_changes should not descend into unversioned directories."""
        tree = self.make_branch_and_tree('.', format='dirstate')
        # We have an unversioned directory at the root, a versioned one with
        # other versioned files and an unversioned directory, and another
        # versioned dir with nothing but an unversioned directory.
        self.build_tree(['unversioned/',
                         'unversioned/a',
                         'unversioned/b/',
                         'versioned/',
                         'versioned/unversioned/',
                         'versioned/unversioned/a',
                         'versioned/unversioned/b/',
                         'versioned2/',
                         'versioned2/a',
                         'versioned2/unversioned/',
                         'versioned2/unversioned/a',
                         'versioned2/unversioned/b/',
                        ])
        tree.add(['versioned', 'versioned2', 'versioned2/a'])
        tree.commit('one', rev_id='rev-1')
        # Trap osutils._walkdirs_utf8 to spy on what dirs have been accessed.
        returned = []
        def walkdirs_spy(*args, **kwargs):
            for val in orig(*args, **kwargs):
                returned.append(val[0][0])
                yield val
        orig = self.overrideAttr(osutils, '_walkdirs_utf8', walkdirs_spy)

        basis = tree.basis_tree()
        tree.lock_read()
        self.addCleanup(tree.unlock)
        basis.lock_read()
        self.addCleanup(basis.unlock)
        changes = [c[1] for c in
                   tree.iter_changes(basis, want_unversioned=True)]
        self.assertEqual([(None, 'unversioned'),
                          (None, 'versioned/unversioned'),
                          (None, 'versioned2/unversioned'),
                         ], changes)
        self.assertEqual(['', 'versioned', 'versioned2'], returned)
        del returned[:] # reset
        changes = [c[1] for c in tree.iter_changes(basis)]
        self.assertEqual([], changes)
        self.assertEqual(['', 'versioned', 'versioned2'], returned)

    def test_iter_changes_unversioned_error(self):
        """ Check if a PathsNotVersionedError is correctly raised and the
            paths list contains all unversioned entries only.
        """
        tree = self.make_branch_and_tree('tree')
        self.build_tree_contents([('tree/bar', '')])
        tree.add(['bar'], ['bar-id'])
        tree.lock_read()
        self.addCleanup(tree.unlock)
        tree_iter_changes = lambda files: [
            c for c in tree.iter_changes(tree.basis_tree(), specific_files=files,
                                         require_versioned=True)
        ]
        e = self.assertRaises(errors.PathsNotVersionedError,
                              tree_iter_changes, ['bar', 'foo'])
        self.assertEqual(e.paths, ['foo'])

    def get_tree_with_cachable_file_foo(self):
        tree = self.make_branch_and_tree('.')
        tree.lock_write()
        self.addCleanup(tree.unlock)
        self.build_tree_contents([('foo', 'a bit of content for foo\n')])
        tree.add(['foo'], ['foo-id'])
        tree.current_dirstate()._cutoff_time = time.time() + 60
        return tree

    def test_commit_updates_hash_cache(self):
        tree = self.get_tree_with_cachable_file_foo()
        revid = tree.commit('a commit')
        # tree's dirstate should now have a valid stat entry for foo.
        entry = tree._get_entry(path='foo')
        expected_sha1 = osutils.sha_file_by_name('foo')
        self.assertEqual(expected_sha1, entry[1][0][1])
        self.assertEqual(len('a bit of content for foo\n'), entry[1][0][2])

    def test_observed_sha1_cachable(self):
        tree = self.get_tree_with_cachable_file_foo()
        expected_sha1 = osutils.sha_file_by_name('foo')
        statvalue = os.lstat("foo")
        tree._observed_sha1("foo-id", "foo", (expected_sha1, statvalue))
        entry = tree._get_entry(path="foo")
        entry_state = entry[1][0]
        self.assertEqual(expected_sha1, entry_state[1])
        self.assertEqual(statvalue.st_size, entry_state[2])
        tree.unlock()
        tree.lock_read()
        tree = tree.bzrdir.open_workingtree()
        tree.lock_read()
        self.addCleanup(tree.unlock)
        entry = tree._get_entry(path="foo")
        entry_state = entry[1][0]
        self.assertEqual(expected_sha1, entry_state[1])
        self.assertEqual(statvalue.st_size, entry_state[2])

    def test_observed_sha1_new_file(self):
        tree = self.make_branch_and_tree('.')
        self.build_tree(['foo'])
        tree.add(['foo'], ['foo-id'])
        tree.lock_read()
        try:
            current_sha1 = tree._get_entry(path="foo")[1][0][1]
        finally:
            tree.unlock()
        tree.lock_write()
        try:
            tree._observed_sha1("foo-id", "foo",
                (osutils.sha_file_by_name('foo'), os.lstat("foo")))
            # Must not have changed
            self.assertEqual(current_sha1,
                tree._get_entry(path="foo")[1][0][1])
        finally:
            tree.unlock()

    def test_get_file_with_stat_id_only(self):
        # Explicit test to ensure we get a lstat value from WT4 trees.
        tree = self.make_branch_and_tree('.')
        self.build_tree(['foo'])
        tree.add(['foo'], ['foo-id'])
        tree.lock_read()
        self.addCleanup(tree.unlock)
        file_obj, statvalue = tree.get_file_with_stat('foo-id')
        expected = os.lstat('foo')
        self.assertEqualStat(expected, statvalue)
        self.assertEqual(["contents of foo\n"], file_obj.readlines())


class TestCorruptDirstate(TestCaseWithTransport):
    """Tests for how we handle when the dirstate has been corrupted."""

    def create_wt4(self):
        control = bzrdir.BzrDirMetaFormat1().initialize(self.get_url())
        control.create_repository()
        control.create_branch()
        tree = workingtree_4.WorkingTreeFormat4().initialize(control)
        return tree

    def test_invalid_rename(self):
        tree = self.create_wt4()
        # Create a corrupted dirstate
        tree.lock_write()
        try:
            # We need a parent, or we always compare with NULL
            tree.commit('init')
            state = tree.current_dirstate()
            state._read_dirblocks_if_needed()
            # Now add in an invalid entry, a rename with a dangling pointer
            state._dirblocks[1][1].append((('', 'foo', 'foo-id'),
                                            [('f', '', 0, False, ''),
                                             ('r', 'bar', 0 , False, '')]))
            self.assertListRaises(errors.CorruptDirstate,
                                  tree.iter_changes, tree.basis_tree())
        finally:
            tree.unlock()

    def get_simple_dirblocks(self, state):
        """Extract the simple information from the DirState.

        This returns the dirblocks, only with the sha1sum and stat details
        filtered out.
        """
        simple_blocks = []
        for block in state._dirblocks:
            simple_block = (block[0], [])
            for entry in block[1]:
                # Include the key for each entry, and for each parent include
                # just the minikind, so we know if it was
                # present/absent/renamed/etc
                simple_block[1].append((entry[0], [i[0] for i in entry[1]]))
            simple_blocks.append(simple_block)
        return simple_blocks

    def test_update_basis_with_invalid_delta(self):
        """When given an invalid delta, it should abort, and not be saved."""
        self.build_tree(['dir/', 'dir/file'])
        tree = self.create_wt4()
        tree.lock_write()
        self.addCleanup(tree.unlock)
        tree.add(['dir', 'dir/file'], ['dir-id', 'file-id'])
        first_revision_id = tree.commit('init')

        root_id = tree.path2id('')
        state = tree.current_dirstate()
        state._read_dirblocks_if_needed()
        self.assertEqual([
            ('', [(('', '', root_id), ['d', 'd'])]),
            ('', [(('', 'dir', 'dir-id'), ['d', 'd'])]),
            ('dir', [(('dir', 'file', 'file-id'), ['f', 'f'])]),
        ],  self.get_simple_dirblocks(state))

        tree.remove(['dir/file'])
        self.assertEqual([
            ('', [(('', '', root_id), ['d', 'd'])]),
            ('', [(('', 'dir', 'dir-id'), ['d', 'd'])]),
            ('dir', [(('dir', 'file', 'file-id'), ['a', 'f'])]),
        ],  self.get_simple_dirblocks(state))
        # Make sure the removal is written to disk
        tree.flush()

        # self.assertRaises(Exception, tree.update_basis_by_delta,
        new_dir = inventory.InventoryDirectory('dir-id', 'new-dir', root_id)
        new_dir.revision = 'new-revision-id'
        new_file = inventory.InventoryFile('file-id', 'new-file', root_id)
        new_file.revision = 'new-revision-id'
        self.assertRaises(errors.InconsistentDelta,
            tree.update_basis_by_delta, 'new-revision-id',
            [('dir', 'new-dir', 'dir-id', new_dir),
             ('dir/file', 'new-dir/new-file', 'file-id', new_file),
            ])
        del state

        # Now when we re-read the file it should not have been modified
        tree.unlock()
        tree.lock_read()
        self.assertEqual(first_revision_id, tree.last_revision())
        state = tree.current_dirstate()
        state._read_dirblocks_if_needed()
        self.assertEqual([
            ('', [(('', '', root_id), ['d', 'd'])]),
            ('', [(('', 'dir', 'dir-id'), ['d', 'd'])]),
            ('dir', [(('dir', 'file', 'file-id'), ['a', 'f'])]),
        ],  self.get_simple_dirblocks(state))


class TestInventoryCoherency(TestCaseWithTransport):

    def test_inventory_is_synced_when_unversioning_a_dir(self):
        """Unversioning the root of a subtree unversions the entire subtree."""
        tree = self.make_branch_and_tree('.')
        self.build_tree(['a/', 'a/b', 'c/'])
        tree.add(['a', 'a/b', 'c'], ['a-id', 'b-id', 'c-id'])
        # within a lock unversion should take effect
        tree.lock_write()
        self.addCleanup(tree.unlock)
        # Force access to the in memory inventory to trigger bug #494221: try
        # maintaining the in-memory inventory
        inv = tree.inventory
        self.assertTrue(inv.has_id('a-id'))
        self.assertTrue(inv.has_id('b-id'))
        tree.unversion(['a-id', 'b-id'])
        self.assertFalse(inv.has_id('a-id'))
        self.assertFalse(inv.has_id('b-id'))
