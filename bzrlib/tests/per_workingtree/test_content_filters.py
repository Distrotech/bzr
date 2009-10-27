# Copyright (C) 2008 Canonical Ltd
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

"""Tests for content filtering conformance"""

import os

from bzrlib.bzrdir import BzrDir
from bzrlib.filters import ContentFilter
from bzrlib.switch import switch
from bzrlib.workingtree import WorkingTree
from bzrlib.tests.per_workingtree import TestCaseWithWorkingTree


def _converter_helper(chunks, fn):
    result = []
    for chunk in chunks:
        result.append(getattr(chunk, fn)())
    return iter(result)


def _swapcase(chunks, context=None):
    """A converter that swaps the case of text."""
    return _converter_helper(chunks, 'swapcase')


def _uppercase(chunks, context=None):
    """A converter that converts text to uppercase."""
    return _converter_helper(chunks, 'upper')


def _lowercase(chunks, context=None):
    """A converter that converts text to lowercase."""
    return _converter_helper(chunks, 'lower')


_trailer_string = '\nend string\n'


def _append_text(chunks, context=None):
    """A content filter that appends a string to the end of the file.

    This tests filters that change the length."""
    return chunks + [_trailer_string]


def _remove_appended_text(chunks, context=None):
    """Remove the appended text."""

    text = ''.join(chunks)
    if text.endswith(_trailer_string):
        text = text[:-len(_trailer_string)]
    return [text]


