# Copyright (C) 2007, 2008, 2009 Canonical Ltd
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

import heapq
import time

from bzrlib import (
    debug,
    errors,
    revision,
    symbol_versioning,
    trace,
    tsort,
    )

STEP_UNIQUE_SEARCHER_EVERY = 5

# DIAGRAM of terminology
#       A
#       /\
#      B  C
#      |  |\
#      D  E F
#      |\/| |
#      |/\|/
#      G  H
#
# In this diagram, relative to G and H:
# A, B, C, D, E are common ancestors.
# C, D and E are border ancestors, because each has a non-common descendant.
# D and E are least common ancestors because none of their descendants are
# common ancestors.
# C is not a least common ancestor because its descendant, E, is a common
# ancestor.
#
# The find_unique_lca algorithm will pick A in two steps:
# 1. find_lca('G', 'H') => ['D', 'E']
# 2. Since len(['D', 'E']) > 1, find_lca('D', 'E') => ['A']


class DictParentsProvider(object):
    """A parents provider for Graph objects."""

    def __init__(self, ancestry):
        self.ancestry = ancestry

    def __repr__(self):
        return 'DictParentsProvider(%r)' % self.ancestry

    def get_parent_map(self, keys):
        """See _StackedParentsProvider.get_parent_map"""
        ancestry = self.ancestry
        return dict((k, ancestry[k]) for k in keys if k in ancestry)


class _StackedParentsProvider(object):

    def __init__(self, parent_providers):
        self._parent_providers = parent_providers

    def __repr__(self):
        return "_StackedParentsProvider(%r)" % self._parent_providers

    def get_parent_map(self, keys):
        """Get a mapping of keys => parents

        A dictionary is returned with an entry for each key present in this
        source. If this source doesn't have information about a key, it should
        not include an entry.

        [NULL_REVISION] is used as the parent of the first user-committed
        revision.  Its parent list is empty.

        :param keys: An iterable returning keys to check (eg revision_ids)
        :return: A dictionary mapping each key to its parents
        """
        found = {}
        remaining = set(keys)
        for parents_provider in self._parent_providers:
            new_found = parents_provider.get_parent_map(remaining)
            found.update(new_found)
            remaining.difference_update(new_found)
            if not remaining:
                break
        return found


class CachingParentsProvider(object):
    """A parents provider which will cache the revision => parents as a dict.

    This is useful for providers which have an expensive look up.

    Either a ParentsProvider or a get_parent_map-like callback may be
    supplied.  If it provides extra un-asked-for parents, they will be cached,
    but filtered out of get_parent_map.

    The cache is enabled by default, but may be disabled and re-enabled.
    """
    def __init__(self, parent_provider=None, get_parent_map=None):
        """Constructor.

        :param parent_provider: The ParentProvider to use.  It or
            get_parent_map must be supplied.
        :param get_parent_map: The get_parent_map callback to use.  It or
            parent_provider must be supplied.
        """
        self._real_provider = parent_provider
        if get_parent_map is None:
            self._get_parent_map = self._real_provider.get_parent_map
        else:
            self._get_parent_map = get_parent_map
        self._cache = None
        self.enable_cache(True)

    def __repr__(self):
        return "%s(%r)" % (self.__class__.__name__, self._real_provider)

    def enable_cache(self, cache_misses=True):
        """Enable cache."""
        if self._cache is not None:
            raise AssertionError('Cache enabled when already enabled.')
        self._cache = {}
        self._cache_misses = cache_misses
        self.missing_keys = set()

    def disable_cache(self):
        """Disable and clear the cache."""
        self._cache = None
        self._cache_misses = None
        self.missing_keys = set()

    def get_cached_map(self):
        """Return any cached get_parent_map values."""
        if self._cache is None:
            return None
        return dict(self._cache)

    def get_parent_map(self, keys):
        """See _StackedParentsProvider.get_parent_map."""
        cache = self._cache
        if cache is None:
            cache = self._get_parent_map(keys)
        else:
            needed_revisions = set(key for key in keys if key not in cache)
            # Do not ask for negatively cached keys
            needed_revisions.difference_update(self.missing_keys)
            if needed_revisions:
                parent_map = self._get_parent_map(needed_revisions)
                cache.update(parent_map)
                if self._cache_misses:
                    for key in needed_revisions:
                        if key not in parent_map:
                            self.note_missing_key(key)
        result = {}
        for key in keys:
            value = cache.get(key)
            if value is not None:
                result[key] = value
        return result

    def note_missing_key(self, key):
        """Note that key is a missing key."""
        if self._cache_misses:
            self.missing_keys.add(key)


