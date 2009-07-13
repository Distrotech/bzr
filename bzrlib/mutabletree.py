# Copyright (C) 2006, 2007 Canonical Ltd
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

"""MutableTree object.

See MutableTree for more details.
"""


from bzrlib.lazy_import import lazy_import
lazy_import(globals(), """
import os

from bzrlib import (
    add,
    bzrdir,
    hooks,
    symbol_versioning,
    )
from bzrlib.osutils import dirname
from bzrlib.revisiontree import RevisionTree
from bzrlib.trace import mutter, warning
""")

from bzrlib import (
    errors,
    osutils,
    tree,
    )
from bzrlib.decorators import needs_read_lock, needs_write_lock
from bzrlib.osutils import splitpath


def needs_tree_write_lock(unbound):
    """Decorate unbound to take out and release a tree_write lock."""
    def tree_write_locked(self, *args, **kwargs):
        self.lock_tree_write()
        try:
            return unbound(self, *args, **kwargs)
        finally:
            self.unlock()
    tree_write_locked.__doc__ = unbound.__doc__
    tree_write_locked.__name__ = unbound.__name__
    return tree_write_locked


class MutableTree(tree.Tree):
    """A MutableTree is a specialisation of Tree which is able to be mutated.

    Generally speaking these mutations are only possible within a lock_write
    context, and will revert if the lock is broken abnormally - but this cannot
    be guaranteed - depending on the exact implementation of the mutable state.

    The most common form of Mutable Tree is WorkingTree, see bzrlib.workingtree.
    For tests we also have MemoryTree which is a MutableTree whose contents are
    entirely in memory.

    For now, we are not treating MutableTree as an interface to provide
    conformance tests for - rather we are testing MemoryTree specifically, and
    interface testing implementations of WorkingTree.

    A mutable tree always has an associated Branch and BzrDir object - the
    branch and bzrdir attributes.
    """
    def __init__(self, *args, **kw):
        super(MutableTree, self).__init__(*args, **kw)
        # Is this tree on a case-insensitive or case-preserving file-system?
        # Sub-classes may initialize to False if they detect they are being
        # used on media which doesn't differentiate the case of names.
        self.case_sensitive = True

    @needs_tree_write_lock
    def add(self, files, ids=None, kinds=None):
        """Add paths to the set of versioned paths.

        Note that the command line normally calls smart_add instead,
        which can automatically recurse.

        This adds the files to the inventory, so that they will be
        recorded by the next commit.

        :param files: List of paths to add, relative to the base of the tree.
        :param ids: If set, use these instead of automatically generated ids.
            Must be the same length as the list of files, but may
            contain None for ids that are to be autogenerated.
        :param kinds: Optional parameter to specify the kinds to be used for
            each file.

        TODO: Perhaps callback with the ids and paths as they're added.
        """
        if isinstance(files, basestring):
            # XXX: Passing a single string is inconsistent and should be
            # deprecated.
            if not (ids is None or isinstance(ids, basestring)):
                raise AssertionError()
            if not (kinds is None or isinstance(kinds, basestring)):
                raise AssertionError()
            files = [files]
            if ids is not None:
                ids = [ids]
            if kinds is not None:
                kinds = [kinds]

        files = [path.strip('/') for path in files]

        if ids is None:
            ids = [None] * len(files)
        else:
            if not (len(ids) == len(files)):
                raise AssertionError()
        if kinds is None:
            kinds = [None] * len(files)
        elif not len(kinds) == len(files):
            raise AssertionError()
        for f in files:
            # generic constraint checks:
            if self.is_control_filename(f):
                raise errors.ForbiddenControlFileError(filename=f)
            fp = splitpath(f)
        # fill out file kinds for all files [not needed when we stop
        # caring about the instantaneous file kind within a uncommmitted tree
        #
        self._gather_kinds(files, kinds)
        self._add(files, ids, kinds)

    def add_reference(self, sub_tree):
        """Add a TreeReference to the tree, pointing at sub_tree"""
        raise errors.UnsupportedOperation(self.add_reference, self)

    def _add_reference(self, sub_tree):
        """Standard add_reference implementation, for use by subclasses"""
        try:
            sub_tree_path = self.relpath(sub_tree.basedir)
        except errors.PathNotChild:
            raise errors.BadReferenceTarget(self, sub_tree,
                                            'Target not inside tree.')
        sub_tree_id = sub_tree.get_root_id()
        if sub_tree_id == self.get_root_id():
            raise errors.BadReferenceTarget(self, sub_tree,
                                     'Trees have the same root id.')
        if sub_tree_id in self.inventory:
            raise errors.BadReferenceTarget(self, sub_tree,
                                            'Root id already present in tree')
        self._add([sub_tree_path], [sub_tree_id], ['tree-reference'])

    def _add(self, files, ids, kinds):
        """Helper function for add - updates the inventory.

        :param files: sequence of pathnames, relative to the tree root
        :param ids: sequence of suggested ids for the files (may be None)
        :param kinds: sequence of  inventory kinds of the files (i.e. may
            contain "tree-reference")
        """
        raise NotImplementedError(self._add)

    @needs_tree_write_lock
    def apply_inventory_delta(self, changes):
        """Apply changes to the inventory as an atomic operation.

        :param changes: An inventory delta to apply to the working tree's
            inventory.
        :return None:
        :seealso Inventory.apply_delta: For details on the changes parameter.
        """
        self.flush()
        inv = self.inventory
        inv.apply_delta(changes)
        self._write_inventory(inv)

    @needs_write_lock
    def commit(self, message=None, revprops=None, *args,
               **kwargs):
        # avoid circular imports
        from bzrlib import commit
        if revprops is None:
            revprops = {}
        possible_master_transports=[]
        if not 'branch-nick' in revprops:
            revprops['branch-nick'] = self.branch._get_nick(
                kwargs.get('local', False),
                possible_master_transports)
        authors = kwargs.pop('authors', None)
        author = kwargs.pop('author', None)
        if authors is not None:
            if author is not None:
                raise AssertionError('Specifying both author and authors '
                        'is not allowed. Specify just authors instead')
            if 'author' in revprops or 'authors' in revprops:
                # XXX: maybe we should just accept one of them?
                raise AssertionError('author property given twice')
            if authors:
                for individual in authors:
                    if '\n' in individual:
                        raise AssertionError('\\n is not a valid character '
                                'in an author identity')
                revprops['authors'] = '\n'.join(authors)
        if author is not None:
            symbol_versioning.warn('The parameter author was deprecated'
                   ' in version 1.13. Use authors instead',
                   DeprecationWarning)
            if 'author' in revprops or 'authors' in revprops:
                # XXX: maybe we should just accept one of them?
                raise AssertionError('author property given twice')
            if '\n' in author:
                raise AssertionError('\\n is not a valid character '
                        'in an author identity')
            revprops['authors'] = author
        # args for wt.commit start at message from the Commit.commit method,
        args = (message, ) + args
        for hook in MutableTree.hooks['start_commit']:
            hook(self)
        committed_id = commit.Commit().commit(working_tree=self,
            revprops=revprops,
            possible_master_transports=possible_master_transports,
            *args, **kwargs)
        return committed_id

    def _gather_kinds(self, files, kinds):
        """Helper function for add - sets the entries of kinds."""
        raise NotImplementedError(self._gather_kinds)

    @needs_read_lock
    def has_changes(self, from_tree):
        """Quickly check that the tree contains at least one change.

        :return: True if a change is found. False otherwise
        """
        changes = self.iter_changes(from_tree)
        try:
            change = changes.next()
            # Exclude root (talk about black magic... --vila 20090629)
            if change[4] == (None, None):
                change = changes.next()
            return True
        except StopIteration:
            # No changes
            return False

    @needs_read_lock
    def last_revision(self):
        """Return the revision id of the last commit performed in this tree.

        In early tree formats the result of last_revision is the same as the
        branch last_revision, but that is no longer the case for modern tree
        formats.

        last_revision returns the left most parent id, or None if there are no
        parents.

        last_revision was deprecated as of 0.11. Please use get_parent_ids
        instead.
        """
        raise NotImplementedError(self.last_revision)

    def lock_tree_write(self):
        """Lock the working tree for write, and the branch for read.

        This is useful for operations which only need to mutate the working
        tree. Taking out branch write locks is a relatively expensive process
        and may fail if the branch is on read only media. So branch write locks
        should only be taken out when we are modifying branch data - such as in
        operations like commit, pull, uncommit and update.
        """
        raise NotImplementedError(self.lock_tree_write)

    def lock_write(self):
        """Lock the tree and its branch. This allows mutating calls to be made.

        Some mutating methods will take out implicit write locks, but in
        general you should always obtain a write lock before calling mutating
        methods on a tree.
        """
        raise NotImplementedError(self.lock_write)

    @needs_write_lock
    def mkdir(self, path, file_id=None):
        """Create a directory in the tree. if file_id is None, one is assigned.

        :param path: A unicode file path.
        :param file_id: An optional file-id.
        :return: the file id of the new directory.
        """
        raise NotImplementedError(self.mkdir)

    def _observed_sha1(self, file_id, path, (sha1, stat_value)):
        """Tell the tree we have observed a paths sha1.

        The intent of this function is to allow trees that have a hashcache to
        update the hashcache during commit. If the observed file is too new
        (based on the stat_value) to be safely hash-cached the tree will ignore
        it.

        The default implementation does nothing.

        :param file_id: The file id
        :param path: The file path
        :param sha1: The sha 1 that was observed.
        :param stat_value: A stat result for the file the sha1 was read from.
        :return: None
        """

    def _fix_case_of_inventory_path(self, path):
        """If our tree isn't case sensitive, return the canonical path"""
        if not self.case_sensitive:
            path = self.get_canonical_inventory_path(path)
        return path

    @needs_write_lock
    def put_file_bytes_non_atomic(self, file_id, bytes):
        """Update the content of a file in the tree.

        Note that the file is written in-place rather than being
        written to a temporary location and renamed. As a consequence,
        readers can potentially see the file half-written.

        :param file_id: file-id of the file
        :param bytes: the new file contents
        """
        raise NotImplementedError(self.put_file_bytes_non_atomic)

    def set_parent_ids(self, revision_ids, allow_leftmost_as_ghost=False):
        """Set the parents ids of the working tree.

        :param revision_ids: A list of revision_ids.
        """
        raise NotImplementedError(self.set_parent_ids)

    def set_parent_trees(self, parents_list, allow_leftmost_as_ghost=False):
        """Set the parents of the working tree.

        :param parents_list: A list of (revision_id, tree) tuples.
            If tree is None, then that element is treated as an unreachable
            parent tree - i.e. a ghost.
        """
        raise NotImplementedError(self.set_parent_trees)

    @needs_tree_write_lock
    def smart_add(self, file_list, recurse=True, action=None, save=True):
        """Version file_list, optionally recursing into directories.

        This is designed more towards DWIM for humans than API clarity.
        For the specific behaviour see the help for cmd_add().

        :param action: A reporter to be called with the inventory, parent_ie,
            path and kind of the path being added. It may return a file_id if
            a specific one should be used.
        :param save: Save the inventory after completing the adds. If False
            this provides dry-run functionality by doing the add and not saving
            the inventory.
        :return: A tuple - files_added, ignored_files. files_added is the count
            of added files, and ignored_files is a dict mapping files that were
            ignored to the rule that caused them to be ignored.
        """
        # not in an inner loop; and we want to remove direct use of this,
        # so here as a reminder for now. RBC 20070703
        from bzrlib.inventory import InventoryEntry
        if action is None:
            action = add.AddAction()

        if not file_list:
            # no paths supplied: add the entire tree.
            file_list = [u'.']
        # mutter("smart add of %r")
        inv = self.inventory
        added = []
        ignored = {}
        dirs_to_add = []
        user_dirs = set()

        # validate user file paths and convert all paths to tree
        # relative : it's cheaper to make a tree relative path an abspath
        # than to convert an abspath to tree relative, and it's cheaper to
        # perform the canonicalization in bulk.
        for filepath in osutils.canonical_relpaths(self.basedir, file_list):
            rf = _FastPath(filepath)
            # validate user parameters. Our recursive code avoids adding new files
            # that need such validation
            if self.is_control_filename(rf.raw_path):
                raise errors.ForbiddenControlFileError(filename=rf.raw_path)

            abspath = self.abspath(rf.raw_path)
            kind = osutils.file_kind(abspath)
            if kind == 'directory':
                # schedule the dir for scanning
                user_dirs.add(rf)
            else:
                if not InventoryEntry.versionable_kind(kind):
                    raise errors.BadFileKindError(filename=abspath, kind=kind)
            # ensure the named path is added, so that ignore rules in the later directory
            # walk dont skip it.
            # we dont have a parent ie known yet.: use the relatively slower inventory
            # probing method
            versioned = inv.has_filename(rf.raw_path)
            if versioned:
                continue
            added.extend(_add_one_and_parent(self, inv, None, rf, kind, action))

        if not recurse:
            # no need to walk any directories at all.
            if len(added) > 0 and save:
                self._write_inventory(inv)
            return added, ignored

        # only walk the minimal parents needed: we have user_dirs to override
        # ignores.
        prev_dir = None

        is_inside = osutils.is_inside_or_parent_of_any
        for path in sorted(user_dirs):
            if (prev_dir is None or not is_inside([prev_dir], path.raw_path)):
                dirs_to_add.append((path, None))
            prev_dir = path.raw_path

        # dirs_to_add is initialised to a list of directories, but as we scan
        # directories we append files to it.
        # XXX: We should determine kind of files when we scan them rather than
        # adding to this list. RBC 20070703
        for directory, parent_ie in dirs_to_add:
            # directory is tree-relative
            abspath = self.abspath(directory.raw_path)

            # get the contents of this directory.

            # find the kind of the path being added.
            kind = osutils.file_kind(abspath)

            if not InventoryEntry.versionable_kind(kind):
                warning("skipping %s (can't add file of kind '%s')", abspath, kind)
                continue

            if parent_ie is not None:
                versioned = directory.base_path in parent_ie.children
            else:
                # without the parent ie, use the relatively slower inventory
                # probing method
                versioned = inv.has_filename(
                        self._fix_case_of_inventory_path(directory.raw_path))

            if kind == 'directory':
                try:
                    sub_branch = bzrdir.BzrDir.open(abspath)
                    sub_tree = True
                except errors.NotBranchError:
                    sub_tree = False
                except errors.UnsupportedFormatError:
                    sub_tree = True
            else:
                sub_tree = False

            if directory.raw_path == '':
                # mutter("tree root doesn't need to be added")
                sub_tree = False
            elif versioned:
                pass
                # mutter("%r is already versioned", abspath)
            elif sub_tree:
                # XXX: This is wrong; people *might* reasonably be trying to add
                # subtrees as subtrees.  This should probably only be done in formats
                # which can represent subtrees, and even then perhaps only when
                # the user asked to add subtrees.  At the moment you can add them
                # specially through 'join --reference', which is perhaps
                # reasonable: adding a new reference is a special operation and
                # can have a special behaviour.  mbp 20070306
                mutter("%r is a nested bzr tree", abspath)
            else:
                _add_one(self, inv, parent_ie, directory, kind, action)
                added.append(directory.raw_path)

            if kind == 'directory' and not sub_tree:
                if parent_ie is not None:
                    # must be present:
                    this_ie = parent_ie.children[directory.base_path]
                else:
                    # without the parent ie, use the relatively slower inventory
                    # probing method
                    this_id = inv.path2id(
                            self._fix_case_of_inventory_path(directory.raw_path))
                    if this_id is None:
                        this_ie = None
                    else:
                        this_ie = inv[this_id]

                for subf in sorted(os.listdir(abspath)):
                    # here we could use TreeDirectory rather than
                    # string concatenation.
                    subp = osutils.pathjoin(directory.raw_path, subf)
                    # TODO: is_control_filename is very slow. Make it faster.
                    # TreeDirectory.is_control_filename could also make this
                    # faster - its impossible for a non root dir to have a
                    # control file.
                    if self.is_control_filename(subp):
                        mutter("skip control directory %r", subp)
                    elif subf in this_ie.children:
                        # recurse into this already versioned subdir.
                        dirs_to_add.append((_FastPath(subp, subf), this_ie))
                    else:
                        # user selection overrides ignoes
                        # ignore while selecting files - if we globbed in the
                        # outer loop we would ignore user files.
                        ignore_glob = self.is_ignored(subp)
                        if ignore_glob is not None:
                            # mutter("skip ignored sub-file %r", subp)
                            ignored.setdefault(ignore_glob, []).append(subp)
                        else:
                            #mutter("queue to add sub-file %r", subp)
                            dirs_to_add.append((_FastPath(subp, subf), this_ie))

        if len(added) > 0:
            if save:
                self._write_inventory(inv)
            else:
                self.read_working_inventory()
        return added, ignored

    def update_basis_by_delta(self, new_revid, delta):
        """Update the parents of this tree after a commit.

        This gives the tree one parent, with revision id new_revid. The
        inventory delta is applied to the current basis tree to generate the
        inventory for the parent new_revid, and all other parent trees are
        discarded.

        All the changes in the delta should be changes synchronising the basis
        tree with some or all of the working tree, with a change to a directory
        requiring that its contents have been recursively included. That is,
        this is not a general purpose tree modification routine, but a helper
        for commit which is not required to handle situations that do not arise
        outside of commit.

        See the inventory developers documentation for the theory behind
        inventory deltas.

        :param new_revid: The new revision id for the trees parent.
        :param delta: An inventory delta (see apply_inventory_delta) describing
            the changes from the current left most parent revision to new_revid.
        """
        # if the tree is updated by a pull to the branch, as happens in
        # WorkingTree2, when there was no separation between branch and tree,
        # then just clear merges, efficiency is not a concern for now as this
        # is legacy environments only, and they are slow regardless.
        if self.last_revision() == new_revid:
            self.set_parent_ids([new_revid])
            return
        # generic implementation based on Inventory manipulation. See
        # WorkingTree classes for optimised versions for specific format trees.
        basis = self.basis_tree()
        basis.lock_read()
        # TODO: Consider re-evaluating the need for this with CHKInventory
        # we don't strictly need to mutate an inventory for this
        # it only makes sense when apply_delta is cheaper than get_inventory()
        inventory = basis.inventory._get_mutable_inventory()
        basis.unlock()
        inventory.apply_delta(delta)
        rev_tree = RevisionTree(self.branch.repository, inventory, new_revid)
        self.set_parent_trees([(new_revid, rev_tree)])


