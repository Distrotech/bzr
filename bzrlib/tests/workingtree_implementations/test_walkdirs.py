# Copyright (C) 2006, 2007 Canonical Ltd
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

"""Tests for the extra cases that WorkingTree.walkdirs can encounter."""

import os

from bzrlib import transform
from bzrlib.osutils import has_symlinks
from bzrlib.tests import TestSkipped
from bzrlib.tests.workingtree_implementations import TestCaseWithWorkingTree

# tests to write:
# type mismatches - file to link, dir, dir to file, link, link to file, dir

class DirBlock:
    """Object representation of the tuples returned by dirstate."""

    def __init__(self, tree, file_path, file_name=None, id=None,
                 inventory_kind=None, stat=None, disk_kind='unknown'):
        self.file_path = file_path
        self.abspath = tree.abspath(file_path)
        self.relpath = tree.relpath(file_path)
        if file_name == None:
           file_name = os.path.split(file_path)[-1]
           if len(file_name) == 0:
               file_name = os.path.split(file_path)[-2]
        self.file_name = file_name
        self.id = id
        self.inventory_kind = inventory_kind
        self.stat = stat
        self.disk_kind = disk_kind

    def as_tuple(self):
         return (self.relpath, self.file_name, self.disk_kind,
                 self.stat, self.id, self.inventory_kind)

    def as_dir_tuple(self):
         return (self.relpath, self.id)

    def __str__(self):
        return """
file_path      = %r
abspath        = %r
relpath        = %r
file_name      = %r
id             = %r
inventory_kind = %r
stat           = %r
disk_kind      = %r""" % (self.file_path, self.abspath, self.relpath,
        self.file_name, self.id, self.inventory_kind, self.stat,
        self.disk_kind)


