# Copyright (C) 2006, 2008-2011 Canonical Ltd
# Authors:  Robert Collins <robert.collins@canonical.com>
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

"""Tests for the RevisionTree class."""

from bzrlib import (
    errors,
    revision,
    )
from bzrlib.tests import TestCaseWithTransport


class TestTreeWithCommits(TestCaseWithTransport):

    def setUp(self):
        super(TestTreeWithCommits, self).setUp()
        self.t = self.make_branch_and_tree('.')
        self.rev_id = self.t.commit('foo', allow_pointless=True)
        self.rev_tree = self.t.branch.repository.revision_tree(self.rev_id)

    def test_empty_no_unknowns(self):
        self.assertEqual([], list(self.rev_tree.unknowns()))

    def test_no_conflicts(self):
        self.assertEqual([], list(self.rev_tree.conflicts()))

    def test_parents(self):
        """RevisionTree.parent_ids should match the revision graph."""
        # XXX: TODO: Should this be a repository_implementation test ?
        # at the end of the graph, we get []
        self.assertEqual([], self.rev_tree.get_parent_ids())
        # do a commit to look further up
        revid_2 = self.t.commit('bar', allow_pointless=True)
        self.assertEqual(
            [self.rev_id],
            self.t.branch.repository.revision_tree(revid_2).get_parent_ids())
        # TODO commit a merge and check it is reported correctly.

        # the parents for a revision_tree(NULL_REVISION) are []:
        self.assertEqual([],
            self.t.branch.repository.revision_tree(
                revision.NULL_REVISION).get_parent_ids())

    def test_empty_no_root(self):
        null_tree = self.t.branch.repository.revision_tree(
            revision.NULL_REVISION)
        self.assertIs(None, null_tree.get_root_id())

    def test_get_file_revision_root(self):
        self.assertEquals(self.rev_id,
            self.rev_tree.get_file_revision(self.rev_tree.get_root_id()))

    def test_get_file_revision(self):
        self.build_tree_contents([('a', 'initial')])
        self.t.add(['a'])
        revid1 = self.t.commit('add a')
        revid2 = self.t.commit('another change', allow_pointless=True)
        tree = self.t.branch.repository.revision_tree(revid2)
        self.assertEquals(revid1,
            tree.get_file_revision(tree.path2id('a')))

    def test_get_file_mtime_ghost(self):
        file_id = iter(self.rev_tree.all_file_ids()).next()
        self.rev_tree.root_inventory[file_id].revision = 'ghostrev'
        self.assertRaises(errors.FileTimestampUnavailable, 
            self.rev_tree.get_file_mtime, file_id)