class Graph(object):
    """Provide incremental access to revision graphs.

    This is the generic implementation; it is intended to be subclassed to
    specialize it for other repository types.
    """

    def __init__(self, parents_provider):
        """Construct a Graph that uses several graphs as its input

        This should not normally be invoked directly, because there may be
        specialized implementations for particular repository types.  See
        Repository.get_graph().

        :param parents_provider: An object providing a get_parent_map call
            conforming to the behavior of
            StackedParentsProvider.get_parent_map.
        """
        if getattr(parents_provider, 'get_parents', None) is not None:
            self.get_parents = parents_provider.get_parents
        if getattr(parents_provider, 'get_parent_map', None) is not None:
            self.get_parent_map = parents_provider.get_parent_map
        self._parents_provider = parents_provider

    def __repr__(self):
        return 'Graph(%r)' % self._parents_provider

    def find_lca(self, *revisions):
        """Determine the lowest common ancestors of the provided revisions

        A lowest common ancestor is a common ancestor none of whose
        descendants are common ancestors.  In graphs, unlike trees, there may
        be multiple lowest common ancestors.

        This algorithm has two phases.  Phase 1 identifies border ancestors,
        and phase 2 filters border ancestors to determine lowest common
        ancestors.

        In phase 1, border ancestors are identified, using a breadth-first
        search starting at the bottom of the graph.  Searches are stopped
        whenever a node or one of its descendants is determined to be common

        In phase 2, the border ancestors are filtered to find the least
        common ancestors.  This is done by searching the ancestries of each
        border ancestor.

        Phase 2 is perfomed on the principle that a border ancestor that is
        not an ancestor of any other border ancestor is a least common
        ancestor.

        Searches are stopped when they find a node that is determined to be a
        common ancestor of all border ancestors, because this shows that it
        cannot be a descendant of any border ancestor.

        The scaling of this operation should be proportional to
        1. The number of uncommon ancestors
        2. The number of border ancestors
        3. The length of the shortest path between a border ancestor and an
           ancestor of all border ancestors.
        """
        border_common, common, sides = self._find_border_ancestors(revisions)
        # We may have common ancestors that can be reached from each other.
        # - ask for the heads of them to filter it down to only ones that
        # cannot be reached from each other - phase 2.
        return self.heads(border_common)

    def find_difference(self, left_revision, right_revision):
        """Determine the graph difference between two revisions"""
        border, common, searchers = self._find_border_ancestors(
            [left_revision, right_revision])
        self._search_for_extra_common(common, searchers)
        left = searchers[0].seen
        right = searchers[1].seen
        return (left.difference(right), right.difference(left))

    def find_distance_to_null(self, target_revision_id, known_revision_ids):
        """Find the left-hand distance to the NULL_REVISION.

        (This can also be considered the revno of a branch at
        target_revision_id.)

        :param target_revision_id: A revision_id which we would like to know
            the revno for.
        :param known_revision_ids: [(revision_id, revno)] A list of known
            revno, revision_id tuples. We'll use this to seed the search.
        """
        # Map from revision_ids to a known value for their revno
        known_revnos = dict(known_revision_ids)
        cur_tip = target_revision_id
        num_steps = 0
        NULL_REVISION = revision.NULL_REVISION
        known_revnos[NULL_REVISION] = 0

        searching_known_tips = list(known_revnos.keys())

        unknown_searched = {}

        while cur_tip not in known_revnos:
            unknown_searched[cur_tip] = num_steps
            num_steps += 1
            to_search = set([cur_tip])
            to_search.update(searching_known_tips)
            parent_map = self.get_parent_map(to_search)
            parents = parent_map.get(cur_tip, None)
            if not parents: # An empty list or None is a ghost
                raise errors.GhostRevisionsHaveNoRevno(target_revision_id,
                                                       cur_tip)
            cur_tip = parents[0]
            next_known_tips = []
            for revision_id in searching_known_tips:
                parents = parent_map.get(revision_id, None)
                if not parents:
                    continue
                next = parents[0]
                next_revno = known_revnos[revision_id] - 1
                if next in unknown_searched:
                    # We have enough information to return a value right now
                    return next_revno + unknown_searched[next]
                if next in known_revnos:
                    continue
                known_revnos[next] = next_revno
                next_known_tips.append(next)
            searching_known_tips = next_known_tips

        # We reached a known revision, so just add in how many steps it took to
        # get there.
        return known_revnos[cur_tip] + num_steps

    def find_unique_ancestors(self, unique_revision, common_revisions):
        """Find the unique ancestors for a revision versus others.

        This returns the ancestry of unique_revision, excluding all revisions
        in the ancestry of common_revisions. If unique_revision is in the
        ancestry, then the empty set will be returned.

        :param unique_revision: The revision_id whose ancestry we are
            interested in.
            XXX: Would this API be better if we allowed multiple revisions on
                 to be searched here?
        :param common_revisions: Revision_ids of ancestries to exclude.
        :return: A set of revisions in the ancestry of unique_revision
        """
        if unique_revision in common_revisions:
            return set()

        # Algorithm description
        # 1) Walk backwards from the unique node and all common nodes.
        # 2) When a node is seen by both sides, stop searching it in the unique
        #    walker, include it in the common walker.
        # 3) Stop searching when there are no nodes left for the unique walker.
        #    At this point, you have a maximal set of unique nodes. Some of
        #    them may actually be common, and you haven't reached them yet.
        # 4) Start new searchers for the unique nodes, seeded with the
        #    information you have so far.
        # 5) Continue searching, stopping the common searches when the search
        #    tip is an ancestor of all unique nodes.
        # 6) Aggregate together unique searchers when they are searching the
        #    same tips. When all unique searchers are searching the same node,
        #    stop move it to a single 'all_unique_searcher'.
        # 7) The 'all_unique_searcher' represents the very 'tip' of searching.
        #    Most of the time this produces very little important information.
        #    So don't step it as quickly as the other searchers.
        # 8) Search is done when all common searchers have completed.

        unique_searcher, common_searcher = self._find_initial_unique_nodes(
            [unique_revision], common_revisions)

        unique_nodes = unique_searcher.seen.difference(common_searcher.seen)
        if not unique_nodes:
            return unique_nodes

        (all_unique_searcher,
         unique_tip_searchers) = self._make_unique_searchers(unique_nodes,
                                    unique_searcher, common_searcher)

        self._refine_unique_nodes(unique_searcher, all_unique_searcher,
                                  unique_tip_searchers, common_searcher)
        true_unique_nodes = unique_nodes.difference(common_searcher.seen)
        if 'graph' in debug.debug_flags:
            trace.mutter('Found %d truly unique nodes out of %d',
                         len(true_unique_nodes), len(unique_nodes))
        return true_unique_nodes

    def _find_initial_unique_nodes(self, unique_revisions, common_revisions):
        """Steps 1-3 of find_unique_ancestors.

        Find the maximal set of unique nodes. Some of these might actually
        still be common, but we are sure that there are no other unique nodes.

        :return: (unique_searcher, common_searcher)
        """

        unique_searcher = self._make_breadth_first_searcher(unique_revisions)
        # we know that unique_revisions aren't in common_revisions, so skip
        # past them.
        unique_searcher.next()
        common_searcher = self._make_breadth_first_searcher(common_revisions)

        # As long as we are still finding unique nodes, keep searching
        while unique_searcher._next_query:
            next_unique_nodes = set(unique_searcher.step())
            next_common_nodes = set(common_searcher.step())

            # Check if either searcher encounters new nodes seen by the other
            # side.
            unique_are_common_nodes = next_unique_nodes.intersection(
                common_searcher.seen)
            unique_are_common_nodes.update(
                next_common_nodes.intersection(unique_searcher.seen))
            if unique_are_common_nodes:
                ancestors = unique_searcher.find_seen_ancestors(
                                unique_are_common_nodes)
                # TODO: This is a bit overboard, we only really care about
                #       the ancestors of the tips because the rest we
                #       already know. This is *correct* but causes us to
                #       search too much ancestry.
                ancestors.update(common_searcher.find_seen_ancestors(ancestors))
                unique_searcher.stop_searching_any(ancestors)
                common_searcher.start_searching(ancestors)

        return unique_searcher, common_searcher

    def _make_unique_searchers(self, unique_nodes, unique_searcher,
                               common_searcher):
        """Create a searcher for all the unique search tips (step 4).

        As a side effect, the common_searcher will stop searching any nodes
        that are ancestors of the unique searcher tips.

        :return: (all_unique_searcher, unique_tip_searchers)
        """
        unique_tips = self._remove_simple_descendants(unique_nodes,
                        self.get_parent_map(unique_nodes))

        if len(unique_tips) == 1:
            unique_tip_searchers = []
            ancestor_all_unique = unique_searcher.find_seen_ancestors(unique_tips)
        else:
            unique_tip_searchers = []
            for tip in unique_tips:
                revs_to_search = unique_searcher.find_seen_ancestors([tip])
                revs_to_search.update(
                    common_searcher.find_seen_ancestors(revs_to_search))
                searcher = self._make_breadth_first_searcher(revs_to_search)
                # We don't care about the starting nodes.
                searcher._label = tip
                searcher.step()
                unique_tip_searchers.append(searcher)

            ancestor_all_unique = None
            for searcher in unique_tip_searchers:
                if ancestor_all_unique is None:
                    ancestor_all_unique = set(searcher.seen)
                else:
                    ancestor_all_unique = ancestor_all_unique.intersection(
                                                searcher.seen)
        # Collapse all the common nodes into a single searcher
        all_unique_searcher = self._make_breadth_first_searcher(
                                ancestor_all_unique)
        if ancestor_all_unique:
            # We've seen these nodes in all the searchers, so we'll just go to
            # the next
            all_unique_searcher.step()

            # Stop any search tips that are already known as ancestors of the
            # unique nodes
            stopped_common = common_searcher.stop_searching_any(
                common_searcher.find_seen_ancestors(ancestor_all_unique))

            total_stopped = 0
            for searcher in unique_tip_searchers:
                total_stopped += len(searcher.stop_searching_any(
                    searcher.find_seen_ancestors(ancestor_all_unique)))
        if 'graph' in debug.debug_flags:
            trace.mutter('For %d unique nodes, created %d + 1 unique searchers'
                         ' (%d stopped search tips, %d common ancestors'
                         ' (%d stopped common)',
                         len(unique_nodes), len(unique_tip_searchers),
                         total_stopped, len(ancestor_all_unique),
                         len(stopped_common))
        return all_unique_searcher, unique_tip_searchers

    def _step_unique_and_common_searchers(self, common_searcher,
                                          unique_tip_searchers,
                                          unique_searcher):
        """Step all the searchers"""
        newly_seen_common = set(common_searcher.step())
        newly_seen_unique = set()
        for searcher in unique_tip_searchers:
            next = set(searcher.step())
            next.update(unique_searcher.find_seen_ancestors(next))
            next.update(common_searcher.find_seen_ancestors(next))
            for alt_searcher in unique_tip_searchers:
                if alt_searcher is searcher:
                    continue
                next.update(alt_searcher.find_seen_ancestors(next))
            searcher.start_searching(next)
            newly_seen_unique.update(next)
        return newly_seen_common, newly_seen_unique

    def _find_nodes_common_to_all_unique(self, unique_tip_searchers,
                                         all_unique_searcher,
                                         newly_seen_unique, step_all_unique):
        """Find nodes that are common to all unique_tip_searchers.

        If it is time, step the all_unique_searcher, and add its nodes to the
        result.
        """
        common_to_all_unique_nodes = newly_seen_unique.copy()
        for searcher in unique_tip_searchers:
            common_to_all_unique_nodes.intersection_update(searcher.seen)
        common_to_all_unique_nodes.intersection_update(
                                    all_unique_searcher.seen)
        # Step all-unique less frequently than the other searchers.
        # In the common case, we don't need to spider out far here, so
        # avoid doing extra work.
        if step_all_unique:
            tstart = time.clock()
            nodes = all_unique_searcher.step()
            common_to_all_unique_nodes.update(nodes)
            if 'graph' in debug.debug_flags:
                tdelta = time.clock() - tstart
                trace.mutter('all_unique_searcher step() took %.3fs'
                             'for %d nodes (%d total), iteration: %s',
                             tdelta, len(nodes), len(all_unique_searcher.seen),
                             all_unique_searcher._iterations)
        return common_to_all_unique_nodes

    def _collapse_unique_searchers(self, unique_tip_searchers,
                                   common_to_all_unique_nodes):
        """Combine searchers that are searching the same tips.

        When two searchers are searching the same tips, we can stop one of the
        searchers. We also know that the maximal set of common ancestors is the
        intersection of the two original searchers.

        :return: A list of searchers that are searching unique nodes.
        """
        # Filter out searchers that don't actually search different
        # nodes. We already have the ancestry intersection for them
        unique_search_tips = {}
        for searcher in unique_tip_searchers:
            stopped = searcher.stop_searching_any(common_to_all_unique_nodes)
            will_search_set = frozenset(searcher._next_query)
            if not will_search_set:
                if 'graph' in debug.debug_flags:
                    trace.mutter('Unique searcher %s was stopped.'
                                 ' (%s iterations) %d nodes stopped',
                                 searcher._label,
                                 searcher._iterations,
                                 len(stopped))
            elif will_search_set not in unique_search_tips:
                # This searcher is searching a unique set of nodes, let it
                unique_search_tips[will_search_set] = [searcher]
            else:
                unique_search_tips[will_search_set].append(searcher)
        # TODO: it might be possible to collapse searchers faster when they
        #       only have *some* search tips in common.
        next_unique_searchers = []
        for searchers in unique_search_tips.itervalues():
            if len(searchers) == 1:
                # Searching unique tips, go for it
                next_unique_searchers.append(searchers[0])
            else:
                # These searchers have started searching the same tips, we
                # don't need them to cover the same ground. The
                # intersection of their ancestry won't change, so create a
                # new searcher, combining their histories.
                next_searcher = searchers[0]
                for searcher in searchers[1:]:
                    next_searcher.seen.intersection_update(searcher.seen)
                if 'graph' in debug.debug_flags:
                    trace.mutter('Combining %d searchers into a single'
                                 ' searcher searching %d nodes with'
                                 ' %d ancestry',
                                 len(searchers),
                                 len(next_searcher._next_query),
                                 len(next_searcher.seen))
                next_unique_searchers.append(next_searcher)
        return next_unique_searchers

    def _refine_unique_nodes(self, unique_searcher, all_unique_searcher,
                             unique_tip_searchers, common_searcher):
        """Steps 5-8 of find_unique_ancestors.

        This function returns when common_searcher has stopped searching for
        more nodes.
        """
        # We step the ancestor_all_unique searcher only every
        # STEP_UNIQUE_SEARCHER_EVERY steps.
        step_all_unique_counter = 0
        # While we still have common nodes to search
        while common_searcher._next_query:
            (newly_seen_common,
             newly_seen_unique) = self._step_unique_and_common_searchers(
                common_searcher, unique_tip_searchers, unique_searcher)
            # These nodes are common ancestors of all unique nodes
            common_to_all_unique_nodes = self._find_nodes_common_to_all_unique(
                unique_tip_searchers, all_unique_searcher, newly_seen_unique,
                step_all_unique_counter==0)
            step_all_unique_counter = ((step_all_unique_counter + 1)
                                       % STEP_UNIQUE_SEARCHER_EVERY)

            if newly_seen_common:
                # If a 'common' node is an ancestor of all unique searchers, we
                # can stop searching it.
                common_searcher.stop_searching_any(
                    all_unique_searcher.seen.intersection(newly_seen_common))
            if common_to_all_unique_nodes:
                common_to_all_unique_nodes.update(
                    common_searcher.find_seen_ancestors(
                        common_to_all_unique_nodes))
                # The all_unique searcher can start searching the common nodes
                # but everyone else can stop.
                # This is the sort of thing where we would like to not have it
                # start_searching all of the nodes, but only mark all of them
                # as seen, and have it search only the actual tips. Otherwise
                # it is another get_parent_map() traversal for it to figure out
                # what we already should know.
                all_unique_searcher.start_searching(common_to_all_unique_nodes)
                common_searcher.stop_searching_any(common_to_all_unique_nodes)

            next_unique_searchers = self._collapse_unique_searchers(
                unique_tip_searchers, common_to_all_unique_nodes)
            if len(unique_tip_searchers) != len(next_unique_searchers):
                if 'graph' in debug.debug_flags:
                    trace.mutter('Collapsed %d unique searchers => %d'
                                 ' at %s iterations',
                                 len(unique_tip_searchers),
                                 len(next_unique_searchers),
                                 all_unique_searcher._iterations)
            unique_tip_searchers = next_unique_searchers

    def get_parent_map(self, revisions):
        """Get a map of key:parent_list for revisions.

        This implementation delegates to get_parents, for old parent_providers
        that do not supply get_parent_map.
        """
        result = {}
        for rev, parents in self.get_parents(revisions):
            if parents is not None:
                result[rev] = parents
        return result

    def _make_breadth_first_searcher(self, revisions):
        return _BreadthFirstSearcher(revisions, self)

    def _find_border_ancestors(self, revisions):
        """Find common ancestors with at least one uncommon descendant.

        Border ancestors are identified using a breadth-first
        search starting at the bottom of the graph.  Searches are stopped
        whenever a node or one of its descendants is determined to be common.

        This will scale with the number of uncommon ancestors.

        As well as the border ancestors, a set of seen common ancestors and a
        list of sets of seen ancestors for each input revision is returned.
        This allows calculation of graph difference from the results of this
        operation.
        """
        if None in revisions:
            raise errors.InvalidRevisionId(None, self)
        common_ancestors = set()
        searchers = [self._make_breadth_first_searcher([r])
                     for r in revisions]
        active_searchers = searchers[:]
        border_ancestors = set()

        while True:
            newly_seen = set()
            for searcher in searchers:
                new_ancestors = searcher.step()
                if new_ancestors:
                    newly_seen.update(new_ancestors)
            new_common = set()
            for revision in newly_seen:
                if revision in common_ancestors:
                    # Not a border ancestor because it was seen as common
                    # already
                    new_common.add(revision)
                    continue
                for searcher in searchers:
                    if revision not in searcher.seen:
                        break
                else:
                    # This is a border because it is a first common that we see
                    # after walking for a while.
                    border_ancestors.add(revision)
                    new_common.add(revision)
            if new_common:
                for searcher in searchers:
                    new_common.update(searcher.find_seen_ancestors(new_common))
                for searcher in searchers:
                    searcher.start_searching(new_common)
                common_ancestors.update(new_common)

            # Figure out what the searchers will be searching next, and if
            # there is only 1 set being searched, then we are done searching,
            # since all searchers would have to be searching the same data,
            # thus it *must* be in common.
            unique_search_sets = set()
            for searcher in searchers:
                will_search_set = frozenset(searcher._next_query)
                if will_search_set not in unique_search_sets:
                    # This searcher is searching a unique set of nodes, let it
                    unique_search_sets.add(will_search_set)

            if len(unique_search_sets) == 1:
                nodes = unique_search_sets.pop()
                uncommon_nodes = nodes.difference(common_ancestors)
                if uncommon_nodes:
                    raise AssertionError("Somehow we ended up converging"
                                         " without actually marking them as"
                                         " in common."
                                         "\nStart_nodes: %s"
                                         "\nuncommon_nodes: %s"
                                         % (revisions, uncommon_nodes))
                break
        return border_ancestors, common_ancestors, searchers

    def heads(self, keys):
        """Return the heads from amongst keys.

        This is done by searching the ancestries of each key.  Any key that is
        reachable from another key is not returned; all the others are.

        This operation scales with the relative depth between any two keys. If
        any two keys are completely disconnected all ancestry of both sides
        will be retrieved.

        :param keys: An iterable of keys.
        :return: A set of the heads. Note that as a set there is no ordering
            information. Callers will need to filter their input to create
            order if they need it.
        """
        candidate_heads = set(keys)
        if revision.NULL_REVISION in candidate_heads:
            # NULL_REVISION is only a head if it is the only entry
            candidate_heads.remove(revision.NULL_REVISION)
            if not candidate_heads:
                return set([revision.NULL_REVISION])
        if len(candidate_heads) < 2:
            return candidate_heads
        searchers = dict((c, self._make_breadth_first_searcher([c]))
                          for c in candidate_heads)
        active_searchers = dict(searchers)
        # skip over the actual candidate for each searcher
        for searcher in active_searchers.itervalues():
            searcher.next()
        # The common walker finds nodes that are common to two or more of the
        # input keys, so that we don't access all history when a currently
        # uncommon search point actually meets up with something behind a
        # common search point. Common search points do not keep searches
        # active; they just allow us to make searches inactive without
        # accessing all history.
        common_walker = self._make_breadth_first_searcher([])
        while len(active_searchers) > 0:
            ancestors = set()
            # advance searches
            try:
                common_walker.next()
            except StopIteration:
                # No common points being searched at this time.
                pass
            for candidate in active_searchers.keys():
                try:
                    searcher = active_searchers[candidate]
                except KeyError:
                    # rare case: we deleted candidate in a previous iteration
                    # through this for loop, because it was determined to be
                    # a descendant of another candidate.
                    continue
                try:
                    ancestors.update(searcher.next())
                except StopIteration:
                    del active_searchers[candidate]
                    continue
            # process found nodes
            new_common = set()
            for ancestor in ancestors:
                if ancestor in candidate_heads:
                    candidate_heads.remove(ancestor)
                    del searchers[ancestor]
                    if ancestor in active_searchers:
                        del active_searchers[ancestor]
                # it may meet up with a known common node
                if ancestor in common_walker.seen:
                    # some searcher has encountered our known common nodes:
                    # just stop it
                    ancestor_set = set([ancestor])
                    for searcher in searchers.itervalues():
                        searcher.stop_searching_any(ancestor_set)
                else:
                    # or it may have been just reached by all the searchers:
                    for searcher in searchers.itervalues():
                        if ancestor not in searcher.seen:
                            break
                    else:
                        # The final active searcher has just reached this node,
                        # making it be known as a descendant of all candidates,
                        # so we can stop searching it, and any seen ancestors
                        new_common.add(ancestor)
                        for searcher in searchers.itervalues():
                            seen_ancestors =\
                                searcher.find_seen_ancestors([ancestor])
                            searcher.stop_searching_any(seen_ancestors)
            common_walker.start_searching(new_common)
        return candidate_heads

    def find_merge_order(self, tip_revision_id, lca_revision_ids):
        """Find the order that each revision was merged into tip.

        This basically just walks backwards with a stack, and walks left-first
        until it finds a node to stop.
        """
        if len(lca_revision_ids) == 1:
            return list(lca_revision_ids)
        looking_for = set(lca_revision_ids)
        # TODO: Is there a way we could do this "faster" by batching up the
        # get_parent_map requests?
        # TODO: Should we also be culling the ancestry search right away? We
        # could add looking_for to the "stop" list, and walk their
        # ancestry in batched mode. The flip side is it might mean we walk a
        # lot of "stop" nodes, rather than only the minimum.
        # Then again, without it we may trace back into ancestry we could have
        # stopped early.
        stack = [tip_revision_id]
        found = []
        stop = set()
        while stack and looking_for:
            next = stack.pop()
            stop.add(next)
            if next in looking_for:
                found.append(next)
                looking_for.remove(next)
                if len(looking_for) == 1:
                    found.append(looking_for.pop())
                    break
                continue
            parent_ids = self.get_parent_map([next]).get(next, None)
            if not parent_ids: # Ghost, nothing to search here
                continue
            for parent_id in reversed(parent_ids):
                # TODO: (performance) We see the parent at this point, but we
                #       wait to mark it until later to make sure we get left
                #       parents before right parents. However, instead of
                #       waiting until we have traversed enough parents, we
                #       could instead note that we've found it, and once all
                #       parents are in the stack, just reverse iterate the
                #       stack for them.
                if parent_id not in stop:
                    # this will need to be searched
                    stack.append(parent_id)
                stop.add(parent_id)
        return found

    def find_unique_lca(self, left_revision, right_revision,
                        count_steps=False):
        """Find a unique LCA.

        Find lowest common ancestors.  If there is no unique  common
        ancestor, find the lowest common ancestors of those ancestors.

        Iteration stops when a unique lowest common ancestor is found.
        The graph origin is necessarily a unique lowest common ancestor.

        Note that None is not an acceptable substitute for NULL_REVISION.
        in the input for this method.

        :param count_steps: If True, the return value will be a tuple of
            (unique_lca, steps) where steps is the number of times that
            find_lca was run.  If False, only unique_lca is returned.
        """
        revisions = [left_revision, right_revision]
        steps = 0
        while True:
            steps += 1
            lca = self.find_lca(*revisions)
            if len(lca) == 1:
                result = lca.pop()
                if count_steps:
                    return result, steps
                else:
                    return result
            if len(lca) == 0:
                raise errors.NoCommonAncestor(left_revision, right_revision)
            revisions = lca

    def iter_ancestry(self, revision_ids):
        """Iterate the ancestry of this revision.

        :param revision_ids: Nodes to start the search
        :return: Yield tuples mapping a revision_id to its parents for the
            ancestry of revision_id.
            Ghosts will be returned with None as their parents, and nodes
            with no parents will have NULL_REVISION as their only parent. (As
            defined by get_parent_map.)
            There will also be a node for (NULL_REVISION, ())
        """
        pending = set(revision_ids)
        processed = set()
        while pending:
            processed.update(pending)
            next_map = self.get_parent_map(pending)
            next_pending = set()
            for item in next_map.iteritems():
                yield item
                next_pending.update(p for p in item[1] if p not in processed)
            ghosts = pending.difference(next_map)
            for ghost in ghosts:
                yield (ghost, None)
            pending = next_pending

    def iter_topo_order(self, revisions):
        """Iterate through the input revisions in topological order.

        This sorting only ensures that parents come before their children.
        An ancestor may sort after a descendant if the relationship is not
        visible in the supplied list of revisions.
        """
        sorter = tsort.TopoSorter(self.get_parent_map(revisions))
        return sorter.iter_topo_order()

    def is_ancestor(self, candidate_ancestor, candidate_descendant):
        """Determine whether a revision is an ancestor of another.

        We answer this using heads() as heads() has the logic to perform the
        smallest number of parent lookups to determine the ancestral
        relationship between N revisions.
        """
        return set([candidate_descendant]) == self.heads(
            [candidate_ancestor, candidate_descendant])

    def is_between(self, revid, lower_bound_revid, upper_bound_revid):
        """Determine whether a revision is between two others.

        returns true if and only if:
        lower_bound_revid <= revid <= upper_bound_revid
        """
        return ((upper_bound_revid is None or
                    self.is_ancestor(revid, upper_bound_revid)) and
               (lower_bound_revid is None or
                    self.is_ancestor(lower_bound_revid, revid)))

    def _search_for_extra_common(self, common, searchers):
        """Make sure that unique nodes are genuinely unique.

        After _find_border_ancestors, all nodes marked "common" are indeed
        common. Some of the nodes considered unique are not, due to history
        shortcuts stopping the searches early.

        We know that we have searched enough when all common search tips are
        descended from all unique (uncommon) nodes because we know that a node
        cannot be an ancestor of its own ancestor.

        :param common: A set of common nodes
        :param searchers: The searchers returned from _find_border_ancestors
        :return: None
        """
        # Basic algorithm...
        #   A) The passed in searchers should all be on the same tips, thus
        #      they should be considered the "common" searchers.
        #   B) We find the difference between the searchers, these are the
        #      "unique" nodes for each side.
        #   C) We do a quick culling so that we only start searching from the
        #      more interesting unique nodes. (A unique ancestor is more
        #      interesting than any of its children.)
        #   D) We start searching for ancestors common to all unique nodes.
        #   E) We have the common searchers stop searching any ancestors of
        #      nodes found by (D)
        #   F) When there are no more common search tips, we stop

        # TODO: We need a way to remove unique_searchers when they overlap with
        #       other unique searchers.
        if len(searchers) != 2:
            raise NotImplementedError(
                "Algorithm not yet implemented for > 2 searchers")
        common_searchers = searchers
        left_searcher = searchers[0]
        right_searcher = searchers[1]
        unique = left_searcher.seen.symmetric_difference(right_searcher.seen)
        if not unique: # No unique nodes, nothing to do
            return
        total_unique = len(unique)
        unique = self._remove_simple_descendants(unique,
                    self.get_parent_map(unique))
        simple_unique = len(unique)

        unique_searchers = []
        for revision_id in unique:
            if revision_id in left_searcher.seen:
                parent_searcher = left_searcher
            else:
                parent_searcher = right_searcher
            revs_to_search = parent_searcher.find_seen_ancestors([revision_id])
            if not revs_to_search: # XXX: This shouldn't be possible
                revs_to_search = [revision_id]
            searcher = self._make_breadth_first_searcher(revs_to_search)
            # We don't care about the starting nodes.
            searcher.step()
            unique_searchers.append(searcher)

        # possible todo: aggregate the common searchers into a single common
        #   searcher, just make sure that we include the nodes into the .seen
        #   properties of the original searchers

        ancestor_all_unique = None
        for searcher in unique_searchers:
            if ancestor_all_unique is None:
                ancestor_all_unique = set(searcher.seen)
            else:
                ancestor_all_unique = ancestor_all_unique.intersection(
                                            searcher.seen)

        trace.mutter('Started %s unique searchers for %s unique revisions',
                     simple_unique, total_unique)

        while True: # If we have no more nodes we have nothing to do
            newly_seen_common = set()
            for searcher in common_searchers:
                newly_seen_common.update(searcher.step())
            newly_seen_unique = set()
            for searcher in unique_searchers:
                newly_seen_unique.update(searcher.step())
            new_common_unique = set()
            for revision in newly_seen_unique:
                for searcher in unique_searchers:
                    if revision not in searcher.seen:
                        break
                else:
                    # This is a border because it is a first common that we see
                    # after walking for a while.
                    new_common_unique.add(revision)
            if newly_seen_common:
                # These are nodes descended from one of the 'common' searchers.
                # Make sure all searchers are on the same page
                for searcher in common_searchers:
                    newly_seen_common.update(
                        searcher.find_seen_ancestors(newly_seen_common))
                # We start searching the whole ancestry. It is a bit wasteful,
                # though. We really just want to mark all of these nodes as
                # 'seen' and then start just the tips. However, it requires a
                # get_parent_map() call to figure out the tips anyway, and all
                # redundant requests should be fairly fast.
                for searcher in common_searchers:
                    searcher.start_searching(newly_seen_common)

                # If a 'common' node is an ancestor of all unique searchers, we
                # can stop searching it.
                stop_searching_common = ancestor_all_unique.intersection(
                                            newly_seen_common)
                if stop_searching_common:
                    for searcher in common_searchers:
                        searcher.stop_searching_any(stop_searching_common)
            if new_common_unique:
                # We found some ancestors that are common
                for searcher in unique_searchers:
                    new_common_unique.update(
                        searcher.find_seen_ancestors(new_common_unique))
                # Since these are common, we can grab another set of ancestors
                # that we have seen
                for searcher in common_searchers:
                    new_common_unique.update(
                        searcher.find_seen_ancestors(new_common_unique))

                # We can tell all of the unique searchers to start at these
                # nodes, and tell all of the common searchers to *stop*
                # searching these nodes
                for searcher in unique_searchers:
                    searcher.start_searching(new_common_unique)
                for searcher in common_searchers:
                    searcher.stop_searching_any(new_common_unique)
                ancestor_all_unique.update(new_common_unique)

                # Filter out searchers that don't actually search different
                # nodes. We already have the ancestry intersection for them
                next_unique_searchers = []
                unique_search_sets = set()
                for searcher in unique_searchers:
                    will_search_set = frozenset(searcher._next_query)
                    if will_search_set not in unique_search_sets:
                        # This searcher is searching a unique set of nodes, let it
                        unique_search_sets.add(will_search_set)
                        next_unique_searchers.append(searcher)
                unique_searchers = next_unique_searchers
            for searcher in common_searchers:
                if searcher._next_query:
                    break
            else:
                # All common searcher have stopped searching
                return

    def _remove_simple_descendants(self, revisions, parent_map):
        """remove revisions which are children of other ones in the set

        This doesn't do any graph searching, it just checks the immediate
        parent_map to find if there are any children which can be removed.

        :param revisions: A set of revision_ids
        :return: A set of revision_ids with the children removed
        """
        simple_ancestors = revisions.copy()
        # TODO: jam 20071214 we *could* restrict it to searching only the
        #       parent_map of revisions already present in 'revisions', but
        #       considering the general use case, I think this is actually
        #       better.

        # This is the same as the following loop. I don't know that it is any
        # faster.
        ## simple_ancestors.difference_update(r for r, p_ids in parent_map.iteritems()
        ##     if p_ids is not None and revisions.intersection(p_ids))
        ## return simple_ancestors

        # Yet Another Way, invert the parent map (which can be cached)
        ## descendants = {}
        ## for revision_id, parent_ids in parent_map.iteritems():
        ##   for p_id in parent_ids:
        ##       descendants.setdefault(p_id, []).append(revision_id)
        ## for revision in revisions.intersection(descendants):
        ##   simple_ancestors.difference_update(descendants[revision])
        ## return simple_ancestors
        for revision, parent_ids in parent_map.iteritems():
            if parent_ids is None:
                continue
            for parent_id in parent_ids:
                if parent_id in revisions:
                    # This node has a parent present in the set, so we can
                    # remove it
                    simple_ancestors.discard(revision)
                    break
        return simple_ancestors


