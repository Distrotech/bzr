# Copyright (C) 2005, 2006 Canonical Ltd
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

"""Reconcilers are able to fix some potential data errors in a branch."""


__all__ = [
    'KnitReconciler',
    'PackReconciler',
    'reconcile',
    'Reconciler',
    'RepoReconciler',
    ]


from bzrlib import (
    errors,
    ui,
    repository,
    repofmt,
    )
from bzrlib.trace import mutter, note
from bzrlib.tsort import topo_sort
from bzrlib.versionedfile import AdapterFactory, FulltextContentFactory


def reconcile(dir, other=None):
    """Reconcile the data in dir.

    Currently this is limited to a inventory 'reweave'.

    This is a convenience method, for using a Reconciler object.

    Directly using Reconciler is recommended for library users that
    desire fine grained control or analysis of the found issues.

    :param other: another bzrdir to reconcile against.
    """
    reconciler = Reconciler(dir, other=other)
    reconciler.reconcile()


class Reconciler(object):
    """Reconcilers are used to reconcile existing data."""

    def __init__(self, dir, other=None):
        """Create a Reconciler."""
        self.bzrdir = dir

    def reconcile(self):
        """Perform reconciliation.

        After reconciliation the following attributes document found issues:
        inconsistent_parents: The number of revisions in the repository whose
                              ancestry was being reported incorrectly.
        garbage_inventories: The number of inventory objects without revisions
                             that were garbage collected.
        fixed_branch_history: None if there was no branch, False if the branch
                              history was correct, True if the branch history
                              needed to be re-normalized.
        """
        self.pb = ui.ui_factory.nested_progress_bar()
        try:
            self._reconcile()
        finally:
            self.pb.finished()

    def _reconcile(self):
        """Helper function for performing reconciliation."""
        self._reconcile_branch()
        self._reconcile_repository()

    def _reconcile_branch(self):
        try:
            self.branch = self.bzrdir.open_branch()
        except errors.NotBranchError:
            # Nothing to check here
            self.fixed_branch_history = None
            return
        self.pb.note('Reconciling branch %s',
                     self.branch.base)
        branch_reconciler = self.branch.reconcile(thorough=True)
        self.fixed_branch_history = branch_reconciler.fixed_history

    def _reconcile_repository(self):
        self.repo = self.bzrdir.find_repository()
        self.pb.note('Reconciling repository %s',
                     self.repo.bzrdir.root_transport.base)
        self.pb.update("Reconciling repository", 0, 1)
        repo_reconciler = self.repo.reconcile(thorough=True)
        self.inconsistent_parents = repo_reconciler.inconsistent_parents
        self.garbage_inventories = repo_reconciler.garbage_inventories
        if repo_reconciler.aborted:
            self.pb.note(
                'Reconcile aborted: revision index has inconsistent parents.')
            self.pb.note(
                'Run "bzr check" for more details.')
        else:
            self.pb.note('Reconciliation complete.')


class BranchReconciler(object):
    """Reconciler that works on a branch."""

    def __init__(self, a_branch, thorough=False):
        self.fixed_history = None
        self.thorough = thorough
        self.branch = a_branch

    def reconcile(self):
        self.branch.lock_write()
        try:
            self.pb = ui.ui_factory.nested_progress_bar()
            try:
                self._reconcile_steps()
            finally:
                self.pb.finished()
        finally:
            self.branch.unlock()

    def _reconcile_steps(self):
        self._reconcile_revision_history()

    def _reconcile_revision_history(self):
        repo = self.branch.repository
        last_revno, last_revision_id = self.branch.last_revision_info()
        real_history = []
        try:
            for revid in repo.iter_reverse_revision_history(
                    last_revision_id):
                real_history.append(revid)
        except errors.RevisionNotPresent:
            pass # Hit a ghost left hand parent
        real_history.reverse()
        if last_revno != len(real_history):
            self.fixed_history = True
            # Technically for Branch5 formats, it is more efficient to use
            # set_revision_history, as this will regenerate it again.
            # Not really worth a whole BranchReconciler class just for this,
            # though.
            self.pb.note('Fixing last revision info %s => %s',
                         last_revno, len(real_history))
            self.branch.set_last_revision_info(len(real_history),
                                               last_revision_id)
        else:
            self.fixed_history = False
            self.pb.note('revision_history ok.')


