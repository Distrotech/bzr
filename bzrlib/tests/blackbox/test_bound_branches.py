# Copyright (C) 2005 Canonical Ltd
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


"""Tests of bound branches (binding, unbinding, commit, etc) command."""

import os
from cStringIO import StringIO

from bzrlib import (
    bzrdir,
    )
from bzrlib.branch import Branch
from bzrlib.bzrdir import (BzrDir, BzrDirFormat, BzrDirMetaFormat1)
from bzrlib.osutils import getcwd
from bzrlib.tests import TestCaseWithTransport
import bzrlib.urlutils as urlutils
from bzrlib.workingtree import WorkingTree


class TestLegacyFormats(TestCaseWithTransport):
    
    def setUp(self):
        super(TestLegacyFormats, self).setUp()
        self.build_tree(['master/', 'child/'])
        self.run_bzr('init master')
        self.run_bzr('init --format=weave child')
        os.chdir('child')
    
    def test_bind_format_6_bzrdir(self):
        # bind on a format 6 bzrdir should error
        out,err = self.run_bzr('bind ../master', retcode=3)
        self.assertEqual('', out)
        # TODO: jam 20060427 Probably something like this really should
        #       print out the actual path, rather than the URL
        cwd = urlutils.local_path_to_url(getcwd())
        self.assertEqual('bzr: ERROR: To use this feature you must '
                         'upgrade your branch at %s/.\n' % cwd, err)
    
    def test_unbind_format_6_bzrdir(self):
        # bind on a format 6 bzrdir should error
        out,err = self.run_bzr('unbind', retcode=3)
        self.assertEqual('', out)
        cwd = urlutils.local_path_to_url(getcwd())
        self.assertEqual('bzr: ERROR: To use this feature you must '
                         'upgrade your branch at %s/.\n' % cwd, err)


