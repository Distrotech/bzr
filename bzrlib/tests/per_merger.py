# Copyright (C) 2009, 2010, 2011 Canonical Ltd
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

"""Implementation tests for bzrlib.merge.Merger."""

import os

from bzrlib.conflicts import TextConflict
from bzrlib import (
    errors,
    merge as _mod_merge,
    option,
    )
from bzrlib.tests import (
    multiply_tests,
    TestCaseWithTransport,
    )
from bzrlib.tests.test_merge_core import MergeBuilder
from bzrlib.transform import TreeTransform



def load_tests(standard_tests, module, loader):
    """Multiply tests for tranport implementations."""
    result = loader.suiteClass()
    scenarios = [
        (name, {'merge_type': merger})
        for name, merger in option._merge_type_registry.items()]
    return multiply_tests(standard_tests, scenarios, result)


class TestMergeImplementation(TestCaseWithTransport):

    def do_merge(self, target_tree, source_tree, **kwargs):
        merger = _mod_merge.Merger.from_revision_ids(None,
            target_tree, source_tree.last_revision(),
            other_branch=source_tree.branch)
        merger.merge_type=self.merge_type
        for name, value in kwargs.items():
            setattr(merger, name, value)
        merger.do_merge()

    def test_merge_specific_file(self):
        this_tree = self.make_branch_and_tree('this')
        this_tree.lock_write()
        self.addCleanup(this_tree.unlock)
        self.build_tree_contents([
            ('this/file1', 'a\nb\n'),
            ('this/file2', 'a\nb\n')
        ])
        this_tree.add(['file1', 'file2'])
        this_tree.commit('Added files')
        other_tree = this_tree.bzrdir.sprout('other').open_workingtree()
        self.build_tree_contents([
            ('other/file1', 'a\nb\nc\n'),
            ('other/file2', 'a\nb\nc\n')
        ])
        other_tree.commit('modified both')
        self.build_tree_contents([
            ('this/file1', 'd\na\nb\n'),
            ('this/file2', 'd\na\nb\n')
        ])
        this_tree.commit('modified both')
        self.do_merge(this_tree, other_tree, interesting_files=['file1'])
        self.assertFileEqual('d\na\nb\nc\n', 'this/file1')
        self.assertFileEqual('d\na\nb\n', 'this/file2')

    def test_merge_move_and_change(self):
        this_tree = self.make_branch_and_tree('this')
        this_tree.lock_write()
        self.addCleanup(this_tree.unlock)
        self.build_tree_contents([
            ('this/file1', 'line 1\nline 2\nline 3\nline 4\n'),
        ])
        this_tree.add('file1',)
        this_tree.commit('Added file')
        other_tree = this_tree.bzrdir.sprout('other').open_workingtree()
        self.build_tree_contents([
            ('other/file1', 'line 1\nline 2 to 2.1\nline 3\nline 4\n'),
        ])
        other_tree.commit('Changed 2 to 2.1')
        self.build_tree_contents([
            ('this/file1', 'line 1\nline 3\nline 2\nline 4\n'),
        ])
        this_tree.commit('Swapped 2 & 3')
        self.do_merge(this_tree, other_tree)
        if self.merge_type is _mod_merge.LCAMerger:
            self.expectFailure(
                "lca merge doesn't conflict for move and change",
                self.assertFileEqual,
                'line 1\n'
                '<<<<<<< TREE\n'
                'line 3\n'
                'line 2\n'
                '=======\n'
                'line 2 to 2.1\n'
                'line 3\n'
                '>>>>>>> MERGE-SOURCE\n'
                'line 4\n', 'this/file1')
        else:
            self.assertFileEqual('line 1\n'
                '<<<<<<< TREE\n'
                'line 3\n'
                'line 2\n'
                '=======\n'
                'line 2 to 2.1\n'
                'line 3\n'
                '>>>>>>> MERGE-SOURCE\n'
                'line 4\n', 'this/file1')

    def test_modify_conflicts_with_delete(self):
        # If one side deletes a line, and the other modifies that line, then
        # the modification should be considered a conflict
        builder = self.make_branch_builder('test')
        builder.start_series()
        builder.build_snapshot('BASE-id', None,
            [('add', ('', None, 'directory', None)),
             ('add', ('foo', 'foo-id', 'file', 'a\nb\nc\nd\ne\n')),
            ])
        # Delete 'b\n'
        builder.build_snapshot('OTHER-id', ['BASE-id'],
            [('modify', ('foo-id', 'a\nc\nd\ne\n'))])
        # Modify 'b\n', add 'X\n'
        builder.build_snapshot('THIS-id', ['BASE-id'],
            [('modify', ('foo-id', 'a\nb2\nc\nd\nX\ne\n'))])
        builder.finish_series()
        branch = builder.get_branch()
        this_tree = branch.bzrdir.create_workingtree()
        this_tree.lock_write()
        self.addCleanup(this_tree.unlock)
        other_tree = this_tree.bzrdir.sprout('other',
                                             'OTHER-id').open_workingtree()
        self.do_merge(this_tree, other_tree)
        if self.merge_type is _mod_merge.LCAMerger:
            self.expectFailure("lca merge doesn't track deleted lines",
                self.assertFileEqual,
                    'a\n'
                    '<<<<<<< TREE\n'
                    'b2\n'
                    '=======\n'
                    '>>>>>>> MERGE-SOURCE\n'
                    'c\n'
                    'd\n'
                    'X\n'
                    'e\n', 'test/foo')
        else:
            self.assertFileEqual(
                'a\n'
                '<<<<<<< TREE\n'
                'b2\n'
                '=======\n'
                '>>>>>>> MERGE-SOURCE\n'
                'c\n'
                'd\n'
                'X\n'
                'e\n', 'test/foo')

    def get_limbodir_deletiondir(self, wt):
        transform = TreeTransform(wt)
        limbodir = transform._limbodir
        deletiondir = transform._deletiondir
        transform.finalize()
        return (limbodir, deletiondir)

    def test_merge_with_existing_limbo_empty(self):
        """Empty limbo dir is just cleaned up - see bug 427773"""
        wt = self.make_branch_and_tree('this')
        (limbodir, deletiondir) =  self.get_limbodir_deletiondir(wt)
        os.mkdir(limbodir)
        self.do_merge(wt, wt)

    def test_merge_with_existing_limbo_non_empty(self):
        wt = self.make_branch_and_tree('this')
        (limbodir, deletiondir) =  self.get_limbodir_deletiondir(wt)
        os.mkdir(limbodir)
        os.mkdir(os.path.join(limbodir, 'something'))
        self.assertRaises(errors.ExistingLimbo, self.do_merge, wt, wt)
        self.assertRaises(errors.LockError, wt.unlock)

    def test_merge_with_pending_deletion_empty(self):
        """Also see bug 427773"""
        wt = self.make_branch_and_tree('this')
        (limbodir, deletiondir) =  self.get_limbodir_deletiondir(wt)
        os.mkdir(deletiondir)
        os.mkdir(os.path.join(deletiondir, 'something'))
        self.assertRaises(errors.ExistingPendingDeletion, self.do_merge, wt, wt)
        self.assertRaises(errors.LockError, wt.unlock)

    def test_merge_with_pending_deletion_non_empty(self):
        wt = self.make_branch_and_tree('this')
        (limbodir, deletiondir) =  self.get_limbodir_deletiondir(wt)
        os.mkdir(deletiondir)
        self.assertRaises(errors.ExistingPendingDeletion, self.do_merge, wt, wt)
        self.assertRaises(errors.LockError, wt.unlock)