class HeadsCache(object):
    """A cache of results for graph heads calls."""

    def __init__(self, graph):
        self.graph = graph
        self._heads = {}

    def heads(self, keys):
        """Return the heads of keys.

        This matches the API of Graph.heads(), specifically the return value is
        a set which can be mutated, and ordering of the input is not preserved
        in the output.

        :see also: Graph.heads.
        :param keys: The keys to calculate heads for.
        :return: A set containing the heads, which may be mutated without
            affecting future lookups.
        """
        keys = frozenset(keys)
        try:
            return set(self._heads[keys])
        except KeyError:
            heads = self.graph.heads(keys)
            self._heads[keys] = heads
            return set(heads)


class FrozenHeadsCache(object):
    """Cache heads() calls, assuming the caller won't modify them."""

    def __init__(self, graph):
        self.graph = graph
        self._heads = {}

    def heads(self, keys):
        """Return the heads of keys.

        Similar to Graph.heads(). The main difference is that the return value
        is a frozen set which cannot be mutated.

        :see also: Graph.heads.
        :param keys: The keys to calculate heads for.
        :return: A frozenset containing the heads.
        """
        keys = frozenset(keys)
        try:
            return self._heads[keys]
        except KeyError:
            heads = frozenset(self.graph.heads(keys))
            self._heads[keys] = heads
            return heads

    def cache(self, keys, heads):
        """Store a known value."""
        self._heads[frozenset(keys)] = frozenset(heads)


