# Copyright (C) 2005, 2006, 2007 Canonical Ltd
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

"""WorkingTree4 format and implementation.

WorkingTree4 provides the dirstate based working tree logic.

To get a WorkingTree, call bzrdir.open_workingtree() or
WorkingTree.open(dir).
"""

from cStringIO import StringIO
import os
import sys

from bzrlib.lazy_import import lazy_import
lazy_import(globals(), """
from bisect import bisect_left
import collections
from copy import deepcopy
import errno
import itertools
import operator
import stat
from time import time
import warnings

import bzrlib
from bzrlib import (
    bzrdir,
    cache_utf8,
    conflicts as _mod_conflicts,
    delta,
    dirstate,
    errors,
    generate_ids,
    globbing,
    hashcache,
    ignores,
    merge,
    osutils,
    revisiontree,
    textui,
    transform,
    urlutils,
    xml5,
    xml6,
    )
import bzrlib.branch
from bzrlib.transport import get_transport
import bzrlib.ui
""")

from bzrlib import symbol_versioning
from bzrlib.decorators import needs_read_lock, needs_write_lock
from bzrlib.inventory import InventoryEntry, Inventory, ROOT_ID, entry_factory
from bzrlib.lockable_files import LockableFiles, TransportLock
from bzrlib.lockdir import LockDir
import bzrlib.mutabletree
from bzrlib.mutabletree import needs_tree_write_lock
from bzrlib.osutils import (
    compact_date,
    file_kind,
    isdir,
    normpath,
    pathjoin,
    rand_chars,
    realpath,
    safe_unicode,
    splitpath,
    supports_executable,
    )
from bzrlib.trace import mutter, note
from bzrlib.transport.local import LocalTransport
from bzrlib.tree import InterTree
from bzrlib.progress import DummyProgress, ProgressPhase
from bzrlib.revision import NULL_REVISION, CURRENT_REVISION
from bzrlib.rio import RioReader, rio_file, Stanza
from bzrlib.symbol_versioning import (deprecated_passed,
        deprecated_method,
        deprecated_function,
        DEPRECATED_PARAMETER,
        zero_eight,
        zero_eleven,
        zero_thirteen,
        )
from bzrlib.tree import Tree
from bzrlib.workingtree import WorkingTree, WorkingTree3, WorkingTreeFormat3