class RepoReconciler(object):
    """Reconciler that reconciles a repository.

    The goal of repository reconciliation is to make any derived data
    consistent with the core data committed by a user. This can involve
    reindexing, or removing unreferenced data if that can interfere with
    queries in a given repository.

    Currently this consists of an inventory reweave with revision cross-checks.
    """

    def __init__(self, repo, other=None, thorough=False):
        """Construct a RepoReconciler.

        :param thorough: perform a thorough check which may take longer but
                         will correct non-data loss issues such as incorrect
                         cached data.
        """
        self.garbage_inventories = 0
        self.inconsistent_parents = 0
        self.aborted = False
        self.repo = repo
        self.thorough = thorough

    def reconcile(self):
        """Perform reconciliation.

        After reconciliation the following attributes document found issues:
        inconsistent_parents: The number of revisions in the repository whose
                              ancestry was being reported incorrectly.
        garbage_inventories: The number of inventory objects without revisions
                             that were garbage collected.
        """
        self.repo.lock_write()
        try:
            self.pb = ui.ui_factory.nested_progress_bar()
            try:
                self._reconcile_steps()
            finally:
                self.pb.finished()
        finally:
            self.repo.unlock()

    def _reconcile_steps(self):
        """Perform the steps to reconcile this repository."""
        self._reweave_inventory()

    def _reweave_inventory(self):
        """Regenerate the inventory weave for the repository from scratch.

        This is a smart function: it will only do the reweave if doing it
        will correct data issues. The self.thorough flag controls whether
        only data-loss causing issues (!self.thorough) or all issues
        (self.thorough) are treated as requiring the reweave.
        """
        # local because needing to know about WeaveFile is a wart we want to hide
        from bzrlib.weave import WeaveFile, Weave
        transaction = self.repo.get_transaction()
        self.pb.update('Reading inventory data')
        self.inventory = self.repo.inventories
        self.revisions = self.repo.revisions
        # the total set of revisions to process
        self.pending = set([key[-1] for key in self.revisions.keys()])

        # mapping from revision_id to parents
        self._rev_graph = {}
        # errors that we detect
        self.inconsistent_parents = 0
        # we need the revision id of each revision and its available parents list
        self._setup_steps(len(self.pending))
        for rev_id in self.pending:
            # put a revision into the graph.
            self._graph_revision(rev_id)
        self._check_garbage_inventories()
        # if there are no inconsistent_parents and
        # (no garbage inventories or we are not doing a thorough check)
        if (not self.inconsistent_parents and
            (not self.garbage_inventories or not self.thorough)):
            self.pb.note('Inventory ok.')
            return
        self.pb.update('Backing up inventory', 0, 0)
        self.repo._backup_inventory()
        self.pb.note('Backup inventory created.')
        new_inventories = self.repo._temp_inventories()

        # we have topological order of revisions and non ghost parents ready.
        self._setup_steps(len(self._rev_graph))
        revision_keys = [(rev_id,) for rev_id in topo_sort(self._rev_graph)]
        stream = self._change_inv_parents(
            self.inventory.get_record_stream(revision_keys, 'unordered', True),
            self._new_inv_parents,
            set(revision_keys))
        new_inventories.insert_record_stream(stream)
        # if this worked, the set of new_inventories.keys should equal
        # self.pending
        if not (set(new_inventories.keys()) ==
            set([(revid,) for revid in self.pending])):
            raise AssertionError()
        self.pb.update('Writing weave')
        self.repo._activate_new_inventory()
        self.inventory = None
        self.pb.note('Inventory regenerated.')

    def _new_inv_parents(self, revision_key):
        """Lookup ghost-filtered parents for revision_key."""
        # Use the filtered ghostless parents list:
        return tuple([(revid,) for revid in self._rev_graph[revision_key[-1]]])

    def _change_inv_parents(self, stream, get_parents, all_revision_keys):
        """Adapt a record stream to reconcile the parents."""
        for record in stream:
            wanted_parents = get_parents(record.key)
            if wanted_parents and wanted_parents[0] not in all_revision_keys:
                # The check for the left most parent only handles knit
                # compressors, but this code only applies to knit and weave
                # repositories anyway.
                bytes = record.get_bytes_as('fulltext')
                yield FulltextContentFactory(record.key, wanted_parents, record.sha1, bytes)
            else:
                adapted_record = AdapterFactory(record.key, wanted_parents, record)
                yield adapted_record
            self._reweave_step('adding inventories')

    def _setup_steps(self, new_total):
        """Setup the markers we need to control the progress bar."""
        self.total = new_total
        self.count = 0

    def _graph_revision(self, rev_id):
        """Load a revision into the revision graph."""
        # pick a random revision
        # analyse revision id rev_id and put it in the stack.
        self._reweave_step('loading revisions')
        rev = self.repo.get_revision_reconcile(rev_id)
        parents = []
        for parent in rev.parent_ids:
            if self._parent_is_available(parent):
                parents.append(parent)
            else:
                mutter('found ghost %s', parent)
        self._rev_graph[rev_id] = parents

    def _check_garbage_inventories(self):
        """Check for garbage inventories which we cannot trust

        We cant trust them because their pre-requisite file data may not
        be present - all we know is that their revision was not installed.
        """
        if not self.thorough:
            return
        inventories = set(self.inventory.keys())
        revisions = set(self.revisions.keys())
        garbage = inventories.difference(revisions)
        self.garbage_inventories = len(garbage)
        for revision_key in garbage:
            mutter('Garbage inventory {%s} found.', revision_key[-1])

    def _parent_is_available(self, parent):
        """True if parent is a fully available revision

        A fully available revision has a inventory and a revision object in the
        repository.
        """
        if parent in self._rev_graph:
            return True
        inv_present = (1 == len(self.inventory.get_parent_map([(parent,)])))
        return (inv_present and self.repo.has_revision(parent))

    def _reweave_step(self, message):
        """Mark a single step of regeneration complete."""
        self.pb.update(message, self.count, self.total)
        self.count += 1


