# Copyright (C) 2007, 2009, 2010 Canonical Ltd
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

"""Test that we can use smart_add on all Tree implementations."""

from cStringIO import StringIO
import os
import sys

from bzrlib import (
    add,
    errors,
    ignores,
    osutils,
    tests,
    workingtree,
    )
from bzrlib.tests import (
    features,
    test_smart_add,
    per_workingtree,
    )


class TestSmartAddTree(per_workingtree.TestCaseWithWorkingTree):

    def test_single_file(self):
        tree = self.make_branch_and_tree('tree')
        self.build_tree(['tree/a'])
        tree.smart_add(['tree'])

        tree.lock_read()
        try:
            files = [(path, status, kind)
                     for path, status, kind, file_id, parent_id
                      in tree.list_files(include_root=True)]
        finally:
            tree.unlock()
        self.assertEqual([('', 'V', 'directory'), ('a', 'V', 'file')],
                         files)

    def assertFilenameSkipped(self, filename):
        tree = self.make_branch_and_tree('tree')
        try:
            self.build_tree(['tree/'+filename])
        except errors.NoSuchFile:
            if sys.platform == 'win32':
                raise tests.TestNotApplicable('Cannot create files named %r on'
                    ' win32' % (filename,))
        tree.smart_add(['tree'])
        self.assertEqual(None, tree.path2id(filename))

    def test_path_containing_newline_skips(self):
        self.assertFilenameSkipped('a\nb')

    def test_path_containing_carriagereturn_skips(self):
        self.assertFilenameSkipped('a\rb')

    def test_save_false(self):
        """Dry-run add doesn't permanently affect the tree."""
        wt = self.make_branch_and_tree('.')
        wt.lock_write()
        try:
            self.build_tree(['file'])
            wt.smart_add(['file'], save=False)
            # the file should not be added - no id.
            self.assertEqual(wt.path2id('file'), None)
        finally:
            wt.unlock()
        # and the disk state should be the same - reopen to check.
        wt = wt.bzrdir.open_workingtree()
        self.assertEqual(wt.path2id('file'), None)

    def test_add_dot_from_root(self):
        """Test adding . from the root of the tree."""
        paths = ("original/", "original/file1", "original/file2")
        self.build_tree(paths)
        wt = self.make_branch_and_tree('.')
        wt.smart_add((u".",))
        for path in paths:
            self.assertNotEqual(wt.path2id(path), None)

    def test_add_dot_from_subdir(self):
        """Test adding . from a subdir of the tree."""
        paths = ("original/", "original/file1", "original/file2")
        self.build_tree(paths)
        wt = self.make_branch_and_tree('.')
        wt.smart_add((u".",))
        for path in paths:
            self.assertNotEqual(wt.path2id(path), None)

    def test_add_tree_from_above_tree(self):
        """Test adding a tree from above the tree."""
        paths = ("original/", "original/file1", "original/file2")
        branch_paths = ("branch/", "branch/original/", "branch/original/file1",
                        "branch/original/file2")
        self.build_tree(branch_paths)
        wt = self.make_branch_and_tree('branch')
        wt.smart_add(("branch",))
        for path in paths:
            self.assertNotEqual(wt.path2id(path), None)

    def test_add_above_tree_preserves_tree(self):
        """Test nested trees are not affect by an add above them."""
        paths = ("original/", "original/file1", "original/file2")
        child_paths = ("path",)
        full_child_paths = ("original/child", "original/child/path")
        build_paths = ("original/", "original/file1", "original/file2",
                       "original/child/", "original/child/path")

        self.build_tree(build_paths)
        wt = self.make_branch_and_tree('.')
        child_tree = self.make_branch_and_tree('original/child')
        wt.smart_add((".",))
        for path in paths:
            self.assertNotEqual((path, wt.path2id(path)),
                                (path, None))
        for path in full_child_paths:
            self.assertEqual((path, wt.path2id(path)),
                             (path, None))
        for path in child_paths:
            self.assertEqual(child_tree.path2id(path), None)

    def test_add_paths(self):
        """Test smart-adding a list of paths."""
        paths = ("file1", "file2")
        self.build_tree(paths)
        wt = self.make_branch_and_tree('.')
        wt.smart_add(paths)
        for path in paths:
            self.assertNotEqual(wt.path2id(path), None)

    def test_add_ignored_nested_paths(self):
        """Test smart-adding a list of paths which includes ignored ones."""
        wt = self.make_branch_and_tree('.')
        tree_shape = ("adir/", "adir/CVS/", "adir/CVS/afile", "adir/CVS/afile2")
        add_paths = ("adir/CVS", "adir/CVS/afile", "adir")
        expected_paths = ("adir", "adir/CVS", "adir/CVS/afile", "adir/CVS/afile2")
        self.build_tree(tree_shape)
        wt.smart_add(add_paths)
        for path in expected_paths:
            self.assertNotEqual(wt.path2id(path), None, "No id added for %s" % path)

    def test_add_non_existant(self):
        """Test smart-adding a file that does not exist."""
        wt = self.make_branch_and_tree('.')
        self.assertRaises(errors.NoSuchFile, wt.smart_add, ['non-existant-file'])

    def test_returns_and_ignores(self):
        """Correctly returns added/ignored files"""
        wt = self.make_branch_and_tree('.')
        # The default ignore list includes '*.py[co]', but not CVS
        ignores._set_user_ignores(['*.py[co]'])
        self.build_tree(['inertiatic/', 'inertiatic/esp', 'inertiatic/CVS',
                        'inertiatic/foo.pyc'])
        added, ignored = wt.smart_add(u'.')
        self.assertSubset(('inertiatic', 'inertiatic/esp', 'inertiatic/CVS'),
                          added)
        self.assertSubset(('*.py[co]',), ignored)
        self.assertSubset(('inertiatic/foo.pyc',), ignored['*.py[co]'])

    def test_add_multiple_dirs(self):
        """Test smart adding multiple directories at once."""
        added_paths = ['file1', 'file2',
                       'dir1/', 'dir1/file3',
                       'dir1/subdir2/', 'dir1/subdir2/file4',
                       'dir2/', 'dir2/file5',
                      ]
        not_added = ['file6', 'dir3/', 'dir3/file7', 'dir3/file8']
        self.build_tree(added_paths)
        self.build_tree(not_added)

        wt = self.make_branch_and_tree('.')
        wt.smart_add(['file1', 'file2', 'dir1', 'dir2'])

        for path in added_paths:
            self.assertNotEqual(None, wt.path2id(path.rstrip('/')),
                    'Failed to add path: %s' % (path,))
        for path in not_added:
            self.assertEqual(None, wt.path2id(path.rstrip('/')),
                    'Accidentally added path: %s' % (path,))

    def test_add_file_in_unknown_dir(self):
        # Test that parent directory addition is implicit
        tree = self.make_branch_and_tree('.')
        self.build_tree(['dir/', 'dir/subdir/', 'dir/subdir/foo'])
        tree.smart_add(['dir/subdir/foo'])
        tree.lock_read()
        self.addCleanup(tree.unlock)
        self.assertEqual(['', 'dir', 'dir/subdir', 'dir/subdir/foo'],
            [path for path, ie in tree.iter_entries_by_dir()])

    def test_add_dir_bug_251864(self):
        """Added file turning into a dir should be detected on add dir

        Similar to bug 205636 but with automatic adding of directory contents.
        """
        tree = self.make_branch_and_tree(".")
        self.build_tree(["dir"]) # whoops, make a file called dir
        tree.smart_add(["dir"])
        os.remove("dir")
        self.build_tree(["dir/", "dir/file"])
        tree.smart_add(["dir"])
        tree.commit("Add dir contents")

    def test_add_subdir_file_bug_205636(self):
        """Added file turning into a dir should be detected on add dir/file"""
        tree = self.make_branch_and_tree(".")
        self.build_tree(["dir"]) # whoops, make a file called dir
        tree.smart_add(["dir"])
        os.remove("dir")
        self.build_tree(["dir/", "dir/file"])
        tree.smart_add(["dir/file"])
        tree.commit("Add file in dir")

    def test_custom_ids(self):
        sio = StringIO()
        action = test_smart_add.AddCustomIDAction(to_file=sio,
                                                  should_print=True)
        self.build_tree(['file1', 'dir1/', 'dir1/file2'])

        wt = self.make_branch_and_tree('.')
        wt.smart_add(['.'], action=action)
        # The order of adds is not strictly fixed:
        sio.seek(0)
        lines = sorted(sio.readlines())
        self.assertEqualDiff(['added dir1 with id directory-dir1\n',
                              'added dir1/file2 with id file-dir1%file2\n',
                              'added file1 with id file-file1\n',
                             ], lines)
        wt.lock_read()
        self.addCleanup(wt.unlock)
        self.assertEqual([('', wt.path2id('')),
                          ('dir1', 'directory-dir1'),
                          ('dir1/file2', 'file-dir1%file2'),
                          ('file1', 'file-file1'),
                         ], [(path, ie.file_id) for path, ie
                                in wt.inventory.iter_entries()])