class WorkingTree4(WorkingTree3):
    """This is the Format 4 working tree.

    This differs from WorkingTree3 by:
     - Having a consolidated internal dirstate, stored in a
       randomly-accessible sorted file on disk.
     - Not having a regular inventory attribute.  One can be synthesized 
       on demand but this is expensive and should be avoided.

    This is new in bzr 0.15.
    """

    def __init__(self, basedir,
                 branch,
                 _control_files=None,
                 _format=None,
                 _bzrdir=None):
        """Construct a WorkingTree for basedir.

        If the branch is not supplied, it is opened automatically.
        If the branch is supplied, it must be the branch for this basedir.
        (branch.base is not cross checked, because for remote branches that
        would be meaningless).
        """
        self._format = _format
        self.bzrdir = _bzrdir
        from bzrlib.hashcache import HashCache
        from bzrlib.trace import note, mutter
        assert isinstance(basedir, basestring), \
            "base directory %r is not a string" % basedir
        basedir = safe_unicode(basedir)
        mutter("opening working tree %r", basedir)
        self._branch = branch
        assert isinstance(self.branch, bzrlib.branch.Branch), \
            "branch %r is not a Branch" % self.branch
        self.basedir = realpath(basedir)
        # if branch is at our basedir and is a format 6 or less
        # assume all other formats have their own control files.
        assert isinstance(_control_files, LockableFiles), \
            "_control_files must be a LockableFiles, not %r" % _control_files
        self._control_files = _control_files
        # update the whole cache up front and write to disk if anything changed;
        # in the future we might want to do this more selectively
        # two possible ways offer themselves : in self._unlock, write the cache
        # if needed, or, when the cache sees a change, append it to the hash
        # cache file, and have the parser take the most recent entry for a
        # given path only.
        cache_filename = self.bzrdir.get_workingtree_transport(None).local_abspath('stat-cache')
        hc = self._hashcache = HashCache(basedir, cache_filename, self._control_files._file_mode)
        hc.read()
        # is this scan needed ? it makes things kinda slow.
        #hc.scan()

        if hc.needs_write:
            mutter("write hc")
            hc.write()

        self._dirty = None
        #-------------
        # during a read or write lock these objects are set, and are
        # None the rest of the time.
        self._dirstate = None
        self._inventory = None
        #-------------

    @needs_tree_write_lock
    def _add(self, files, ids, kinds):
        """See MutableTree._add."""
        state = self.current_dirstate()
        for f, file_id, kind in zip(files, ids, kinds):
            f = f.strip('/')
            assert '//' not in f
            assert '..' not in f
            if file_id is None:
                file_id = generate_ids.gen_file_id(f)
            # deliberately add the file with no cached stat or sha1
            # - on the first access it will be gathered, and we can
            # always change this once tests are all passing.
            state.add(f, file_id, kind, None, '')
        self._dirty = True

    @needs_tree_write_lock
    def add_reference(self, sub_tree):
        # use standard implementation, which calls back to self._add
        # 
        # So we don't store the reference_revision in the working dirstate,
        # it's just recorded at the moment of commit. 
        self._add_reference(sub_tree)

    def break_lock(self):
        """Break a lock if one is present from another instance.

        Uses the ui factory to ask for confirmation if the lock may be from
        an active process.

        This will probe the repository for its lock as well.
        """
        # if the dirstate is locked by an active process, reject the break lock
        # call.
        try:
            if self._dirstate is None:
                clear = True
            else:
                clear = False
            state = self._current_dirstate()
            if state._lock_token is not None:
                # we already have it locked. sheese, cant break our own lock.
                raise errors.LockActive(self.basedir)
            else:
                try:
                    # try for a write lock - need permission to get one anyhow
                    # to break locks.
                    state.lock_write()
                except errors.LockContention:
                    # oslocks fail when a process is still live: fail.
                    # TODO: get the locked lockdir info and give to the user to
                    # assist in debugging.
                    raise errors.LockActive(self.basedir)
                else:
                    state.unlock()
        finally:
            if clear:
                self._dirstate = None
        self._control_files.break_lock()
        self.branch.break_lock()

    def _comparison_data(self, entry, path):
        kind, executable, stat_value = \
            WorkingTree3._comparison_data(self, entry, path)
        # it looks like a plain directory, but it's really a reference
        if kind == 'directory' and entry.kind == 'tree-reference':
            kind = 'tree-reference'
        return kind, executable, stat_value

    def current_dirstate(self):
        """Return the current dirstate object. 

        This is not part of the tree interface and only exposed for ease of
        testing.

        :raises errors.NotWriteLocked: when not in a lock. 
        """
        self._must_be_locked()
        return self._current_dirstate()

    def _current_dirstate(self):
        """Internal function that does not check lock status.
        
        This is needed for break_lock which also needs the dirstate.
        """
        if self._dirstate is not None:
            return self._dirstate
        local_path = self.bzrdir.get_workingtree_transport(None
            ).local_abspath('dirstate')
        self._dirstate = dirstate.DirState.on_file(local_path)
        return self._dirstate

    def filter_unversioned_files(self, paths):
        """Filter out paths that are not versioned.

        :return: set of paths.
        """
        # TODO: make a generic multi-bisect routine roughly that should list
        # the paths, then process one half at a time recursively, and feed the
        # results of each bisect in further still
        paths = sorted(paths)
        result = set()
        state = self.current_dirstate()
        # TODO we want a paths_to_dirblocks helper I think
        for path in paths:
            dirname, basename = os.path.split(path.encode('utf8'))
            _, _, _, path_is_versioned = state._get_block_entry_index(
                dirname, basename, 0)
            if path_is_versioned:
                result.add(path)
        return result

    def flush(self):
        """Write all cached data to disk."""
        if self._control_files._lock_mode != 'w':
            raise errors.NotWriteLocked(self)
        self.current_dirstate().save()
        self._inventory = None
        self._dirty = False

    def _generate_inventory(self):
        """Create and set self.inventory from the dirstate object.
        
        This is relatively expensive: we have to walk the entire dirstate.
        Ideally we would not, and can deprecate this function.
        """
        #: uncomment to trap on inventory requests.
        # import pdb;pdb.set_trace()
        state = self.current_dirstate()
        state._read_dirblocks_if_needed()
        root_key, current_entry = self._get_entry(path='')
        current_id = root_key[2]
        assert current_entry[0][0] == 'd' # directory
        inv = Inventory(root_id=current_id)
        # Turn some things into local variables
        minikind_to_kind = dirstate.DirState._minikind_to_kind
        factory = entry_factory
        utf8_decode = cache_utf8._utf8_decode
        inv_byid = inv._byid
        # we could do this straight out of the dirstate; it might be fast
        # and should be profiled - RBC 20070216
        parent_ies = {'' : inv.root}
        for block in state._dirblocks[1:]: # skip the root
            dirname = block[0]
            try:
                parent_ie = parent_ies[block[0]]
            except KeyError:
                # all the paths in this block are not versioned in this tree
                continue
            for key, entry in block[1]:
                minikind, link_or_sha1, size, executable, stat = entry[0]
                if minikind in ('a', 'r'): # absent, relocated
                    # a parent tree only entry
                    continue
                name = key[1]
                name_unicode = utf8_decode(name)[0]
                file_id = key[2]
                kind = minikind_to_kind[minikind]
                inv_entry = factory[kind](file_id, name_unicode,
                                          parent_ie.file_id)
                if kind == 'file':
                    # not strictly needed: working tree
                    #entry.executable = executable
                    #entry.text_size = size
                    #entry.text_sha1 = sha1
                    pass
                elif kind == 'directory':
                    # add this entry to the parent map.
                    parent_ies[(dirname + '/' + name).strip('/')] = inv_entry
                # These checks cost us around 40ms on a 55k entry tree
                assert file_id not in inv_byid
                assert name_unicode not in parent_ie.children
                inv_byid[file_id] = inv_entry
                parent_ie.children[name_unicode] = inv_entry
        self._inventory = inv

    def _get_entry(self, file_id=None, path=None):
        """Get the dirstate row for file_id or path.

        If either file_id or path is supplied, it is used as the key to lookup.
        If both are supplied, the fastest lookup is used, and an error is
        raised if they do not both point at the same row.
        
        :param file_id: An optional unicode file_id to be looked up.
        :param path: An optional unicode path to be looked up.
        :return: The dirstate row tuple for path/file_id, or (None, None)
        """
        if file_id is None and path is None:
            raise errors.BzrError('must supply file_id or path')
        state = self.current_dirstate()
        if path is not None:
            path = path.encode('utf8')
        return state._get_entry(0, fileid_utf8=file_id, path_utf8=path)

    def get_file_sha1(self, file_id, path=None, stat_value=None):
        # check file id is valid unconditionally.
        key, details = self._get_entry(file_id=file_id, path=path)
        assert key is not None, 'what error should this raise'
        # TODO:
        # if row stat is valid, use cached sha1, else, get a new sha1.
        if path is None:
            path = os.path.join(*key[0:2]).decode('utf8')
        return self._hashcache.get_sha1(path, stat_value)

    def _get_inventory(self):
        """Get the inventory for the tree. This is only valid within a lock."""
        if self._inventory is not None:
            return self._inventory
        self._must_be_locked()
        self._generate_inventory()
        return self._inventory

    inventory = property(_get_inventory,
                         doc="Inventory of this Tree")

    @needs_read_lock
    def get_parent_ids(self):
        """See Tree.get_parent_ids.
        
        This implementation requests the ids list from the dirstate file.
        """
        return self.current_dirstate().get_parent_ids()

    def get_reference_revision(self, entry, path=None):
        # referenced tree's revision is whatever's currently there
        return self.get_nested_tree(entry, path).last_revision()

    def get_nested_tree(self, entry, path=None):
        if path is None:
            path = self.id2path(entry.file_id)
        return WorkingTree.open(self.abspath(path))

    @needs_read_lock
    def get_root_id(self):
        """Return the id of this trees root"""
        return self._get_entry(path='')[0][2]

    def has_id(self, file_id):
        state = self.current_dirstate()
        file_id = osutils.safe_file_id(file_id)
        row, parents = self._get_entry(file_id=file_id)
        if row is None:
            return False
        return osutils.lexists(pathjoin(
                    self.basedir, row[0].decode('utf8'), row[1].decode('utf8')))

    @needs_read_lock
    def id2path(self, file_id):
        file_id = osutils.safe_file_id(file_id)
        state = self.current_dirstate()
        # XXX: possible_dir_name_ids is unused - is this just called for
        # side-effects? mbp 20070226
        possible_dir_name_ids = state._get_id_index().get(file_id, None)
        entry = self._get_entry(file_id=file_id)
        if entry == (None, None):
            return None
        path_utf8 = osutils.pathjoin(entry[0][0], entry[0][1])
        return path_utf8.decode('utf8')

    @needs_read_lock
    def __iter__(self):
        """Iterate through file_ids for this tree.

        file_ids are in a WorkingTree if they are in the working inventory
        and the working file exists.
        """
        result = []
        for key, tree_details in self.current_dirstate()._iter_entries():
            if tree_details[0][0] in ('a', 'r'): # absent, relocated
                # not relevant to the working tree
                continue
            path = pathjoin(self.basedir, key[0].decode('utf8'), key[1].decode('utf8'))
            if osutils.lexists(path):
                result.append(key[2])
        return iter(result)

    @needs_read_lock
    def kind(self, file_id):
        # The kind of a file is whatever it actually is on disk, except that 
        # tree-references need to be reported as such rather than as the
        # directiories
        #
        # TODO: Possibly we should check that the directory still really
        # contains a subtree, at least during commit? mbp 20070227
        kind = WorkingTree3.kind(self, file_id)
        if kind == 'directory':
            # TODO: ask the dirstate not the inventory -- mbp 20060227
            entry = self.inventory[file_id]
            if entry.kind == 'tree-reference':
                kind = 'tree-reference'
        return kind

    @needs_read_lock
    def _last_revision(self):
        """See Mutable.last_revision."""
        parent_ids = self.current_dirstate().get_parent_ids()
        if parent_ids:
            return parent_ids[0]
        else:
            return None

    def lock_read(self):
        super(WorkingTree4, self).lock_read()
        if self._dirstate is None:
            self.current_dirstate()
            self._dirstate.lock_read()

    def lock_tree_write(self):
        super(WorkingTree4, self).lock_tree_write()
        if self._dirstate is None:
            self.current_dirstate()
            self._dirstate.lock_write()

    def lock_write(self):
        super(WorkingTree4, self).lock_write()
        if self._dirstate is None:
            self.current_dirstate()
            self._dirstate.lock_write()

    @needs_tree_write_lock
    def move(self, from_paths, to_dir, after=False):
        """See WorkingTree.move()."""
        if not from_paths:
            return ()

        state = self.current_dirstate()

        assert not isinstance(from_paths, basestring)
        to_dir_utf8 = to_dir.encode('utf8')
        to_entry_dirname, to_basename = os.path.split(to_dir_utf8)
        id_index = state._get_id_index()
        # check destination directory
        # get the details for it
        to_entry_block_index, to_entry_entry_index, dir_present, entry_present = \
            state._get_block_entry_index(to_entry_dirname, to_basename, 0)
        if not entry_present:
            raise errors.BzrMoveFailedError('', to_dir,
                errors.NotInWorkingDirectory(to_dir))
        to_entry = state._dirblocks[to_entry_block_index][1][to_entry_entry_index]
        # get a handle on the block itself.
        to_block_index = state._ensure_block(
            to_entry_block_index, to_entry_entry_index, to_dir_utf8)
        to_block = state._dirblocks[to_block_index]
        to_abs = self.abspath(to_dir)
        if not isdir(to_abs):
            raise errors.BzrMoveFailedError('',to_dir,
                errors.NotADirectory(to_abs))

        if to_entry[1][0][0] != 'd':
            raise errors.BzrMoveFailedError('',to_dir,
                errors.NotADirectory(to_abs))

        if self._inventory is not None:
            update_inventory = True
            inv = self.inventory
            to_dir_ie = inv[to_dir_id]
            to_dir_id = to_entry[0][2]
        else:
            update_inventory = False

        rollbacks = []
        def move_one(old_entry, from_path_utf8, minikind, executable,
                     fingerprint, packed_stat, size,
                     to_block, to_key, to_path_utf8):
            state._make_absent(old_entry)
            from_key = old_entry[0]
            rollbacks.append(
                lambda:state.update_minimal(from_key,
                    minikind,
                    executable=executable,
                    fingerprint=fingerprint,
                    packed_stat=packed_stat,
                    size=size,
                    path_utf8=from_path_utf8))
            state.update_minimal(to_key,
                    minikind,
                    executable=executable,
                    fingerprint=fingerprint,
                    packed_stat=packed_stat,
                    size=size,
                    path_utf8=to_path_utf8)
            added_entry_index, _ = state._find_entry_index(to_key, to_block[1])
            new_entry = to_block[1][added_entry_index]
            rollbacks.append(lambda:state._make_absent(new_entry))

        # create rename entries and tuples
        for from_rel in from_paths:
            # from_rel is 'pathinroot/foo/bar'
            from_rel_utf8 = from_rel.encode('utf8')
            from_dirname, from_tail = osutils.split(from_rel)
            from_dirname, from_tail_utf8 = osutils.split(from_rel_utf8)
            from_entry = self._get_entry(path=from_rel)
            if from_entry == (None, None):
                raise errors.BzrMoveFailedError(from_rel,to_dir,
                    errors.NotVersionedError(path=str(from_rel)))

            from_id = from_entry[0][2]
            to_rel = pathjoin(to_dir, from_tail)
            to_rel_utf8 = pathjoin(to_dir_utf8, from_tail_utf8)
            item_to_entry = self._get_entry(path=to_rel)
            if item_to_entry != (None, None):
                raise errors.BzrMoveFailedError(from_rel, to_rel,
                    "Target is already versioned.")

            if from_rel == to_rel:
                raise errors.BzrMoveFailedError(from_rel, to_rel,
                    "Source and target are identical.")

            from_missing = not self.has_filename(from_rel)
            to_missing = not self.has_filename(to_rel)
            if after:
                move_file = False
            else:
                move_file = True
            if to_missing:
                if not move_file:
                    raise errors.BzrMoveFailedError(from_rel, to_rel,
                        errors.NoSuchFile(path=to_rel,
                        extra="New file has not been created yet"))
                elif from_missing:
                    # neither path exists
                    raise errors.BzrRenameFailedError(from_rel, to_rel,
                        errors.PathsDoNotExist(paths=(from_rel, to_rel)))
            else:
                if from_missing: # implicitly just update our path mapping
                    move_file = False
                elif not after:
                    raise errors.RenameFailedFilesExist(from_rel, to_rel,
                        extra="(Use --after to update the Bazaar id)")

            rollbacks = []
            def rollback_rename():
                """A single rename has failed, roll it back."""
                exc_info = None
                for rollback in reversed(rollbacks):
                    try:
                        rollback()
                    except Exception, e:
                        import pdb;pdb.set_trace()
                        exc_info = sys.exc_info()
                if exc_info:
                    raise exc_info[0], exc_info[1], exc_info[2]

            # perform the disk move first - its the most likely failure point.
            if move_file:
                from_rel_abs = self.abspath(from_rel)
                to_rel_abs = self.abspath(to_rel)
                try:
                    osutils.rename(from_rel_abs, to_rel_abs)
                except OSError, e:
                    raise errors.BzrMoveFailedError(from_rel, to_rel, e[1])
                rollbacks.append(lambda: osutils.rename(to_rel_abs, from_rel_abs))
            try:
                # perform the rename in the inventory next if needed: its easy
                # to rollback
                if update_inventory:
                    # rename the entry
                    from_entry = inv[from_id]
                    current_parent = from_entry.parent_id
                    inv.rename(from_id, to_dir_id, from_tail)
                    rollbacks.append(
                        lambda: inv.rename(from_id, current_parent, from_tail))
                # finally do the rename in the dirstate, which is a little
                # tricky to rollback, but least likely to need it.
                old_block_index, old_entry_index, dir_present, file_present = \
                    state._get_block_entry_index(from_dirname, from_tail_utf8, 0)
                old_block = state._dirblocks[old_block_index][1]
                old_entry = old_block[old_entry_index]
                from_key, old_entry_details = old_entry
                cur_details = old_entry_details[0]
                # remove the old row
                to_key = ((to_block[0],) + from_key[1:3])
                minikind = cur_details[0]
                move_one(old_entry, from_path_utf8=from_rel_utf8,
                         minikind=minikind,
                         executable=cur_details[3],
                         fingerprint=cur_details[1],
                         packed_stat=cur_details[4],
                         size=cur_details[2],
                         to_block=to_block,
                         to_key=to_key,
                         to_path_utf8=to_rel_utf8)

                if minikind == 'd':
                    def update_dirblock(from_dir, to_key, to_dir_utf8):
                        """all entries in this block need updating.

                        TODO: This is pretty ugly, and doesn't support
                        reverting, but it works.
                        """
                        assert from_dir != '', "renaming root not supported"
                        from_key = (from_dir, '')
                        from_block_idx, present = \
                            state._find_block_index_from_key(from_key)
                        if not present:
                            # This is the old record, if it isn't present, then
                            # there is theoretically nothing to update.
                            # (Unless it isn't present because of lazy loading,
                            # but we don't do that yet)
                            return
                        from_block = state._dirblocks[from_block_idx]
                        to_block_index, to_entry_index, _, _ = \
                            state._get_block_entry_index(to_key[0], to_key[1], 0)
                        to_block_index = state._ensure_block(
                            to_block_index, to_entry_index, to_dir_utf8)
                        to_block = state._dirblocks[to_block_index]
                        for entry in from_block[1]:
                            assert entry[0][0] == from_dir
                            cur_details = entry[1][0]
                            to_key = (to_dir_utf8, entry[0][1], entry[0][2])
                            from_path_utf8 = osutils.pathjoin(entry[0][0], entry[0][1])
                            to_path_utf8 = osutils.pathjoin(to_dir_utf8, entry[0][1])
                            minikind = cur_details[0]
                            move_one(entry, from_path_utf8=from_path_utf8,
                                     minikind=minikind,
                                     executable=cur_details[3],
                                     fingerprint=cur_details[1],
                                     packed_stat=cur_details[4],
                                     size=cur_details[2],
                                     to_block=to_block,
                                     to_key=to_key,
                                     to_path_utf8=to_rel_utf8)
                            if minikind == 'd':
                                # We need to move all the children of this
                                # entry
                                update_dirblock(from_path_utf8, to_key,
                                                to_path_utf8)
                    update_dirblock(from_rel_utf8, to_key, to_rel_utf8)
            except:
                rollback_rename()
                raise
            state._dirblock_state = dirstate.DirState.IN_MEMORY_MODIFIED
            self._dirty = True

        return #rename_tuples

    def _must_be_locked(self):
        if not self._control_files._lock_count:
            raise errors.ObjectNotLocked(self)

    def _new_tree(self):
        """Initialize the state in this tree to be a new tree."""
        self._dirty = True

    @needs_read_lock
    def path2id(self, path):
        """Return the id for path in this tree."""
        entry = self._get_entry(path=path)
        if entry == (None, None):
            return None
        return entry[0][2]

    def paths2ids(self, paths, trees=[], require_versioned=True):
        """See Tree.paths2ids().
        
        This specialisation fast-paths the case where all the trees are in the
        dirstate.
        """
        if paths is None:
            return None
        parents = self.get_parent_ids()
        for tree in trees:
            if not (isinstance(tree, DirStateRevisionTree) and tree._revision_id in
                parents):
                return super(WorkingTree4, self).paths2ids(paths, trees, require_versioned)
        search_indexes = [0] + [1 + parents.index(tree._revision_id) for tree in trees]
        # -- make all paths utf8 --
        paths_utf8 = set()
        for path in paths:
            paths_utf8.add(path.encode('utf8'))
        paths = paths_utf8
        # -- paths is now a utf8 path set --
        # -- get the state object and prepare it.
        state = self.current_dirstate()
        if False and (state._dirblock_state == dirstate.DirState.NOT_IN_MEMORY
            and '' not in paths):
            paths2ids = self._paths2ids_using_bisect
        else:
            paths2ids = self._paths2ids_in_memory
        return paths2ids(paths, search_indexes,
                         require_versioned=require_versioned)

    def _paths2ids_in_memory(self, paths, search_indexes,
                             require_versioned=True):
        state = self.current_dirstate()
        state._read_dirblocks_if_needed()
        def _entries_for_path(path):
            """Return a list with all the entries that match path for all ids.
            """
            dirname, basename = os.path.split(path)
            key = (dirname, basename, '')
            block_index, present = state._find_block_index_from_key(key)
            if not present:
                # the block which should contain path is absent.
                return []
            result = []
            block = state._dirblocks[block_index][1]
            entry_index, _ = state._find_entry_index(key, block)
            # we may need to look at multiple entries at this path: walk while the paths match.
            while (entry_index < len(block) and
                block[entry_index][0][0:2] == key[0:2]):
                result.append(block[entry_index])
                entry_index += 1
            return result
        if require_versioned:
            # -- check all supplied paths are versioned in a search tree. --
            all_versioned = True
            for path in paths:
                path_entries = _entries_for_path(path)
                if not path_entries:
                    # this specified path is not present at all: error
                    all_versioned = False
                    break
                found_versioned = False
                # for each id at this path
                for entry in path_entries:
                    # for each tree.
                    for index in search_indexes:
                        if entry[1][index][0] != 'a': # absent
                            found_versioned = True
                            # all good: found a versioned cell
                            break
                if not found_versioned:
                    # none of the indexes was not 'absent' at all ids for this
                    # path.
                    all_versioned = False
                    break
            if not all_versioned:
                raise errors.PathsNotVersionedError(paths)
        # -- remove redundancy in supplied paths to prevent over-scanning --
        search_paths = set()
        for path in paths:
            other_paths = paths.difference(set([path]))
            if not osutils.is_inside_any(other_paths, path):
                # this is a top level path, we must check it.
                search_paths.add(path)
        # sketch: 
        # for all search_indexs in each path at or under each element of
        # search_paths, if the detail is relocated: add the id, and add the
        # relocated path as one to search if its not searched already. If the
        # detail is not relocated, add the id.
        searched_paths = set()
        found_ids = set()
        def _process_entry(entry):
            """Look at search_indexes within entry.

            If a specific tree's details are relocated, add the relocation
            target to search_paths if not searched already. If it is absent, do
            nothing. Otherwise add the id to found_ids.
            """
            for index in search_indexes:
                if entry[1][index][0] == 'r': # relocated
                    if not osutils.is_inside_any(searched_paths, entry[1][index][1]):
                        search_paths.add(entry[1][index][1])
                elif entry[1][index][0] != 'a': # absent
                    found_ids.add(entry[0][2])
        while search_paths:
            current_root = search_paths.pop()
            searched_paths.add(current_root)
            # process the entries for this containing directory: the rest will be
            # found by their parents recursively.
            root_entries = _entries_for_path(current_root)
            if not root_entries:
                # this specified path is not present at all, skip it.
                continue
            for entry in root_entries:
                _process_entry(entry)
            initial_key = (current_root, '', '')
            block_index, _ = state._find_block_index_from_key(initial_key)
            while (block_index < len(state._dirblocks) and
                osutils.is_inside(current_root, state._dirblocks[block_index][0])):
                for entry in state._dirblocks[block_index][1]:
                    _process_entry(entry)
                block_index += 1
        return found_ids

    def _paths2ids_using_bisect(self, paths, search_indexes,
                                require_versioned=True):
        state = self.current_dirstate()
        found_ids = set()

        split_paths = sorted(osutils.split(p) for p in paths)
        found = state._bisect_recursive(split_paths)

        if require_versioned:
            found_dir_names = set(dir_name_id[:2] for dir_name_id in found)
            for dir_name in split_paths:
                if dir_name not in found_dir_names:
                    raise errors.PathsNotVersionedError(paths)

        for dir_name_id, trees_info in found.iteritems():
            for index in search_indexes:
                if trees_info[index][0] not in ('r', 'a'):
                    found_ids.add(dir_name_id[2])
        return found_ids

    def read_working_inventory(self):
        """Read the working inventory.
        
        This is a meaningless operation for dirstate, but we obey it anyhow.
        """
        return self.inventory

    @needs_read_lock
    def revision_tree(self, revision_id):
        """See Tree.revision_tree.

        WorkingTree4 supplies revision_trees for any basis tree.
        """
        revision_id = osutils.safe_revision_id(revision_id)
        dirstate = self.current_dirstate()
        parent_ids = dirstate.get_parent_ids()
        if revision_id not in parent_ids:
            raise errors.NoSuchRevisionInTree(self, revision_id)
        if revision_id in dirstate.get_ghosts():
            raise errors.NoSuchRevisionInTree(self, revision_id)
        return DirStateRevisionTree(dirstate, revision_id,
            self.branch.repository)

    @needs_tree_write_lock
    def set_last_revision(self, new_revision):
        """Change the last revision in the working tree."""
        new_revision = osutils.safe_revision_id(new_revision)
        parents = self.get_parent_ids()
        if new_revision in (NULL_REVISION, None):
            assert len(parents) < 2, (
                "setting the last parent to none with a pending merge is "
                "unsupported.")
            self.set_parent_ids([])
        else:
            self.set_parent_ids([new_revision] + parents[1:],
                allow_leftmost_as_ghost=True)

    @needs_tree_write_lock
    def set_parent_ids(self, revision_ids, allow_leftmost_as_ghost=False):
        """Set the parent ids to revision_ids.
        
        See also set_parent_trees. This api will try to retrieve the tree data
        for each element of revision_ids from the trees repository. If you have
        tree data already available, it is more efficient to use
        set_parent_trees rather than set_parent_ids. set_parent_ids is however
        an easier API to use.

        :param revision_ids: The revision_ids to set as the parent ids of this
            working tree. Any of these may be ghosts.
        """
        revision_ids = [osutils.safe_revision_id(r) for r in revision_ids]
        trees = []
        for revision_id in revision_ids:
            try:
                revtree = self.branch.repository.revision_tree(revision_id)
                # TODO: jam 20070213 KnitVersionedFile raises
                #       RevisionNotPresent rather than NoSuchRevision if a
                #       given revision_id is not present. Should Repository be
                #       catching it and re-raising NoSuchRevision?
            except (errors.NoSuchRevision, errors.RevisionNotPresent):
                revtree = None
            trees.append((revision_id, revtree))
        self.set_parent_trees(trees,
            allow_leftmost_as_ghost=allow_leftmost_as_ghost)

    @needs_tree_write_lock
    def set_parent_trees(self, parents_list, allow_leftmost_as_ghost=False):
        """Set the parents of the working tree.

        :param parents_list: A list of (revision_id, tree) tuples.
            If tree is None, then that element is treated as an unreachable
            parent tree - i.e. a ghost.
        """
        dirstate = self.current_dirstate()
        if len(parents_list) > 0:
            if not allow_leftmost_as_ghost and parents_list[0][1] is None:
                raise errors.GhostRevisionUnusableHere(parents_list[0][0])
        real_trees = []
        ghosts = []
        # convert absent trees to the null tree, which we convert back to
        # missing on access.
        for rev_id, tree in parents_list:
            rev_id = osutils.safe_revision_id(rev_id)
            if tree is not None:
                real_trees.append((rev_id, tree))
            else:
                real_trees.append((rev_id,
                    self.branch.repository.revision_tree(None)))
                ghosts.append(rev_id)
        dirstate.set_parent_trees(real_trees, ghosts=ghosts)
        self._dirty = True

    def _set_root_id(self, file_id):
        """See WorkingTree.set_root_id."""
        state = self.current_dirstate()
        state.set_path_id('', file_id)
        self._dirty = state._dirblock_state == dirstate.DirState.IN_MEMORY_MODIFIED

    def unlock(self):
        """Unlock in format 4 trees needs to write the entire dirstate."""
        if self._control_files._lock_count == 1:
            self._write_hashcache_if_dirty()
            # eventually we should do signature checking during read locks for
            # dirstate updates.
            if self._control_files._lock_mode == 'w':
                if self._dirty:
                    self.flush()
            if self._dirstate is not None:
                self._dirstate.unlock()
            self._dirstate = None
            self._inventory = None
        # reverse order of locking.
        try:
            return self._control_files.unlock()
        finally:
            self.branch.unlock()

    @needs_tree_write_lock
    def unversion(self, file_ids):
        """Remove the file ids in file_ids from the current versioned set.

        When a file_id is unversioned, all of its children are automatically
        unversioned.

        :param file_ids: The file ids to stop versioning.
        :raises: NoSuchId if any fileid is not currently versioned.
        """
        if not file_ids:
            return
        state = self.current_dirstate()
        state._read_dirblocks_if_needed()
        ids_to_unversion = set()
        for file_id in file_ids:
            ids_to_unversion.add(osutils.safe_file_id(file_id))
        paths_to_unversion = set()
        # sketch:
        # check if the root is to be unversioned, if so, assert for now.
        # walk the state marking unversioned things as absent.
        # if there are any un-unversioned ids at the end, raise
        for key, details in state._dirblocks[0][1]:
            if (details[0][0] not in ('a', 'r') and # absent or relocated
                key[2] in ids_to_unversion):
                # I haven't written the code to unversion / yet - it should be
                # supported.
                raise errors.BzrError('Unversioning the / is not currently supported')
        details_length = len(state._dirblocks[0][1][0][1])
        block_index = 0
        while block_index < len(state._dirblocks):
            # process one directory at a time.
            block = state._dirblocks[block_index]
            # first check: is the path one to remove - it or its children
            delete_block = False
            for path in paths_to_unversion:
                if (block[0].startswith(path) and
                    (len(block[0]) == len(path) or
                     block[0][len(path)] == '/')):
                    # this entire block should be deleted - its the block for a
                    # path to unversion; or the child of one
                    delete_block = True
                    break
            # TODO: trim paths_to_unversion as we pass by paths
            if delete_block:
                # this block is to be deleted: process it.
                # TODO: we can special case the no-parents case and
                # just forget the whole block.
                entry_index = 0
                while entry_index < len(block[1]):
                    if not state._make_absent(block[1][entry_index]):
                        entry_index += 1
                # go to the next block. (At the moment we dont delete empty
                # dirblocks)
                block_index += 1
                continue
            entry_index = 0
            while entry_index < len(block[1]):
                entry = block[1][entry_index]
                if (entry[1][0][0] in ('a', 'r') or # absent, relocated
                    # ^ some parent row.
                    entry[0][2] not in ids_to_unversion):
                    # ^ not an id to unversion
                    entry_index += 1
                    continue
                if entry[1][0][0] == 'd':
                    paths_to_unversion.add(os.path.join(*entry[0][0:2]))
                if not state._make_absent(entry):
                    entry_index += 1
                # we have unversioned this id
                ids_to_unversion.remove(entry[0][2])
            block_index += 1
        if ids_to_unversion:
            raise errors.NoSuchId(self, iter(ids_to_unversion).next())
        self._dirty = True
        # have to change the legacy inventory too.
        if self._inventory is not None:
            for file_id in file_ids:
                self._inventory.remove_recursive_id(file_id)

    @needs_tree_write_lock
    def _write_inventory(self, inv):
        """Write inventory as the current inventory."""
        assert not self._dirty, "attempting to write an inventory when the dirstate is dirty will cause data loss"
        self.current_dirstate().set_state_from_inventory(inv)
        self._dirty = True
        self.flush()