class TestBoundBranches(TestCaseWithTransport):

    def create_branches(self):
        bzr = self.run_bzr
        self.build_tree(['base/', 'base/a', 'base/b'])

        branch = self.init_meta_branch('base')
        tree = branch.bzrdir.open_workingtree()
        tree.lock_write()
        tree.add(['a', 'b'])
        tree.commit('init')
        tree.unlock()

        self.run_bzr('checkout base child')

        self.check_revno(1, 'child')
        d = BzrDir.open('child')
        self.assertNotEqual(None, d.open_branch().get_master_branch())

    def check_revno(self, val, loc='.'):
        self.assertEqual(
            val, len(BzrDir.open(loc).open_branch().revision_history()))

    def test_simple_binding(self):
        self.build_tree(['base/', 'base/a', 'base/b'])

        self.init_meta_branch('base')
        self.run_bzr('add base')
        self.run_bzr('commit -m init base')

        self.run_bzr('branch base child')

        os.chdir('child')
        self.run_bzr('bind ../base')

        d = BzrDir.open('')
        self.assertNotEqual(None, d.open_branch().get_master_branch())

        self.run_bzr('unbind')
        self.assertEqual(None, d.open_branch().get_master_branch())

        self.run_bzr('unbind', retcode=3)

    def test_bind_branch6(self):
        branch1 = self.make_branch('branch1', format='dirstate-tags')
        os.chdir('branch1')
        error = self.run_bzr('bind', retcode=3)[1]
        self.assertContainsRe(error, 'no previous location known')

    def setup_rebind(self, format):
        branch1 = self.make_branch('branch1')
        branch2 = self.make_branch('branch2', format=format)
        branch2.bind(branch1)
        branch2.unbind()

    def test_rebind_branch6(self):
        self.setup_rebind('dirstate-tags')
        os.chdir('branch2')
        self.run_bzr('bind')
        b = Branch.open('.')
        self.assertContainsRe(b.get_bound_location(), '\/branch1\/$')

    def test_rebind_branch5(self):
        self.setup_rebind('knit')
        os.chdir('branch2')
        error = self.run_bzr('bind', retcode=3)[1]
        self.assertContainsRe(error, 'old locations')

    def init_meta_branch(self, path):
        format = bzrdir.format_registry.make_bzrdir('default')
        return BzrDir.create_branch_convenience(path, format=format)

    def test_bound_commit(self):
        bzr = self.run_bzr
        self.create_branches()

        os.chdir('child')
        open('a', 'wb').write('new contents\n')
        bzr('commit -m child')

        self.check_revno(2)

        # Make sure it committed on the parent
        self.check_revno(2, '../base')

    def test_bound_fail(self):
        # Make sure commit fails if out of date.
        bzr = self.run_bzr
        self.create_branches()

        os.chdir('base')
        open('a', 'wb').write('new base contents\n')
        bzr('commit -m base')
        self.check_revno(2)

        os.chdir('../child')
        self.check_revno(1)
        open('b', 'wb').write('new b child contents\n')
        bzr('commit -m child', retcode=3)
        self.check_revno(1)

        bzr('update')
        self.check_revno(2)

        bzr('commit -m child')
        self.check_revno(3)
        self.check_revno(3, '../base')

    def test_double_binding(self):
        bzr = self.run_bzr
        self.create_branches()

        bzr('branch child child2')
        os.chdir('child2')

        # Double binding succeeds, but committing to child2 should fail
        bzr('bind ../child')

        bzr('commit -m child2 --unchanged', retcode=3)

    def test_unbinding(self):
        bzr = self.run_bzr
        self.create_branches()

        os.chdir('base')
        open('a', 'wb').write('new base contents\n')
        bzr('commit -m base')
        self.check_revno(2)

        os.chdir('../child')
        open('b', 'wb').write('new b child contents\n')
        self.check_revno(1)
        bzr('commit -m child', retcode=3)
        self.check_revno(1)
        bzr('unbind')
        bzr('commit -m child')
        self.check_revno(2)

        bzr('bind', retcode=3)

    def test_commit_remote_bound(self):
        # It is not possible to commit to a branch
        # which is bound to a branch which is bound
        bzr = self.run_bzr
        self.create_branches()
        bzr('branch base newbase')
        os.chdir('base')
        
        # There is no way to know that B has already
        # been bound by someone else, otherwise it
        # might be nice if this would fail
        bzr('bind ../newbase')

        os.chdir('../child')
        bzr('commit -m failure --unchanged', retcode=3)

    def test_pull_updates_both(self):
        bzr = self.run_bzr
        self.create_branches()
        bzr('branch base newchild')
        os.chdir('newchild')
        open('b', 'wb').write('newchild b contents\n')
        bzr('commit -m newchild')
        self.check_revno(2)

        os.chdir('../child')
        # The pull should succeed, and update
        # the bound parent branch
        bzr('pull ../newchild')
        self.check_revno(2)

        self.check_revno(2, '../base')

    def test_bind_diverged(self):
        bzr = self.run_bzr
        self.create_branches()

        os.chdir('child')
        bzr('unbind')

        bzr('commit -m child --unchanged')
        self.check_revno(2)

        os.chdir('../base')
        self.check_revno(1)
        bzr('commit -m base --unchanged')
        self.check_revno(2)

        os.chdir('../child')
        # These branches have diverged
        bzr('bind ../base', retcode=3)

        # TODO: In the future, this might require actual changes
        # to have occurred, rather than just a new revision entry
        bzr('merge ../base')
        bzr('commit -m merged')
        self.check_revno(3)

        # After binding, the revision history should be unaltered
        base_branch = Branch.open('../base')
        child_branch = Branch.open('.')
        # take a copy before
        base_history = base_branch.revision_history()
        child_history = child_branch.revision_history()

        # After a merge, trying to bind again should succeed
        # keeping the new change as a local commit.
        bzr('bind ../base')
        self.check_revno(3)
        self.check_revno(2, '../base')

        # and compare the revision history now
        self.assertEqual(base_history, base_branch.revision_history())
        self.assertEqual(child_history, child_branch.revision_history())

    def test_bind_parent_ahead(self):
        bzr = self.run_bzr
        self.create_branches()

        os.chdir('child')
        bzr('unbind')

        os.chdir('../base')
        bzr('commit -m base --unchanged')

        os.chdir('../child')
        self.check_revno(1)
        bzr('bind ../base')

        # binding does not pull data:
        self.check_revno(1)
        bzr('unbind')

        # Check and make sure it also works if parent is ahead multiple
        os.chdir('../base')
        bzr(['commit', '-m', 'base 3', '--unchanged'])
        bzr(['commit', '-m', 'base 4', '--unchanged'])
        bzr(['commit', '-m', 'base 5', '--unchanged'])
        self.check_revno(5)

        os.chdir('../child')
        self.check_revno(1)
        bzr('bind ../base')
        self.check_revno(1)

    def test_bind_child_ahead(self):
        # test binding when the master branches history is a prefix of the 
        # childs - it should bind ok but the revision histories should not
        # be altered
        bzr = self.run_bzr
        self.create_branches()

        os.chdir('child')
        bzr('unbind')
        bzr('commit -m child --unchanged')
        self.check_revno(2)
        self.check_revno(1, '../base')

        bzr('bind ../base')
        self.check_revno(1, '../base')

        # Check and make sure it also works if child is ahead multiple
        bzr('unbind')
        bzr(['commit', '-m', 'child 3', '--unchanged'])
        bzr(['commit', '-m', 'child 4', '--unchanged'])
        bzr(['commit', '-m', 'child 5', '--unchanged'])
        self.check_revno(5)

        self.check_revno(1, '../base')
        bzr('bind ../base')
        self.check_revno(1, '../base')

    def test_commit_after_merge(self):
        bzr = self.run_bzr
        self.create_branches()

        # We want merge to be able to be a local only
        # operation, because it can be without violating
        # the binding invariants.
        # But we can't fail afterwards

        bzr('branch child other')

        os.chdir('other')
        open('c', 'wb').write('file c\n')
        bzr('add c')
        bzr(['commit', '-m', 'adding c'])
        new_rev_id = bzr('revision-history')[0].strip().split('\n')[-1]

        os.chdir('../child')
        bzr('merge ../other')

        self.failUnlessExists('c')
        tree = WorkingTree.open('.') # opens child
        self.assertEqual([new_rev_id], tree.get_parent_ids()[1:])

        # Make sure the local branch has the installed revision
        bzr(['cat-revision', new_rev_id])
        
        # And make sure that the base tree does not
        os.chdir('../base')
        bzr(['cat-revision', new_rev_id], retcode=3)

        # Commit should succeed, and cause merged revisions to
        # be pulled into base
        os.chdir('../child')
        bzr(['commit', '-m', 'merge other'])

        self.check_revno(2)

        os.chdir('../base')
        self.check_revno(2)

        bzr(['cat-revision', new_rev_id])

    def test_pull_overwrite(self):
        # XXX: This test should be moved to branch-implemenations/test_pull
        bzr = self.run_bzr
        self.create_branches()

        bzr('branch child other')
        
        os.chdir('other')
        open('a', 'wb').write('new contents\n')
        bzr(['commit', '-m', 'changed a'])
        self.check_revno(2)
        open('a', 'ab').write('and then some\n')
        bzr(['commit', '-m', 'another a'])
        self.check_revno(3)
        open('a', 'ab').write('and some more\n')
        bzr(['commit', '-m', 'yet another a'])
        self.check_revno(4)

        os.chdir('../child')
        open('a', 'wb').write('also changed a\n')
        bzr(['commit', '-m', 'child modified a'])

        self.check_revno(2)
        self.check_revno(2, '../base')

        bzr('pull --overwrite ../other')

        # both the local and master should have been updated.
        self.check_revno(4)
        self.check_revno(4, '../base')
