# Copyright (C) 2005-2011 Canonical Ltd
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


"""Copying of history from one branch to another.

The basic plan is that every branch knows the history of everything
that has merged into it.  As the first step of a merge, pull, or
branch operation we copy history from the source into the destination
branch.
"""

import operator

from bzrlib.lazy_import import lazy_import
lazy_import(globals(), """
from bzrlib import (
    graph,
    tsort,
    versionedfile,
    )
""")
from bzrlib import (
    errors,
    ui,
    )
from bzrlib.revision import NULL_REVISION
from bzrlib.trace import mutter


class RepoFetcher(object):
    """Pull revisions and texts from one repository to another.

    This should not be used directly, it's essential a object to encapsulate
    the logic in InterRepository.fetch().
    """

    def __init__(self, to_repository, from_repository, last_revision=None,
        find_ghosts=True, fetch_spec=None):
        """Create a repo fetcher.

        :param last_revision: If set, try to limit to the data this revision
            references.
        :param fetch_spec: A SearchResult specifying which revisions to fetch.
            If set, this overrides last_revision.
        :param find_ghosts: If True search the entire history for ghosts.
        """
        # repository.fetch has the responsibility for short-circuiting
        # attempts to copy between a repository and itself.
        self.to_repository = to_repository
        self.from_repository = from_repository
        self.sink = to_repository._get_sink()
        # must not mutate self._last_revision as its potentially a shared instance
        self._last_revision = last_revision
        self._fetch_spec = fetch_spec
        self.find_ghosts = find_ghosts
        self.from_repository.lock_read()
        mutter("Using fetch logic to copy between %s(%s) and %s(%s)",
               self.from_repository, self.from_repository._format,
               self.to_repository, self.to_repository._format)
        try:
            self.__fetch()
        finally:
            self.from_repository.unlock()

    def __fetch(self):
        """Primary worker function.

        This initialises all the needed variables, and then fetches the
        requested revisions, finally clearing the progress bar.
        """
        # Roughly this is what we're aiming for fetch to become:
        #
        # missing = self.sink.insert_stream(self.source.get_stream(search))
        # if missing:
        #     missing = self.sink.insert_stream(self.source.get_items(missing))
        # assert not missing
        self.count_total = 0
        self.file_ids_names = {}
        pb = ui.ui_factory.nested_progress_bar()
        pb.show_pct = pb.show_count = False
        try:
            pb.update("Finding revisions", 0, 2)
            search_result = self._revids_to_fetch()
            mutter('fetching: %s', search_result)
            if search_result.is_empty():
                return
            pb.update("Fetching revisions", 1, 2)
            self._fetch_everything_for_search(search_result)
        finally:
            pb.finished()

    def _fetch_everything_for_search(self, search):
        """Fetch all data for the given set of revisions."""
        # The first phase is "file".  We pass the progress bar for it directly
        # into item_keys_introduced_by, which has more information about how
        # that phase is progressing than we do.  Progress updates for the other
        # phases are taken care of in this function.
        # XXX: there should be a clear owner of the progress reporting.  Perhaps
        # item_keys_introduced_by should have a richer API than it does at the
        # moment, so that it can feed the progress information back to this
        # function?
        if (self.from_repository._format.rich_root_data and
            not self.to_repository._format.rich_root_data):
            raise errors.IncompatibleRepositories(
                self.from_repository, self.to_repository,
                "different rich-root support")
        pb = ui.ui_factory.nested_progress_bar()
        try:
            pb.update("Get stream source")
            source = self.from_repository._get_source(
                self.to_repository._format)
            stream = source.get_stream(search)
            from_format = self.from_repository._format
            pb.update("Inserting stream")
            resume_tokens, missing_keys = self.sink.insert_stream(
                stream, from_format, [])
            if missing_keys:
                pb.update("Missing keys")
                stream = source.get_stream_for_missing_keys(missing_keys)
                pb.update("Inserting missing keys")
                resume_tokens, missing_keys = self.sink.insert_stream(
                    stream, from_format, resume_tokens)
            if missing_keys:
                raise AssertionError(
                    "second push failed to complete a fetch %r." % (
                        missing_keys,))
            if resume_tokens:
                raise AssertionError(
                    "second push failed to commit the fetch %r." % (
                        resume_tokens,))
            pb.update("Finishing stream")
            self.sink.finished()
        finally:
            pb.finished()

    def _revids_to_fetch(self):
        """Determines the exact revisions needed from self.from_repository to
        install self._last_revision in self.to_repository.

        :returns: A SearchResult of some sort.  (Possibly a
            PendingAncestryResult, EmptySearchResult, etc.)
        """
        if self._fetch_spec is not None:
            # The fetch spec is already a concrete search result.
            return self._fetch_spec
        elif self._last_revision == NULL_REVISION:
            # fetch_spec is None + last_revision is null => empty fetch.
            # explicit limit of no revisions needed
            return graph.EmptySearchResult()
        elif self._last_revision is not None:
            return graph.NotInOtherForRevs(self.to_repository,
                self.from_repository, [self._last_revision],
                find_ghosts=self.find_ghosts).execute()
        else: # self._last_revision is None:
            return graph.EverythingNotInOther(self.to_repository,
                self.from_repository,
                find_ghosts=self.find_ghosts).execute()