class TestSmartAddConflictRelatedFiles(per_workingtree.TestCaseWithWorkingTree):

    def make_tree_with_text_conflict(self):
        tb = self.make_branch_and_tree('base')
        self.build_tree_contents([('base/file', 'content in base')])
        tb.add('file')
        tb.commit('Adding file')

        t1 = tb.bzrdir.sprout('t1').open_workingtree()

        self.build_tree_contents([('base/file', 'content changed in base')])
        tb.commit('Changing file in base')

        self.build_tree_contents([('t1/file', 'content in t1')])
        t1.commit('Changing file in t1')
        t1.merge_from_branch(tb.branch)
        return t1

    def test_cant_add_generated_files_implicitly(self):
        t = self.make_tree_with_text_conflict()
        added, ignored = t.smart_add([t.basedir])
        self.assertEqual(([], {}), (added, ignored))

    def test_can_add_generated_files_explicitly(self):
        fnames = ['file.%s' % s  for s in ('BASE', 'THIS', 'OTHER')]
        t = self.make_tree_with_text_conflict()
        added, ignored = t.smart_add([t.basedir + '/%s' % f for f in fnames])
        self.assertEqual((fnames, {}), (added, ignored))


class TestSmartAddTreeUnicode(per_workingtree.TestCaseWithWorkingTree):

    _test_needs_features = [tests.UnicodeFilenameFeature]

    def setUp(self):
        super(TestSmartAddTreeUnicode, self).setUp()
        self.build_tree([u'a\u030a'])
        self.wt = self.make_branch_and_tree('.')
        self.overrideAttr(osutils, 'normalized_filename')

    def test_accessible_explicit(self):
        osutils.normalized_filename = osutils._accessible_normalized_filename
        if isinstance(self.workingtree_format, workingtree.WorkingTreeFormat2):
            self.expectFailure(
                'With WorkingTreeFormat2, smart_add requires'
                ' normalized unicode filenames',
                self.assertRaises, errors.NoSuchFile,
                self.wt.smart_add, [u'a\u030a'])
        else:
            self.wt.smart_add([u'a\u030a'])
        self.wt.lock_read()
        self.addCleanup(self.wt.unlock)
        self.assertEqual([('', 'directory'), (u'\xe5', 'file')],
                         [(path, ie.kind) for path,ie in
                          self.wt.inventory.iter_entries()])

    def test_accessible_implicit(self):
        osutils.normalized_filename = osutils._accessible_normalized_filename
        if isinstance(self.workingtree_format, workingtree.WorkingTreeFormat2):
            self.expectFailure(
                'With WorkingTreeFormat2, smart_add requires'
                ' normalized unicode filenames',
                self.assertRaises, errors.NoSuchFile,
                self.wt.smart_add, [])
        else:
            self.wt.smart_add([])
        self.wt.lock_read()
        self.addCleanup(self.wt.unlock)
        self.assertEqual([('', 'directory'), (u'\xe5', 'file')],
                         [(path, ie.kind) for path,ie
                          in self.wt.inventory.iter_entries()])

    def test_inaccessible_explicit(self):
        osutils.normalized_filename = osutils._inaccessible_normalized_filename
        self.assertRaises(errors.InvalidNormalization,
                          self.wt.smart_add, [u'a\u030a'])

    def test_inaccessible_implicit(self):
        osutils.normalized_filename = osutils._inaccessible_normalized_filename
        # TODO: jam 20060701 In the future, this should probably
        #       just ignore files that don't fit the normalization
        #       rules, rather than exploding
        self.assertRaises(errors.InvalidNormalization, self.wt.smart_add, [])
