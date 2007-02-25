# Copyright (C) 2005, 2007 Canonical Ltd
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


"""Black-box tests for bzr push."""

import os

from bzrlib import (
    errors,
    urlutils,
    )
from bzrlib.branch import Branch
from bzrlib.bzrdir import BzrDirMetaFormat1
from bzrlib.osutils import abspath
from bzrlib.repofmt.knitrepo import RepositoryFormatKnit1
from bzrlib.tests.blackbox import ExternalBase
from bzrlib.uncommit import uncommit
from bzrlib.urlutils import local_path_from_url
from bzrlib.workingtree import WorkingTree


class TestPush(ExternalBase):

    def test_push_remember(self):
        """Push changes from one branch to another and test push location."""
        transport = self.get_transport()
        tree_a = self.make_branch_and_tree('branch_a')
        branch_a = tree_a.branch
        self.build_tree(['branch_a/a'])
        tree_a.add('a')
        tree_a.commit('commit a')
        tree_b = branch_a.bzrdir.sprout('branch_b').open_workingtree()
        branch_b = tree_b.branch
        tree_c = branch_a.bzrdir.sprout('branch_c').open_workingtree()
        branch_c = tree_c.branch
        self.build_tree(['branch_a/b'])
        tree_a.add('b')
        tree_a.commit('commit b')
        self.build_tree(['branch_b/c'])
        tree_b.add('c')
        tree_b.commit('commit c')
        # initial push location must be empty
        self.assertEqual(None, branch_b.get_push_location())

        # test push for failure without push location set
        os.chdir('branch_a')
        out = self.runbzr('push', retcode=3)
        self.assertEquals(out,
                ('','bzr: ERROR: No push location known or specified.\n'))

        # test not remembered if cannot actually push
        self.run_bzr('push', '../path/which/doesnt/exist', retcode=3)
        out = self.run_bzr('push', retcode=3)
        self.assertEquals(
                ('', 'bzr: ERROR: No push location known or specified.\n'),
                out)

        # test implicit --remember when no push location set, push fails
        out = self.run_bzr('push', '../branch_b', retcode=3)
        self.assertEquals(out,
                ('','bzr: ERROR: These branches have diverged.  '
                    'Try using "merge" and then "push".\n'))
        self.assertEquals(abspath(branch_a.get_push_location()),
                          abspath(branch_b.bzrdir.root_transport.base))

        # test implicit --remember after resolving previous failure
        uncommit(branch=branch_b, tree=tree_b)
        transport.delete('branch_b/c')
        out = self.run_bzr('push')
        path = branch_a.get_push_location()
        self.assertEquals(('Using saved location: %s\n' 
                           % (local_path_from_url(path),)
                          , 'All changes applied successfully.\n'
                            'Pushed up to revision 2.\n'), out)
        self.assertEqual(path,
                         branch_b.bzrdir.root_transport.base)
        # test explicit --remember
        self.run_bzr('push', '../branch_c', '--remember')
        self.assertEquals(branch_a.get_push_location(),
                          branch_c.bzrdir.root_transport.base)
    
    def test_push_without_tree(self):
        # bzr push from a branch that does not have a checkout should work.
        b = self.make_branch('.')
        out, err = self.run_bzr('push', 'pushed-location')
        self.assertEqual('', out)
        self.assertEqual('Created new branch.\n', err)
        b2 = Branch.open('pushed-location')
        self.assertEndsWith(b2.base, 'pushed-location/')

    def test_push_new_branch_revision_count(self):
        # bzr push of a branch with revisions to a new location 
        # should print the number of revisions equal to the length of the 
        # local branch.
        t = self.make_branch_and_tree('tree')
        self.build_tree(['tree/file'])
        t.add('file')
        t.commit('commit 1')
        os.chdir('tree')
        out, err = self.run_bzr('push', 'pushed-to')
        os.chdir('..')
        self.assertEqual('', out)
        self.assertEqual('Created new branch.\n', err)

    def test_push_only_pushes_history(self):
        # Knit branches should only push the history for the current revision.
        format = BzrDirMetaFormat1()
        format.repository_format = RepositoryFormatKnit1()
        shared_repo = self.make_repository('repo', format=format, shared=True)
        shared_repo.set_make_working_trees(True)

        def make_shared_tree(path):
            shared_repo.bzrdir.root_transport.mkdir(path)
            shared_repo.bzrdir.create_branch_convenience('repo/' + path)
            return WorkingTree.open('repo/' + path)
        tree_a = make_shared_tree('a')
        self.build_tree(['repo/a/file'])
        tree_a.add('file')
        tree_a.commit('commit a-1', rev_id='a-1')
        f = open('repo/a/file', 'ab')
        f.write('more stuff\n')
        f.close()
        tree_a.commit('commit a-2', rev_id='a-2')

        tree_b = make_shared_tree('b')
        self.build_tree(['repo/b/file'])
        tree_b.add('file')
        tree_b.commit('commit b-1', rev_id='b-1')

        self.assertTrue(shared_repo.has_revision('a-1'))
        self.assertTrue(shared_repo.has_revision('a-2'))
        self.assertTrue(shared_repo.has_revision('b-1'))

        # Now that we have a repository with shared files, make sure
        # that things aren't copied out by a 'push'
        os.chdir('repo/b')
        self.run_bzr('push', '../../push-b')
        pushed_tree = WorkingTree.open('../../push-b')
        pushed_repo = pushed_tree.branch.repository
        self.assertFalse(pushed_repo.has_revision('a-1'))
        self.assertFalse(pushed_repo.has_revision('a-2'))
        self.assertTrue(pushed_repo.has_revision('b-1'))

    def test_push_funky_id(self):
        t = self.make_branch_and_tree('tree')
        os.chdir('tree')
        self.build_tree(['filename'])
        t.add('filename', 'funky-chars<>%&;"\'')
        t.commit('commit filename')
        self.run_bzr('push', '../new-tree')

    def test_push_dash_d(self):
        t = self.make_branch_and_tree('from')
        t.commit(allow_pointless=True,
                message='first commit')
        self.runbzr('push -d from to-one')
        self.failUnlessExists('to-one')
        self.runbzr('push -d %s %s' 
            % tuple(map(urlutils.local_path_to_url, ['from', 'to-two'])))
        self.failUnlessExists('to-two')

    def create_simple_tree(self):
        tree = self.make_branch_and_tree('tree')
        self.build_tree(['tree/a'])
        tree.add(['a'], ['a-id'])
        tree.commit('one', rev_id='r1')
        return tree

    def test_push_create_prefix(self):
        """'bzr push --create-prefix' will create leading directories."""
        tree = self.create_simple_tree()

        self.run_bzr_error(['Parent directory of ../new/tree does not exist'],
                           'push', '../new/tree',
                           working_dir='tree')
        self.run_bzr('push', '../new/tree', '--create-prefix',
                     working_dir='tree')
        new_tree = WorkingTree.open('new/tree')
        self.assertEqual(tree.last_revision(), new_tree.last_revision())
        self.failUnlessExists('new/tree/a')

    def test_push_use_existing(self):
        """'bzr push --use-existing-dir' can push into an existing dir.

        By default, 'bzr push' will not use an existing, non-versioned dir.
        """
        tree = self.create_simple_tree()
        self.build_tree(['target/'])

        self.run_bzr_error(['Target directory ../target already exists',
                            'Supply --use-existing-dir',
                           ], 'push', '../target',
                           working_dir='tree')

        self.run_bzr('push', '--use-existing-dir', '../target',
                     working_dir='tree')

        new_tree = WorkingTree.open('target')
        self.assertEqual(tree.last_revision(), new_tree.last_revision())
        # The push should have created target/a
        self.failUnlessExists('target/a')

    def test_push_onto_repo(self):
        """We should be able to 'bzr push' into an existing bzrdir."""
        tree = self.create_simple_tree()
        repo = self.make_repository('repo', shared=True)

        self.run_bzr('push', '../repo',
                     working_dir='tree')

        # Pushing onto an existing bzrdir will create a repository and
        # branch as needed, but will only create a working tree if there was
        # no BzrDir before.
        self.assertRaises(errors.NoWorkingTree, WorkingTree.open, 'repo')
        new_branch = Branch.open('repo')
        self.assertEqual(tree.last_revision(), new_branch.last_revision())

    def test_push_onto_just_bzrdir(self):
        """We don't handle when the target is just a bzrdir.

        Because you shouldn't be able to create *just* a bzrdir in the wild.
        """
        # TODO: jam 20070109 Maybe it would be better to create the repository
        #       if at this point
        tree = self.create_simple_tree()
        a_bzrdir = self.make_bzrdir('dir')

        self.run_bzr_error(['At ../dir you have a valid .bzr control'],
                'push', '../dir',
                working_dir='tree')
