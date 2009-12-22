# Copyright (C) 2005, 2006, 2007, 2008, 2009 Canonical Ltd
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
import errno
import stat

import bzrlib
from bzrlib import (
    bzrdir,
    cache_utf8,
    debug,
    dirstate,
    errors,
    generate_ids,
    osutils,
    revision as _mod_revision,
    revisiontree,
    trace,
    transform,
    views,
    )
import bzrlib.branch
import bzrlib.ui
""")

from bzrlib.decorators import needs_read_lock, needs_write_lock
from bzrlib.filters import filtered_input_file, internal_size_sha_file_byname
from bzrlib.inventory import Inventory, ROOT_ID, entry_factory
import bzrlib.mutabletree
from bzrlib.mutabletree import needs_tree_write_lock
from bzrlib.osutils import (
    file_kind,
    isdir,
    pathjoin,
    realpath,
    safe_unicode,
    )
from bzrlib.trace import mutter
from bzrlib.transport.local import LocalTransport
from bzrlib.tree import InterTree
from bzrlib.tree import Tree
from bzrlib.workingtree import WorkingTree, WorkingTree3, WorkingTreeFormat3


class DirStateWorkingTree(WorkingTree3):
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
        basedir = safe_unicode(basedir)
        mutter("opening working tree %r", basedir)
        self._branch = branch
        self.basedir = realpath(basedir)
        # if branch is at our basedir and is a format 6 or less
        # assume all other formats have their own control files.
        self._control_files = _control_files
        self._transport = self._control_files._transport
        self._dirty = None
        #-------------
        # during a read or write lock these objects are set, and are
        # None the rest of the time.
        self._dirstate = None
        self._inventory = None
        #-------------
        self._setup_directory_is_tree_reference()
        self._detect_case_handling()
        self._rules_searcher = None
        self.views = self._make_views()
        #--- allow tests to select the dirstate iter_changes implementation
        self._iter_changes = dirstate._process_entry

    @needs_tree_write_lock
    def _add(self, files, ids, kinds):
        """See MutableTree._add."""
        state = self.current_dirstate()
        for f, file_id, kind in zip(files, ids, kinds):
            f = f.strip('/')
            if self.path2id(f):
                # special case tree root handling.
                if f == '' and self.path2id(f) == ROOT_ID:
                    state.set_path_id('', generate_ids.gen_file_id(f))
                continue
            if file_id is None:
                file_id = generate_ids.gen_file_id(f)
            # deliberately add the file with no cached stat or sha1
            # - on the first access it will be gathered, and we can
            # always change this once tests are all passing.
            state.add(f, file_id, kind, None, '')
        self._make_dirty(reset_inventory=True)

    def _make_dirty(self, reset_inventory):
        """Make the tree state dirty.

        :param reset_inventory: True if the cached inventory should be removed
            (presuming there is one).
        """
        self._dirty = True
        if reset_inventory and self._inventory is not None:
            self._inventory = None

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
        # it looks like a plain directory, but it's really a reference -- see
        # also kind()
        if (self._repo_supports_tree_reference and kind == 'directory'
            and entry is not None and entry.kind == 'tree-reference'):
            kind = 'tree-reference'
        return kind, executable, stat_value

    @needs_write_lock
    def commit(self, message=None, revprops=None, *args, **kwargs):
        # mark the tree as dirty post commit - commit
        # can change the current versioned list by doing deletes.
        result = WorkingTree3.commit(self, message, revprops, *args, **kwargs)
        self._make_dirty(reset_inventory=True)
        return result

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
        self._dirstate = dirstate.DirState.on_file(local_path,
            self._sha1_provider())
        return self._dirstate

    def _sha1_provider(self):
        """A function that returns a SHA1Provider suitable for this tree.

        :return: None if content filtering is not supported by this tree.
          Otherwise, a SHA1Provider is returned that sha's the canonical
          form of files, i.e. after read filters are applied.
        """
        if self.supports_content_filtering():
            return ContentFilterAwareSHA1Provider(self)
        else:
            return None

    def filter_unversioned_files(self, paths):
        """Filter out paths that are versioned.

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
            if not path_is_versioned:
                result.add(path)
        return result

    def flush(self):
        """Write all cached data to disk."""
        if self._control_files._lock_mode != 'w':
            raise errors.NotWriteLocked(self)
        self.current_dirstate().save()
        self._inventory = None
        self._dirty = False

    @needs_tree_write_lock
    def _gather_kinds(self, files, kinds):
        """See MutableTree._gather_kinds."""
        for pos, f in enumerate(files):
            if kinds[pos] is None:
                kinds[pos] = self._kind(f)

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
        if not (current_entry[0][0] == 'd'): # directory
            raise AssertionError(current_entry)
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
                parent_ie = parent_ies[dirname]
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
                    # This is only needed on win32, where this is the only way
                    # we know the executable bit.
                    inv_entry.executable = executable
                    # not strictly needed: working tree
                    #inv_entry.text_size = size
                    #inv_entry.text_sha1 = sha1
                elif kind == 'directory':
                    # add this entry to the parent map.
                    parent_ies[(dirname + '/' + name).strip('/')] = inv_entry
                elif kind == 'tree-reference':
                    if not self._repo_supports_tree_reference:
                        raise errors.UnsupportedOperation(
                            self._generate_inventory,
                            self.branch.repository)
                    inv_entry.reference_revision = link_or_sha1 or None
                elif kind != 'symlink':
                    raise AssertionError("unknown kind %r" % kind)
                # These checks cost us around 40ms on a 55k entry tree
                if file_id in inv_byid:
                    raise AssertionError('file_id %s already in'
                        ' inventory as %s' % (file_id, inv_byid[file_id]))
                if name_unicode in parent_ie.children:
                    raise AssertionError('name %r already in parent'
                        % (name_unicode,))
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
        entry = self._get_entry(file_id=file_id, path=path)
        if entry[0] is None:
            raise errors.NoSuchId(self, file_id)
        if path is None:
            path = pathjoin(entry[0][0], entry[0][1]).decode('utf8')

        file_abspath = self.abspath(path)
        state = self.current_dirstate()
        if stat_value is None:
            try:
                stat_value = os.lstat(file_abspath)
            except OSError, e:
                if e.errno == errno.ENOENT:
                    return None
                else:
                    raise
        link_or_sha1 = dirstate.update_entry(state, entry, file_abspath,
            stat_value=stat_value)
        if entry[1][0][0] == 'f':
            if link_or_sha1 is None:
                file_obj, statvalue = self.get_file_with_stat(file_id, path)
                try:
                    sha1 = osutils.sha_file(file_obj)
                finally:
                    file_obj.close()
                self._observed_sha1(file_id, path, (sha1, statvalue))
                return sha1
            else:
                return link_or_sha1
        return None

    def _get_inventory(self):
        """Get the inventory for the tree. This is only valid within a lock."""
        if 'evil' in debug.debug_flags:
            trace.mutter_callsite(2,
                "accessing .inventory forces a size of tree translation.")
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

    def get_reference_revision(self, file_id, path=None):
        # referenced tree's revision is whatever's currently there
        return self.get_nested_tree(file_id, path).last_revision()

    def get_nested_tree(self, file_id, path=None):
        if path is None:
            path = self.id2path(file_id)
        # else: check file_id is at path?
        return WorkingTree.open(self.abspath(path))

    @needs_read_lock
    def get_root_id(self):
        """Return the id of this trees root"""
        return self._get_entry(path='')[0][2]

    def has_id(self, file_id):
        state = self.current_dirstate()
        row, parents = self._get_entry(file_id=file_id)
        if row is None:
            return False
        return osutils.lexists(pathjoin(
                    self.basedir, row[0].decode('utf8'), row[1].decode('utf8')))

    def has_or_had_id(self, file_id):
        state = self.current_dirstate()
        row, parents = self._get_entry(file_id=file_id)
        return row is not None

    @needs_read_lock
    def id2path(self, file_id):
        "Convert a file-id to a path."
        state = self.current_dirstate()
        entry = self._get_entry(file_id=file_id)
        if entry == (None, None):
            raise errors.NoSuchId(tree=self, file_id=file_id)
        path_utf8 = osutils.pathjoin(entry[0][0], entry[0][1])
        return path_utf8.decode('utf8')

    def _is_executable_from_path_and_stat_from_basis(self, path, stat_result):
        entry = self._get_entry(path=path)
        if entry == (None, None):
            return False # Missing entries are not executable
        return entry[1][0][3] # Executable?

    if not osutils.supports_executable():
        def is_executable(self, file_id, path=None):
            """Test if a file is executable or not.

            Note: The caller is expected to take a read-lock before calling this.
            """
            entry = self._get_entry(file_id=file_id, path=path)
            if entry == (None, None):
                return False
            return entry[1][0][3]

        _is_executable_from_path_and_stat = \
            _is_executable_from_path_and_stat_from_basis
    else:
        def is_executable(self, file_id, path=None):
            """Test if a file is executable or not.

            Note: The caller is expected to take a read-lock before calling this.
            """
            self._must_be_locked()
            if not path:
                path = self.id2path(file_id)
            mode = os.lstat(self.abspath(path)).st_mode
            return bool(stat.S_ISREG(mode) and stat.S_IEXEC & mode)

    def all_file_ids(self):
        """See Tree.iter_all_file_ids"""
        self._must_be_locked()
        result = set()
        for key, tree_details in self.current_dirstate()._iter_entries():
            if tree_details[0][0] in ('a', 'r'): # relocated
                continue
            result.add(key[2])
        return result

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

    def iter_references(self):
        if not self._repo_supports_tree_reference:
            # When the repo doesn't support references, we will have nothing to
            # return
            return
        for key, tree_details in self.current_dirstate()._iter_entries():
            if tree_details[0][0] in ('a', 'r'): # absent, relocated
                # not relevant to the working tree
                continue
            if not key[1]:
                # the root is not a reference.
                continue
            relpath = pathjoin(key[0].decode('utf8'), key[1].decode('utf8'))
            try:
                if self._kind(relpath) == 'tree-reference':
                    yield relpath, key[2]
            except errors.NoSuchFile:
                # path is missing on disk.
                continue

    def _observed_sha1(self, file_id, path, (sha1, statvalue)):
        """See MutableTree._observed_sha1."""
        state = self.current_dirstate()
        entry = self._get_entry(file_id=file_id, path=path)
        state._observed_sha1(entry, sha1, statvalue)

    def kind(self, file_id):
        """Return the kind of a file.

        This is always the actual kind that's on disk, regardless of what it
        was added as.

        Note: The caller is expected to take a read-lock before calling this.
        """
        relpath = self.id2path(file_id)
        if relpath is None:
            raise AssertionError(
                "path for id {%s} is None!" % file_id)
        return self._kind(relpath)

    def _kind(self, relpath):
        abspath = self.abspath(relpath)
        kind = file_kind(abspath)
        if (self._repo_supports_tree_reference and kind == 'directory'):
            entry = self._get_entry(path=relpath)
            if entry[1] is not None:
                if entry[1][0][0] == 't':
                    kind = 'tree-reference'
        return kind

    @needs_read_lock
    def _last_revision(self):
        """See Mutable.last_revision."""
        parent_ids = self.current_dirstate().get_parent_ids()
        if parent_ids:
            return parent_ids[0]
        else:
            return _mod_revision.NULL_REVISION

    def lock_read(self):
        """See Branch.lock_read, and WorkingTree.unlock."""
        self.branch.lock_read()
        try:
            self._control_files.lock_read()
            try:
                state = self.current_dirstate()
                if not state._lock_token:
                    state.lock_read()
                # set our support for tree references from the repository in
                # use.
                self._repo_supports_tree_reference = getattr(
                    self.branch.repository._format, "supports_tree_reference",
                    False)
            except:
                self._control_files.unlock()
                raise
        except:
            self.branch.unlock()
            raise

    def _lock_self_write(self):
        """This should be called after the branch is locked."""
        try:
            self._control_files.lock_write()
            try:
                state = self.current_dirstate()
                if not state._lock_token:
                    state.lock_write()
                # set our support for tree references from the repository in
                # use.
                self._repo_supports_tree_reference = getattr(
                    self.branch.repository._format, "supports_tree_reference",
                    False)
            except:
                self._control_files.unlock()
                raise
        except:
            self.branch.unlock()
            raise

    def lock_tree_write(self):
        """See MutableTree.lock_tree_write, and WorkingTree.unlock."""
        self.branch.lock_read()
        self._lock_self_write()

    def lock_write(self):
        """See MutableTree.lock_write, and WorkingTree.unlock."""
        self.branch.lock_write()
        self._lock_self_write()

    @needs_tree_write_lock
    def move(self, from_paths, to_dir, after=False):
        """See WorkingTree.move()."""
        result = []
        if not from_paths:
            return result
        state = self.current_dirstate()
        if isinstance(from_paths, basestring):
            raise ValueError()
        to_dir_utf8 = to_dir.encode('utf8')
        to_entry_dirname, to_basename = os.path.split(to_dir_utf8)
        id_index = state._get_id_index()
        # check destination directory
        # get the details for it
        to_entry_block_index, to_entry_entry_index, dir_present, entry_present = \
            state._get_block_entry_index(to_entry_dirname, to_basename, 0)
        if not entry_present:
            raise errors.BzrMoveFailedError('', to_dir,
                errors.NotVersionedError(to_dir))
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
            to_dir_id = to_entry[0][2]
            to_dir_ie = inv[to_dir_id]
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

        for from_rel in from_paths:
            # from_rel is 'pathinroot/foo/bar'
            from_rel_utf8 = from_rel.encode('utf8')
            from_dirname, from_tail = osutils.split(from_rel)
            from_dirname, from_tail_utf8 = osutils.split(from_rel_utf8)
            from_entry = self._get_entry(path=from_rel)
            if from_entry == (None, None):
                raise errors.BzrMoveFailedError(from_rel,to_dir,
                    errors.NotVersionedError(path=from_rel))

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
                    raise errors.RenameFailedFilesExist(from_rel, to_rel)

            rollbacks = []
            def rollback_rename():
                """A single rename has failed, roll it back."""
                # roll back everything, even if we encounter trouble doing one
                # of them.
                #
                # TODO: at least log the other exceptions rather than just
                # losing them mbp 20070307
                exc_info = None
                for rollback in reversed(rollbacks):
                    try:
                        rollback()
                    except Exception, e:
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
                        """Recursively update all entries in this dirblock."""
                        if from_dir == '':
                            raise AssertionError("renaming root not supported")
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

                        # Grab a copy since move_one may update the list.
                        for entry in from_block[1][:]:
                            if not (entry[0][0] == from_dir):
                                raise AssertionError()
                            cur_details = entry[1][0]
                            to_key = (to_dir_utf8, entry[0][1], entry[0][2])
                            from_path_utf8 = osutils.pathjoin(entry[0][0], entry[0][1])
                            to_path_utf8 = osutils.pathjoin(to_dir_utf8, entry[0][1])
                            minikind = cur_details[0]
                            if minikind in 'ar':
                                # Deleted children of a renamed directory
                                # Do not need to be updated.
                                # Children that have been renamed out of this
                                # directory should also not be updated
                                continue
                            move_one(entry, from_path_utf8=from_path_utf8,
                                     minikind=minikind,
                                     executable=cur_details[3],
                                     fingerprint=cur_details[1],
                                     packed_stat=cur_details[4],
                                     size=cur_details[2],
                                     to_block=to_block,
                                     to_key=to_key,
                                     to_path_utf8=to_path_utf8)
                            if minikind == 'd':
                                # We need to move all the children of this
                                # entry
                                update_dirblock(from_path_utf8, to_key,
                                                to_path_utf8)
                    update_dirblock(from_rel_utf8, to_key, to_rel_utf8)
            except:
                rollback_rename()
                raise
            result.append((from_rel, to_rel))
            state._dirblock_state = dirstate.DirState.IN_MEMORY_MODIFIED
            self._make_dirty(reset_inventory=False)

        return result

    def _must_be_locked(self):
        if not self._control_files._lock_count:
            raise errors.ObjectNotLocked(self)

    def _new_tree(self):
        """Initialize the state in this tree to be a new tree."""
        self._dirty = True

    @needs_read_lock
    def path2id(self, path):
        """Return the id for path in this tree."""
        path = path.strip('/')
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
                return super(DirStateWorkingTree, self).paths2ids(paths,
                    trees, require_versioned)
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
        search_paths = osutils.minimum_path_selection(paths)
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
        parents = self.get_parent_ids()
        if new_revision in (_mod_revision.NULL_REVISION, None):
            if len(parents) >= 2:
                raise AssertionError(
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

        parent_ids = [rev_id for rev_id, tree in parents_list]
        graph = self.branch.repository.get_graph()
        heads = graph.heads(parent_ids)
        accepted_revisions = set()

        # convert absent trees to the null tree, which we convert back to
        # missing on access.
        for rev_id, tree in parents_list:
            if len(accepted_revisions) > 0:
                # we always accept the first tree
                if rev_id in accepted_revisions or rev_id not in heads:
                    # We have already included either this tree, or its
                    # descendent, so we skip it.
                    continue
            _mod_revision.check_not_reserved_id(rev_id)
            if tree is not None:
                real_trees.append((rev_id, tree))
            else:
                real_trees.append((rev_id,
                    self.branch.repository.revision_tree(
                        _mod_revision.NULL_REVISION)))
                ghosts.append(rev_id)
            accepted_revisions.add(rev_id)
        dirstate.set_parent_trees(real_trees, ghosts=ghosts)
        self._make_dirty(reset_inventory=False)

    def _set_root_id(self, file_id):
        """See WorkingTree.set_root_id."""
        state = self.current_dirstate()
        state.set_path_id('', file_id)
        if state._dirblock_state == dirstate.DirState.IN_MEMORY_MODIFIED:
            self._make_dirty(reset_inventory=True)

    def _sha_from_stat(self, path, stat_result):
        """Get a sha digest from the tree's stat cache.

        The default implementation assumes no stat cache is present.

        :param path: The path.
        :param stat_result: The stat result being looked up.
        """
        return self.current_dirstate().sha1_from_stat(path, stat_result)

    @needs_read_lock
    def supports_tree_reference(self):
        return self._repo_supports_tree_reference

    def unlock(self):
        """Unlock in format 4 trees needs to write the entire dirstate."""
        # do non-implementation specific cleanup
        self._cleanup()

        if self._control_files._lock_count == 1:
            # eventually we should do signature checking during read locks for
            # dirstate updates.
            if self._control_files._lock_mode == 'w':
                if self._dirty:
                    self.flush()
            if self._dirstate is not None:
                # This is a no-op if there are no modifications.
                self._dirstate.save()
                self._dirstate.unlock()
            # TODO: jam 20070301 We shouldn't have to wipe the dirstate at this
            #       point. Instead, it could check if the header has been
            #       modified when it is locked, and if not, it can hang on to
            #       the data it has in memory.
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
        ids_to_unversion = set(file_ids)
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
                    entry = block[1][entry_index]
                    if entry[1][0][0] in 'ar':
                        # don't remove absent or renamed entries
                        entry_index += 1
                    else:
                        # Mark this file id as having been removed
                        ids_to_unversion.discard(entry[0][2])
                        if not state._make_absent(entry):
                            # The block has not shrunk.
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
                    paths_to_unversion.add(pathjoin(entry[0][0], entry[0][1]))
                if not state._make_absent(entry):
                    entry_index += 1
                # we have unversioned this id
                ids_to_unversion.remove(entry[0][2])
            block_index += 1
        if ids_to_unversion:
            raise errors.NoSuchId(self, iter(ids_to_unversion).next())
        self._make_dirty(reset_inventory=False)
        # have to change the legacy inventory too.
        if self._inventory is not None:
            for file_id in file_ids:
                self._inventory.remove_recursive_id(file_id)

    @needs_tree_write_lock
    def rename_one(self, from_rel, to_rel, after=False):
        """See WorkingTree.rename_one"""
        self.flush()
        WorkingTree.rename_one(self, from_rel, to_rel, after)

    @needs_tree_write_lock
    def apply_inventory_delta(self, changes):
        """See MutableTree.apply_inventory_delta"""
        state = self.current_dirstate()
        state.update_by_delta(changes)
        self._make_dirty(reset_inventory=True)

    def update_basis_by_delta(self, new_revid, delta):
        """See MutableTree.update_basis_by_delta."""
        if self.last_revision() == new_revid:
            raise AssertionError()
        self.current_dirstate().update_basis_by_delta(delta, new_revid)

    @needs_read_lock
    def _validate(self):
        self._dirstate._validate()

    @needs_tree_write_lock
    def _write_inventory(self, inv):
        """Write inventory as the current inventory."""
        if self._dirty:
            raise AssertionError("attempting to write an inventory when the "
                "dirstate is dirty will lose pending changes")
        had_inventory = self._inventory is not None
        # Setting self._inventory = None forces the dirstate to regenerate the
        # working inventory. We do this because self.inventory may be inv, or
        # may have been modified, and either case would prevent a clean delta
        # being created.
        self._inventory = None
        # generate a delta,
        delta = inv._make_delta(self.inventory)
        # and apply it.
        self.apply_inventory_delta(delta)
        if had_inventory:
            self._inventory = inv
        self.flush()


class ContentFilterAwareSHA1Provider(dirstate.SHA1Provider):

    def __init__(self, tree):
        self.tree = tree

    def sha1(self, abspath):
        """See dirstate.SHA1Provider.sha1()."""
        filters = self.tree._content_filter_stack(
            self.tree.relpath(osutils.safe_unicode(abspath)))
        return internal_size_sha_file_byname(abspath, filters)[1]

    def stat_and_sha1(self, abspath):
        """See dirstate.SHA1Provider.stat_and_sha1()."""
        filters = self.tree._content_filter_stack(
            self.tree.relpath(osutils.safe_unicode(abspath)))
        file_obj = file(abspath, 'rb', 65000)
        try:
            statvalue = os.fstat(file_obj.fileno())
            if filters:
                file_obj = filtered_input_file(file_obj, filters)
            sha1 = osutils.size_sha_file(file_obj)[1]
        finally:
            file_obj.close()
        return statvalue, sha1


class ContentFilteringDirStateWorkingTree(DirStateWorkingTree):
    """Dirstate working tree that supports content filtering.

    The dirstate holds the hash and size of the canonical form of the file, 
    and most methods must return that.
    """

    def _file_content_summary(self, path, stat_result):
        # This is to support the somewhat obsolete path_content_summary method
        # with content filtering: see
        # <https://bugs.edge.launchpad.net/bzr/+bug/415508>.
        #
        # If the dirstate cache is up to date and knows the hash and size,
        # return that.
        # Otherwise if there are no content filters, return the on-disk size
        # and leave the hash blank.
        # Otherwise, read and filter the on-disk file and use its size and
        # hash.
        #
        # The dirstate doesn't store the size of the canonical form so we
        # can't trust it for content-filtered trees.  We just return None.
        dirstate_sha1 = self._dirstate.sha1_from_stat(path, stat_result)
        executable = self._is_executable_from_path_and_stat(path, stat_result)
        return ('file', None, executable, dirstate_sha1)


class WorkingTree4(DirStateWorkingTree):
    """This is the Format 4 working tree.

    This differs from WorkingTree3 by:
     - Having a consolidated internal dirstate, stored in a
       randomly-accessible sorted file on disk.
     - Not having a regular inventory attribute.  One can be synthesized
       on demand but this is expensive and should be avoided.

    This is new in bzr 0.15.
    """


class WorkingTree5(ContentFilteringDirStateWorkingTree):
    """This is the Format 5 working tree.

    This differs from WorkingTree4 by:
     - Supporting content filtering.

    This is new in bzr 1.11.
    """


class WorkingTree6(ContentFilteringDirStateWorkingTree):
    """This is the Format 6 working tree.

    This differs from WorkingTree5 by:
     - Supporting a current view that may mask the set of files in a tree
       impacted by most user operations.

    This is new in bzr 1.14.
    """

    def _make_views(self):
        return views.PathBasedViews(self)


class DirStateWorkingTreeFormat(WorkingTreeFormat3):
    def initialize(self, a_bzrdir, revision_id=None, from_branch=None,
                   accelerator_tree=None, hardlink=False):
        """See WorkingTreeFormat.initialize().

        :param revision_id: allows creating a working tree at a different
        revision than the branch is at.
        :param accelerator_tree: A tree which can be used for retrieving file
            contents more quickly than the revision tree, i.e. a workingtree.
            The revision tree will be used for cases where accelerator_tree's
            content is different.
        :param hardlink: If true, hard-link files from accelerator_tree,
            where possible.

        These trees get an initial random root id, if their repository supports
        rich root data, TREE_ROOT otherwise.
        """
        if not isinstance(a_bzrdir.transport, LocalTransport):
            raise errors.NotLocalUrl(a_bzrdir.transport.base)
        transport = a_bzrdir.get_workingtree_transport(self)
        control_files = self._open_control_files(a_bzrdir)
        control_files.create_lock()
        control_files.lock_write()
        transport.put_bytes('format', self.get_format_string(),
            mode=a_bzrdir._get_file_mode())
        if from_branch is not None:
            branch = from_branch
        else:
            branch = a_bzrdir.open_branch()
        if revision_id is None:
            revision_id = branch.last_revision()
        local_path = transport.local_abspath('dirstate')
        # write out new dirstate (must exist when we create the tree)
        state = dirstate.DirState.initialize(local_path)
        state.unlock()
        del state
        wt = self._tree_class(a_bzrdir.root_transport.local_abspath('.'),
                         branch,
                         _format=self,
                         _bzrdir=a_bzrdir,
                         _control_files=control_files)
        wt._new_tree()
        wt.lock_tree_write()
        try:
            self._init_custom_control_files(wt)
            if revision_id in (None, _mod_revision.NULL_REVISION):
                if branch.repository.supports_rich_root():
                    wt._set_root_id(generate_ids.gen_root_id())
                else:
                    wt._set_root_id(ROOT_ID)
                wt.flush()
            basis = None
            # frequently, we will get here due to branching.  The accelerator
            # tree will be the tree from the branch, so the desired basis
            # tree will often be a parent of the accelerator tree.
            if accelerator_tree is not None:
                try:
                    basis = accelerator_tree.revision_tree(revision_id)
                except errors.NoSuchRevision:
                    pass
            if basis is None:
                basis = branch.repository.revision_tree(revision_id)
            if revision_id == _mod_revision.NULL_REVISION:
                parents_list = []
            else:
                parents_list = [(revision_id, basis)]
            basis.lock_read()
            try:
                wt.set_parent_trees(parents_list, allow_leftmost_as_ghost=True)
                wt.flush()
                # if the basis has a root id we have to use that; otherwise we
                # use a new random one
                basis_root_id = basis.get_root_id()
                if basis_root_id is not None:
                    wt._set_root_id(basis_root_id)
                    wt.flush()
                if wt.supports_content_filtering():
                    # The original tree may not have the same content filters
                    # applied so we can't safely build the inventory delta from
                    # the source tree.
                    delta_from_tree = False
                else:
                    delta_from_tree = True
                # delta_from_tree is safe even for DirStateRevisionTrees,
                # because wt4.apply_inventory_delta does not mutate the input
                # inventory entries.
                transform.build_tree(basis, wt, accelerator_tree,
                                     hardlink=hardlink,
                                     delta_from_tree=delta_from_tree)
            finally:
                basis.unlock()
        finally:
            control_files.unlock()
            wt.unlock()
        return wt

    def _init_custom_control_files(self, wt):
        """Subclasses with custom control files should override this method.

        The working tree and control files are locked for writing when this
        method is called.

        :param wt: the WorkingTree object
        """

    def _open(self, a_bzrdir, control_files):
        """Open the tree itself.

        :param a_bzrdir: the dir for the tree.
        :param control_files: the control files for the tree.
        """
        return self._tree_class(a_bzrdir.root_transport.local_abspath('.'),
                           branch=a_bzrdir.open_branch(),
                           _format=self,
                           _bzrdir=a_bzrdir,
                           _control_files=control_files)

    def __get_matchingbzrdir(self):
        return self._get_matchingbzrdir()

    def _get_matchingbzrdir(self):
        """Overrideable method to get a bzrdir for testing."""
        # please test against something that will let us do tree references
        return bzrdir.format_registry.make_bzrdir(
            'dirstate-with-subtree')

    _matchingbzrdir = property(__get_matchingbzrdir)


class WorkingTreeFormat4(DirStateWorkingTreeFormat):
    """The first consolidated dirstate working tree format.

    This format:
        - exists within a metadir controlling .bzr
        - includes an explicit version marker for the workingtree control
          files, separate from the BzrDir format
        - modifies the hash cache format
        - is new in bzr 0.15
        - uses a LockDir to guard access to it.
    """

    upgrade_recommended = False

    _tree_class = WorkingTree4

    def get_format_string(self):
        """See WorkingTreeFormat.get_format_string()."""
        return "Bazaar Working Tree Format 4 (bzr 0.15)\n"

    def get_format_description(self):
        """See WorkingTreeFormat.get_format_description()."""
        return "Working tree format 4"


class WorkingTreeFormat5(DirStateWorkingTreeFormat):
    """WorkingTree format supporting content filtering.
    """

    upgrade_recommended = False

    _tree_class = WorkingTree5

    def get_format_string(self):
        """See WorkingTreeFormat.get_format_string()."""
        return "Bazaar Working Tree Format 5 (bzr 1.11)\n"

    def get_format_description(self):
        """See WorkingTreeFormat.get_format_description()."""
        return "Working tree format 5"

    def supports_content_filtering(self):
        return True


class WorkingTreeFormat6(DirStateWorkingTreeFormat):
    """WorkingTree format supporting views.
    """

    upgrade_recommended = False

    _tree_class = WorkingTree6

    def get_format_string(self):
        """See WorkingTreeFormat.get_format_string()."""
        return "Bazaar Working Tree Format 6 (bzr 1.14)\n"

    def get_format_description(self):
        """See WorkingTreeFormat.get_format_description()."""
        return "Working tree format 6"

    def _init_custom_control_files(self, wt):
        """Subclasses with custom control files should override this method."""
        wt._transport.put_bytes('views', '', mode=wt.bzrdir._get_file_mode())

    def supports_content_filtering(self):
        return True

    def supports_views(self):
        return True


class DirStateRevisionTree(Tree):
    """A revision tree pulling the inventory from a dirstate.
    
    Note that this is one of the historical (ie revision) trees cached in the
    dirstate for easy access, not the workingtree.
    """

    def __init__(self, dirstate, revision_id, repository):
        self._dirstate = dirstate
        self._revision_id = revision_id
        self._repository = repository
        self._inventory = None
        self._locked = 0
        self._dirstate_locked = False
        self._repo_supports_tree_reference = getattr(
            repository._format, "supports_tree_reference",
            False)

    def __repr__(self):
        return "<%s of %s in %s>" % \
            (self.__class__.__name__, self._revision_id, self._dirstate)

    def annotate_iter(self, file_id,
                      default_revision=_mod_revision.CURRENT_REVISION):
        """See Tree.annotate_iter"""
        text_key = (file_id, self.inventory[file_id].revision)
        annotations = self._repository.texts.annotate(text_key)
        return [(key[-1], line) for (key, line) in annotations]

    def _get_ancestors(self, default_revision):
        return set(self._repository.get_ancestry(self._revision_id,
                                                 topo_sorted=False))
    def _comparison_data(self, entry, path):
        """See Tree._comparison_data."""
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

    def get_root_id(self):
        return self.path2id('')

    def id2path(self, file_id):
        "Convert a file-id to a path."
        entry = self._get_entry(file_id=file_id)
        if entry == (None, None):
            raise errors.NoSuchId(tree=self, file_id=file_id)
        path_utf8 = osutils.pathjoin(entry[0][0], entry[0][1])
        return path_utf8.decode('utf8')

    def iter_references(self):
        if not self._repo_supports_tree_reference:
            # When the repo doesn't support references, we will have nothing to
            # return
            return iter([])
        # Otherwise, fall back to the default implementation
        return super(DirStateRevisionTree, self).iter_references()

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
        if path is not None:
            path = path.encode('utf8')
        parent_index = self._get_parent_index()
        return self._dirstate._get_entry(parent_index, fileid_utf8=file_id, path_utf8=path)

    def _generate_inventory(self):
        """Create and set self.inventory from the dirstate object.

        (So this is only called the first time the inventory is requested for
        this tree; it then remains in memory until it's out of date.)

        This is relatively expensive: we have to walk the entire dirstate.
        """
        if not self._locked:
            raise AssertionError(
                'cannot generate inventory of an unlocked '
                'dirstate revision tree')
        # separate call for profiling - makes it clear where the costs are.
        self._dirstate._read_dirblocks_if_needed()
        if self._revision_id not in self._dirstate.get_parent_ids():
            raise AssertionError(
                'parent %s has disappeared from %s' % (
                self._revision_id, self._dirstate.get_parent_ids()))
        parent_index = self._dirstate.get_parent_ids().index(self._revision_id) + 1
        # This is identical now to the WorkingTree _generate_inventory except
        # for the tree index use.
        root_key, current_entry = self._dirstate._get_entry(parent_index, path_utf8='')
        current_id = root_key[2]
        if current_entry[parent_index][0] != 'd':
            raise AssertionError()
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
                    inv_entry.text_size = None
                    inv_entry.symlink_target = utf8_decode(fingerprint)[0]
                elif kind == 'tree-reference':
                    inv_entry.reference_revision = fingerprint or None
                else:
                    raise AssertionError("cannot convert entry %r into an InventoryEntry"
                            % entry)
                # These checks cost us around 40ms on a 55k entry tree
                if file_id in inv_byid:
                    raise AssertionError('file_id %s already in'
                        ' inventory as %s' % (file_id, inv_byid[file_id]))
                if name_unicode in parent_ie.children:
                    raise AssertionError('name %r already in parent'
                        % (name_unicode,))
                inv_byid[file_id] = inv_entry
                parent_ie.children[name_unicode] = inv_entry
        self._inventory = inv

    def get_file_mtime(self, file_id, path=None):
        """Return the modification time for this record.

        We return the timestamp of the last-changed revision.
        """
        # Make sure the file exists
        entry = self._get_entry(file_id, path=path)
        if entry == (None, None): # do we raise?
            return None
        parent_index = self._get_parent_index()
        last_changed_revision = entry[1][parent_index][4]
        return self._repository.get_revision(last_changed_revision).timestamp

    def get_file_sha1(self, file_id, path=None, stat_value=None):
        entry = self._get_entry(file_id=file_id, path=path)
        parent_index = self._get_parent_index()
        parent_details = entry[1][parent_index]
        if parent_details[0] == 'f':
            return parent_details[1]
        return None

    def get_file(self, file_id, path=None):
        return StringIO(self.get_file_text(file_id))

    def get_file_size(self, file_id):
        """See Tree.get_file_size"""
        return self.inventory[file_id].text_size

    def get_file_text(self, file_id, path=None):
        _, content = list(self.iter_files_bytes([(file_id, None)]))[0]
        return ''.join(content)

    def get_reference_revision(self, file_id, path=None):
        return self.inventory[file_id].reference_revision

    def iter_files_bytes(self, desired_files):
        """See Tree.iter_files_bytes.

        This version is implemented on top of Repository.iter_files_bytes"""
        parent_index = self._get_parent_index()
        repo_desired_files = []
        for file_id, identifier in desired_files:
            entry = self._get_entry(file_id)
            if entry == (None, None):
                raise errors.NoSuchId(self, file_id)
            repo_desired_files.append((file_id, entry[1][parent_index][4],
                                       identifier))
        return self._repository.iter_files_bytes(repo_desired_files)

    def get_symlink_target(self, file_id):
        entry = self._get_entry(file_id=file_id)
        parent_index = self._get_parent_index()
        if entry[1][parent_index][0] != 'l':
            return None
        else:
            target = entry[1][parent_index][1]
            target = target.decode('utf8')
            return target

    def get_revision_id(self):
        """Return the revision id for this tree."""
        return self._revision_id

    def _get_inventory(self):
        if self._inventory is not None:
            return self._inventory
        self._must_be_locked()
        self._generate_inventory()
        return self._inventory

    inventory = property(_get_inventory,
                         doc="Inventory of this Tree")

    def get_parent_ids(self):
        """The parents of a tree in the dirstate are not cached."""
        return self._repository.get_revision(self._revision_id).parent_ids

    def has_filename(self, filename):
        return bool(self.path2id(filename))

    def kind(self, file_id):
        entry = self._get_entry(file_id=file_id)[1]
        if entry is None:
            raise errors.NoSuchId(tree=self, file_id=file_id)
        return dirstate.DirState._minikind_to_kind[entry[1][0]]

    def stored_kind(self, file_id):
        """See Tree.stored_kind"""
        return self.kind(file_id)

    def path_content_summary(self, path):
        """See Tree.path_content_summary."""
        id = self.inventory.path2id(path)
        if id is None:
            return ('missing', None, None, None)
        entry = self._inventory[id]
        kind = entry.kind
        if kind == 'file':
            return (kind, entry.text_size, entry.executable, entry.text_sha1)
        elif kind == 'symlink':
            return (kind, None, None, entry.symlink_target)
        else:
            return (kind, None, None, None)

    def is_executable(self, file_id, path=None):
        ie = self.inventory[file_id]
        if ie.kind != "file":
            return None
        return ie.executable

    def list_files(self, include_root=False, from_dir=None, recursive=True):
        # We use a standard implementation, because DirStateRevisionTree is
        # dealing with one of the parents of the current state
        inv = self._get_inventory()
        if from_dir is None:
            from_dir_id = None
        else:
            from_dir_id = inv.path2id(from_dir)
            if from_dir_id is None:
                # Directory not versioned
                return
        entries = inv.iter_entries(from_dir=from_dir_id, recursive=recursive)
        if inv.root is not None and not include_root and from_dir is None:
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

    def _must_be_locked(self):
        if not self._locked:
            raise errors.ObjectNotLocked(self)

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

    @needs_read_lock
    def supports_tree_reference(self):
        return self._repo_supports_tree_reference

    def walkdirs(self, prefix=""):
        # TODO: jam 20070215 This is the lazy way by using the RevisionTree
        # implementation based on an inventory.
        # This should be cleaned up to use the much faster Dirstate code
        # So for now, we just build up the parent inventory, and extract
        # it the same way RevisionTree does.
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
    """Fast path optimiser for changes_from with dirstate trees.

    This is used only when both trees are in the dirstate working file, and
    the source is any parent within the dirstate, and the destination is
    the current working tree of the same dirstate.
    """
    # this could be generalized to allow comparisons between any trees in the
    # dirstate, and possibly between trees stored in different dirstates.

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

    @classmethod
    def make_source_parent_tree_python_dirstate(klass, test_case, source, target):
        result = klass.make_source_parent_tree(source, target)
        result[1]._iter_changes = dirstate.ProcessEntryPython
        return result

    @classmethod
    def make_source_parent_tree_compiled_dirstate(klass, test_case, source,
                                                  target):
        from bzrlib.tests.test__dirstate_helpers import \
            compiled_dirstate_helpers_feature
        test_case.requireFeature(compiled_dirstate_helpers_feature)
        from bzrlib._dirstate_helpers_pyx import ProcessEntryC
        result = klass.make_source_parent_tree(source, target)
        result[1]._iter_changes = ProcessEntryC
        return result

    _matching_from_tree_format = WorkingTreeFormat4()
    _matching_to_tree_format = WorkingTreeFormat4()

    @classmethod
    def _test_mutable_trees_to_test_trees(klass, test_case, source, target):
        # This method shouldn't be called, because we have python and C
        # specific flavours.
        raise NotImplementedError

    def iter_changes(self, include_unchanged=False,
                      specific_files=None, pb=None, extra_trees=[],
                      require_versioned=True, want_unversioned=False):
        """Return the changes from source to target.

        :return: An iterator that yields tuples. See InterTree.iter_changes
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
        :param want_unversioned: Should unversioned files be returned in the
            output. An unversioned file is defined as one with (False, False)
            for the versioned pair.
        """
        # TODO: handle extra trees in the dirstate.
        if (extra_trees or specific_files == []):
            # we can't fast-path these cases (yet)
            return super(InterDirStateTree, self).iter_changes(
                include_unchanged, specific_files, pb, extra_trees,
                require_versioned, want_unversioned=want_unversioned)
        parent_ids = self.target.get_parent_ids()
        if not (self.source._revision_id in parent_ids
                or self.source._revision_id == _mod_revision.NULL_REVISION):
            raise AssertionError(
                "revision {%s} is not stored in {%s}, but %s "
                "can only be used for trees stored in the dirstate"
                % (self.source._revision_id, self.target, self.iter_changes))
        target_index = 0
        if self.source._revision_id == _mod_revision.NULL_REVISION:
            source_index = None
            indices = (target_index,)
        else:
            if not (self.source._revision_id in parent_ids):
                raise AssertionError(
                    "Failure: source._revision_id: %s not in target.parent_ids(%s)" % (
                    self.source._revision_id, parent_ids))
            source_index = 1 + parent_ids.index(self.source._revision_id)
            indices = (source_index, target_index)
        # -- make all specific_files utf8 --
        if specific_files:
            specific_files_utf8 = set()
            for path in specific_files:
                # Note, if there are many specific files, using cache_utf8
                # would be good here.
                specific_files_utf8.add(path.encode('utf8'))
            specific_files = specific_files_utf8
        else:
            specific_files = set([''])
        # -- specific_files is now a utf8 path set --

        # -- get the state object and prepare it.
        state = self.target.current_dirstate()
        state._read_dirblocks_if_needed()
        if require_versioned:
            # -- check all supplied paths are versioned in a search tree. --
            not_versioned = []
            for path in specific_files:
                path_entries = state._entries_for_path(path)
                if not path_entries:
                    # this specified path is not present at all: error
                    not_versioned.append(path)
                    continue
                found_versioned = False
                # for each id at this path
                for entry in path_entries:
                    # for each tree.
                    for index in indices:
                        if entry[1][index][0] != 'a': # absent
                            found_versioned = True
                            # all good: found a versioned cell
                            break
                if not found_versioned:
                    # none of the indexes was not 'absent' at all ids for this
                    # path.
                    not_versioned.append(path)
            if len(not_versioned) > 0:
                raise errors.PathsNotVersionedError(not_versioned)
        # -- remove redundancy in supplied specific_files to prevent over-scanning --
        search_specific_files = osutils.minimum_path_selection(specific_files)

        use_filesystem_for_exec = (sys.platform != 'win32')
        iter_changes = self.target._iter_changes(include_unchanged,
            use_filesystem_for_exec, search_specific_files, state,
            source_index, target_index, want_unversioned, self.target)
        return iter_changes.iter_changes()

    @staticmethod
    def is_compatible(source, target):
        # the target must be a dirstate working tree
        if not isinstance(target, DirStateWorkingTree):
            return False
        # the source must be a revtree or dirstate rev tree.
        if not isinstance(source,
            (revisiontree.RevisionTree, DirStateRevisionTree)):
            return False
        # the source revid must be in the target dirstate
        if not (source._revision_id == _mod_revision.NULL_REVISION or
            source._revision_id in target.get_parent_ids()):
            # TODO: what about ghosts? it may well need to
            # check for them explicitly.
            return False
        return True

InterTree.register_optimiser(InterDirStateTree)


class Converter3to4(object):
    """Perform an in-place upgrade of format 3 to format 4 trees."""

    def __init__(self):
        self.target_format = WorkingTreeFormat4()

    def convert(self, tree):
        # lock the control files not the tree, so that we dont get tree
        # on-unlock behaviours, and so that noone else diddles with the
        # tree during upgrade.
        tree._control_files.lock_write()
        try:
            tree.read_working_inventory()
            self.create_dirstate_data(tree)
            self.update_format(tree)
            self.remove_xml_files(tree)
        finally:
            tree._control_files.unlock()

    def create_dirstate_data(self, tree):
        """Create the dirstate based data for tree."""
        local_path = tree.bzrdir.get_workingtree_transport(None
            ).local_abspath('dirstate')
        state = dirstate.DirState.from_tree(tree, local_path)
        state.save()
        state.unlock()

    def remove_xml_files(self, tree):
        """Remove the oldformat 3 data."""
        transport = tree.bzrdir.get_workingtree_transport(None)
        for path in ['basis-inventory-cache', 'inventory', 'last-revision',
            'pending-merges', 'stat-cache']:
            try:
                transport.delete(path)
            except errors.NoSuchFile:
                # some files are optional - just deal.
                pass

    def update_format(self, tree):
        """Change the format marker."""
        tree._transport.put_bytes('format',
            self.target_format.get_format_string(),
            mode=tree.bzrdir._get_file_mode())


class Converter4to5(object):
    """Perform an in-place upgrade of format 4 to format 5 trees."""

    def __init__(self):
        self.target_format = WorkingTreeFormat5()

    def convert(self, tree):
        # lock the control files not the tree, so that we don't get tree
        # on-unlock behaviours, and so that no-one else diddles with the
        # tree during upgrade.
        tree._control_files.lock_write()
        try:
            self.update_format(tree)
        finally:
            tree._control_files.unlock()

    def update_format(self, tree):
        """Change the format marker."""
        tree._transport.put_bytes('format',
            self.target_format.get_format_string(),
            mode=tree.bzrdir._get_file_mode())


class Converter4or5to6(object):
    """Perform an in-place upgrade of format 4 or 5 to format 6 trees."""

    def __init__(self):
        self.target_format = WorkingTreeFormat6()

    def convert(self, tree):
        # lock the control files not the tree, so that we don't get tree
        # on-unlock behaviours, and so that no-one else diddles with the
        # tree during upgrade.
        tree._control_files.lock_write()
        try:
            self.init_custom_control_files(tree)
            self.update_format(tree)
        finally:
            tree._control_files.unlock()

    def init_custom_control_files(self, tree):
        """Initialize custom control files."""
        tree._transport.put_bytes('views', '',
            mode=tree.bzrdir._get_file_mode())

    def update_format(self, tree):
        """Change the format marker."""
        tree._transport.put_bytes('format',
            self.target_format.get_format_string(),
            mode=tree.bzrdir._get_file_mode())