class WorkingTreeFormat4(WorkingTreeFormat3):
    """The first consolidated dirstate working tree format.

    This format:
        - exists within a metadir controlling .bzr
        - includes an explicit version marker for the workingtree control
          files, separate from the BzrDir format
        - modifies the hash cache format
        - is new in bzr TODO FIXME SETBEFOREMERGE
        - uses a LockDir to guard access to it.
    """

    supports_tree_reference = True

    def get_format_string(self):
        """See WorkingTreeFormat.get_format_string()."""
        return "Bazaar Working Tree format 4\n"

    def get_format_description(self):
        """See WorkingTreeFormat.get_format_description()."""
        return "Working tree format 4"

    def initialize(self, a_bzrdir, revision_id=None):
        """See WorkingTreeFormat.initialize().

        revision_id allows creating a working tree at a different
        revision than the branch is at.
        """
        revision_id = osutils.safe_revision_id(revision_id)
        if not isinstance(a_bzrdir.transport, LocalTransport):
            raise errors.NotLocalUrl(a_bzrdir.transport.base)
        transport = a_bzrdir.get_workingtree_transport(self)
        control_files = self._open_control_files(a_bzrdir)
        control_files.create_lock()
        control_files.lock_write()
        control_files.put_utf8('format', self.get_format_string())
        branch = a_bzrdir.open_branch()
        if revision_id is None:
            revision_id = branch.last_revision()
        local_path = transport.local_abspath('dirstate')
        state = dirstate.DirState.initialize(local_path)
        state.unlock()
        wt = WorkingTree4(a_bzrdir.root_transport.local_abspath('.'),
                         branch,
                         _format=self,
                         _bzrdir=a_bzrdir,
                         _control_files=control_files)
        wt._new_tree()
        wt.lock_write()
        try:
            #wt.current_dirstate().set_path_id('', NEWROOT)
            wt.set_last_revision(revision_id)
            wt.flush()
            basis = wt.basis_tree()
            basis.lock_read()
            transform.build_tree(basis, wt)
            basis.unlock()
        finally:
            control_files.unlock()
            wt.unlock()
        return wt

    def _open(self, a_bzrdir, control_files):
        """Open the tree itself.

        :param a_bzrdir: the dir for the tree.
        :param control_files: the control files for the tree.
        """
        return WorkingTree4(a_bzrdir.root_transport.local_abspath('.'),
                           branch=a_bzrdir.open_branch(),
                           _format=self,
                           _bzrdir=a_bzrdir,
                           _control_files=control_files)

    def __get_matchingbzrdir(self):
        # please test against something that will let us do tree references
        return bzrdir.format_registry.make_bzrdir(
            'experimental-reference-dirstate')

    _matchingbzrdir = property(__get_matchingbzrdir)


