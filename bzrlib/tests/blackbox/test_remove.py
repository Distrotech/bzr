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


import os

from bzrlib.tests.blackbox import ExternalBase
from bzrlib.workingtree import WorkingTree
from bzrlib import osutils

_id='-id'
a='a'
b='b/'
c='b/c'
files=(a,b,c)


class TestRemove(ExternalBase):

    def _make_add_and_assert_tree(self,files):
        tree = self.make_branch_and_tree('.')
        self.build_tree(files)
        for f in files:
            id=f+_id
            tree.add(f, id)
            self.assertEqual(tree.path2id(f), id)
            self.failUnlessExists(f)
            self.assertInWorkingTree(f)
        return tree

    def assertFilesDeleted(self,files):
        for f in files:
            id=f+_id
            self.assertNotInWorkingTree(f)
            self.failIfExists(f)

    def assertFilesUnversioned(self,files):
        for f in files:
            self.assertNotInWorkingTree(f)
            self.failUnlessExists(f)

    def test_remove_no_files_specified(self):
        tree = self._make_add_and_assert_tree([])

        (out,err) = self.runbzr('remove', retcode=3)
        self.assertEquals(err.strip(),
            "bzr: ERROR: Specify one or more files to remove, or use --new.")

        (out,err) = self.runbzr('remove --new', retcode=3)
        self.assertEquals(err.strip(),"bzr: ERROR: No matching files.")
        (out,err) = self.runbzr('remove --new .', retcode=3)
        self.assertEquals(out.strip(), "")
        self.assertEquals(err.strip(), "bzr: ERROR: No matching files.")

    def test_remove_invalid_files(self):
        self.build_tree([a])
        tree = self.make_branch_and_tree('.')

        (out,err) = self.runbzr('remove .')
        self.assertEquals(out.strip(), "")
        self.assertEquals(err.strip(), "")

    def test_remove_unversioned_files(self):
        self.build_tree([a])
        tree = self.make_branch_and_tree('.')
        
        (out,err) = self.runbzr('remove a')
        self.assertEquals(out.strip(), "")
        self.assertEquals(err.strip(), "a is not versioned.")

    def test_remove_keep_unversioned_files(self):
        self.build_tree([a])
        tree = self.make_branch_and_tree('.')
        
        (out,err) = self.runbzr('remove --keep a')
        self.assertEquals(out.strip(), "")
        self.assertEquals(err.strip(), "a is not versioned.")

    def test_remove_force_unversioned_files(self):
        self.build_tree([a])
        tree = self.make_branch_and_tree('.')

        (out,err) = self.runbzr('remove --force a')
        self.assertEquals(out.strip(), "")
        self.assertEquals(err.strip(), "deleted a")
        self.assertFilesDeleted([a])

    def test_remove_non_existing_files(self):
        tree = self._make_add_and_assert_tree([])
        (out,err) = self.runbzr('remove b')
        self.assertEquals(out.strip(), "")
        self.assertEquals(err.strip(), "b does not exist.")

    def test_remove_keep_non_existing_files(self):
        tree = self._make_add_and_assert_tree([])
        (out,err) = self.runbzr('remove --keep b')
        self.assertEquals(out.strip(), "")
        self.assertEquals(err.strip(), "b is not versioned.")

    def test_remove_one_file(self):
        tree = self._make_add_and_assert_tree([a])
        (out,err) = self.runbzr('remove a')
        self.assertEquals(out.strip(), "")
        self.assertEquals(err.strip(), "a has changed and won't be deleted.")
        self.assertFilesUnversioned([a])
        
    def test_remove_keep_one_file(self):
        tree = self._make_add_and_assert_tree([a])
        (out,err) = self.runbzr('remove --keep a')
        self.assertEquals(out.strip(), "")
        self.assertEquals(err.strip(), "removed a")
        self.assertFilesUnversioned([a])

    def test_command_on_deleted(self):
        tree = self._make_add_and_assert_tree([a])
        self.runbzr(['commit', '-m', 'added a'])
        os.unlink(a)
        self.assertInWorkingTree(a)
        self.runbzr('remove a')
        self.assertNotInWorkingTree(a)

    def test_command_with_new(self):
        tree = self._make_add_and_assert_tree(files)

        self.runbzr('remove --new')
        self.assertFilesUnversioned(files)

    def test_command_with_new_in_dir1(self):
        tree = self._make_add_and_assert_tree(files)
        self.runbzr('remove --new %s %s'%(b,c))
        tree = WorkingTree.open('.')
        self.assertInWorkingTree(a)
        self.assertEqual(tree.path2id(a), a+_id)
        self.assertFilesUnversioned([b,c])

    def test_command_with_new_in_dir2(self):
        tree = self._make_add_and_assert_tree(files)
        self.runbzr('remove --new .')
        tree = WorkingTree.open('.')
        self.assertFilesUnversioned([a])