class TestWorkingTreeWithContentFilters(TestCaseWithWorkingTree):

    def create_cf_tree(self, txt_reader, txt_writer, dir='.'):
        tree = self.make_branch_and_tree(dir)
        def _content_filter_stack(path=None, file_id=None):
            if path.endswith('.txt'):
                return [ContentFilter(txt_reader, txt_writer)]
            else:
                return []
        tree._content_filter_stack = _content_filter_stack
        self.build_tree_contents([
            (dir + '/file1.txt', 'Foo Txt'),
            (dir + '/file2.bin', 'Foo Bin')])
        tree.add(['file1.txt', 'file2.bin'])
        tree.commit('commit raw content')
        txt_fileid = tree.path2id('file1.txt')
        bin_fileid = tree.path2id('file2.bin')
        return tree, txt_fileid, bin_fileid

    def create_cf_tree_with_two_revisions(self, txt_reader, txt_writer,
        dir='.'):
        tree = self.make_branch_and_tree(dir)
        def _content_filter_stack(path=None, file_id=None):
            if path.endswith('.txt'):
                return [ContentFilter(txt_reader, txt_writer)]
            else:
                return []
        tree._content_filter_stack = _content_filter_stack
        self.build_tree_contents([
            (dir + '/file1.txt', 'Foo Txt'),
            (dir + '/file2.bin', 'Foo Bin'),
            (dir + '/file3.txt', 'Bar Txt'),
            ])
        tree.add(['file1.txt', 'file2.bin', 'file3.txt'])
        tree.commit('commit raw content')
        # Commit another revision with various changes
        self.build_tree_contents([
            (dir + '/file1.txt', 'Foo ROCKS!'),
            (dir + '/file2.txt', 'Hello World'),
            ])
        tree.add(['file2.txt'])
        tree.remove(['file3.txt'], keep_files=False)
        tree.rename_one('file2.bin', 'file3.txt')
        tree.commit("change, add and rename stuff")
        txt1_fileid = tree.path2id('file1.txt')
        txt2_fileid = tree.path2id('file2.txt')
        txt3_fileid = tree.path2id('file3.txt')
        return tree, txt1_fileid, txt2_fileid, txt3_fileid

    def patch_in_content_filter(self):
        # Patch in a custom, symmetric content filter stack
        self.real_content_filter_stack = WorkingTree._content_filter_stack
        def restore_real_content_filter_stack():
            WorkingTree._content_filter_stack = self.real_content_filter_stack
        self.addCleanup(restore_real_content_filter_stack)
        def _content_filter_stack(tree, path=None, file_id=None):
            if path.endswith('.txt'):
                return [ContentFilter(_swapcase, _swapcase)]
            else:
                return []
        WorkingTree._content_filter_stack = _content_filter_stack

    def assert_basis_content(self, expected_content, branch, file_id):
        # Note: We need to use try/finally here instead of addCleanup()
        # as the latter leaves the read lock in place too long
        basis = branch.basis_tree()
        basis.lock_read()
        try:
            self.assertEqual(expected_content, basis.get_file_text(file_id))
        finally:
            basis.unlock()

    def test_symmetric_content_filtering(self):
        # test handling when read then write gives back the initial content
        tree, txt_fileid, bin_fileid = self.create_cf_tree(
            txt_reader=_swapcase, txt_writer=_swapcase)
        # Check that the basis tree has the expected content
        basis = tree.basis_tree()
        basis.lock_read()
        self.addCleanup(basis.unlock)
        if tree.supports_content_filtering():
            expected = "fOO tXT"
        else:
            expected = "Foo Txt"
        self.assertEqual(expected, basis.get_file_text(txt_fileid))
        self.assertEqual('Foo Bin', basis.get_file_text(bin_fileid))
        # Check that the working tree has the original content
        tree.lock_read()
        self.addCleanup(tree.unlock)
        self.assertEqual('Foo Txt', tree.get_file(txt_fileid,
            filtered=False).read())
        self.assertEqual('Foo Bin', tree.get_file(bin_fileid,
            filtered=False).read())

    def test_readonly_content_filtering(self):
        # test handling with a read filter but no write filter
        tree, txt_fileid, bin_fileid = self.create_cf_tree(
            txt_reader=_uppercase, txt_writer=None)
        # Check that the basis tree has the expected content
        basis = tree.basis_tree()
        basis.lock_read()
        self.addCleanup(basis.unlock)
        if tree.supports_content_filtering():
            expected = "FOO TXT"
        else:
            expected = "Foo Txt"
        self.assertEqual(expected, basis.get_file_text(txt_fileid))
        self.assertEqual('Foo Bin', basis.get_file_text(bin_fileid))
        # We expect the workingtree content to be unchanged (for now at least)
        tree.lock_read()
        self.addCleanup(tree.unlock)
        self.assertEqual('Foo Txt', tree.get_file(txt_fileid,
            filtered=False).read())
        self.assertEqual('Foo Bin', tree.get_file(bin_fileid,
            filtered=False).read())

    def test_branch_source_filtered_target_not(self):
        # Create a source branch with content filtering
        source, txt_fileid, bin_fileid = self.create_cf_tree(
            txt_reader=_uppercase, txt_writer=_lowercase, dir='source')
        if not source.supports_content_filtering():
            return
        self.assertFileEqual("Foo Txt", 'source/file1.txt')
        self.assert_basis_content("FOO TXT", source, txt_fileid)

        # Now branch it
        self.run_bzr('branch source target')
        target = WorkingTree.open('target')
        # Even though the content in source and target are different
        # due to different filters, iter_changes should be clean
        self.assertFileEqual("FOO TXT", 'target/file1.txt')
        changes = target.changes_from(source.basis_tree())
        self.assertFalse(changes.has_changed())

    def test_branch_source_not_filtered_target_is(self):
        # Create a source branch with content filtering
        source, txt_fileid, bin_fileid = self.create_cf_tree(
            txt_reader=None, txt_writer=None, dir='source')
        if not source.supports_content_filtering():
            return
        self.assertFileEqual("Foo Txt", 'source/file1.txt')
        self.assert_basis_content("Foo Txt", source, txt_fileid)

        # Now patch in content filtering and branch the source
        self.patch_in_content_filter()
        self.run_bzr('branch source target')
        target = WorkingTree.open('target')
        # Even though the content in source and target are different
        # due to different filters, iter_changes should be clean
        self.assertFileEqual("fOO tXT", 'target/file1.txt')
        changes = target.changes_from(source.basis_tree())
        self.assertFalse(changes.has_changed())

    def test_path_content_summary(self):
        """path_content_summary should always talk about the canonical form."""
        # see https://bugs.edge.launchpad.net/bzr/+bug/415508
        #
        # set up a tree where the canonical form has a string added to the
        # end
        source, txt_fileid, bin_fileid = self.create_cf_tree(
            txt_reader=_append_text,
            txt_writer=_remove_appended_text,
            dir='source')
        if not source.supports_content_filtering():
            return
        source.lock_read()
        self.addCleanup(source.unlock)

        expected_canonical_form = 'Foo Txt\nend string\n'
        self.assertEquals(source.get_file(txt_fileid, filtered=True).read(),
            expected_canonical_form)
        self.assertEquals(source.get_file(txt_fileid, filtered=False).read(),
            'Foo Txt')

        # results are: kind, size, executable, sha1_or_link_target
        result = source.path_content_summary('file1.txt')

        self.assertEquals(result,
            ('file', None, False, None))

        # we could give back the length of the canonical form, but in general
        # that will be expensive to compute, so it's acceptable to just return
        # None.

    def test_content_filtering_applied_on_pull(self):
        # Create a source branch with two revisions
        source, txt_fileid, txt2_fileid, txt3_fileid = \
            self.create_cf_tree_with_two_revisions(txt_reader=None,
            txt_writer=None, dir='source')
        if not source.supports_content_filtering():
            return
        self.assertFileEqual("Foo ROCKS!", 'source/file1.txt')
        self.assert_basis_content("Foo ROCKS!", source, txt_fileid)

        # Now patch in content filtering and branch from revision 1
        self.patch_in_content_filter()
        self.run_bzr('branch -r1 source target')
        target = WorkingTree.open('target')
        self.assertFileEqual("fOO tXT", 'target/file1.txt')
        self.assert_basis_content("Foo Txt", target, txt_fileid)

        # Pull the latter change and check the target tree is updated
        self.run_bzr('pull -d target')
        self.assertFileEqual("fOO rocks!", 'target/file1.txt')
        self.assert_basis_content("Foo ROCKS!", target, txt_fileid)
        self.assertFileEqual("hELLO wORLD", 'target/file2.txt')
        self.assert_basis_content("Hello World", target, txt2_fileid)

    def test_content_filtering_applied_on_merge(self):
        # Create a source branch with two revisions
        source, txt_fileid, txt2_fileid, txt3_fileid = \
            self.create_cf_tree_with_two_revisions(txt_reader=None,
            txt_writer=None, dir='source')
        if not source.supports_content_filtering():
            return
        self.assertFileEqual("Foo ROCKS!", 'source/file1.txt')
        self.assert_basis_content("Foo ROCKS!", source, txt_fileid)

        # Now patch in content filtering and branch from revision 1
        self.patch_in_content_filter()
        self.run_bzr('branch -r1 source target')
        target = WorkingTree.open('target')
        self.assertFileEqual("fOO tXT", 'target/file1.txt')
        self.assert_basis_content("Foo Txt", target, txt_fileid)

        # Merge the latter change and check the target tree is updated
        self.run_bzr('merge -d target source')
        self.assertFileEqual("fOO rocks!", 'target/file1.txt')
        self.assertFileEqual("hELLO wORLD", 'target/file2.txt')

        # Commit the merge and check the right content is stored
        target.commit("merge file1.txt changes from source")
        self.assert_basis_content("Foo ROCKS!", target, txt_fileid)
        self.assert_basis_content("Hello World", target, txt2_fileid)

    def test_content_filtering_applied_on_switch(self):
        # Create a source branch with two revisions
        source, txt_fileid, txt2_fileid, txt3_fileid = \
            self.create_cf_tree_with_two_revisions(txt_reader=None,
            txt_writer=None, dir='branch-a')
        if not source.supports_content_filtering():
            return

        # Now patch in content filtering and branch from revision 1
        self.patch_in_content_filter()
        self.run_bzr('branch -r1 branch-a branch-b')

        # Now create a lightweight checkout referring to branch-b
        self.run_bzr('checkout --lightweight branch-b checkout')
        self.assertFileEqual("fOO tXT", 'checkout/file1.txt')

        # Switch it to branch-b and check the tree is updated
        checkout_control_dir = BzrDir.open_containing('checkout')[0]
        switch(checkout_control_dir, source.branch)
        self.assertFileEqual("fOO rocks!", 'checkout/file1.txt')
        self.assertFileEqual("hELLO wORLD", 'checkout/file2.txt')

    def test_content_filtering_applied_on_revert(self):
        # Create a source branch with content filtering
        source, txt_fileid, bin_fileid = self.create_cf_tree(
            txt_reader=_uppercase, txt_writer=_lowercase, dir='source')
        if not source.supports_content_filtering():
            return
        self.assertFileEqual("Foo Txt", 'source/file1.txt')
        self.assert_basis_content("FOO TXT", source, txt_fileid)

        # Now delete the file, revert it and check the content
        os.unlink('source/file1.txt')
        self.assertFalse(os.path.exists('source/file1.txt'))
        source.revert(['file1.txt'])
        self.assertTrue(os.path.exists('source/file1.txt'))
        # Note: we don't get back exactly what was in the tree
        # previously because lower(upper(text)) is a lossy transformation
        self.assertFileEqual("foo txt", 'source/file1.txt')