class DirStateRevisionTree(Tree):
    """A revision tree pulling the inventory from a dirstate."""

    def __init__(self, dirstate, revision_id, repository):
        self._dirstate = dirstate
        self._revision_id = osutils.safe_revision_id(revision_id)
        self._repository = repository
        self._inventory = None
        self._locked = 0
        self._dirstate_locked = False

    def annotate_iter(self, file_id):
        """See Tree.annotate_iter"""
        w = self._repository.weave_store.get_weave(file_id,
                           self._repository.get_transaction())
        return w.annotate_iter(self.inventory[file_id].revision)

    def _comparison_data(self, entry, path):
        if entry is None:
            return None, False, None
        # trust the entry as RevisionTree does, but this may not be
        # sensible: the entry might not have come from us?
        return entry.kind, entry.executable, None

    def _file_size(self, entry, stat_value):
        return entry.text_size

    def filter_unversioned_files(self, paths):
        """Filter out paths that are not versioned.

        :return: set of paths.
        """
        pred = self.has_filename
        return set((p for p in paths if not pred(p)))

    def _get_parent_index(self):
        """Return the index in the dirstate referenced by this tree."""
        return self._dirstate.get_parent_ids().index(self._revision_id) + 1

    def _get_entry(self, file_id=None, path=None):
        """Get the dirstate row for file_id or path.

        If either file_id or path is supplied, it is used as the key to lookup.
        If both are supplied, the fastest lookup is used, and an error is
        raised if they do not both point at the same row.
        
        :param file_id: An optional unicode file_id to be looked up.
        :param path: An optional unicode path to be looked up.
        :return: The dirstate row tuple for path/file_id, or (None, None)
        """
        if file_id is None and path is None:
            raise errors.BzrError('must supply file_id or path')
        file_id = osutils.safe_file_id(file_id)
        if path is not None:
            path = path.encode('utf8')
        parent_index = self._get_parent_index()
        return self._dirstate._get_entry(parent_index, fileid_utf8=file_id, path_utf8=path)

    def _generate_inventory(self):
        """Create and set self.inventory from the dirstate object.

        (So this is only called the first time the inventory is requested for
        this tree; it then remains in memory.)

        This is relatively expensive: we have to walk the entire dirstate.
        """
        assert self._locked, 'cannot generate inventory of an unlocked '\
            'dirstate revision tree'
        # separate call for profiling - makes it clear where the costs are.
        self._dirstate._read_dirblocks_if_needed()
        assert self._revision_id in self._dirstate.get_parent_ids(), \
            'parent %s has disappeared from %s' % (
            self._revision_id, self._dirstate.get_parent_ids())
        parent_index = self._dirstate.get_parent_ids().index(self._revision_id) + 1
        # This is identical now to the WorkingTree _generate_inventory except
        # for the tree index use.
        root_key, current_entry = self._dirstate._get_entry(parent_index, path_utf8='')
        current_id = root_key[2]
        assert current_entry[parent_index][0] == 'd'
        inv = Inventory(root_id=current_id, revision_id=self._revision_id)
        inv.root.revision = current_entry[parent_index][4]
        # Turn some things into local variables
        minikind_to_kind = dirstate.DirState._minikind_to_kind
        factory = entry_factory
        utf8_decode = cache_utf8._utf8_decode
        inv_byid = inv._byid
        # we could do this straight out of the dirstate; it might be fast
        # and should be profiled - RBC 20070216
        parent_ies = {'' : inv.root}
        for block in self._dirstate._dirblocks[1:]: #skip root
            dirname = block[0]
            try:
                parent_ie = parent_ies[dirname]
            except KeyError:
                # all the paths in this block are not versioned in this tree
                continue
            for key, entry in block[1]:
                minikind, fingerprint, size, executable, revid = entry[parent_index]
                if minikind in ('a', 'r'): # absent, relocated
                    # not this tree
                    continue
                name = key[1]
                name_unicode = utf8_decode(name)[0]
                file_id = key[2]
                kind = minikind_to_kind[minikind]
                inv_entry = factory[kind](file_id, name_unicode,
                                          parent_ie.file_id)
                inv_entry.revision = revid
                if kind == 'file':
                    inv_entry.executable = executable
                    inv_entry.text_size = size
                    inv_entry.text_sha1 = fingerprint
                elif kind == 'directory':
                    parent_ies[(dirname + '/' + name).strip('/')] = inv_entry
                elif kind == 'symlink':
                    inv_entry.executable = False
                    inv_entry.text_size = size
                    inv_entry.symlink_target = utf8_decode(fingerprint)[0]
                elif kind == 'tree-reference':
                    inv_entry.reference_revision = fingerprint
                else:
                    raise AssertionError("cannot convert entry %r into an InventoryEntry"
                            % entry)
                # These checks cost us around 40ms on a 55k entry tree
                assert file_id not in inv_byid
                assert name_unicode not in parent_ie.children
                inv_byid[file_id] = inv_entry
                parent_ie.children[name_unicode] = inv_entry
        self._inventory = inv

    def get_file_sha1(self, file_id, path=None, stat_value=None):
        # TODO: if path is present, fast-path on that, as inventory
        # might not be present
        ie = self.inventory[file_id]
        if ie.kind == "file":
            return ie.text_sha1
        return None

    def get_file(self, file_id):
        return StringIO(self.get_file_text(file_id))

    def get_file_lines(self, file_id):
        ie = self.inventory[file_id]
        return self._repository.weave_store.get_weave(file_id,
                self._repository.get_transaction()).get_lines(ie.revision)

    def get_file_size(self, file_id):
        return self.inventory[file_id].text_size

    def get_file_text(self, file_id):
        return ''.join(self.get_file_lines(file_id))

    def get_symlink_target(self, file_id):
        entry = self._get_entry(file_id=file_id)
        parent_index = self._get_parent_index()
        if entry[1][parent_index][0] != 'l':
            return None
        else:
            # At present, none of the tree implementations supports non-ascii
            # symlink targets. So we will just assume that the dirstate path is
            # correct.
            return entry[1][parent_index][1]

    def get_revision_id(self):
        """Return the revision id for this tree."""
        return self._revision_id

    def _get_inventory(self):
        if self._inventory is not None:
            return self._inventory
        self._generate_inventory()
        return self._inventory

    inventory = property(_get_inventory,
                         doc="Inventory of this Tree")

    def get_parent_ids(self):
        """The parents of a tree in the dirstate are not cached."""
        return self._repository.get_revision(self._revision_id).parent_ids

    def has_filename(self, filename):
        return bool(self.path2id(filename))

    def is_executable(self, file_id, path=None):
        ie = self.inventory[file_id]
        if ie.kind != "file":
            return None
        return ie.executable

    @needs_read_lock
    def kind(self, file_id):
        return self.inventory[file_id].kind

    def list_files(self, include_root=False):
        # We use a standard implementation, because DirStateRevisionTree is
        # dealing with one of the parents of the current state
        inv = self._get_inventory()
        entries = inv.iter_entries()
        if self.inventory.root is not None and not include_root:
            entries.next()
        for path, entry in entries:
            yield path, 'V', entry.kind, entry.file_id, entry

    def lock_read(self):
        """Lock the tree for a set of operations."""
        if not self._locked:
            self._repository.lock_read()
            if self._dirstate._lock_token is None:
                self._dirstate.lock_read()
                self._dirstate_locked = True
        self._locked += 1

    @needs_read_lock
    def path2id(self, path):
        """Return the id for path in this tree."""
        # lookup by path: faster than splitting and walking the ivnentory.
        entry = self._get_entry(path=path)
        if entry == (None, None):
            return None
        return entry[0][2]

    def unlock(self):
        """Unlock, freeing any cache memory used during the lock."""
        # outside of a lock, the inventory is suspect: release it.
        self._locked -=1
        if not self._locked:
            self._inventory = None
            self._locked = 0
            if self._dirstate_locked:
                self._dirstate.unlock()
                self._dirstate_locked = False
            self._repository.unlock()

    def walkdirs(self, prefix=""):
        # TODO: jam 20070215 This is the cheap way by cheating and using the
        #       RevisionTree implementation.
        #       This should be cleaned up to use the much faster Dirstate code
        #       This is a little tricky, though, because the dirstate is
        #       indexed by current path, not by parent path.
        #       So for now, we just build up the parent inventory, and extract
        #       it the same way RevisionTree does.
        _directory = 'directory'
        inv = self._get_inventory()
        top_id = inv.path2id(prefix)
        if top_id is None:
            pending = []
        else:
            pending = [(prefix, top_id)]
        while pending:
            dirblock = []
            relpath, file_id = pending.pop()
            # 0 - relpath, 1- file-id
            if relpath:
                relroot = relpath + '/'
            else:
                relroot = ""
            # FIXME: stash the node in pending
            entry = inv[file_id]
            for name, child in entry.sorted_children():
                toppath = relroot + name
                dirblock.append((toppath, name, child.kind, None,
                    child.file_id, child.kind
                    ))
            yield (relpath, entry.file_id), dirblock
            # push the user specified dirs from dirblock
            for dir in reversed(dirblock):
                if dir[2] == _directory:
                    pending.append((dir[0], dir[4]))