class MutableTreeHooks(hooks.Hooks):
    """A dictionary mapping a hook name to a list of callables for mutabletree
    hooks.
    """

    def __init__(self):
        """Create the default hooks.

        """
        hooks.Hooks.__init__(self)
        self.create_hook(hooks.HookPoint('start_commit',
            "Called before a commit is performed on a tree. The start commit "
            "hook is able to change the tree before the commit takes place. "
            "start_commit is called with the bzrlib.tree.MutableTree that the "
            "commit is being performed on.", (1, 4), None))


# install the default hooks into the MutableTree class.
MutableTree.hooks = MutableTreeHooks()


class _FastPath(object):
    """A path object with fast accessors for things like basename."""

    __slots__ = ['raw_path', 'base_path']

    def __init__(self, path, base_path=None):
        """Construct a FastPath from path."""
        if base_path is None:
            self.base_path = osutils.basename(path)
        else:
            self.base_path = base_path
        self.raw_path = path

    def __cmp__(self, other):
        return cmp(self.raw_path, other.raw_path)

    def __hash__(self):
        return hash(self.raw_path)


def _add_one_and_parent(tree, inv, parent_ie, path, kind, action):
    """Add a new entry to the inventory and automatically add unversioned parents.

    :param inv: Inventory which will receive the new entry.
    :param parent_ie: Parent inventory entry if known, or None.  If
        None, the parent is looked up by name and used if present, otherwise it
        is recursively added.
    :param kind: Kind of new entry (file, directory, etc)
    :param action: callback(inv, parent_ie, path, kind); return ignored.
    :return: A list of paths which have been added.
    """
    # Nothing to do if path is already versioned.
    # This is safe from infinite recursion because the tree root is
    # always versioned.
    if parent_ie is not None:
        # we have a parent ie already
        added = []
    else:
        # slower but does not need parent_ie
        if inv.has_filename(tree._fix_case_of_inventory_path(path.raw_path)):
            return []
        # its really not there : add the parent
        # note that the dirname use leads to some extra str copying etc but as
        # there are a limited number of dirs we can be nested under, it should
        # generally find it very fast and not recurse after that.
        added = _add_one_and_parent(tree, inv, None,
            _FastPath(dirname(path.raw_path)), 'directory', action)
        parent_id = inv.path2id(dirname(path.raw_path))
        parent_ie = inv[parent_id]
    _add_one(tree, inv, parent_ie, path, kind, action)
    return added + [path.raw_path]


def _add_one(tree, inv, parent_ie, path, kind, file_id_callback):
    """Add a new entry to the inventory.

    :param inv: Inventory which will receive the new entry.
    :param parent_ie: Parent inventory entry.
    :param kind: Kind of new entry (file, directory, etc)
    :param file_id_callback: callback(inv, parent_ie, path, kind); return a
        file_id or None to generate a new file id
    :returns: None
    """
    file_id = file_id_callback(inv, parent_ie, path, kind)
    entry = inv.make_entry(kind, path.base_path, parent_ie.file_id,
        file_id=file_id)
    inv.add(entry)