class TestHookMergeFileContent(TestCaseWithTransport):
    """Tests that the 'merge_file_content' hook is invoked."""

    def setUp(self):
        TestCaseWithTransport.setUp(self)
        self.hook_log = []

    def install_hook_inactive(self):
        def inactive_factory(merger):
            # This hook is never active
            self.hook_log.append(('inactive',))
            return None
        _mod_merge.Merger.hooks.install_named_hook(
            'merge_file_content', inactive_factory, 'test hook (inactive)')

    def install_hook_noop(self):
        test = self
        class HookNA(_mod_merge.AbstractPerFileMerger):
            def merge_contents(self, merge_params):
                # This hook unconditionally does nothing.
                test.hook_log.append(('no-op',))
                return 'not_applicable', None
        def hook_na_factory(merger):
            return HookNA(merger)
        _mod_merge.Merger.hooks.install_named_hook(
            'merge_file_content', hook_na_factory, 'test hook (no-op)')

    def install_hook_success(self):
        test = self
        class HookSuccess(_mod_merge.AbstractPerFileMerger):
            def merge_contents(self, merge_params):
                test.hook_log.append(('success',))
                if merge_params.file_id == '1':
                    return 'success', ['text-merged-by-hook']
                return 'not_applicable', None
        def hook_success_factory(merger):
            return HookSuccess(merger)
        _mod_merge.Merger.hooks.install_named_hook(
            'merge_file_content', hook_success_factory, 'test hook (success)')

    def install_hook_conflict(self):
        test = self
        class HookConflict(_mod_merge.AbstractPerFileMerger):
            def merge_contents(self, merge_params):
                test.hook_log.append(('conflict',))
                if merge_params.file_id == '1':
                    return ('conflicted',
                        ['text-with-conflict-markers-from-hook'])
                return 'not_applicable', None
        def hook_conflict_factory(merger):
            return HookConflict(merger)
        _mod_merge.Merger.hooks.install_named_hook(
            'merge_file_content', hook_conflict_factory, 'test hook (delete)')

    def install_hook_delete(self):
        test = self
        class HookDelete(_mod_merge.AbstractPerFileMerger):
            def merge_contents(self, merge_params):
                test.hook_log.append(('delete',))
                if merge_params.file_id == '1':
                    return 'delete', None
                return 'not_applicable', None
        def hook_delete_factory(merger):
            return HookDelete(merger)
        _mod_merge.Merger.hooks.install_named_hook(
            'merge_file_content', hook_delete_factory, 'test hook (delete)')

    def install_hook_log_lines(self):
        """Install a hook that saves the get_lines for the this, base and other
        versions of the file.
        """
        test = self
        class HookLogLines(_mod_merge.AbstractPerFileMerger):
            def merge_contents(self, merge_params):
                test.hook_log.append((
                    'log_lines',
                    merge_params.this_lines,
                    merge_params.other_lines,
                    merge_params.base_lines,
                    ))
                return 'not_applicable', None
        def hook_log_lines_factory(merger):
            return HookLogLines(merger)
        _mod_merge.Merger.hooks.install_named_hook(
            'merge_file_content', hook_log_lines_factory,
            'test hook (log_lines)')

    def make_merge_builder(self):
        builder = MergeBuilder(self.test_base_dir)
        self.addCleanup(builder.cleanup)
        return builder

    def create_file_needing_contents_merge(self, builder, file_id):
        builder.add_file(file_id, builder.tree_root, "name1", "text1", True)
        builder.change_contents(file_id, other="text4", this="text3")

    def test_change_vs_change(self):
        """Hook is used for (changed, changed)"""
        self.install_hook_success()
        builder = self.make_merge_builder()
        builder.add_file("1", builder.tree_root, "name1", "text1", True)
        builder.change_contents("1", other="text4", this="text3")
        conflicts = builder.merge(self.merge_type)
        self.assertEqual(conflicts, [])
        self.assertEqual(
            builder.this.get_file('1').read(), 'text-merged-by-hook')

    def test_change_vs_deleted(self):
        """Hook is used for (changed, deleted)"""
        self.install_hook_success()
        builder = self.make_merge_builder()
        builder.add_file("1", builder.tree_root, "name1", "text1", True)
        builder.change_contents("1", this="text2")
        builder.remove_file("1", other=True)
        conflicts = builder.merge(self.merge_type)
        self.assertEqual(conflicts, [])
        self.assertEqual(
            builder.this.get_file('1').read(), 'text-merged-by-hook')

    def test_result_can_be_delete(self):
        """A hook's result can be the deletion of a file."""
        self.install_hook_delete()
        builder = self.make_merge_builder()
        self.create_file_needing_contents_merge(builder, "1")
        conflicts = builder.merge(self.merge_type)
        self.assertEqual(conflicts, [])
        self.assertRaises(errors.NoSuchId, builder.this.id2path, '1')
        self.assertEqual([], list(builder.this.list_files()))

    def test_result_can_be_conflict(self):
        """A hook's result can be a conflict."""
        self.install_hook_conflict()
        builder = self.make_merge_builder()
        self.create_file_needing_contents_merge(builder, "1")
        conflicts = builder.merge(self.merge_type)
        self.assertEqual(conflicts, [TextConflict('name1', file_id='1')])
        # The hook still gets to set the file contents in this case, so that it
        # can insert custom conflict markers.
        self.assertEqual(
            builder.this.get_file('1').read(),
            'text-with-conflict-markers-from-hook')

    def test_can_access_this_other_and_base_versions(self):
        """The hook function can call params.merger.get_lines to access the
        THIS/OTHER/BASE versions of the file.
        """
        self.install_hook_log_lines()
        builder = self.make_merge_builder()
        builder.add_file("1", builder.tree_root, "name1", "text1", True)
        builder.change_contents("1", this="text2", other="text3")
        conflicts = builder.merge(self.merge_type)
        self.assertEqual(
            [('log_lines', ['text2'], ['text3'], ['text1'])], self.hook_log)

    def test_chain_when_not_active(self):
        """When a hook function returns None, merging still works."""
        self.install_hook_inactive()
        self.install_hook_success()
        builder = self.make_merge_builder()
        self.create_file_needing_contents_merge(builder, "1")
        conflicts = builder.merge(self.merge_type)
        self.assertEqual(conflicts, [])
        self.assertEqual(
            builder.this.get_file('1').read(), 'text-merged-by-hook')
        self.assertEqual([('inactive',), ('success',)], self.hook_log)

    def test_chain_when_not_applicable(self):
        """When a hook function returns not_applicable, the next function is
        tried (when one exists).
        """
        self.install_hook_noop()
        self.install_hook_success()
        builder = self.make_merge_builder()
        self.create_file_needing_contents_merge(builder, "1")
        conflicts = builder.merge(self.merge_type)
        self.assertEqual(conflicts, [])
        self.assertEqual(
            builder.this.get_file('1').read(), 'text-merged-by-hook')
        self.assertEqual([('no-op',), ('success',)], self.hook_log)

    def test_chain_stops_after_success(self):
        """When a hook function returns success, no later functions are tried.
        """
        self.install_hook_success()
        self.install_hook_noop()
        builder = self.make_merge_builder()
        self.create_file_needing_contents_merge(builder, "1")
        conflicts = builder.merge(self.merge_type)
        self.assertEqual([('success',)], self.hook_log)

    def test_chain_stops_after_conflict(self):
        """When a hook function returns conflict, no later functions are tried.
        """
        self.install_hook_conflict()
        self.install_hook_noop()
        builder = self.make_merge_builder()
        self.create_file_needing_contents_merge(builder, "1")
        conflicts = builder.merge(self.merge_type)
        self.assertEqual([('conflict',)], self.hook_log)

    def test_chain_stops_after_delete(self):
        """When a hook function returns delete, no later functions are tried.
        """
        self.install_hook_delete()
        self.install_hook_noop()
        builder = self.make_merge_builder()
        self.create_file_needing_contents_merge(builder, "1")
        conflicts = builder.merge(self.merge_type)
        self.assertEqual([('delete',)], self.hook_log)