class _KnownGraphNode(object):
    """Represents a single object in the known graph."""

    __slots__ = ('key', 'parent_keys', 'child_keys', 'linear_dominator',
                 'dominator_distance', 'gdfo', 'ancestor_of')

    def __init__(self, key, parent_keys):
        self.key = key
        self.parent_keys = parent_keys
        self.child_keys = []
        self.linear_dominator = None
        self.dominator_distance = 0
        # Greatest distance from origin
        self.gdfo = None
        # This will become a tuple of known heads that have this node as an
        # ancestor
        self.ancestor_of = None

    def __repr__(self):
        return '%s(%s  gdfo:%s par:%s child:%s dom:%s %s)' % (
            self.__class__.__name__, self.key, self.gdfo,
            self.parent_keys, self.child_keys,
            self.linear_dominator, self.dominator_distance)


_counters = [0, 0, 0, 0, 0, 0]
class KnownGraph(object):
    """This is a class which assumes we already know the full graph."""

    def __init__(self, parent_map):
        """Create a new KnownGraph instance.

        :param parent_map: A dictionary mapping key => parent_keys
        """
        self._nodes = {}
        self._initialize_nodes(parent_map)

    def _initialize_nodes(self, parent_map):
        """Populate self._nodes.

        After this has finished, self._nodes will have an entry for every entry
        in parent_map. Ghosts will have a parent_keys = None, all nodes found
        will also have .child_keys populated with all known child_keys.
        """
        nodes = self._nodes
        for key, parent_keys in parent_map.iteritems():
            if key in nodes:
                node = nodes[key]
                assert node.parent_keys is None
                node.parent_keys = parent_keys
            else:
                node = _KnownGraphNode(key, parent_keys)
                nodes[key] = node
            for parent_key in parent_keys:
                try:
                    parent_node = nodes[parent_key]
                except KeyError:
                    parent_node = _KnownGraphNode(parent_key, None)
                    nodes[parent_key] = parent_node
                parent_node.child_keys.append(key)
        self._find_linear_dominators()
        self._find_gdfo()

    def _find_linear_dominators(self):
        """For each node in the set, find any linear dominators.

        For any given node, the 'linear dominator' is an ancestor, such that
        all parents between this node and that one have a single parent, and a
        single child. So if A->B->C->D then B,C,D all have a linear dominator
        of A. Because there are no interesting siblings, we can quickly skip to
        the nearest dominator when doing comparisons.
        """
        def check_node(node):
            if node.parent_keys is None or len(node.parent_keys) != 1:
                # This node is either a ghost, a tail, or has multiple parents
                # It its own dominator
                node.linear_dominator = node.key
                node.dominator_distance = 0
                return None
            parent_node = self._nodes[node.parent_keys[0]]
            if len(parent_node.child_keys) > 1:
                # The parent has multiple children, so *this* node is the
                # dominator
                node.linear_dominator = node.key
                node.dominator_distance = 0
                return None
            # The parent is already filled in, so add and continue
            if parent_node.linear_dominator is not None:
                node.linear_dominator = parent_node.linear_dominator
                node.dominator_distance = parent_node.dominator_distance + 1
                return None
            # We don't know this node, or its parent node, so start walking to
            # next
            return parent_node

        for node in self._nodes.itervalues():
            # The parent is not filled in, so walk until we get somewhere
            if node.linear_dominator is not None: #already done
                continue
            next_node = check_node(node)
            if next_node is None:
                # Nothing more needs to be done
                continue
            stack = []
            while next_node is not None:
                stack.append(node)
                node = next_node
                next_node = check_node(node)
            # The stack now contains the linear chain, and 'node' should have
            # been labeled
            assert node.linear_dominator is not None
            dominator = node.linear_dominator
            while stack:
                next_node = stack.pop()
                next_node.linear_dominator = dominator
                next_node.dominator_distance = node.dominator_distance + 1
                node = next_node

    def _find_gdfo(self):
        # TODO: Consider moving the tails search into the first-pass over the
        #       data, inside _find_linear_dominators
        def find_tails():
            return [node for node in self._nodes.itervalues()
                       if not node.parent_keys]
        tails = find_tails()
        todo = []
        for node in tails:
            node.gdfo = 1
            heapq.heappush(todo, (1, node))
        processed = 0
        skipped = 0
        max_gdfo = len(self._nodes) + 1
        while todo:
            gdfo, next = heapq.heappop(todo)
            processed += 1
            if next.gdfo is not None and gdfo < next.gdfo:
                # This node was reached from a longer path, we assume it was
                # enqued correctly with the longer gdfo, so don't continue
                # processing now
                assert gdfo < next.gdfo
                skipped += 1
                continue
            next_gdfo = gdfo + 1
            assert next_gdfo <= max_gdfo
            for child_key in next.child_keys:
                child_node = self._nodes[child_key]
                if child_node.gdfo is None or child_node.gdfo < next_gdfo:
                    # Only enque children when all of their parents have been
                    # resolved
                    for parent_key in child_node.parent_keys:
                        # We know that 'this' parent is counted
                        if parent_key != next.key:
                            parent_node = self._nodes[parent_key]
                            if parent_node.gdfo is None:
                                break
                    else:
                        child_node.gdfo = next_gdfo
                        heapq.heappush(todo, (next_gdfo, child_node))

    def heads(self, keys):
        """Return the heads from amongst keys.

        This is done by searching the ancestries of each key.  Any key that is
        reachable from another key is not returned; all the others are.

        This operation scales with the relative depth between any two keys. If
        any two keys are completely disconnected all ancestry of both sides
        will be retrieved.

        :param keys: An iterable of keys.
        :return: A set of the heads. Note that as a set there is no ordering
            information. Callers will need to filter their input to create
            order if they need it.
        """
        candidate_nodes = dict((key, self._nodes[key]) for key in keys)
        if revision.NULL_REVISION in candidate_nodes:
            # NULL_REVISION is only a head if it is the only entry
            candidate_nodes.pop(revision.NULL_REVISION)
            if not candidate_nodes:
                return set([revision.NULL_REVISION])
        if len(candidate_nodes) < 2:
            return set(candidate_nodes)
        dominator = None
        # TODO: We could optimize for the len(candidate_nodes) > 2 by checking
        #       for *any* pair-wise matching, and then eliminating one of the
        #       nodes trivially. However, the fairly common case is just 2
        #       keys, so we'll focus on that, first
        for node in candidate_nodes.itervalues():
            if dominator is None:
                dominator = node.linear_dominator
            elif dominator != node.linear_dominator:
                break
        else:
            # All of these nodes have the same linear_dominator, which means
            # they are in a line, the head is just the one with the highest
            # distance
            def get_distance(key):
                return self._nodes[key].dominator_distance
            def get_linear_head():
                return max(candidate_nodes, key=get_distance)
            return set([get_linear_head()])
        return self._heads_from_candidate_nodes(candidate_nodes)

    def _heads_from_candidate_nodes(self, candidate_nodes):
        queue = []
        to_cleanup = []
        to_cleanup_append = to_cleanup.append
        for node in candidate_nodes.itervalues():
            assert node.ancestor_of is None
            node.ancestor_of = (node.key,)
            queue.append((-node.gdfo, node))
            to_cleanup_append(node)
        heapq.heapify(queue)
        # These are nodes that we determined are 'common' that we are no longer
        # walking
        # Now we walk nodes until all nodes that are being walked are 'common'
        num_candidates = len(candidate_nodes)
        counters = _counters
        nodes = self._nodes
        heappop = heapq.heappop
        heappush = heapq.heappush
        while queue and len(candidate_nodes) > 1:
            # counters[0] += 1
            _, next = heappop(queue)
            # assert next.ancestor_of is not None
            next_ancestor_of = next.ancestor_of
            if len(next_ancestor_of) == num_candidates:
                # This node is now considered 'common'
                # Make sure all parent nodes are marked as such
                for parent_key in node.parent_keys:
                    parent_node = nodes[parent_key]
                    if parent_node.ancestor_of is not None:
                        parent_node.ancestor_of = next_ancestor_of
                if node.linear_dominator != node.key:
                    parent_node = nodes[node.linear_dominator]
                    if parent_node.ancestor_of is not None:
                        parent_node.ancestor_of = next_ancestor_of
                continue
            if next.parent_keys is None:
                # This is a ghost
                continue
            # Now project the current nodes ancestor list to the parent nodes,
            # and queue them up to be walked
            # Note: using linear_dominator speeds things up quite a bit
            #       enough that we actually start to be slightly faster
            #       than the default heads() implementation
            if next.linear_dominator != next.key:
                # We are at the tip of a long linear region
                # We know that there is nothing between here and the tail
                # that is interesting, so skip to the end
                # counters[5] += 1
                parent_keys = [next.linear_dominator]
            else:
                parent_keys = next.parent_keys
            for parent_key in parent_keys:
                # counters[1] += 1
                if parent_key in candidate_nodes:
                    candidate_nodes.pop(parent_key)
                    if len(candidate_nodes) <= 1:
                        break
                parent_node = nodes[parent_key]
                ancestor_of = parent_node.ancestor_of
                if ancestor_of is None:
                    # counters[2] += 1
                    # This node hasn't been walked yet
                    parent_node.ancestor_of = next_ancestor_of
                    # Enqueue this node
                    heappush(queue, (-parent_node.gdfo, parent_node))
                    to_cleanup_append(parent_node)
                elif ancestor_of != next_ancestor_of:
                    # counters[3] += 1
                    # Combine to get the full set of parents
                    all_ancestors = set(ancestor_of)
                    all_ancestors.update(next_ancestor_of)
                    parent_node.ancestor_of = tuple(sorted(all_ancestors))
        def cleanup():
            for node in to_cleanup:
                # counters[4] += 1
                node.ancestor_of = None
        cleanup()
        return set(candidate_nodes)

    def get_parent_map(self, keys):
        # Thunk to match the Graph._parents_provider api.
        nodes = [self._nodes[key] for key in keys]
        return dict((node.key, node.parent_keys)
                    for node in nodes if node.parent_keys is not None)



