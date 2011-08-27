# Copyright (C) 2008 Canonical Ltd
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

"""Tests for Repository.add_fallback_repository."""

from bzrlib import (
    bzrdir,
    errors,
    remote,
    )
from bzrlib.revision import NULL_REVISION
from bzrlib.tests import TestNotApplicable
from bzrlib.tests.per_repository import TestCaseWithRepository


class TestAddFallbackRepository(TestCaseWithRepository):

    def test_add_fallback_repository(self):
        repo = self.make_repository('repo')
        tree = self.make_branch_and_tree('branch')
        if not repo._format.supports_external_lookups:
            self.assertRaises(errors.UnstackableRepositoryFormat,
                repo.add_fallback_repository, tree.branch.repository)
            raise TestNotApplicable
        repo.add_fallback_repository(tree.branch.repository)
        # the repository has been added correctly if we can query against it.
        revision_id = tree.commit('1st post')
        repo.lock_read()
        self.addCleanup(repo.unlock)
        # can see all revisions
        self.assertEqual(set([revision_id]), set(repo.all_revision_ids()))
        # and can also query the parent map, either on the revisions
        # versionedfiles, which works in tuple keys...
        self.assertEqual({(revision_id,): ()},
            repo.revisions.get_parent_map([(revision_id,)]))
        # ... or on the repository directly...
        self.assertEqual({revision_id: (NULL_REVISION,)},
            repo.get_parent_map([revision_id]))
        # ... or on the repository's graph.
        self.assertEqual({revision_id: (NULL_REVISION,)},
            repo.get_graph().get_parent_map([revision_id]))
        # ... or on the repository's graph, when there is an other repository.
        other = self.make_repository('other')
        other.lock_read()
        self.addCleanup(other.unlock)
        self.assertEqual({revision_id: (NULL_REVISION,)},
            repo.get_graph(other).get_parent_map([revision_id]))

    def test_add_incompatible_fallback_repository(self):
        # Adding an incompatible repository should fail, without the
        # repository locking (lp bug 835035).
        # XXX This is run for every repository format, but we need to
        # carefully specify formats.  OTOH, it is good that this is tested
        # both for local and remote repositories, because the code
        # path is different.
        repo = self.make_repository('repo', format='1.9')
        tree = self.make_branch_and_tree('branch', format='2a')
        repo.lock_read()
        self.addCleanup(repo.unlock)
        # Assert precondition.
        self.assertFalse(tree.branch.repository.is_locked())
        # Assert action.
        self.assertRaises(
            errors.IncompatibleRepositories,
            repo.add_fallback_repository, tree.branch.repository)
        # Assert postcondition.
        self.assertFalse(tree.branch.repository.is_locked())
