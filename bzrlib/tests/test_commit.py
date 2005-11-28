# Copyright (C) 2005 by Canonical Ltd

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA


import os

import bzrlib
from bzrlib.tests import TestCaseInTempDir
from bzrlib.branch import Branch
from bzrlib.workingtree import WorkingTree
from bzrlib.commit import Commit
from bzrlib.config import BranchConfig
from bzrlib.errors import PointlessCommit, BzrError, SigningFailed


# TODO: Test commit with some added, and added-but-missing files

class MustSignConfig(BranchConfig):

    def signature_needed(self):
        return True

    def gpg_signing_command(self):
        return ['cat', '-']


class BranchWithHooks(BranchConfig):

    def post_commit(self):
        return "bzrlib.ahook bzrlib.ahook"


class TestCommit(TestCaseInTempDir):

    def test_simple_commit(self):
        """Commit and check two versions of a single file."""
        b = Branch.initialize('.')
        file('hello', 'w').write('hello world')
        b.working_tree().add('hello')
        b.working_tree().commit(message='add hello')
        file_id = b.working_tree().path2id('hello')

        file('hello', 'w').write('version 2')
        b.working_tree().commit(message='commit 2')

        eq = self.assertEquals
        eq(b.revno(), 2)
        rh = b.revision_history()
        rev = b.storage.get_revision(rh[0])
        eq(rev.message, 'add hello')

        tree1 = b.storage.revision_tree(rh[0])
        text = tree1.get_file_text(file_id)
        eq(text, 'hello world')

        tree2 = b.storage.revision_tree(rh[1])
        eq(tree2.get_file_text(file_id), 'version 2')

    def test_delete_commit(self):
        """Test a commit with a deleted file"""
        b = Branch.initialize('.')
        file('hello', 'w').write('hello world')
        b.working_tree().add(['hello'], ['hello-id'])
        b.working_tree().commit(message='add hello')

        os.remove('hello')
        b.working_tree().commit('removed hello', rev_id='rev2')

        tree = b.storage.revision_tree('rev2')
        self.assertFalse(tree.has_id('hello-id'))

    def test_pointless_commit(self):
        """Commit refuses unless there are changes or it's forced."""
        b = Branch.initialize('.')
        file('hello', 'w').write('hello')
        b.working_tree().add(['hello'])
        b.working_tree().commit(message='add hello')
        self.assertEquals(b.revno(), 1)
        self.assertRaises(PointlessCommit,
                          b.working_tree().commit,
                          message='fails',
                          allow_pointless=False)
        self.assertEquals(b.revno(), 1)
        
    def test_commit_empty(self):
        """Commiting an empty tree works."""
        b = Branch.initialize('.')
        b.working_tree().commit(message='empty tree', allow_pointless=True)
        self.assertRaises(PointlessCommit,
                          b.working_tree().commit,
                          message='empty tree',
                          allow_pointless=False)
        b.working_tree().commit(message='empty tree', allow_pointless=True)
        self.assertEquals(b.revno(), 2)


    def test_selective_delete(self):
        """Selective commit in tree with deletions"""
        b = Branch.initialize('.')
        file('hello', 'w').write('hello')
        file('buongia', 'w').write('buongia')
        b.working_tree().add(['hello', 'buongia'],
              ['hello-id', 'buongia-id'])
        b.working_tree().commit(message='add files',
                 rev_id='test@rev-1')
        
        os.remove('hello')
        file('buongia', 'w').write('new text')
        b.working_tree().commit(message='update text',
                 specific_files=['buongia'],
                 allow_pointless=False,
                 rev_id='test@rev-2')

        b.working_tree().commit(message='remove hello',
                 specific_files=['hello'],
                 allow_pointless=False,
                 rev_id='test@rev-3')

        eq = self.assertEquals
        eq(b.revno(), 3)

        tree2 = b.storage.revision_tree('test@rev-2')
        self.assertTrue(tree2.has_filename('hello'))
        self.assertEquals(tree2.get_file_text('hello-id'), 'hello')
        self.assertEquals(tree2.get_file_text('buongia-id'), 'new text')
        
        tree3 = b.storage.revision_tree('test@rev-3')
        self.assertFalse(tree3.has_filename('hello'))
        self.assertEquals(tree3.get_file_text('buongia-id'), 'new text')


    def test_commit_rename(self):
        """Test commit of a revision where a file is renamed."""
        b = Branch.initialize('.')
        tree = WorkingTree('.', b)
        self.build_tree(['hello'], line_endings='binary')
        tree.add(['hello'], ['hello-id'])
        tree.commit(message='one', rev_id='test@rev-1', allow_pointless=False)

        tree.rename_one('hello', 'fruity')
        tree.commit(message='renamed', rev_id='test@rev-2', allow_pointless=False)

        eq = self.assertEquals
        tree1 = b.storage.revision_tree('test@rev-1')
        eq(tree1.id2path('hello-id'), 'hello')
        eq(tree1.get_file_text('hello-id'), 'contents of hello\n')
        self.assertFalse(tree1.has_filename('fruity'))
        self.check_inventory_shape(tree1.inventory, ['hello'])
        ie = tree1.inventory['hello-id']
        eq(ie.revision, 'test@rev-1')

        tree2 = b.storage.revision_tree('test@rev-2')
        eq(tree2.id2path('hello-id'), 'fruity')
        eq(tree2.get_file_text('hello-id'), 'contents of hello\n')
        self.check_inventory_shape(tree2.inventory, ['fruity'])
        ie = tree2.inventory['hello-id']
        eq(ie.revision, 'test@rev-2')

    def test_reused_rev_id(self):
        """Test that a revision id cannot be reused in a branch"""
        b = Branch.initialize('.')
        b.working_tree().commit('initial', rev_id='test@rev-1', allow_pointless=True)
        self.assertRaises(Exception,
                          b.working_tree().commit,
                          message='reused id',
                          rev_id='test@rev-1',
                          allow_pointless=True)

    def test_commit_move(self):
        """Test commit of revisions with moved files and directories"""
        eq = self.assertEquals
        b = Branch.initialize('.')
        r1 = 'test@rev-1'
        self.build_tree(['hello', 'a/', 'b/'])
        b.working_tree().add(['hello', 'a', 'b'], ['hello-id', 'a-id', 'b-id'])
        b.working_tree().commit('initial', rev_id=r1, allow_pointless=False)
        b.working_tree().move(['hello'], 'a')
        r2 = 'test@rev-2'
        b.working_tree().commit('two', rev_id=r2, allow_pointless=False)
        self.check_inventory_shape(b.working_tree().read_working_inventory(),
                                   ['a', 'a/hello', 'b'])

        b.working_tree().move(['b'], 'a')
        r3 = 'test@rev-3'
        b.working_tree().commit('three', rev_id=r3, allow_pointless=False)
        self.check_inventory_shape(b.working_tree().read_working_inventory(),
                                   ['a', 'a/hello', 'a/b'])
        self.check_inventory_shape(b.storage.get_revision_inventory(r3),
                                   ['a', 'a/hello', 'a/b'])

        b.working_tree().move([os.sep.join(['a', 'hello'])],
               os.sep.join(['a', 'b']))
        r4 = 'test@rev-4'
        b.working_tree().commit('four', rev_id=r4, allow_pointless=False)
        self.check_inventory_shape(b.working_tree().read_working_inventory(),
                                   ['a', 'a/b/hello', 'a/b'])

        inv = b.storage.get_revision_inventory(r4)
        eq(inv['hello-id'].revision, r4)
        eq(inv['a-id'].revision, r1)
        eq(inv['b-id'].revision, r3)
        
    def test_removed_commit(self):
        """Commit with a removed file"""
        b = Branch.initialize('.')
        wt = b.working_tree()
        file('hello', 'w').write('hello world')
        b.working_tree().add(['hello'], ['hello-id'])
        b.working_tree().commit(message='add hello')

        wt = b.working_tree()  # FIXME: kludge for aliasing of working inventory
        wt.remove('hello')
        b.working_tree().commit('removed hello', rev_id='rev2')

        tree = b.storage.revision_tree('rev2')
        self.assertFalse(tree.has_id('hello-id'))


    def test_committed_ancestry(self):
        """Test commit appends revisions to ancestry."""
        b = Branch.initialize('.')
        rev_ids = []
        for i in range(4):
            file('hello', 'w').write((str(i) * 4) + '\n')
            if i == 0:
                b.working_tree().add(['hello'], ['hello-id'])
            rev_id = 'test@rev-%d' % (i+1)
            rev_ids.append(rev_id)
            b.working_tree().commit(message='rev %d' % (i+1),
                     rev_id=rev_id)
        eq = self.assertEquals
        eq(b.revision_history(), rev_ids)
        for i in range(4):
            anc = b.storage.get_ancestry(rev_ids[i])
            eq(anc, [None] + rev_ids[:i+1])

    def test_commit_new_subdir_child_selective(self):
        b = Branch.initialize('.')
        self.build_tree(['dir/', 'dir/file1', 'dir/file2'])
        b.working_tree().add(['dir', 'dir/file1', 'dir/file2'],
              ['dirid', 'file1id', 'file2id'])
        b.working_tree().commit('dir/file1', specific_files=['dir/file1'], rev_id='1')
        inv = b.storage.get_inventory('1')
        self.assertEqual('1', inv['dirid'].revision)
        self.assertEqual('1', inv['file1id'].revision)
        # FIXME: This should raise a KeyError I think, rbc20051006
        self.assertRaises(BzrError, inv.__getitem__, 'file2id')

    def test_strict_commit(self):
        """Try and commit with unknown files and strict = True, should fail."""
        from bzrlib.errors import StrictCommitFailed
        b = Branch.initialize('.')
        file('hello', 'w').write('hello world')
        b.working_tree().add('hello')
        file('goodbye', 'w').write('goodbye cruel world!')
        self.assertRaises(StrictCommitFailed, b.working_tree().commit,
            message='add hello but not goodbye', strict=True)

    def test_strict_commit_without_unknowns(self):
        """Try and commit with no unknown files and strict = True,
        should work."""
        from bzrlib.errors import StrictCommitFailed
        b = Branch.initialize('.')
        file('hello', 'w').write('hello world')
        b.working_tree().add('hello')
        b.working_tree().commit(message='add hello', strict=True)

    def test_nonstrict_commit(self):
        """Try and commit with unknown files and strict = False, should work."""
        b = Branch.initialize('.')
        file('hello', 'w').write('hello world')
        b.working_tree().add('hello')
        file('goodbye', 'w').write('goodbye cruel world!')
        b.working_tree().commit(message='add hello but not goodbye', strict=False)

    def test_nonstrict_commit_without_unknowns(self):
        """Try and commit with no unknown files and strict = False,
        should work."""
        b = Branch.initialize('.')
        file('hello', 'w').write('hello world')
        b.working_tree().add('hello')
        b.working_tree().commit(message='add hello', strict=False)

    def test_signed_commit(self):
        import bzrlib.gpg
        import bzrlib.commit as commit
        oldstrategy = bzrlib.gpg.GPGStrategy
        branch = Branch.initialize('.')
        branch.working_tree().commit("base", allow_pointless=True, rev_id='A')
        self.failIf(branch.storage.revision_store.has_id('A', 'sig'))
        try:
            from bzrlib.testament import Testament
            # monkey patch gpg signing mechanism
            bzrlib.gpg.GPGStrategy = bzrlib.gpg.LoopbackGPGStrategy
            commit.Commit(config=MustSignConfig(branch)).commit(branch, "base",
                                                      allow_pointless=True,
                                                      rev_id='B')
            self.assertEqual(Testament.from_revision(branch.storage,
                             'B').as_short_text(),
                             branch.storage.revision_store.get('B', 
                                                               'sig').read())
        finally:
            bzrlib.gpg.GPGStrategy = oldstrategy

    def test_commit_failed_signature(self):
        import bzrlib.gpg
        import bzrlib.commit as commit
        oldstrategy = bzrlib.gpg.GPGStrategy
        branch = Branch.initialize('.')
        branch.working_tree().commit("base", allow_pointless=True, rev_id='A')
        self.failIf(branch.storage.revision_store.has_id('A', 'sig'))
        try:
            from bzrlib.testament import Testament
            # monkey patch gpg signing mechanism
            bzrlib.gpg.GPGStrategy = bzrlib.gpg.DisabledGPGStrategy
            config = MustSignConfig(branch)
            self.assertRaises(SigningFailed,
                              commit.Commit(config=config).commit,
                              branch, "base",
                              allow_pointless=True,
                              rev_id='B')
            branch = Branch.open('.')
            self.assertEqual(branch.revision_history(), ['A'])
            self.failIf(branch.storage.revision_store.has_id('B'))
        finally:
            bzrlib.gpg.GPGStrategy = oldstrategy

    def test_commit_invokes_hooks(self):
        import bzrlib.commit as commit
        branch = Branch.initialize('.')
        calls = []
        def called(branch, rev_id):
            calls.append('called')
        bzrlib.ahook = called
        try:
            config = BranchWithHooks(branch)
            commit.Commit(config=config).commit(
                            branch, "base",
                            allow_pointless=True,
                            rev_id='A')
            self.assertEqual(['called', 'called'], calls)
        finally:
            del bzrlib.ahook