class _BreadthFirstSearcher(object):
    """Parallel search breadth-first the ancestry of revisions.

    This class implements the iterator protocol, but additionally
    1. provides a set of seen ancestors, and
    2. allows some ancestries to be unsearched, via stop_searching_any
    """

    def __init__(self, revisions, parents_provider):
        self._iterations = 0
        self._next_query = set(revisions)
        self.seen = set()
        self._started_keys = set(self._next_query)
        self._stopped_keys = set()
        self._parents_provider = parents_provider
        self._returning = 'next_with_ghosts'
        self._current_present = set()
        self._current_ghosts = set()
        self._current_parents = {}

    def __repr__(self):
        if self._iterations:
            prefix = "searching"
        else:
            prefix = "starting"
        search = '%s=%r' % (prefix, list(self._next_query))
        return ('_BreadthFirstSearcher(iterations=%d, %s,'
                ' seen=%r)' % (self._iterations, search, list(self.seen)))

    def get_result(self):
        """Get a SearchResult for the current state of this searcher.

        :return: A SearchResult for this search so far. The SearchResult is
            static - the search can be advanced and the search result will not
            be invalidated or altered.
        """
        if self._returning == 'next':
            # We have to know the current nodes children to be able to list the
            # exclude keys for them. However, while we could have a second
            # look-ahead result buffer and shuffle things around, this method
            # is typically only called once per search - when memoising the
            # results of the search.
            found, ghosts, next, parents = self._do_query(self._next_query)
            # pretend we didn't query: perhaps we should tweak _do_query to be
            # entirely stateless?
            self.seen.difference_update(next)
            next_query = next.union(ghosts)
        else:
            next_query = self._next_query
        excludes = self._stopped_keys.union(next_query)
        included_keys = self.seen.difference(excludes)
        return SearchResult(self._started_keys, excludes, len(included_keys),
            included_keys)

    def step(self):
        try:
            return self.next()
        except StopIteration:
            return ()

    def next(self):
        """Return the next ancestors of this revision.

        Ancestors are returned in the order they are seen in a breadth-first
        traversal.  No ancestor will be returned more than once. Ancestors are
        returned before their parentage is queried, so ghosts and missing
        revisions (including the start revisions) are included in the result.
        This can save a round trip in LCA style calculation by allowing
        convergence to be detected without reading the data for the revision
        the convergence occurs on.

        :return: A set of revision_ids.
        """
        if self._returning != 'next':
            # switch to returning the query, not the results.
            self._returning = 'next'
            self._iterations += 1
        else:
            self._advance()
        if len(self._next_query) == 0:
            raise StopIteration()
        # We have seen what we're querying at this point as we are returning
        # the query, not the results.
        self.seen.update(self._next_query)
        return self._next_query

    def next_with_ghosts(self):
        """Return the next found ancestors, with ghosts split out.

        Ancestors are returned in the order they are seen in a breadth-first
        traversal.  No ancestor will be returned more than once. Ancestors are
        returned only after asking for their parents, which allows us to detect
        which revisions are ghosts and which are not.

        :return: A tuple with (present ancestors, ghost ancestors) sets.
        """
        if self._returning != 'next_with_ghosts':
            # switch to returning the results, not the current query.
            self._returning = 'next_with_ghosts'
            self._advance()
        if len(self._next_query) == 0:
            raise StopIteration()
        self._advance()
        return self._current_present, self._current_ghosts

    def _advance(self):
        """Advance the search.

        Updates self.seen, self._next_query, self._current_present,
        self._current_ghosts, self._current_parents and self._iterations.
        """
        self._iterations += 1
        found, ghosts, next, parents = self._do_query(self._next_query)
        self._current_present = found
        self._current_ghosts = ghosts
        self._next_query = next
        self._current_parents = parents
        # ghosts are implicit stop points, otherwise the search cannot be
        # repeated when ghosts are filled.
        self._stopped_keys.update(ghosts)

    def _do_query(self, revisions):
        """Query for revisions.

        Adds revisions to the seen set.

        :param revisions: Revisions to query.
        :return: A tuple: (set(found_revisions), set(ghost_revisions),
           set(parents_of_found_revisions), dict(found_revisions:parents)).
        """
        found_revisions = set()
        parents_of_found = set()
        # revisions may contain nodes that point to other nodes in revisions:
        # we want to filter them out.
        self.seen.update(revisions)
        parent_map = self._parents_provider.get_parent_map(revisions)
        found_revisions.update(parent_map)
        for rev_id, parents in parent_map.iteritems():
            if parents is None:
                continue
            new_found_parents = [p for p in parents if p not in self.seen]
            if new_found_parents:
                # Calling set.update() with an empty generator is actually
                # rather expensive.
                parents_of_found.update(new_found_parents)
        ghost_revisions = revisions - found_revisions
        return found_revisions, ghost_revisions, parents_of_found, parent_map

    def __iter__(self):
        return self

    def find_seen_ancestors(self, revisions):
        """Find ancestors of these revisions that have already been seen.

        This function generally makes the assumption that querying for the
        parents of a node that has already been queried is reasonably cheap.
        (eg, not a round trip to a remote host).
        """
        # TODO: Often we might ask one searcher for its seen ancestors, and
        #       then ask another searcher the same question. This can result in
        #       searching the same revisions repeatedly if the two searchers
        #       have a lot of overlap.
        all_seen = self.seen
        pending = set(revisions).intersection(all_seen)
        seen_ancestors = set(pending)

        if self._returning == 'next':
            # self.seen contains what nodes have been returned, not what nodes
            # have been queried. We don't want to probe for nodes that haven't
            # been searched yet.
            not_searched_yet = self._next_query
        else:
            not_searched_yet = ()
        pending.difference_update(not_searched_yet)
        get_parent_map = self._parents_provider.get_parent_map
        while pending:
            parent_map = get_parent_map(pending)
            all_parents = []
            # We don't care if it is a ghost, since it can't be seen if it is
            # a ghost
            for parent_ids in parent_map.itervalues():
                all_parents.extend(parent_ids)
            next_pending = all_seen.intersection(all_parents).difference(seen_ancestors)
            seen_ancestors.update(next_pending)
            next_pending.difference_update(not_searched_yet)
            pending = next_pending

        return seen_ancestors

    def stop_searching_any(self, revisions):
        """
        Remove any of the specified revisions from the search list.

        None of the specified revisions are required to be present in the
        search list.

        It is okay to call stop_searching_any() for revisions which were seen
        in previous iterations. It is the callers responsibility to call
        find_seen_ancestors() to make sure that current search tips that are
        ancestors of those revisions are also stopped.  All explicitly stopped
        revisions will be excluded from the search result's get_keys(), though.
        """
        # TODO: does this help performance?
        # if not revisions:
        #     return set()
        revisions = frozenset(revisions)
        if self._returning == 'next':
            stopped = self._next_query.intersection(revisions)
            self._next_query = self._next_query.difference(revisions)
        else:
            stopped_present = self._current_present.intersection(revisions)
            stopped = stopped_present.union(
                self._current_ghosts.intersection(revisions))
            self._current_present.difference_update(stopped)
            self._current_ghosts.difference_update(stopped)
            # stopping 'x' should stop returning parents of 'x', but
            # not if 'y' always references those same parents
            stop_rev_references = {}
            for rev in stopped_present:
                for parent_id in self._current_parents[rev]:
                    if parent_id not in stop_rev_references:
                        stop_rev_references[parent_id] = 0
                    stop_rev_references[parent_id] += 1
            # if only the stopped revisions reference it, the ref count will be
            # 0 after this loop
            for parents in self._current_parents.itervalues():
                for parent_id in parents:
                    try:
                        stop_rev_references[parent_id] -= 1
                    except KeyError:
                        pass
            stop_parents = set()
            for rev_id, refs in stop_rev_references.iteritems():
                if refs == 0:
                    stop_parents.add(rev_id)
            self._next_query.difference_update(stop_parents)
        self._stopped_keys.update(stopped)
        self._stopped_keys.update(revisions)
        return stopped

    def start_searching(self, revisions):
        """Add revisions to the search.

        The parents of revisions will be returned from the next call to next()
        or next_with_ghosts(). If next_with_ghosts was the most recently used
        next* call then the return value is the result of looking up the
        ghost/not ghost status of revisions. (A tuple (present, ghosted)).
        """
        revisions = frozenset(revisions)
        self._started_keys.update(revisions)
        new_revisions = revisions.difference(self.seen)
        if self._returning == 'next':
            self._next_query.update(new_revisions)
            self.seen.update(new_revisions)
        else:
            # perform a query on revisions
            revs, ghosts, query, parents = self._do_query(revisions)
            self._stopped_keys.update(ghosts)
            self._current_present.update(revs)
            self._current_ghosts.update(ghosts)
            self._next_query.update(query)
            self._current_parents.update(parents)
            return revs, ghosts