class KnitReconciler(RepoReconciler):
    """Reconciler that reconciles a knit format repository.

    This will detect garbage inventories and remove them in thorough mode.
    """

    def _reconcile_steps(self):
        """Perform the steps to reconcile this repository."""
        if self.thorough:
            try:
                self._load_indexes()
            except errors.BzrCheckError:
                self.aborted = True
                return
            # knits never suffer this
            self._gc_inventory()
            self._fix_text_parents()

    def _load_indexes(self):
        """Load indexes for the reconciliation."""
        self.transaction = self.repo.get_transaction()
        self.pb.update('Reading indexes', 0, 2)
        self.inventory = self.repo.inventories
        self.pb.update('Reading indexes', 1, 2)
        self.repo._check_for_inconsistent_revision_parents()
        self.revisions = self.repo.revisions
        self.pb.update('Reading indexes', 2, 2)

    def _gc_inventory(self):
        """Remove inventories that are not referenced from the revision store."""
        self.pb.update('Checking unused inventories', 0, 1)
        self._check_garbage_inventories()
        self.pb.update('Checking unused inventories', 1, 3)
        if not self.garbage_inventories:
            self.pb.note('Inventory ok.')
            return
        self.pb.update('Backing up inventory', 0, 0)
        self.repo._backup_inventory()
        self.pb.note('Backup Inventory created')
        # asking for '' should never return a non-empty weave
        new_inventories = self.repo._temp_inventories()
        # we have topological order of revisions and non ghost parents ready.
        graph = self.revisions.get_parent_map(self.revisions.keys())
        revision_keys = topo_sort(graph)
        revision_ids = [key[-1] for key in revision_keys]
        self._setup_steps(len(revision_keys))
        stream = self._change_inv_parents(
            self.inventory.get_record_stream(revision_keys, 'unordered', True),
            graph.__getitem__,
            set(revision_keys))
        new_inventories.insert_record_stream(stream)
        # if this worked, the set of new_inventory_vf.names should equal
        # the revisionds list
        if not(set(new_inventories.keys()) == set(revision_keys)):
            raise AssertionError()
        self.pb.update('Writing weave')
        self.repo._activate_new_inventory()
        self.inventory = None
        self.pb.note('Inventory regenerated.')

    def _fix_text_parents(self):
        """Fix bad versionedfile parent entries.

        It is possible for the parents entry in a versionedfile entry to be
        inconsistent with the values in the revision and inventory.

        This method finds entries with such inconsistencies, corrects their
        parent lists, and replaces the versionedfile with a corrected version.
        """
        transaction = self.repo.get_transaction()
        versions = [key[-1] for key in self.revisions.keys()]
        mutter('Prepopulating revision text cache with %d revisions',
                len(versions))
        vf_checker = self.repo._get_versioned_file_checker()
        bad_parents, unused_versions = vf_checker.check_file_version_parents(
            self.repo.texts, self.pb)
        text_index = vf_checker.text_index
        per_id_bad_parents = {}
        for key in unused_versions:
            # Ensure that every file with unused versions gets rewritten.
            # NB: This is really not needed, reconcile != pack.
            per_id_bad_parents[key[0]] = {}
        # Generate per-knit/weave data.
        for key, details in bad_parents.iteritems():
            file_id = key[0]
            rev_id = key[1]
            knit_parents = tuple([parent[-1] for parent in details[0]])
            correct_parents = tuple([parent[-1] for parent in details[1]])
            file_details = per_id_bad_parents.setdefault(file_id, {})
            file_details[rev_id] = (knit_parents, correct_parents)
        file_id_versions = {}
        for text_key in text_index:
            versions_list = file_id_versions.setdefault(text_key[0], [])
            versions_list.append(text_key[1])
        # Do the reconcile of individual weaves.
        for num, file_id in enumerate(per_id_bad_parents):
            self.pb.update('Fixing text parents', num,
                           len(per_id_bad_parents))
            versions_with_bad_parents = per_id_bad_parents[file_id]
            id_unused_versions = set(key[-1] for key in unused_versions
                if key[0] == file_id)
            if file_id in file_id_versions:
                file_versions = file_id_versions[file_id]
            else:
                # This id was present in the disk store but is not referenced
                # by any revision at all.
                file_versions = []
            self._fix_text_parent(file_id, versions_with_bad_parents,
                 id_unused_versions, file_versions)

    def _fix_text_parent(self, file_id, versions_with_bad_parents,
            unused_versions, all_versions):
        """Fix bad versionedfile entries in a single versioned file."""
        mutter('fixing text parent: %r (%d versions)', file_id,
                len(versions_with_bad_parents))
        mutter('(%d are unused)', len(unused_versions))
        new_file_id = 'temp:%s' % file_id
        new_parents = {}
        needed_keys = set()
        for version in all_versions:
            if version in unused_versions:
                continue
            elif version in versions_with_bad_parents:
                parents = versions_with_bad_parents[version][1]
            else:
                pmap = self.repo.texts.get_parent_map([(file_id, version)])
                parents = [key[-1] for key in pmap[(file_id, version)]]
            new_parents[(new_file_id, version)] = [
                (new_file_id, parent) for parent in parents]
            needed_keys.add((file_id, version))
        def fix_parents(stream):
            for record in stream:
                bytes = record.get_bytes_as('fulltext')
                new_key = (new_file_id, record.key[-1])
                parents = new_parents[new_key]
                yield FulltextContentFactory(new_key, parents, record.sha1, bytes)
        stream = self.repo.texts.get_record_stream(needed_keys, 'topological', True)
        self.repo._remove_file_id(new_file_id)
        self.repo.texts.insert_record_stream(fix_parents(stream))
        self.repo._remove_file_id(file_id)
        if len(new_parents):
            self.repo._move_file_id(new_file_id, file_id)