class Inter1and2Helper(object):
    """Helper for operations that convert data from model 1 and 2

    This is for use by fetchers and converters.
    """

    # This is a class variable so that the test suite can override it.
    known_graph_threshold = 100

    def __init__(self, source):
        """Constructor.

        :param source: The repository data comes from
        """
        self.source = source

    def iter_rev_trees(self, revs):
        """Iterate through RevisionTrees efficiently.

        Additionally, the inventory's revision_id is set if unset.

        Trees are retrieved in batches of 100, and then yielded in the order
        they were requested.

        :param revs: A list of revision ids
        """
        # In case that revs is not a list.
        revs = list(revs)
        while revs:
            for tree in self.source.revision_trees(revs[:100]):
                if tree.inventory.revision_id is None:
                    tree.inventory.revision_id = tree.get_revision_id()
                yield tree
            revs = revs[100:]

    def _find_root_ids(self, revs, parent_map, graph):
        revision_root = {}
        for tree in self.iter_rev_trees(revs):
            revision_id = tree.inventory.root.revision
            root_id = tree.get_root_id()
            revision_root[revision_id] = root_id
        # Find out which parents we don't already know root ids for
        parents = set()
        for revision_parents in parent_map.itervalues():
            parents.update(revision_parents)
        parents.difference_update(revision_root.keys() + [NULL_REVISION])
        # Limit to revisions present in the versionedfile
        parents = graph.get_parent_map(parents).keys()
        for tree in self.iter_rev_trees(parents):
            root_id = tree.get_root_id()
            revision_root[tree.get_revision_id()] = root_id
        return revision_root

    def generate_root_texts(self, revs):
        """Generate VersionedFiles for all root ids.

        :param revs: the revisions to include
        """
        graph = self.source.get_graph()
        parent_map = graph.get_parent_map(revs)
        rev_order = tsort.topo_sort(parent_map)
        rev_id_to_root_id = self._find_root_ids(revs, parent_map, graph)
        root_id_order = [(rev_id_to_root_id[rev_id], rev_id) for rev_id in
            rev_order]
        # Guaranteed stable, this groups all the file id operations together
        # retaining topological order within the revisions of a file id.
        # File id splits and joins would invalidate this, but they don't exist
        # yet, and are unlikely to in non-rich-root environments anyway.
        root_id_order.sort(key=operator.itemgetter(0))
        # Create a record stream containing the roots to create.
        if len(revs) > self.known_graph_threshold:
            graph = self.source.get_known_graph_ancestry(revs)
        new_roots_stream = _new_root_data_stream(
            root_id_order, rev_id_to_root_id, parent_map, self.source, graph)
        return [('texts', new_roots_stream)]


def _new_root_data_stream(
    root_keys_to_create, rev_id_to_root_id_map, parent_map, repo, graph=None):
    """Generate a texts substream of synthesised root entries.

    Used in fetches that do rich-root upgrades.
    
    :param root_keys_to_create: iterable of (root_id, rev_id) pairs describing
        the root entries to create.
    :param rev_id_to_root_id_map: dict of known rev_id -> root_id mappings for
        calculating the parents.  If a parent rev_id is not found here then it
        will be recalculated.
    :param parent_map: a parent map for all the revisions in
        root_keys_to_create.
    :param graph: a graph to use instead of repo.get_graph().
    """
    for root_key in root_keys_to_create:
        root_id, rev_id = root_key
        parent_keys = _parent_keys_for_root_version(
            root_id, rev_id, rev_id_to_root_id_map, parent_map, repo, graph)
        yield versionedfile.FulltextContentFactory(
            root_key, parent_keys, None, '')


