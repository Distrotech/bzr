# Copyright (C) 2006 by Canonical Ltd

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

"""Tests for reconiliation of repositories."""


import bzrlib
import bzrlib.errors as errors
from bzrlib.reconcile import reconcile
from bzrlib.revision import Revision
from bzrlib.tests.repository_implementations.test_repository import TestCaseWithRepository
from bzrlib.transport import get_transport
from bzrlib.tree import EmptyTree
from bzrlib.workingtree import WorkingTree


class TestNeedingReweave(TestCaseWithRepository):

    def setUp(self):
        super(TestNeedingReweave, self).setUp()
        
        t = get_transport(self.get_url())
        # an empty inventory with no revision for testing with.
        repo = self.make_repository('inventory_no_revision')
        inv = EmptyTree().inventory
        repo.add_inventory('missing', inv, [])

        # a inventory with no parents and the revision has parents..
        # i.e. a ghost.
        t.copy_tree('inventory_no_revision', 'inventory_one_ghost')
        repo = bzrlib.repository.Repository.open('inventory_one_ghost')
        sha1 = repo.add_inventory('ghost', inv, [])
        rev = Revision(timestamp=0,
                       timezone=None,
                       committer="Foo Bar <foo@example.com>",
                       message="Message",
                       inventory_sha1=sha1,
                       revision_id='ghost')
        rev.parent_ids = ['the_ghost']
        repo.add_revision('ghost', rev)
         
        # a inventory with a ghost that can be corrected now.
        t.copy_tree('inventory_one_ghost', 'inventory_ghost_present')
        repo = bzrlib.repository.Repository.open('inventory_ghost_present')
        sha1 = repo.add_inventory('the_ghost', inv, [])
        rev = Revision(timestamp=0,
                       timezone=None,
                       committer="Foo Bar <foo@example.com>",
                       message="Message",
                       inventory_sha1=sha1,
                       revision_id='the_ghost')
        rev.parent_ids = []
        repo.add_revision('the_ghost', rev)
         

    def test_reweave_empty_makes_backup_wave(self):
        self.make_repository('empty')
        d = bzrlib.bzrdir.BzrDir.open('empty')
        reconcile(d)
        repo = d.open_repository()
        repo.control_weaves.get_weave('inventory.backup',
                                      repo.get_transaction())

    def test_reweave_inventory_without_revision(self):
        d = bzrlib.bzrdir.BzrDir.open('inventory_no_revision')
        reconcile(d)
        # now the backup should have it but not the current inventory
        repo = d.open_repository()
        backup = repo.control_weaves.get_weave('inventory.backup',
                                               repo.get_transaction())
        self.assertTrue('missing' in backup.names())
        self.assertRaises(errors.WeaveRevisionNotPresent,
                          repo.get_inventory, 'missing')

    def test_reweave_inventory_preserves_a_revision_with_ghosts(self):
        d = bzrlib.bzrdir.BzrDir.open('inventory_one_ghost')
        reconcile(d)
        # now the current inventory should still have 'ghost'
        repo = d.open_repository()
        repo.get_inventory('ghost')
        self.assertEqual([None, 'ghost'], repo.get_ancestry('ghost'))
        
    def test_reweave_inventory_fixes_ancestryfor_a_present_ghost(self):
        d = bzrlib.bzrdir.BzrDir.open('inventory_ghost_present')
        repo = d.open_repository()
        self.assertEqual([None, 'ghost'], repo.get_ancestry('ghost'))
        reconcile(d)
        # now the current inventory should still have 'ghost'
        repo = d.open_repository()
        repo.get_inventory('ghost')
        repo.get_inventory('the_ghost')
        self.assertEqual([None, 'the_ghost', 'ghost'], repo.get_ancestry('ghost'))
        self.assertEqual([None, 'the_ghost'], repo.get_ancestry('the_ghost'))