class SearchResult(object):
    """The result of a breadth first search.

    A SearchResult provides the ability to reconstruct the search or access a
    set of the keys the search found.
    """

    def __init__(self, start_keys, exclude_keys, key_count, keys):
        """Create a SearchResult.

        :param start_keys: The keys the search started at.
        :param exclude_keys: The keys the search excludes.
        :param key_count: The total number of keys (from start to but not
            including exclude).
        :param keys: The keys the search found. Note that in future we may get
            a SearchResult from a smart server, in which case the keys list is
            not necessarily immediately available.
        """
        self._recipe = ('search', start_keys, exclude_keys, key_count)
        self._keys = frozenset(keys)

    def get_recipe(self):
        """Return a recipe that can be used to replay this search.

        The recipe allows reconstruction of the same results at a later date
        without knowing all the found keys. The essential elements are a list
        of keys to start and to stop at. In order to give reproducible
        results when ghosts are encountered by a search they are automatically
        added to the exclude list (or else ghost filling may alter the
        results).

        :return: A tuple ('search', start_keys_set, exclude_keys_set,
            revision_count). To recreate the results of this search, create a
            breadth first searcher on the same graph starting at start_keys.
            Then call next() (or next_with_ghosts()) repeatedly, and on every
            result, call stop_searching_any on any keys from the exclude_keys
            set. The revision_count value acts as a trivial cross-check - the
            found revisions of the new search should have as many elements as
            revision_count. If it does not, then additional revisions have been
            ghosted since the search was executed the first time and the second
            time.
        """
        return self._recipe

    def get_keys(self):
        """Return the keys found in this search.

        :return: A set of keys.
        """
        return self._keys

    def is_empty(self):
        """Return false if the search lists 1 or more revisions."""
        return self._recipe[3] == 0

    def refine(self, seen, referenced):
        """Create a new search by refining this search.

        :param seen: Revisions that have been satisfied.
        :param referenced: Revision references observed while satisfying some
            of this search.
        """
        start = self._recipe[1]
        exclude = self._recipe[2]
        count = self._recipe[3]
        keys = self.get_keys()
        # New heads = referenced + old heads - seen things - exclude
        pending_refs = set(referenced)
        pending_refs.update(start)
        pending_refs.difference_update(seen)
        pending_refs.difference_update(exclude)
        # New exclude = old exclude + satisfied heads
        seen_heads = start.intersection(seen)
        exclude.update(seen_heads)
        # keys gets seen removed
        keys = keys - seen
        # length is reduced by len(seen)
        count -= len(seen)
        return SearchResult(pending_refs, exclude, count, keys)