class TestWalkdirs(TestCaseWithWorkingTree):

    added='added'
    missing='missing'
    unknown='unknown'

    def get_tree(self, file_status, prefix=None):
        tree = self.make_branch_and_tree('.')
        dirblocks = []
        paths = [
            file_status + ' file',
            file_status + ' dir/',
            file_status + ' dir/a file',
            file_status + ' empty dir/',
            ]
        self.build_tree(paths)

        def add_dirblock(path, kind):
            dirblock = DirBlock(tree, path)
            if file_status != self.unknown:
                dirblock.id = 'a ' + str(path).replace('/','-') + '-id'
                dirblock.inventory_kind = kind
            if file_status != self.missing:
                dirblock.disk_kind = kind
                dirblock.stat = os.lstat(dirblock.relpath)
            dirblocks.append(dirblock)

        add_dirblock(paths[0], 'file')
        add_dirblock(paths[1], 'directory')
        add_dirblock(paths[2], 'file')
        add_dirblock(paths[3], 'directory')

        if file_status != self.unknown:
            tree.add(paths, [db.id for db in dirblocks])

        if file_status == self.missing:
            # now make the files be missing
            tree.bzrdir.root_transport.delete(dirblocks[0].relpath)
            tree.bzrdir.root_transport.delete_tree(dirblocks[1].relpath)
            tree.bzrdir.root_transport.delete_tree(dirblocks[3].relpath)

        expected_dirblocks = [
            (('', tree.path2id('')),
             [dirblocks[1].as_tuple(), dirblocks[3].as_tuple(),
              dirblocks[0].as_tuple()]
            ),
            (dirblocks[1].as_dir_tuple(),
             [dirblocks[2].as_tuple()]
            ),
            (dirblocks[3].as_dir_tuple(),
             []
            ),
            ]
        if prefix:
            expected_dirblocks = [e for e in expected_dirblocks
                if len(e) > 0 and len(e[0]) > 0 and e[0][0] == prefix]
        return tree, expected_dirblocks

    def _test_walkdir(self, file_status, prefix=""):
        result = []
        tree, expected_dirblocks = self.get_tree(file_status, prefix)
        tree.lock_read()
        for dirinfo, dirblock in tree.walkdirs(prefix):
            result.append((dirinfo, list(dirblock)))
        tree.unlock()

        # check each return value for debugging ease.
        for pos, item in enumerate(expected_dirblocks):
            result_pos = []
            if len(result) > pos:
                result_pos = result[pos]
            self.assertEqual(item, result_pos)
        self.assertEqual(expected_dirblocks, result)

    def test_walkdir_unknowns(self):
        """unknown files and directories should be reported by walkdirs."""
        self._test_walkdir(self.unknown)

    def test_walkdir_from_unknown_dir(self):
        """Doing a walkdir when the requested prefix is unknown but on disk."""
        self._test_walkdir(self.unknown, 'unknown dir')

    def test_walkdir_missings(self):
        """missing files and directories should be reported by walkdirs."""
        self._test_walkdir(self.missing)

    def test_walkdir_from_dir(self):
        """Doing a walkdir when the requested prefix is known and on disk."""
        self._test_walkdir(self.added, 'added dir')

    def test_walkdir_from_empty_dir(self):
        """Doing a walkdir when the requested prefix is empty dir."""
        self._test_walkdir(self.added, 'added empty dir')

    def test_walkdir_from_missing_dir(self):
        """Doing a walkdir when the requested prefix is missing but on disk."""
        self._test_walkdir(self.missing, 'missing dir')

    def test_walkdirs_type_changes(self):
        """Walkdir shows the actual kinds on disk and the recorded kinds."""
        if not has_symlinks():
            raise TestSkipped('No symlink support')
        tree = self.make_branch_and_tree('.')
        paths = ['file1', 'file2', 'dir1/', 'dir2/']
        ids = ['file1', 'file2', 'dir1', 'dir2']
        self.build_tree(paths)
        tree.add(paths, ids)
        tt = transform.TreeTransform(tree)
        root_transaction_id = tt.trans_id_tree_path('')
        tt.new_symlink('link1',
            root_transaction_id, 'link-target', 'link1')
        tt.new_symlink('link2',
            root_transaction_id, 'link-target', 'link2')
        tt.apply()
        tree.bzrdir.root_transport.delete_tree('dir1')
        tree.bzrdir.root_transport.delete_tree('dir2')
        tree.bzrdir.root_transport.delete('file1')
        tree.bzrdir.root_transport.delete('file2')
        tree.bzrdir.root_transport.delete('link1')
        tree.bzrdir.root_transport.delete('link2')
        changed_paths = ['dir1', 'file1/', 'link1', 'link2/']
        self.build_tree(changed_paths)
        os.symlink('target', 'dir2')
        os.symlink('target', 'file2')
        dir1_stat = os.lstat('dir1')
        dir2_stat = os.lstat('dir2')
        file1_stat = os.lstat('file1')
        file2_stat = os.lstat('file2')
        link1_stat = os.lstat('link1')
        link2_stat = os.lstat('link2')
        expected_dirblocks = [
             (('', tree.path2id('')),
              [('dir1', 'dir1', 'file', dir1_stat, 'dir1', 'directory'),
               ('dir2', 'dir2', 'symlink', dir2_stat, 'dir2', 'directory'),
               ('file1', 'file1', 'directory', file1_stat, 'file1', 'file'),
               ('file2', 'file2', 'symlink', file2_stat, 'file2', 'file'),
               ('link1', 'link1', 'file', link1_stat, 'link1', 'symlink'),
               ('link2', 'link2', 'directory', link2_stat, 'link2', 'symlink'),
              ]
             ),
             (('dir1', 'dir1'),
              [
              ]
             ),
             (('dir2', 'dir2'),
              [
              ]
             ),
             (('file1', None),
              [
              ]
             ),
             (('link2', None),
              [
              ]
             ),
            ]
        tree.lock_read()
        result = list(tree.walkdirs())
        tree.unlock()
        # check each return value for debugging ease.
        for pos, item in enumerate(expected_dirblocks):
            self.assertEqual(item, result[pos])
        self.assertEqual(len(expected_dirblocks), len(result))

    def test_walkdirs_type_changes_wo_symlinks(self):
        # similar to test_walkdirs_type_changes
        # but don't use symlinks for safe testing on win32
        tree = self.make_branch_and_tree('.')
        paths = ['file1', 'dir1/']
        ids = ['file1', 'dir1']
        self.build_tree(paths)
        tree.add(paths, ids)
        tree.bzrdir.root_transport.delete_tree('dir1')
        tree.bzrdir.root_transport.delete('file1')
        changed_paths = ['dir1', 'file1/']
        self.build_tree(changed_paths)
        dir1_stat = os.lstat('dir1')
        file1_stat = os.lstat('file1')
        expected_dirblocks = [
             (('', tree.path2id('')),
              [('dir1', 'dir1', 'file', dir1_stat, 'dir1', 'directory'),
               ('file1', 'file1', 'directory', file1_stat, 'file1', 'file'),
              ]
             ),
             (('dir1', 'dir1'),
              [
              ]
             ),
             (('file1', None),
              [
              ]
             ),
            ]
        tree.lock_read()
        result = list(tree.walkdirs())
        tree.unlock()
        # check each return value for debugging ease.
        for pos, item in enumerate(expected_dirblocks):
            self.assertEqual(item, result[pos])
        self.assertEqual(len(expected_dirblocks), len(result))