class PackReconciler(RepoReconciler):
    """Reconciler that reconciles a pack based repository.

    Garbage inventories do not affect ancestry queries, and removal is
    considerably more expensive as there is no separate versioned file for
    them, so they are not cleaned. In short it is currently a no-op.

    In future this may be a good place to hook in annotation cache checking,
    index recreation etc.
    """

    # XXX: The index corruption that _fix_text_parents performs is needed for
    # packs, but not yet implemented. The basic approach is to:
    #  - lock the names list
    #  - perform a customised pack() that regenerates data as needed
    #  - unlock the names list
    # https://bugs.launchpad.net/bzr/+bug/154173

    def _reconcile_steps(self):
        """Perform the steps to reconcile this repository."""
        if not self.thorough:
            return
        collection = self.repo._pack_collection
        collection.ensure_loaded()
        collection.lock_names()
        try:
            packs = collection.all_packs()
            all_revisions = self.repo.all_revision_ids()
            total_inventories = len(list(
                collection.inventory_index.combined_index.iter_all_entries()))
            if len(all_revisions):
                new_pack =  self.repo._reconcile_pack(collection, packs,
                    ".reconcile", all_revisions, self.pb)
                if new_pack is not None:
                    self._discard_and_save(packs)
            else:
                # only make a new pack when there is data to copy.
                self._discard_and_save(packs)
            self.garbage_inventories = total_inventories - len(list(
                collection.inventory_index.combined_index.iter_all_entries()))
        finally:
            collection._unlock_names()

    def _discard_and_save(self, packs):
        """Discard some packs from the repository.

        This removes them from the memory index, saves the in-memory index
        which makes the newly reconciled pack visible and hides the packs to be
        discarded, and finally renames the packs being discarded into the
        obsolete packs directory.

        :param packs: The packs to discard.
        """
        for pack in packs:
            self.repo._pack_collection._remove_pack_from_memory(pack)
        self.repo._pack_collection._save_pack_names()
        self.repo._pack_collection._obsolete_packs(packs)