class PendingAncestryResult(object):
    """A search result that will reconstruct the ancestry for some graph heads.

    Unlike SearchResult, this doesn't hold the complete search result in
    memory, it just holds a description of how to generate it.
    """

    def __init__(self, heads, repo):
        """Constructor.

        :param heads: an iterable of graph heads.
        :param repo: a repository to use to generate the ancestry for the given
            heads.
        """
        self.heads = frozenset(heads)
        self.repo = repo

    def get_recipe(self):
        """Return a recipe that can be used to replay this search.

        The recipe allows reconstruction of the same results at a later date.

        :seealso SearchResult.get_recipe:

        :return: A tuple ('proxy-search', start_keys_set, set(), -1)
            To recreate this result, create a PendingAncestryResult with the
            start_keys_set.
        """
        return ('proxy-search', self.heads, set(), -1)

    def get_keys(self):
        """See SearchResult.get_keys.

        Returns all the keys for the ancestry of the heads, excluding
        NULL_REVISION.
        """
        return self._get_keys(self.repo.get_graph())

    def _get_keys(self, graph):
        NULL_REVISION = revision.NULL_REVISION
        keys = [key for (key, parents) in graph.iter_ancestry(self.heads)
                if key != NULL_REVISION and parents is not None]
        return keys

    def is_empty(self):
        """Return false if the search lists 1 or more revisions."""
        if revision.NULL_REVISION in self.heads:
            return len(self.heads) == 1
        else:
            return len(self.heads) == 0

    def refine(self, seen, referenced):
        """Create a new search by refining this search.

        :param seen: Revisions that have been satisfied.
        :param referenced: Revision references observed while satisfying some
            of this search.
        """
        referenced = self.heads.union(referenced)
        return PendingAncestryResult(referenced - seen, self.repo)