class InterDirStateTree(InterTree):
    """Fast path optimiser for changes_from with dirstate trees."""

    def __init__(self, source, target):
        super(InterDirStateTree, self).__init__(source, target)
        if not InterDirStateTree.is_compatible(source, target):
            raise Exception, "invalid source %r and target %r" % (source, target)

    @staticmethod
    def make_source_parent_tree(source, target):
        """Change the source tree into a parent of the target."""
        revid = source.commit('record tree')
        target.branch.repository.fetch(source.branch.repository, revid)
        target.set_parent_ids([revid])
        return target.basis_tree(), target
    _matching_from_tree_format = WorkingTreeFormat4()
    _matching_to_tree_format = WorkingTreeFormat4()
    _test_mutable_trees_to_test_trees = make_source_parent_tree

    def _iter_changes(self, include_unchanged=False,
                      specific_files=None, pb=None, extra_trees=[],
                      require_versioned=True):
        """Return the changes from source to target.

        :return: An iterator that yields tuples. See InterTree._iter_changes
            for details.
        :param specific_files: An optional list of file paths to restrict the
            comparison to. When mapping filenames to ids, all matches in all
            trees (including optional extra_trees) are used, and all children of
            matched directories are included.
        :param include_unchanged: An optional boolean requesting the inclusion of
            unchanged entries in the result.
        :param extra_trees: An optional list of additional trees to use when
            mapping the contents of specific_files (paths) to file_ids.
        :param require_versioned: If True, all files in specific_files must be
            versioned in one of source, target, extra_trees or
            PathsNotVersionedError is raised.
        """
        # NB: show_status depends on being able to pass in non-versioned files
        # and report them as unknown
        # TODO: handle extra trees in the dirstate.
        # TODO: handle comparisons as an empty tree as a different special
        # case? mbp 20070226
        if extra_trees or (self.source._revision_id == NULL_REVISION):
            # we can't fast-path these cases (yet)
            for f in super(InterDirStateTree, self)._iter_changes(
                include_unchanged, specific_files, pb, extra_trees,
                require_versioned):
                yield f
            return
        assert (self.source._revision_id in self.target.get_parent_ids()), \
                "revision {%s} is not stored in {%s}, but %s " \
                "can only be used for trees stored in the dirstate" \
                % (self.source._revision_id, self.target, self._iter_changes)
        parents = self.target.get_parent_ids()
        target_index = 0
        source_index = 1 + parents.index(self.source._revision_id)
        # -- make all specific_files utf8 --
        if specific_files:
            specific_files_utf8 = set()
            for path in specific_files:
                specific_files_utf8.add(path.encode('utf8'))
            specific_files = specific_files_utf8
        else:
            specific_files = set([''])
        # -- specific_files is now a utf8 path set --
        # -- get the state object and prepare it.
        state = self.target.current_dirstate()
        state._read_dirblocks_if_needed()
        def _entries_for_path(path):
            """Return a list with all the entries that match path for all ids.
            """
            dirname, basename = os.path.split(path)
            key = (dirname, basename, '')
            block_index, present = state._find_block_index_from_key(key)
            if not present:
                # the block which should contain path is absent.
                return []
            result = []
            block = state._dirblocks[block_index][1]
            entry_index, _ = state._find_entry_index(key, block)
            # we may need to look at multiple entries at this path: walk while the specific_files match.
            while (entry_index < len(block) and
                block[entry_index][0][0:2] == key[0:2]):
                result.append(block[entry_index])
                entry_index += 1
            return result
        if require_versioned:
            # -- check all supplied paths are versioned in a search tree. --
            all_versioned = True
            for path in specific_files:
                path = path.encode('utf8')
                path_entries = _entries_for_path(path)
                if not path_entries:
                    # this specified path is not present at all: error
                    all_versioned = False
                    break
                found_versioned = False
                # for each id at this path
                for entry in path_entries:
                    # for each tree.
                    for index in source_index, target_index:
                        if entry[1][index][0] != 'a': # absent
                            found_versioned = True
                            # all good: found a versioned cell
                            break
                if not found_versioned:
                    # none of the indexes was not 'absent' at all ids for this
                    # path.
                    all_versioned = False
                    break
            if not all_versioned:
                raise errors.PathsNotVersionedError(paths)
        # -- remove redundancy in supplied specific_files to prevent over-scanning --
        search_specific_files = set()
        for path in specific_files:
            other_specific_files = specific_files.difference(set([path]))
            if not osutils.is_inside_any(other_specific_files, path):
                # this is a top level path, we must check it.
                search_specific_files.add(path)
        # sketch: 
        # compare source_index and target_index at or under each element of search_specific_files.
        # follow the following comparison table. Note that we only want to do diff operations when
        # the target is fdl because thats when the walkdirs logic will have exposed the pathinfo 
        # for the target.
        # cases:
        # 
        # Source | Target | disk | action
        #   r    | fdl    |      | add source to search, add id path move and perform
        #        |        |      | diff check on source-target
        #   r    | fdl    |  a   | dangling file that was present in the basis. 
        #        |        |      | ???
        #   r    |  a     |      | add source to search
        #   r    |  a     |  a   | 
        #   r    |  r     |      | this path is present in a non-examined tree, skip.
        #   r    |  r     |  a   | this path is present in a non-examined tree, skip.
        #   a    | fdl    |      | add new id
        #   a    | fdl    |  a   | dangling locally added file, skip
        #   a    |  a     |      | not present in either tree, skip
        #   a    |  a     |  a   | not present in any tree, skip
        #   a    |  r     |      | not present in either tree at this path, skip as it
        #        |        |      | may not be selected by the users list of paths.
        #   a    |  r     |  a   | not present in either tree at this path, skip as it
        #        |        |      | may not be selected by the users list of paths.
        #  fdl   | fdl    |      | content in both: diff them
        #  fdl   | fdl    |  a   | deleted locally, but not unversioned - show as deleted ?
        #  fdl   |  a     |      | unversioned: output deleted id for now
        #  fdl   |  a     |  a   | unversioned and deleted: output deleted id
        #  fdl   |  r     |      | relocated in this tree, so add target to search.
        #        |        |      | Dont diff, we will see an r,fd; pair when we reach
        #        |        |      | this id at the other path.
        #  fdl   |  r     |  a   | relocated in this tree, so add target to search.
        #        |        |      | Dont diff, we will see an r,fd; pair when we reach
        #        |        |      | this id at the other path.

        # for all search_indexs in each path at or under each element of
        # search_specific_files, if the detail is relocated: add the id, and add the
        # relocated path as one to search if its not searched already. If the
        # detail is not relocated, add the id.
        searched_specific_files = set()
        def _process_entry(entry, path_info):
            """Compare an entry and real disk to generate delta information.

            :param path_info: top_relpath, basename, kind, lstat, abspath for
                the path of entry. If None, then the path is considered absent.
                (Perhaps we should pass in a concrete entry for this ?)
            """
            # TODO: when a parent has been renamed, dont emit path renames for children,
            source_details = entry[1][source_index]
            target_details = entry[1][target_index]
            if source_details[0] in 'rfdl' and target_details[0] in 'fdl':
                # claimed content in both: diff
                #   r    | fdl    |      | add source to search, add id path move and perform
                #        |        |      | diff check on source-target
                #   r    | fdl    |  a   | dangling file that was present in the basis. 
                #        |        |      | ???
                if source_details[0] in 'r':
                    # add the source to the search path to find any children it
                    # has.  TODO ? : only add if it is a container ?
                    if not osutils.is_inside_any(searched_specific_files, source_details[1]):
                        search_specific_files.add(source_details[1])
                    # generate the old path; this is needed for stating later
                    # as well.
                    old_path = source_details[1]
                    old_dirname, old_basename = os.path.split(old_path)
                    path = os.path.join(*entry[0][0:2])
                    old_entry = state._get_entry(source_index, path_utf8=old_path)
                    # update the source details variable to be the real
                    # location.
                    source_details = old_entry[1][source_index]
                else:
                    old_path = path = os.path.join(*entry[0][0:2])
                    old_dirname, old_basename = entry[0][0:2]
                if path_info is None:
                    # the file is missing on disk, show as removed.
                    print "missing file"
                    old_path = os.path.join(*entry[0][0:2])
                    result.removed.append((old_path, entry[0][2], dirstate.DirState._minikind_to_kind[source_details[0]]))
                # use the kind from disk.
                elif source_details[0] != path_info[2][0]:
                    # different kind
                    import pdb;pdb.set_trace()
                    print "kind change"
                else:
                    # same kind
                    if path_info[2][0] == 'd':
                        # directories have no fingerprint
                        content_change = False
                        executable_change = False
                    elif path_info[2][0] == 'f':
                        # has it changed? fast path: size, slow path: sha1.
                        executable_change = source_details[3] != bool(
                            stat.S_ISREG(path_info[3].st_mode)
                            and stat.S_IEXEC & path_info[3].st_mode)
                        if source_details[2] != path_info[3].st_size:
                            content_change = True
                        else:
                            # maybe the same. Get the hash
                            new_hash = self.target._hashcache.get_sha1(path, path_info[3])
                            content_change = (new_hash != source_details[1])
                    elif path_info[2][0] == 'l':
                        import pdb;pdb.set_trace()
                        print "link"
                    else:
                        raise Exception, "unknown minikind"
                    # parent id is the entry for the path in the target tree
                    # TODO: the target is the same for an entire directory: cache em.
                    source_parent_id = state._get_entry(source_index, path_utf8=old_dirname)[0][2]
                    if source_parent_id == entry[0][2]:
                        source_parent_id = None
                    target_parent_id = state._get_entry(target_index, path_utf8=entry[0][0])[0][2]
                    if target_parent_id == entry[0][2]:
                        target_parent_id = None
                    source_exec = source_details[3]
                    target_exec = bool(
                        stat.S_ISREG(path_info[3].st_mode)
                        and stat.S_IEXEC & path_info[3].st_mode)
                    return ((entry[0][2], path, content_change, (True, True), (source_parent_id, target_parent_id), (old_basename, entry[0][1]), (dirstate.DirState._minikind_to_kind[source_details[0]], path_info[2]), (source_exec, target_exec)),)
            elif source_details[0] in 'a' and target_details[0] in 'fdl':
                # looks like a new file
                if path_info is not None:
                    path = os.path.join(*entry[0][0:2])
                    # parent id is the entry for the path in the target tree
                    # TODO: these are the same for an entire directory: cache em.
                    parent_id = state._get_entry(target_index, path_utf8=entry[0][0])[0][2]
                    if parent_id == entry[0][2]:
                        parent_id = None
                    # basename
                    new_executable = bool(
                        stat.S_ISREG(path_info[3].st_mode)
                        and stat.S_IEXEC & path_info[3].st_mode)
                    return ((entry[0][2], path, True, (False, True), (None, parent_id), (None, entry[0][1]), (None, path_info[2]), (None, new_executable)),)
                else:
                    # but its not on disk: we deliberately treat this as just
                    # never-present. (Why ?! - RBC 20070224)
                    pass
            elif source_details[0] in 'fdl' and target_details[0] in 'a':
                # unversioned, possibly, or possibly not deleted: we dont care.
                # if its still on disk, *and* theres no other entry at this
                # path [we dont know this in this routine at the moment -
                # perhaps we should change this - then it would be an unknown.
                old_path = os.path.join(*entry[0][0:2])
                # parent id is the entry for the path in the target tree
                parent_id = state._get_entry(source_index, path_utf8=entry[0][0])[0][2]
                if parent_id == entry[0][2]:
                    parent_id = None
                return ((entry[0][2], old_path, True, (True, False), (parent_id, None), (entry[0][1], None), (dirstate.DirState._minikind_to_kind[source_details[0]], None), (source_details[3], None)),)
            elif source_details[0] in 'fdl' and target_details[0] in 'r':
                # a rename; could be a true rename, or a rename inherited from
                # a renamed parent. TODO: handle this efficiently. Its not
                # common case to rename dirs though, so a correct but slow
                # implementation will do.
                if not osutils.is_inside_any(searched_specific_files, target_details[1]):
                    search_specific_files.add(target_details[1])
            else:
                import pdb;pdb.set_trace()
            return ()
        while search_specific_files:
            # TODO: the pending list should be lexically sorted?
            current_root = search_specific_files.pop()
            searched_specific_files.add(current_root)
            # process the entries for this containing directory: the rest will be
            # found by their parents recursively.
            root_entries = _entries_for_path(current_root)
            root_abspath = self.target.abspath(current_root)
            try:
                root_stat = os.lstat(root_abspath)
            except OSError, e:
                if e.errno == errno.ENOENT:
                    # the path does not exist: let _process_entry know that.
                    root_dir_info = None
                else:
                    # some other random error: hand it up.
                    raise
            else:
                root_dir_info = ('', current_root,
                    osutils.file_kind_from_stat_mode(root_stat.st_mode), root_stat,
                    root_abspath)
            if not root_entries and not root_dir_info:
                # this specified path is not present at all, skip it.
                continue
            for entry in root_entries:
                for result in _process_entry(entry, root_dir_info):
                    # this check should probably be outside the loop: one
                    # 'iterate two trees' api, and then _iter_changes filters
                    # unchanged pairs. - RBC 20070226
                    if include_unchanged or result[2] or True in map(lambda x:x[0]!=x[1], result[3:8]):
                        yield result
            dir_iterator = osutils.walkdirs(root_abspath, prefix=current_root)
            initial_key = (current_root, '', '')
            block_index, _ = state._find_block_index_from_key(initial_key)
            if block_index == 0:
                # we have processed the total root already, but because the
                # initial key matched it we sould skip it here.
                block_index +=1
            try:
                current_dir_info = dir_iterator.next()
            except OSError, e:
                if e.errno in (errno.ENOENT, errno.ENOTDIR):
                    # there may be directories in the inventory even though
                    # this path is not a file on disk: so mark it as end of
                    # iterator
                    current_dir_info = None
                else:
                    raise
            else:
                if current_dir_info[0][0] == '':
                    # remove .bzr from iteration
                    bzr_index = bisect_left(current_dir_info[1], ('.bzr',))
                    assert current_dir_info[1][bzr_index][0] == '.bzr'
                    del current_dir_info[1][bzr_index]
                # convert the unicode relpaths in the dir index to uf8 for
                # comparison with dirstate data.
                # TODO: keep the utf8 version around for giving to the caller.
                current_dir_info = ((current_dir_info[0][0].encode('utf8'), current_dir_info[0][1]),
                    [(line[0].encode('utf8'), line[1].encode('utf8')) + line[2:] for line in current_dir_info[1]])
            # walk until both the directory listing and the versioned metadata
            # are exhausted. TODO: reevaluate this, perhaps we should stop when
            # the versioned data runs out.
            if (block_index < len(state._dirblocks) and
                osutils.is_inside(current_root, state._dirblocks[block_index][0])):
                current_block = state._dirblocks[block_index]
            else:
                current_block = None
            while (current_dir_info is not None or
                current_block is not None):
                if current_dir_info and current_block and current_dir_info[0][0] != current_block[0]:
                    if current_block[0] < current_dir_info[0][0]:
                        # extra dir on disk: pass for now? should del from info ?
                        import pdb;pdb.set_trace()
                        print 'unversioned dir'
                    else:
                        # entry referring to missing dir.
                        import pdb;pdb.set_trace()
                        print 'missing dir'
                entry_index = 0
                if current_block and entry_index < len(current_block[1]):
                    current_entry = current_block[1][entry_index]
                else:
                    current_entry = None
                advance_entry = True
                path_index = 0
                if current_dir_info and path_index < len(current_dir_info[1]):
                    current_path_info = current_dir_info[1][path_index]
                else:
                    current_path_info = None
                advance_path = True
                while (current_entry is not None or
                    current_path_info is not None):
                    if current_entry is None:
                        # no more entries: yield current_pathinfo as an
                        # unversioned file: its not the same as a path in any
                        # tree in the dirstate.
                        new_executable = bool(
                            stat.S_ISREG(current_path_info[3].st_mode)
                            and stat.S_IEXEC & current_path_info[3].st_mode)
                        yield (None, current_path_info[0], True, (False, False), (None, None), (None, current_path_info[1]), (None, current_path_info[2]), (None, new_executable))
                    elif current_path_info is None:
                        # no path is fine: the per entry code will handle it.
                        for result in _process_entry(current_entry, current_path_info):
                            # this check should probably be outside the loop: one
                            # 'iterate two trees' api, and then _iter_changes filters
                            # unchanged pairs. - RBC 20070226
                            if include_unchanged or result[2] or True in map(lambda x:x[0]!=x[1], result[3:8]):
                                yield result
                    elif current_entry[0][1] != current_path_info[1]:
                        if current_path_info[1] < current_entry[0][1]:
                            # extra file on disk: pass for now
                            import pdb;pdb.set_trace()
                            print 'unversioned file'
                        else:
                            # entry referring to file not present on disk.
                            # advance the entry only, after processing.
                            for result in _process_entry(current_entry, None):
                                # this check should probably be outside the loop: one
                                # 'iterate two trees' api, and then _iter_changes filters
                                # unchanged pairs. - RBC 20070226
                                if include_unchanged or result[2] or True in map(lambda x:x[0]!=x[1], result[3:8]):
                                    yield result
                            advance_path = False
                    else:
                        for result in _process_entry(current_entry, current_path_info):
                            # this check should probably be outside the loop: one
                            # 'iterate two trees' api, and then _iter_changes filters
                            # unchanged pairs. - RBC 20070226
                            if include_unchanged or result[2] or True in map(lambda x:x[0]!=x[1], result[3:8]):
                                yield result
                    if advance_entry and current_entry is not None:
                        entry_index += 1
                        if entry_index < len(current_block[1]):
                            current_entry = current_block[1][entry_index]
                        else:
                            current_entry = None
                    else:
                        advance_entry = True # reset the advance flaga
                    if advance_path and current_path_info is not None:
                        path_index += 1
                        if path_index < len(current_dir_info[1]):
                            current_path_info = current_dir_info[1][path_index]
                        else:
                            current_path_info = None
                    else:
                        advance_path = True # reset the advance flagg.
                if current_block is not None:
                    block_index += 1
                    if (block_index < len(state._dirblocks) and
                        osutils.is_inside(current_root, state._dirblocks[block_index][0])):
                        current_block = state._dirblocks[block_index]
                    else:
                        current_block = None
                if current_dir_info is not None:
                    try:
                        current_dir_info = dir_iterator.next()
                        # convert the unicode relpaths in the dir index to uf8 for
                        # comparison with dirstate data.
                        # TODO: keep the utf8 version around for giving to the caller.
                        current_dir_info = ((current_dir_info[0][0].encode('utf8'), current_dir_info[0][1]),
                            [(line[0].encode('utf8'), line[1].encode('utf8')) + line[2:] for line in current_dir_info[1]])
                    except StopIteration:
                        current_dir_info = None


    @staticmethod
    def is_compatible(source, target):
        # the target must be a dirstate working tree
        if not isinstance(target, WorkingTree4):
            return False
        # the source must be a revtreee or dirstate rev tree.
        if not isinstance(source,
            (revisiontree.RevisionTree, DirStateRevisionTree)):
            return False
        # the source revid must be in the target dirstate
        if not (source._revision_id == NULL_REVISION or
            source._revision_id in target.get_parent_ids()):
            # TODO: what about ghosts? it may well need to 
            # check for them explicitly.
            return False
        return True

InterTree.register_optimiser(InterDirStateTree)
