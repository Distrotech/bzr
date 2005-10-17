import os
import unittest

from bzrlib.selftest import TestCaseInTempDir, TestCase
from bzrlib.branch import ScratchBranch, Branch
from bzrlib.errors import NotBranchError, NotVersionedError


class TestBranch(TestCaseInTempDir):

    def test_unknowns(self):
        b = Branch.initialize('.')

        self.build_tree(['hello.txt',
                         'hello.txt~'])

        self.assertEquals(list(b.unknowns()),
                          ['hello.txt'])

    def test_no_changes(self):
        from bzrlib.errors import PointlessCommit
        
        b = Branch.initialize('.')

        self.build_tree(['hello.txt'])

        self.assertRaises(PointlessCommit,
                          b.commit,
                          'commit without adding',
                          allow_pointless=False)

        b.commit('commit pointless tree',
                 allow_pointless=True)

        b.add('hello.txt')
        
        b.commit('commit first added file',
                 allow_pointless=False)
        
        self.assertRaises(PointlessCommit,
                          b.commit,
                          'commit after adding file',
                          allow_pointless=False)
        
        b.commit('commit pointless revision with one file',
                 allow_pointless=True)


class MoreTests(TestCaseInTempDir):

    def test_revert(self):
        """Test selected-file revert"""
        b = Branch.initialize('.')

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

    def test_rename_dirs(self):
        """Test renaming directories and the files within them."""
        b = Branch.initialize('.')
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

    def test_relpath(self):
        """test for branch path lookups
    
        bzrlib.osutils._relpath do a simple but subtle
        job: given a path (either relative to cwd or absolute), work out
        if it is inside a branch and return the path relative to the base.
        """
        from bzrlib.osutils import relpath
        import tempfile, shutil
        
        savedir = os.getcwdu()
        dtmp = tempfile.mkdtemp()
        # On Mac OSX, /tmp actually expands to /private/tmp
        dtmp = os.path.realpath(dtmp)

        def rp(p):
            return relpath(dtmp, p)
        
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

            FOO_BAR_QUUX = os.path.join('foo', 'bar', 'quux')
            self.assertEqual(rp('foo/bar/quux'), FOO_BAR_QUUX)

            self.assertEqual(rp('foo'), 'foo')

            self.assertEqual(rp('./foo'), 'foo')

            self.assertEqual(rp(os.path.abspath('foo')), 'foo')

            self.assertRaises(NotBranchError,
                              rp, '../foo')

        finally:
            os.chdir(savedir)
            shutil.rmtree(dtmp)