def collapse_linear_regions(parent_map):
    """Collapse regions of the graph that are 'linear'.

    For example::

      A:[B], B:[C]

    can be collapsed by removing B and getting::

      A:[C]

    :param parent_map: A dictionary mapping children to their parents
    :return: Another dictionary with 'linear' chains collapsed
    """
    # Note: this isn't a strictly minimal collapse. For example:
    #   A
    #  / \
    # B   C
    #  \ /
    #   D
    #   |
    #   E
    # Will not have 'D' removed, even though 'E' could fit. Also:
    #   A
    #   |    A
    #   B => |
    #   |    C
    #   C
    # A and C are both kept because they are edges of the graph. We *could* get
    # rid of A if we wanted.
    #   A
    #  / \
    # B   C
    # |   |
    # D   E
    #  \ /
    #   F
    # Will not have any nodes removed, even though you do have an
    # 'uninteresting' linear D->B and E->C
    children = {}
    for child, parents in parent_map.iteritems():
        children.setdefault(child, [])
        for p in parents:
            children.setdefault(p, []).append(child)

    orig_children = dict(children)
    removed = set()
    result = dict(parent_map)
    for node in parent_map:
        parents = result[node]
        if len(parents) == 1:
            parent_children = children[parents[0]]
            if len(parent_children) != 1:
                # This is not the only child
                continue
            node_children = children[node]
            if len(node_children) != 1:
                continue
            child_parents = result.get(node_children[0], None)
            if len(child_parents) != 1:
                # This is not its only parent
                continue
            # The child of this node only points at it, and the parent only has
            # this as a child. remove this node, and join the others together
            result[node_children[0]] = parents
            children[parents[0]] = node_children
            del result[node]
            del children[node]
            removed.add(node)

    return result