def _parent_keys_for_root_version(
    root_id, rev_id, rev_id_to_root_id_map, parent_map, repo, graph=None):
    """Get the parent keys for a given root id.
    
    A helper function for _new_root_data_stream.
    """
    # Include direct parents of the revision, but only if they used the same
    # root_id and are heads.
    rev_parents = parent_map[rev_id]
    parent_ids = []
    for parent_id in rev_parents:
        if parent_id == NULL_REVISION:
            continue
        if parent_id not in rev_id_to_root_id_map:
            # We probably didn't read this revision, go spend the extra effort
            # to actually check
            try:
                tree = repo.revision_tree(parent_id)
            except errors.NoSuchRevision:
                # Ghost, fill out rev_id_to_root_id in case we encounter this
                # again.
                # But set parent_root_id to None since we don't really know
                parent_root_id = None
            else:
                parent_root_id = tree.get_root_id()
            rev_id_to_root_id_map[parent_id] = None
            # XXX: why not:
            #   rev_id_to_root_id_map[parent_id] = parent_root_id
            # memory consumption maybe?
        else:
            parent_root_id = rev_id_to_root_id_map[parent_id]
        if root_id == parent_root_id:
            # With stacking we _might_ want to refer to a non-local revision,
            # but this code path only applies when we have the full content
            # available, so ghosts really are ghosts, not just the edge of
            # local data.
            parent_ids.append(parent_id)
        else:
            # root_id may be in the parent anyway.
            try:
                tree = repo.revision_tree(parent_id)
            except errors.NoSuchRevision:
                # ghost, can't refer to it.
                pass
            else:
                try:
                    parent_ids.append(tree.get_file_revision(root_id))
                except errors.NoSuchId:
                    # not in the tree
                    pass
    # Drop non-head parents
    if graph is None:
        graph = repo.get_graph()
    heads = graph.heads(parent_ids)
    selected_ids = []
    for parent_id in parent_ids:
        if parent_id in heads and parent_id not in selected_ids:
            selected_ids.append(parent_id)
    parent_keys = [(root_id, parent_id) for parent_id in selected_ids]
    return parent_keys


class TargetRepoKinds(object):
    """An enum-like set of constants.
    
    They are the possible values of FetchSpecFactory.target_repo_kinds.
    """
    
    PREEXISTING = 'preexisting'
    STACKED = 'stacked'
    EMPTY = 'empty'


class FetchSpecFactory(object):
    """A helper for building the best fetch spec for a sprout call.

    Factors that go into determining the sort of fetch to perform:
     * did the caller specify any revision IDs?
     * did the caller specify a source branch (need to fetch its
       heads_to_fetch(), usually the tip + tags)
     * is there an existing target repo (don't need to refetch revs it
       already has)
     * target is stacked?  (similar to pre-existing target repo: even if
       the target itself is new don't want to refetch existing revs)

    :ivar source_branch: the source branch if one specified, else None.
    :ivar source_branch_stop_revision_id: fetch up to this revision of
        source_branch, rather than its tip.
    :ivar source_repo: the source repository if one found, else None.
    :ivar target_repo: the target repository acquired by sprout.
    :ivar target_repo_kind: one of the TargetRepoKinds constants.
    """

    def __init__(self):
        self._explicit_rev_ids = set()
        self.source_branch = None
        self.source_branch_stop_revision_id = None
        self.source_repo = None
        self.target_repo = None
        self.target_repo_kind = None

    def add_revision_ids(self, revision_ids):
        """Add revision_ids to the set of revision_ids to be fetched."""
        self._explicit_rev_ids.update(revision_ids)

    def make_fetch_spec(self):
        """Build a SearchResult or PendingAncestryResult or etc."""
        if self.target_repo_kind is None or self.source_repo is None:
            raise AssertionError(
                'Incomplete FetchSpecFactory: %r' % (self.__dict__,))
        if len(self._explicit_rev_ids) == 0 and self.source_branch is None:
            # Caller hasn't specified any revisions or source branch
            if self.target_repo_kind == TargetRepoKinds.EMPTY:
                return graph.EverythingResult(self.source_repo)
            else:
                # We want everything not already in the target (or target's
                # fallbacks).
                return graph.EverythingNotInOther(
                    self.target_repo, self.source_repo).execute()
        heads_to_fetch = set(self._explicit_rev_ids)
        if self.source_branch is not None:
            must_fetch, if_present_fetch = self.source_branch.heads_to_fetch(
                stop_revision=self.source_branch_stop_revision_id)
            heads_to_fetch.update(must_fetch)
        else:
            if_present_fetch = set()
        if self.target_repo_kind == TargetRepoKinds.EMPTY:
            # PendingAncestryResult does not raise errors if a requested head
            # is absent.  Ideally it would support the
            # required_ids/if_present_ids distinction, but in practice
            # heads_to_fetch will almost certainly be present so this doesn't
            # matter much.
            all_heads = heads_to_fetch.union(if_present_fetch)
            return graph.PendingAncestryResult(all_heads, self.source_repo)
        return graph.NotInOtherForRevs(self.target_repo, self.source_repo,
            required_ids=heads_to_fetch, if_present_ids=if_present_fetch
            ).execute()


