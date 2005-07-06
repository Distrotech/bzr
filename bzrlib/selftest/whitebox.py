#! /usr/bin/python

import os
import unittest

from bzrlib.selftest import InTempDir, TestBase
from bzrlib.branch import ScratchBranch, Branch
from bzrlib.errors import NotBranchError, NotVersionedError


class Unknowns(InTempDir):
    def runTest(self):
        b = Branch('.', init=True)

        self.build_tree(['hello.txt',
                         'hello.txt~'])

        self.assertEquals(list(b.unknowns()),
                          ['hello.txt'])



class ValidateRevisionId(TestBase):
    def runTest(self):
        from bzrlib.revision import validate_revision_id
        validate_revision_id('mbp@sourcefrog.net-20050311061123-96a255005c7c9dbe')
        
        self.assertRaises(ValueError,
                          validate_revision_id,
                          ' asdkjas')


        self.assertRaises(ValueError,
                          validate_revision_id,
                          'mbp@sourcefrog.net-20050311061123-96a255005c7c9dbe\n')


        self.assertRaises(ValueError,
                          validate_revision_id,
                          ' mbp@sourcefrog.net-20050311061123-96a255005c7c9dbe')

        self.assertRaises(ValueError,
                          validate_revision_id,
                          'Martin Pool <mbp@sourcefrog.net>-20050311061123-96a255005c7c9dbe')



class PendingMerges(InTempDir):
    """Tracking pending-merged revisions."""
    def runTest(self):
        b = Branch('.', init=True)

        self.assertEquals(b.pending_merges(), [])
        
        b.add_pending_merge('foo@azkhazan-123123-abcabc')
        
        self.assertEquals(b.pending_merges(), ['foo@azkhazan-123123-abcabc'])
    
        b.add_pending_merge('foo@azkhazan-123123-abcabc')
        
        self.assertEquals(b.pending_merges(), ['foo@azkhazan-123123-abcabc'])

        b.add_pending_merge('wibble@fofof--20050401--1928390812')
        self.assertEquals(b.pending_merges(),
                          ['foo@azkhazan-123123-abcabc',
                           'wibble@fofof--20050401--1928390812'])

        b.commit("commit from base with two merges")

        rev = b.get_revision(b.revision_history()[0])
        self.assertEquals(len(rev.parents), 2)
        self.assertEquals(rev.parents[0].revision_id,
                          'foo@azkhazan-123123-abcabc')
        self.assertEquals(rev.parents[1].revision_id,
                           'wibble@fofof--20050401--1928390812')

        # list should be cleared when we do a commit
        self.assertEquals(b.pending_merges(), [])
        
        
        

class Revert(InTempDir):
    """Test selected-file revert"""
    def runTest(self):
        b = Branch('.', init=True)

        self.build_tree(['hello.txt'])
        file('hello.txt', 'w').write('initial hello')

        self.assertRaises(NotVersionedError,
                          b.revert, ['hello.txt'])
        
        b.add(['hello.txt'])
        b.commit('create initial hello.txt')

        self.check_file_contents('hello.txt', 'initial hello')
        file('hello.txt', 'w').write('new hello')
        self.check_file_contents('hello.txt', 'new hello')

        # revert file modified since last revision
        b.revert(['hello.txt'])
        self.check_file_contents('hello.txt', 'initial hello')
        self.check_file_contents('hello.txt~', 'new hello')

        # reverting again clobbers the backup
        b.revert(['hello.txt'])
        self.check_file_contents('hello.txt', 'initial hello')
        self.check_file_contents('hello.txt~', 'initial hello')



class RenameDirs(InTempDir):
    """Test renaming directories and the files within them."""
    def runTest(self):
        b = Branch('.', init=True)
        self.build_tree(['dir/', 'dir/sub/', 'dir/sub/file'])
        b.add(['dir', 'dir/sub', 'dir/sub/file'])

        b.commit('create initial state')

        # TODO: lift out to a test helper that checks the shape of
        # an inventory
        
        revid = b.revision_history()[0]
        self.log('first revision_id is {%s}' % revid)
        
        inv = b.get_revision_inventory(revid)
        self.log('contents of inventory: %r' % inv.entries())

        self.check_inventory_shape(inv,
                                   ['dir', 'dir/sub', 'dir/sub/file'])

        b.rename_one('dir', 'newdir')

        self.check_inventory_shape(b.inventory,
                                   ['newdir', 'newdir/sub', 'newdir/sub/file'])

        b.rename_one('newdir/sub', 'newdir/newsub')
        self.check_inventory_shape(b.inventory,
                                   ['newdir', 'newdir/newsub',
                                    'newdir/newsub/file'])

        


class BranchPathTestCase(TestBase):
    """test for branch path lookups

    Branch.relpath and bzrlib.branch._relpath do a simple but subtle
    job: given a path (either relative to cwd or absolute), work out
    if it is inside a branch and return the path relative to the base.
    """

    def runTest(self):
        from bzrlib.branch import _relpath
        import tempfile, shutil
        
        savedir = os.getcwdu()
        dtmp = tempfile.mkdtemp()

        def rp(p):
            return _relpath(dtmp, p)
        
        try:
            # check paths inside dtmp while standing outside it
            self.assertEqual(rp(os.path.join(dtmp, 'foo')), 'foo')

            # root = nothing
            self.assertEqual(rp(dtmp), '')

            self.assertRaises(NotBranchError,
                              rp,
                              '/etc')

            # now some near-miss operations -- note that
            # os.path.commonprefix gets these wrong!
            self.assertRaises(NotBranchError,
                              rp,
                              dtmp.rstrip('\\/') + '2')

            self.assertRaises(NotBranchError,
                              rp,
                              dtmp.rstrip('\\/') + '2/foo')

            # now operations based on relpath of files in current
            # directory, or nearby
            os.chdir(dtmp)

            self.assertEqual(rp('foo/bar/quux'), 'foo/bar/quux')

            self.assertEqual(rp('foo'), 'foo')

            self.assertEqual(rp('./foo'), 'foo')

            self.assertEqual(rp(os.path.abspath('foo')), 'foo')

            self.assertRaises(NotBranchError,
                              rp, '../foo')

        finally:
            os.chdir(savedir)
            shutil.rmtree(dtmp)




TEST_CLASSES = [Unknowns,
                ValidateRevisionId,
                PendingMerges,
                Revert,
                RenameDirs,
                BranchPathTestCase,
                ]
