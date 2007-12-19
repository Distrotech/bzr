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
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

import os
import errno
from stat import S_ISREG

from bzrlib.lazy_import import lazy_import
lazy_import(globals(), """
from bzrlib import (
    bzrdir,
    delta,
    errors,
    inventory
    )
""")
from bzrlib.errors import (DuplicateKey, MalformedTransform, NoSuchFile,
                           ReusingTransform, NotVersionedError, CantMoveRoot,
                           ExistingLimbo, ImmortalLimbo, NoFinalPath,
                           UnableCreateSymlink)
from bzrlib.inventory import InventoryEntry
from bzrlib.osutils import (file_kind, supports_executable, pathjoin, lexists,
                            delete_any, has_symlinks)
from bzrlib.progress import DummyProgress, ProgressPhase
from bzrlib.symbol_versioning import (
        deprecated_function,
        zero_fifteen,
        zero_ninety,
        )
from bzrlib.trace import mutter, warning
from bzrlib import tree
import bzrlib.ui
import bzrlib.urlutils as urlutils


ROOT_PARENT = "root-parent"


def unique_add(map, key, value):
    if key in map:
        raise DuplicateKey(key=key)
    map[key] = value


class _TransformResults(object):
    def __init__(self, modified_paths, rename_count):
        object.__init__(self)
        self.modified_paths = modified_paths
        self.rename_count = rename_count


class TreeTransform(object):
    """Represent a tree transformation.
    
    This object is designed to support incremental generation of the transform,
    in any order.

    However, it gives optimum performance when parent directories are created
    before their contents.  The transform is then able to put child files
    directly in their parent directory, avoiding later renames.
    
    It is easy to produce malformed transforms, but they are generally
    harmless.  Attempting to apply a malformed transform will cause an
    exception to be raised before any modifications are made to the tree.  

    Many kinds of malformed transforms can be corrected with the 
    resolve_conflicts function.  The remaining ones indicate programming error,
    such as trying to create a file with no path.

    Two sets of file creation methods are supplied.  Convenience methods are:
     * new_file
     * new_directory
     * new_symlink

    These are composed of the low-level methods:
     * create_path
     * create_file or create_directory or create_symlink
     * version_file
     * set_executability

    Transform/Transaction ids
    -------------------------
    trans_ids are temporary ids assigned to all files involved in a transform.
    It's possible, even common, that not all files in the Tree have trans_ids.

    trans_ids are used because filenames and file_ids are not good enough
    identifiers; filenames change, and not all files have file_ids.  File-ids
    are also associated with trans-ids, so that moving a file moves its
    file-id.

    trans_ids are only valid for the TreeTransform that generated them.

    Limbo
    -----
    Limbo is a temporary directory use to hold new versions of files.
    Files are added to limbo by create_file, create_directory, create_symlink,
    and their convenience variants (new_*).  Files may be removed from limbo
    using cancel_creation.  Files are renamed from limbo into their final
    location as part of TreeTransform.apply

    Limbo must be cleaned up, by either calling TreeTransform.apply or
    calling TreeTransform.finalize.

    Files are placed into limbo inside their parent directories, where
    possible.  This reduces subsequent renames, and makes operations involving
    lots of files faster.  This optimization is only possible if the parent
    directory is created *before* creating any of its children, so avoid
    creating children before parents, where possible.

    Pending-deletion
    ----------------
    This temporary directory is used by _FileMover for storing files that are
    about to be deleted.  In case of rollback, the files will be restored.
    FileMover does not delete files until it is sure that a rollback will not
    happen.  
    """
    def __init__(self, tree, pb=DummyProgress()):
        """Note: a tree_write lock is taken on the tree.
        
        Use TreeTransform.finalize() to release the lock (can be omitted if
        TreeTransform.apply() called).
        """
        object.__init__(self)
        self._tree = tree
        self._tree.lock_tree_write()
        try:
            control_files = self._tree._control_files
            self._limbodir = urlutils.local_path_from_url(
                control_files.controlfilename('limbo'))
            try:
                os.mkdir(self._limbodir)
            except OSError, e:
                if e.errno == errno.EEXIST:
                    raise ExistingLimbo(self._limbodir)
            self._deletiondir = urlutils.local_path_from_url(
                control_files.controlfilename('pending-deletion'))
            try:
                os.mkdir(self._deletiondir)
            except OSError, e:
                if e.errno == errno.EEXIST:
                    raise errors.ExistingPendingDeletion(self._deletiondir)

        except: 
            self._tree.unlock()
            raise

        # counter used to generate trans-ids (which are locally unique)
        self._id_number = 0
        # mapping of trans_id -> new basename
        self._new_name = {}
        # mapping of trans_id -> new parent trans_id
        self._new_parent = {}
        # mapping of trans_id with new contents -> new file_kind
        self._new_contents = {}
        # A mapping of transform ids to their limbo filename
        self._limbo_files = {}
        # A mapping of transform ids to a set of the transform ids of children
        # that their limbo directory has
        self._limbo_children = {}
        # Map transform ids to maps of child filename to child transform id
        self._limbo_children_names = {}
        # List of transform ids that need to be renamed from limbo into place
        self._needs_rename = set()
        # Set of trans_ids whose contents will be removed
        self._removed_contents = set()
        # Mapping of trans_id -> new execute-bit value
        self._new_executability = {}
        # Mapping of trans_id -> new tree-reference value
        self._new_reference_revision = {}
        # Mapping of trans_id -> new file_id
        self._new_id = {}
        # Mapping of old file-id -> trans_id
        self._non_present_ids = {}
        # Mapping of new file_id -> trans_id
        self._r_new_id = {}
        # Set of file_ids that will be removed
        self._removed_id = set()
        # Mapping of path in old tree -> trans_id
        self._tree_path_ids = {}
        # Mapping trans_id -> path in old tree
        self._tree_id_paths = {}
        # Cache of realpath results, to speed up canonical_path
        self._realpaths = {}
        # Cache of relpath results, to speed up canonical_path
        self._relpaths = {}
        # The trans_id that will be used as the tree root
        self._new_root = self.trans_id_tree_file_id(tree.get_root_id())
        # Indictor of whether the transform has been applied
        self.__done = False
        # A progress bar
        self._pb = pb
        # A counter of how many files have been renamed
        self.rename_count = 0

    def __get_root(self):
        return self._new_root

    root = property(__get_root)

    def finalize(self):
        """Release the working tree lock, if held, clean up limbo dir.

        This is required if apply has not been invoked, but can be invoked
        even after apply.
        """
        if self._tree is None:
            return
        try:
            entries = [(self._limbo_name(t), t, k) for t, k in
                       self._new_contents.iteritems()]
            entries.sort(reverse=True)
            for path, trans_id, kind in entries:
                if kind == "directory":
                    os.rmdir(path)
                else:
                    os.unlink(path)
            try:
                os.rmdir(self._limbodir)
            except OSError:
                # We don't especially care *why* the dir is immortal.
                raise ImmortalLimbo(self._limbodir)
            try:
                os.rmdir(self._deletiondir)
            except OSError:
                raise errors.ImmortalPendingDeletion(self._deletiondir)
        finally:
            self._tree.unlock()
            self._tree = None

    def _assign_id(self):
        """Produce a new tranform id"""
        new_id = "new-%s" % self._id_number
        self._id_number +=1
        return new_id

    def create_path(self, name, parent):
        """Assign a transaction id to a new path"""
        trans_id = self._assign_id()
        unique_add(self._new_name, trans_id, name)
        unique_add(self._new_parent, trans_id, parent)
        return trans_id

    def adjust_path(self, name, parent, trans_id):
        """Change the path that is assigned to a transaction id."""
        if trans_id == self._new_root:
            raise CantMoveRoot
        previous_parent = self._new_parent.get(trans_id)
        previous_name = self._new_name.get(trans_id)
        self._new_name[trans_id] = name
        self._new_parent[trans_id] = parent
        if (trans_id in self._limbo_files and
            trans_id not in self._needs_rename):
            self._rename_in_limbo([trans_id])
            self._limbo_children[previous_parent].remove(trans_id)
            del self._limbo_children_names[previous_parent][previous_name]

    def _rename_in_limbo(self, trans_ids):
        """Fix limbo names so that the right final path is produced.

        This means we outsmarted ourselves-- we tried to avoid renaming
        these files later by creating them with their final names in their
        final parents.  But now the previous name or parent is no longer
        suitable, so we have to rename them.

        Even for trans_ids that have no new contents, we must remove their
        entries from _limbo_files, because they are now stale.
        """
        for trans_id in trans_ids:
            old_path = self._limbo_files.pop(trans_id)
            if trans_id not in self._new_contents:
                continue
            new_path = self._limbo_name(trans_id)
            os.rename(old_path, new_path)

    def adjust_root_path(self, name, parent):
        """Emulate moving the root by moving all children, instead.
        
        We do this by undoing the association of root's transaction id with the
        current tree.  This allows us to create a new directory with that
        transaction id.  We unversion the root directory and version the 
        physically new directory, and hope someone versions the tree root
        later.
        """
        old_root = self._new_root
        old_root_file_id = self.final_file_id(old_root)
        # force moving all children of root
        for child_id in self.iter_tree_children(old_root):
            if child_id != parent:
                self.adjust_path(self.final_name(child_id), 
                                 self.final_parent(child_id), child_id)
            file_id = self.final_file_id(child_id)
            if file_id is not None:
                self.unversion_file(child_id)
            self.version_file(file_id, child_id)
        
        # the physical root needs a new transaction id
        self._tree_path_ids.pop("")
        self._tree_id_paths.pop(old_root)
        self._new_root = self.trans_id_tree_file_id(self._tree.get_root_id())
        if parent == old_root:
            parent = self._new_root
        self.adjust_path(name, parent, old_root)
        self.create_directory(old_root)
        self.version_file(old_root_file_id, old_root)
        self.unversion_file(self._new_root)

    def trans_id_tree_file_id(self, inventory_id):
        """Determine the transaction id of a working tree file.
        
        This reflects only files that already exist, not ones that will be
        added by transactions.
        """
        path = self._tree.inventory.id2path(inventory_id)
        return self.trans_id_tree_path(path)

    def trans_id_file_id(self, file_id):
        """Determine or set the transaction id associated with a file ID.
        A new id is only created for file_ids that were never present.  If
        a transaction has been unversioned, it is deliberately still returned.
        (this will likely lead to an unversioned parent conflict.)
        """
        if file_id in self._r_new_id and self._r_new_id[file_id] is not None:
            return self._r_new_id[file_id]
        elif file_id in self._tree.inventory:
            return self.trans_id_tree_file_id(file_id)
        elif file_id in self._non_present_ids:
            return self._non_present_ids[file_id]
        else:
            trans_id = self._assign_id()
            self._non_present_ids[file_id] = trans_id
            return trans_id

    def canonical_path(self, path):
        """Get the canonical tree-relative path"""
        # don't follow final symlinks
        abs = self._tree.abspath(path)
        if abs in self._relpaths:
            return self._relpaths[abs]
        dirname, basename = os.path.split(abs)
        if dirname not in self._realpaths:
            self._realpaths[dirname] = os.path.realpath(dirname)
        dirname = self._realpaths[dirname]
        abs = pathjoin(dirname, basename)
        if dirname in self._relpaths:
            relpath = pathjoin(self._relpaths[dirname], basename)
            relpath = relpath.rstrip('/\\')
        else:
            relpath = self._tree.relpath(abs)
        self._relpaths[abs] = relpath
        return relpath

    def trans_id_tree_path(self, path):
        """Determine (and maybe set) the transaction ID for a tree path."""
        path = self.canonical_path(path)
        if path not in self._tree_path_ids:
            self._tree_path_ids[path] = self._assign_id()
            self._tree_id_paths[self._tree_path_ids[path]] = path
        return self._tree_path_ids[path]

    def get_tree_parent(self, trans_id):
        """Determine id of the parent in the tree."""
        path = self._tree_id_paths[trans_id]
        if path == "":
            return ROOT_PARENT
        return self.trans_id_tree_path(os.path.dirname(path))

    def create_file(self, contents, trans_id, mode_id=None):
        """Schedule creation of a new file.

        See also new_file.
        
        Contents is an iterator of strings, all of which will be written
        to the target destination.

        New file takes the permissions of any existing file with that id,
        unless mode_id is specified.
        """
        name = self._limbo_name(trans_id)
        f = open(name, 'wb')
        try:
            try:
                unique_add(self._new_contents, trans_id, 'file')
            except:
                # Clean up the file, it never got registered so
                # TreeTransform.finalize() won't clean it up.
                f.close()
                os.unlink(name)
                raise

            f.writelines(contents)
        finally:
            f.close()
        self._set_mode(trans_id, mode_id, S_ISREG)

    def _set_mode(self, trans_id, mode_id, typefunc):
        """Set the mode of new file contents.
        The mode_id is the existing file to get the mode from (often the same
        as trans_id).  The operation is only performed if there's a mode match
        according to typefunc.
        """
        if mode_id is None:
            mode_id = trans_id
        try:
            old_path = self._tree_id_paths[mode_id]
        except KeyError:
            return
        try:
            mode = os.stat(self._tree.abspath(old_path)).st_mode
        except OSError, e:
            if e.errno == errno.ENOENT:
                return
            else:
                raise
        if typefunc(mode):
            os.chmod(self._limbo_name(trans_id), mode)

    def create_directory(self, trans_id):
        """Schedule creation of a new directory.
        
        See also new_directory.
        """
        os.mkdir(self._limbo_name(trans_id))
        unique_add(self._new_contents, trans_id, 'directory')

    def create_symlink(self, target, trans_id):
        """Schedule creation of a new symbolic link.

        target is a bytestring.
        See also new_symlink.
        """
        if has_symlinks():
            os.symlink(target, self._limbo_name(trans_id))
            unique_add(self._new_contents, trans_id, 'symlink')
        else:
            try:
                path = FinalPaths(self).get_path(trans_id)
            except KeyError:
                path = None
            raise UnableCreateSymlink(path=path)

    def cancel_creation(self, trans_id):
        """Cancel the creation of new file contents."""
        del self._new_contents[trans_id]
        children = self._limbo_children.get(trans_id)
        # if this is a limbo directory with children, move them before removing
        # the directory
        if children is not None:
            self._rename_in_limbo(children)
            del self._limbo_children[trans_id]
            del self._limbo_children_names[trans_id]
        delete_any(self._limbo_name(trans_id))

    def delete_contents(self, trans_id):
        """Schedule the contents of a path entry for deletion"""
        self.tree_kind(trans_id)
        self._removed_contents.add(trans_id)

    def cancel_deletion(self, trans_id):
        """Cancel a scheduled deletion"""
        self._removed_contents.remove(trans_id)

    def unversion_file(self, trans_id):
        """Schedule a path entry to become unversioned"""
        self._removed_id.add(trans_id)

    def delete_versioned(self, trans_id):
        """Delete and unversion a versioned file"""
        self.delete_contents(trans_id)
        self.unversion_file(trans_id)

    def set_executability(self, executability, trans_id):
        """Schedule setting of the 'execute' bit
        To unschedule, set to None
        """
        if executability is None:
            del self._new_executability[trans_id]
        else:
            unique_add(self._new_executability, trans_id, executability)

    def set_tree_reference(self, revision_id, trans_id):
        """Set the reference associated with a directory"""
        unique_add(self._new_reference_revision, trans_id, revision_id)

    def version_file(self, file_id, trans_id):
        """Schedule a file to become versioned."""
        assert file_id is not None
        unique_add(self._new_id, trans_id, file_id)
        unique_add(self._r_new_id, file_id, trans_id)

    def cancel_versioning(self, trans_id):
        """Undo a previous versioning of a file"""
        file_id = self._new_id[trans_id]
        del self._new_id[trans_id]
        del self._r_new_id[file_id]

    def new_paths(self):
        """Determine the paths of all new and changed files"""
        new_ids = set()
        fp = FinalPaths(self)
        for id_set in (self._new_name, self._new_parent, self._new_contents,
                       self._new_id, self._new_executability):
            new_ids.update(id_set)
        new_paths = [(fp.get_path(t), t) for t in new_ids]
        new_paths.sort()
        return new_paths

    def tree_kind(self, trans_id):
        """Determine the file kind in the working tree.

        Raises NoSuchFile if the file does not exist
        """
        path = self._tree_id_paths.get(trans_id)
        if path is None:
            raise NoSuchFile(None)
        try:
            return file_kind(self._tree.abspath(path))
        except OSError, e:
            if e.errno != errno.ENOENT:
                raise
            else:
                raise NoSuchFile(path)

    def final_kind(self, trans_id):
        """Determine the final file kind, after any changes applied.
        
        Raises NoSuchFile if the file does not exist/has no contents.
        (It is conceivable that a path would be created without the
        corresponding contents insertion command)
        """
        if trans_id in self._new_contents:
            return self._new_contents[trans_id]
        elif trans_id in self._removed_contents:
            raise NoSuchFile(None)
        else:
            return self.tree_kind(trans_id)

    def tree_file_id(self, trans_id):
        """Determine the file id associated with the trans_id in the tree"""
        try:
            path = self._tree_id_paths[trans_id]
        except KeyError:
            # the file is a new, unversioned file, or invalid trans_id
            return None
        # the file is old; the old id is still valid
        if self._new_root == trans_id:
            return self._tree.get_root_id()
        return self._tree.inventory.path2id(path)

    def final_file_id(self, trans_id):
        """Determine the file id after any changes are applied, or None.
        
        None indicates that the file will not be versioned after changes are
        applied.
        """
        try:
            # there is a new id for this file
            assert self._new_id[trans_id] is not None
            return self._new_id[trans_id]
        except KeyError:
            if trans_id in self._removed_id:
                return None
        return self.tree_file_id(trans_id)

    def inactive_file_id(self, trans_id):
        """Return the inactive file_id associated with a transaction id.
        That is, the one in the tree or in non_present_ids.
        The file_id may actually be active, too.
        """
        file_id = self.tree_file_id(trans_id)
        if file_id is not None:
            return file_id
        for key, value in self._non_present_ids.iteritems():
            if value == trans_id:
                return key

    def final_parent(self, trans_id):
        """Determine the parent file_id, after any changes are applied.

        ROOT_PARENT is returned for the tree root.
        """
        try:
            return self._new_parent[trans_id]
        except KeyError:
            return self.get_tree_parent(trans_id)

    def final_name(self, trans_id):
        """Determine the final filename, after all changes are applied."""
        try:
            return self._new_name[trans_id]
        except KeyError:
            try:
                return os.path.basename(self._tree_id_paths[trans_id])
            except KeyError:
                raise NoFinalPath(trans_id, self)

    def by_parent(self):
        """Return a map of parent: children for known parents.
        
        Only new paths and parents of tree files with assigned ids are used.
        """
        by_parent = {}
        items = list(self._new_parent.iteritems())
        items.extend((t, self.final_parent(t)) for t in 
                      self._tree_id_paths.keys())
        for trans_id, parent_id in items:
            if parent_id not in by_parent:
                by_parent[parent_id] = set()
            by_parent[parent_id].add(trans_id)
        return by_parent

    def path_changed(self, trans_id):
        """Return True if a trans_id's path has changed."""
        return (trans_id in self._new_name) or (trans_id in self._new_parent)

    def new_contents(self, trans_id):
        return (trans_id in self._new_contents)

    def find_conflicts(self):
        """Find any violations of inventory or filesystem invariants"""
        if self.__done is True:
            raise ReusingTransform()
        conflicts = []
        # ensure all children of all existent parents are known
        # all children of non-existent parents are known, by definition.
        self._add_tree_children()
        by_parent = self.by_parent()
        conflicts.extend(self._unversioned_parents(by_parent))
        conflicts.extend(self._parent_loops())
        conflicts.extend(self._duplicate_entries(by_parent))
        conflicts.extend(self._duplicate_ids())
        conflicts.extend(self._parent_type_conflicts(by_parent))
        conflicts.extend(self._improper_versioning())
        conflicts.extend(self._executability_conflicts())
        conflicts.extend(self._overwrite_conflicts())
        return conflicts

    def _add_tree_children(self):
        """Add all the children of all active parents to the known paths.

        Active parents are those which gain children, and those which are
        removed.  This is a necessary first step in detecting conflicts.
        """
        parents = self.by_parent().keys()
        parents.extend([t for t in self._removed_contents if 
                        self.tree_kind(t) == 'directory'])
        for trans_id in self._removed_id:
            file_id = self.tree_file_id(trans_id)
            if self._tree.inventory[file_id].kind == 'directory':
                parents.append(trans_id)

        for parent_id in parents:
            # ensure that all children are registered with the transaction
            list(self.iter_tree_children(parent_id))

    def iter_tree_children(self, parent_id):
        """Iterate through the entry's tree children, if any"""
        try:
            path = self._tree_id_paths[parent_id]
        except KeyError:
            return
        try:
            children = os.listdir(self._tree.abspath(path))
        except OSError, e:
            if e.errno != errno.ENOENT and e.errno != errno.ESRCH:
                raise
            return
            
        for child in children:
            childpath = joinpath(path, child)
            if self._tree.is_control_filename(childpath):
                continue
            yield self.trans_id_tree_path(childpath)

    def has_named_child(self, by_parent, parent_id, name):
        try:
            children = by_parent[parent_id]
        except KeyError:
            children = []
        for child in children:
            if self.final_name(child) == name:
                return True
        try:
            path = self._tree_id_paths[parent_id]
        except KeyError:
            return False
        childpath = joinpath(path, name)
        child_id = self._tree_path_ids.get(childpath)
        if child_id is None:
            return lexists(self._tree.abspath(childpath))
        else:
            if self.final_parent(child_id) != parent_id:
                return False
            if child_id in self._removed_contents:
                # XXX What about dangling file-ids?
                return False
            else:
                return True

    def _parent_loops(self):
        """No entry should be its own ancestor"""
        conflicts = []
        for trans_id in self._new_parent:
            seen = set()
            parent_id = trans_id
            while parent_id is not ROOT_PARENT:
                seen.add(parent_id)
                try:
                    parent_id = self.final_parent(parent_id)
                except KeyError:
                    break
                if parent_id == trans_id:
                    conflicts.append(('parent loop', trans_id))
                if parent_id in seen:
                    break
        return conflicts

    def _unversioned_parents(self, by_parent):
        """If parent directories are versioned, children must be versioned."""
        conflicts = []
        for parent_id, children in by_parent.iteritems():
            if parent_id is ROOT_PARENT:
                continue
            if self.final_file_id(parent_id) is not None:
                continue
            for child_id in children:
                if self.final_file_id(child_id) is not None:
                    conflicts.append(('unversioned parent', parent_id))
                    break;
        return conflicts

    def _improper_versioning(self):
        """Cannot version a file with no contents, or a bad type.
        
        However, existing entries with no contents are okay.
        """
        conflicts = []
        for trans_id in self._new_id.iterkeys():
            try:
                kind = self.final_kind(trans_id)
            except NoSuchFile:
                conflicts.append(('versioning no contents', trans_id))
                continue
            if not InventoryEntry.versionable_kind(kind):
                conflicts.append(('versioning bad kind', trans_id, kind))
        return conflicts

    def _executability_conflicts(self):
        """Check for bad executability changes.
        
        Only versioned files may have their executability set, because
        1. only versioned entries can have executability under windows
        2. only files can be executable.  (The execute bit on a directory
           does not indicate searchability)
        """
        conflicts = []
        for trans_id in self._new_executability:
            if self.final_file_id(trans_id) is None:
                conflicts.append(('unversioned executability', trans_id))
            else:
                try:
                    non_file = self.final_kind(trans_id) != "file"
                except NoSuchFile:
                    non_file = True
                if non_file is True:
                    conflicts.append(('non-file executability', trans_id))
        return conflicts

    def _overwrite_conflicts(self):
        """Check for overwrites (not permitted on Win32)"""
        conflicts = []
        for trans_id in self._new_contents:
            try:
                self.tree_kind(trans_id)
            except NoSuchFile:
                continue
            if trans_id not in self._removed_contents:
                conflicts.append(('overwrite', trans_id,
                                 self.final_name(trans_id)))
        return conflicts

    def _duplicate_entries(self, by_parent):
        """No directory may have two entries with the same name."""
        conflicts = []
        if (self._new_name, self._new_parent) == ({}, {}):
            return conflicts
        for children in by_parent.itervalues():
            name_ids = [(self.final_name(t), t) for t in children]
            if not self._tree.case_sensitive:
                name_ids = [(n.lower(), t) for n, t in name_ids]
            name_ids.sort()
            last_name = None
            last_trans_id = None
            for name, trans_id in name_ids:
                try:
                    kind = self.final_kind(trans_id)
                except NoSuchFile:
                    kind = None
                file_id = self.final_file_id(trans_id)
                if kind is None and file_id is None:
                    continue
                if name == last_name:
                    conflicts.append(('duplicate', last_trans_id, trans_id,
                    name))
                last_name = name
                last_trans_id = trans_id
        return conflicts

    def _duplicate_ids(self):
        """Each inventory id may only be used once"""
        conflicts = []
        removed_tree_ids = set((self.tree_file_id(trans_id) for trans_id in
                                self._removed_id))
        active_tree_ids = set((f for f in self._tree.inventory if
                               f not in removed_tree_ids))
        for trans_id, file_id in self._new_id.iteritems():
            if file_id in active_tree_ids:
                old_trans_id = self.trans_id_tree_file_id(file_id)
                conflicts.append(('duplicate id', old_trans_id, trans_id))
        return conflicts

    def _parent_type_conflicts(self, by_parent):
        """parents must have directory 'contents'."""
        conflicts = []
        for parent_id, children in by_parent.iteritems():
            if parent_id is ROOT_PARENT:
                continue
            if not self._any_contents(children):
                continue
            for child in children:
                try:
                    self.final_kind(child)
                except NoSuchFile:
                    continue
            try:
                kind = self.final_kind(parent_id)
            except NoSuchFile:
                kind = None
            if kind is None:
                conflicts.append(('missing parent', parent_id))
            elif kind != "directory":
                conflicts.append(('non-directory parent', parent_id))
        return conflicts

    def _any_contents(self, trans_ids):
        """Return true if any of the trans_ids, will have contents."""
        for trans_id in trans_ids:
            try:
                kind = self.final_kind(trans_id)
            except NoSuchFile:
                continue
            return True
        return False
            
    def apply(self, no_conflicts=False, _mover=None):
        """Apply all changes to the inventory and filesystem.
        
        If filesystem or inventory conflicts are present, MalformedTransform
        will be thrown.

        If apply succeeds, finalize is not necessary.

        :param no_conflicts: if True, the caller guarantees there are no
            conflicts, so no check is made.
        :param _mover: Supply an alternate FileMover, for testing
        """
        if not no_conflicts:
            conflicts = self.find_conflicts()
            if len(conflicts) != 0:
                raise MalformedTransform(conflicts=conflicts)
        inv = self._tree.inventory
        inventory_delta = []
        child_pb = bzrlib.ui.ui_factory.nested_progress_bar()
        try:
            if _mover is None:
                mover = _FileMover()
            else:
                mover = _mover
            try:
                child_pb.update('Apply phase', 0, 2)
                self._apply_removals(inv, inventory_delta, mover)
                child_pb.update('Apply phase', 1, 2)
                modified_paths = self._apply_insertions(inv, inventory_delta,
                                                        mover)
            except:
                mover.rollback()
                raise
            else:
                mover.apply_deletions()
        finally:
            child_pb.finished()
        self._tree.apply_inventory_delta(inventory_delta)
        self.__done = True
        self.finalize()
        return _TransformResults(modified_paths, self.rename_count)

    def _limbo_name(self, trans_id):
        """Generate the limbo name of a file"""
        limbo_name = self._limbo_files.get(trans_id)
        if limbo_name is not None:
            return limbo_name
        parent = self._new_parent.get(trans_id)
        # if the parent directory is already in limbo (e.g. when building a
        # tree), choose a limbo name inside the parent, to reduce further
        # renames.
        use_direct_path = False
        if self._new_contents.get(parent) == 'directory':
            filename = self._new_name.get(trans_id)
            if filename is not None:
                if parent not in self._limbo_children:
                    self._limbo_children[parent] = set()
                    self._limbo_children_names[parent] = {}
                    use_direct_path = True
                # the direct path can only be used if no other file has
                # already taken this pathname, i.e. if the name is unused, or
                # if it is already associated with this trans_id.
                elif self._tree.case_sensitive:
                    if (self._limbo_children_names[parent].get(filename)
                        in (trans_id, None)):
                        use_direct_path = True
                else:
                    for l_filename, l_trans_id in\
                        self._limbo_children_names[parent].iteritems():
                        if l_trans_id == trans_id:
                            continue
                        if l_filename.lower() == filename.lower():
                            break
                    else:
                        use_direct_path = True

        if use_direct_path:
            limbo_name = pathjoin(self._limbo_files[parent], filename)
            self._limbo_children[parent].add(trans_id)
            self._limbo_children_names[parent][filename] = trans_id
        else:
            limbo_name = pathjoin(self._limbodir, trans_id)
            self._needs_rename.add(trans_id)
        self._limbo_files[trans_id] = limbo_name
        return limbo_name

    def _apply_removals(self, inv, inventory_delta, mover):
        """Perform tree operations that remove directory/inventory names.
        
        That is, delete files that are to be deleted, and put any files that
        need renaming into limbo.  This must be done in strict child-to-parent
        order.
        """
        tree_paths = list(self._tree_path_ids.iteritems())
        tree_paths.sort(reverse=True)
        child_pb = bzrlib.ui.ui_factory.nested_progress_bar()
        try:
            for num, data in enumerate(tree_paths):
                path, trans_id = data
                child_pb.update('removing file', num, len(tree_paths))
                full_path = self._tree.abspath(path)
                if trans_id in self._removed_contents:
                    mover.pre_delete(full_path, os.path.join(self._deletiondir,
                                     trans_id))
                elif trans_id in self._new_name or trans_id in \
                    self._new_parent:
                    try:
                        mover.rename(full_path, self._limbo_name(trans_id))
                    except OSError, e:
                        if e.errno != errno.ENOENT:
                            raise
                    else:
                        self.rename_count += 1
                if trans_id in self._removed_id:
                    if trans_id == self._new_root:
                        file_id = self._tree.get_root_id()
                    else:
                        file_id = self.tree_file_id(trans_id)
                    assert file_id is not None
                    inventory_delta.append((path, None, file_id, None))
        finally:
            child_pb.finished()

    def _apply_insertions(self, inv, inventory_delta, mover):
        """Perform tree operations that insert directory/inventory names.
        
        That is, create any files that need to be created, and restore from
        limbo any files that needed renaming.  This must be done in strict
        parent-to-child order.
        """
        new_paths = self.new_paths()
        modified_paths = []
        child_pb = bzrlib.ui.ui_factory.nested_progress_bar()
        completed_new = []
        try:
            for num, (path, trans_id) in enumerate(new_paths):
                new_entry = None
                child_pb.update('adding file', num, len(new_paths))
                try:
                    kind = self._new_contents[trans_id]
                except KeyError:
                    kind = contents = None
                if trans_id in self._new_contents or \
                    self.path_changed(trans_id):
                    full_path = self._tree.abspath(path)
                    if trans_id in self._needs_rename:
                        try:
                            mover.rename(self._limbo_name(trans_id), full_path)
                        except OSError, e:
                            # We may be renaming a dangling inventory id
                            if e.errno != errno.ENOENT:
                                raise
                        else:
                            self.rename_count += 1
                    if trans_id in self._new_contents:
                        modified_paths.append(full_path)
                        completed_new.append(trans_id)

                if trans_id in self._new_id:
                    if kind is None:
                        kind = file_kind(self._tree.abspath(path))
                    if trans_id in self._new_reference_revision:
                        new_entry = inventory.TreeReference(
                            self._new_id[trans_id],
                            self._new_name[trans_id], 
                            self.final_file_id(self._new_parent[trans_id]),
                            None, self._new_reference_revision[trans_id])
                    else:
                        new_entry = inventory.make_entry(kind,
                            self.final_name(trans_id),
                            self.final_file_id(self.final_parent(trans_id)),
                            self._new_id[trans_id])
                else:
                    if trans_id in self._new_name or trans_id in\
                        self._new_parent or\
                        trans_id in self._new_executability:
                        file_id = self.final_file_id(trans_id)
                        if file_id is not None:
                            entry = inv[file_id]
                            new_entry = entry.copy()

                    if trans_id in self._new_name or trans_id in\
                        self._new_parent:
                            if new_entry is not None:
                                new_entry.name = self.final_name(trans_id)
                                parent = self.final_parent(trans_id)
                                parent_id = self.final_file_id(parent)
                                new_entry.parent_id = parent_id

                if trans_id in self._new_executability:
                    self._set_executability(path, new_entry, trans_id)
                if new_entry is not None:
                    if new_entry.file_id in inv:
                        old_path = inv.id2path(new_entry.file_id)
                    else:
                        old_path = None
                    inventory_delta.append((old_path, path,
                                            new_entry.file_id,
                                            new_entry))
        finally:
            child_pb.finished()
        for trans_id in completed_new:
            del self._new_contents[trans_id]
        return modified_paths

    def _set_executability(self, path, entry, trans_id):
        """Set the executability of versioned files """
        new_executability = self._new_executability[trans_id]
        entry.executable = new_executability
        if supports_executable():
            abspath = self._tree.abspath(path)
            current_mode = os.stat(abspath).st_mode
            if new_executability:
                umask = os.umask(0)
                os.umask(umask)
                to_mode = current_mode | (0100 & ~umask)
                # Enable x-bit for others only if they can read it.
                if current_mode & 0004:
                    to_mode |= 0001 & ~umask
                if current_mode & 0040:
                    to_mode |= 0010 & ~umask
            else:
                to_mode = current_mode & ~0111
            os.chmod(abspath, to_mode)

    def _new_entry(self, name, parent_id, file_id):
        """Helper function to create a new filesystem entry."""
        trans_id = self.create_path(name, parent_id)
        if file_id is not None:
            self.version_file(file_id, trans_id)
        return trans_id

    def new_file(self, name, parent_id, contents, file_id=None, 
                 executable=None):
        """Convenience method to create files.
        
        name is the name of the file to create.
        parent_id is the transaction id of the parent directory of the file.
        contents is an iterator of bytestrings, which will be used to produce
        the file.
        :param file_id: The inventory ID of the file, if it is to be versioned.
        :param executable: Only valid when a file_id has been supplied.
        """
        trans_id = self._new_entry(name, parent_id, file_id)
        # TODO: rather than scheduling a set_executable call,
        # have create_file create the file with the right mode.
        self.create_file(contents, trans_id)
        if executable is not None:
            self.set_executability(executable, trans_id)
        return trans_id

    def new_directory(self, name, parent_id, file_id=None):
        """Convenience method to create directories.

        name is the name of the directory to create.
        parent_id is the transaction id of the parent directory of the
        directory.
        file_id is the inventory ID of the directory, if it is to be versioned.
        """
        trans_id = self._new_entry(name, parent_id, file_id)
        self.create_directory(trans_id)
        return trans_id 

    def new_symlink(self, name, parent_id, target, file_id=None):
        """Convenience method to create symbolic link.
        
        name is the name of the symlink to create.
        parent_id is the transaction id of the parent directory of the symlink.
        target is a bytestring of the target of the symlink.
        file_id is the inventory ID of the file, if it is to be versioned.
        """
        trans_id = self._new_entry(name, parent_id, file_id)
        self.create_symlink(target, trans_id)
        return trans_id

    def _affected_ids(self):
        """Return the set of transform ids affected by the transform"""
        trans_ids = set(self._removed_id)
        trans_ids.update(self._new_id.keys())
        trans_ids.update(self._removed_contents)
        trans_ids.update(self._new_contents.keys())
        trans_ids.update(self._new_executability.keys())
        trans_ids.update(self._new_name.keys())
        trans_ids.update(self._new_parent.keys())
        return trans_ids

    def _get_file_id_maps(self):
        """Return mapping of file_ids to trans_ids in the to and from states"""
        trans_ids = self._affected_ids()
        from_trans_ids = {}
        to_trans_ids = {}
        # Build up two dicts: trans_ids associated with file ids in the
        # FROM state, vs the TO state.
        for trans_id in trans_ids:
            from_file_id = self.tree_file_id(trans_id)
            if from_file_id is not None:
                from_trans_ids[from_file_id] = trans_id
            to_file_id = self.final_file_id(trans_id)
            if to_file_id is not None:
                to_trans_ids[to_file_id] = trans_id
        return from_trans_ids, to_trans_ids

    def _from_file_data(self, from_trans_id, from_versioned, file_id):
        """Get data about a file in the from (tree) state

        Return a (name, parent, kind, executable) tuple
        """
        from_path = self._tree_id_paths.get(from_trans_id)
        if from_versioned:
            # get data from working tree if versioned
            from_entry = self._tree.inventory[file_id]
            from_name = from_entry.name
            from_parent = from_entry.parent_id
        else:
            from_entry = None
            if from_path is None:
                # File does not exist in FROM state
                from_name = None
                from_parent = None
            else:
                # File exists, but is not versioned.  Have to use path-
                # splitting stuff
                from_name = os.path.basename(from_path)
                tree_parent = self.get_tree_parent(from_trans_id)
                from_parent = self.tree_file_id(tree_parent)
        if from_path is not None:
            from_kind, from_executable, from_stats = \
                self._tree._comparison_data(from_entry, from_path)
        else:
            from_kind = None
            from_executable = False
        return from_name, from_parent, from_kind, from_executable

    def _to_file_data(self, to_trans_id, from_trans_id, from_executable):
        """Get data about a file in the to (target) state

        Return a (name, parent, kind, executable) tuple
        """
        to_name = self.final_name(to_trans_id)
        try:
            to_kind = self.final_kind(to_trans_id)
        except NoSuchFile:
            to_kind = None
        to_parent = self.final_file_id(self.final_parent(to_trans_id))
        if to_trans_id in self._new_executability:
            to_executable = self._new_executability[to_trans_id]
        elif to_trans_id == from_trans_id:
            to_executable = from_executable
        else:
            to_executable = False
        return to_name, to_parent, to_kind, to_executable

    def _iter_changes(self):
        """Produce output in the same format as Tree._iter_changes.

        Will produce nonsensical results if invoked while inventory/filesystem
        conflicts (as reported by TreeTransform.find_conflicts()) are present.

        This reads the Transform, but only reproduces changes involving a
        file_id.  Files that are not versioned in either of the FROM or TO
        states are not reflected.
        """
        final_paths = FinalPaths(self)
        from_trans_ids, to_trans_ids = self._get_file_id_maps()
        results = []
        # Now iterate through all active file_ids
        for file_id in set(from_trans_ids.keys() + to_trans_ids.keys()):
            modified = False
            from_trans_id = from_trans_ids.get(file_id)
            # find file ids, and determine versioning state
            if from_trans_id is None:
                from_versioned = False
                from_trans_id = to_trans_ids[file_id]
            else:
                from_versioned = True
            to_trans_id = to_trans_ids.get(file_id)
            if to_trans_id is None:
                to_versioned = False
                to_trans_id = from_trans_id
            else:
                to_versioned = True

            from_name, from_parent, from_kind, from_executable = \
                self._from_file_data(from_trans_id, from_versioned, file_id)

            to_name, to_parent, to_kind, to_executable = \
                self._to_file_data(to_trans_id, from_trans_id, from_executable)

            if not from_versioned:
                from_path = None
            else:
                from_path = self._tree_id_paths.get(from_trans_id)
            if not to_versioned:
                to_path = None
            else:
                to_path = final_paths.get_path(to_trans_id)
            if from_kind != to_kind:
                modified = True
            elif to_kind in ('file', 'symlink') and (
                to_trans_id != from_trans_id or
                to_trans_id in self._new_contents):
                modified = True
            if (not modified and from_versioned == to_versioned and
                from_parent==to_parent and from_name == to_name and
                from_executable == to_executable):
                continue
            results.append((file_id, (from_path, to_path), modified,
                   (from_versioned, to_versioned),
                   (from_parent, to_parent),
                   (from_name, to_name),
                   (from_kind, to_kind),
                   (from_executable, to_executable)))
        return iter(sorted(results, key=lambda x:x[1]))


def joinpath(parent, child):
    """Join tree-relative paths, handling the tree root specially"""
    if parent is None or parent == "":
        return child
    else:
        return pathjoin(parent, child)


class FinalPaths(object):
    """Make path calculation cheap by memoizing paths.

    The underlying tree must not be manipulated between calls, or else
    the results will likely be incorrect.
    """
    def __init__(self, transform):
        object.__init__(self)
        self._known_paths = {}
        self.transform = transform

    def _determine_path(self, trans_id):
        if trans_id == self.transform.root:
            return ""
        name = self.transform.final_name(trans_id)
        parent_id = self.transform.final_parent(trans_id)
        if parent_id == self.transform.root:
            return name
        else:
            return pathjoin(self.get_path(parent_id), name)

    def get_path(self, trans_id):
        """Find the final path associated with a trans_id"""
        if trans_id not in self._known_paths:
            self._known_paths[trans_id] = self._determine_path(trans_id)
        return self._known_paths[trans_id]


def topology_sorted_ids(tree):
    """Determine the topological order of the ids in a tree"""
    file_ids = list(tree)
    file_ids.sort(key=tree.id2path)
    return file_ids


def build_tree(tree, wt, accelerator_tree=None):
    """Create working tree for a branch, using a TreeTransform.
    
    This function should be used on empty trees, having a tree root at most.
    (see merge and revert functionality for working with existing trees)

    Existing files are handled like so:
    
    - Existing bzrdirs take precedence over creating new items.  They are
      created as '%s.diverted' % name.
    - Otherwise, if the content on disk matches the content we are building,
      it is silently replaced.
    - Otherwise, conflict resolution will move the old file to 'oldname.moved'.
    """
    wt.lock_tree_write()
    try:
        tree.lock_read()
        try:
            if accelerator_tree is not None:
                accelerator_tree.lock_read()
            try:
                return _build_tree(tree, wt, accelerator_tree)
            finally:
                if accelerator_tree is not None:
                    accelerator_tree.unlock()
        finally:
            tree.unlock()
    finally:
        wt.unlock()


def _build_tree(tree, wt, accelerator_tree):
    """See build_tree."""
    if len(wt.inventory) > 1:  # more than just a root
        raise errors.WorkingTreeAlreadyPopulated(base=wt.basedir)
    file_trans_id = {}
    top_pb = bzrlib.ui.ui_factory.nested_progress_bar()
    pp = ProgressPhase("Build phase", 2, top_pb)
    if tree.inventory.root is not None:
        # This is kind of a hack: we should be altering the root
        # as part of the regular tree shape diff logic.
        # The conditional test here is to avoid doing an
        # expensive operation (flush) every time the root id
        # is set within the tree, nor setting the root and thus
        # marking the tree as dirty, because we use two different
        # idioms here: tree interfaces and inventory interfaces.
        if wt.get_root_id() != tree.get_root_id():
            wt.set_root_id(tree.get_root_id())
            wt.flush()
    tt = TreeTransform(wt)
    divert = set()
    try:
        pp.next_phase()
        file_trans_id[wt.get_root_id()] = \
            tt.trans_id_tree_file_id(wt.get_root_id())
        pb = bzrlib.ui.ui_factory.nested_progress_bar()
        try:
            deferred_contents = []
            for num, (tree_path, entry) in \
                enumerate(tree.inventory.iter_entries_by_dir()):
                pb.update("Building tree", num - len(deferred_contents),
                          len(tree.inventory))
                if entry.parent_id is None:
                    continue
                reparent = False
                file_id = entry.file_id
                target_path = wt.abspath(tree_path)
                try:
                    kind = file_kind(target_path)
                except NoSuchFile:
                    pass
                else:
                    if kind == "directory":
                        try:
                            bzrdir.BzrDir.open(target_path)
                        except errors.NotBranchError:
                            pass
                        else:
                            divert.add(file_id)
                    if (file_id not in divert and
                        _content_match(tree, entry, file_id, kind,
                        target_path)):
                        tt.delete_contents(tt.trans_id_tree_path(tree_path))
                        if kind == 'directory':
                            reparent = True
                if entry.parent_id not in file_trans_id:
                    raise AssertionError(
                        'entry %s parent id %r is not in file_trans_id %r'
                        % (entry, entry.parent_id, file_trans_id))
                parent_id = file_trans_id[entry.parent_id]
                if entry.kind == 'file':
                    # We *almost* replicate new_by_entry, so that we can defer
                    # getting the file text, and get them all at once.
                    trans_id = tt.create_path(entry.name, parent_id)
                    file_trans_id[file_id] = trans_id
                    tt.version_file(entry.file_id, trans_id)
                    executable = tree.is_executable(entry.file_id, tree_path)
                    if executable is not None:
                        tt.set_executability(executable, trans_id)
                    deferred_contents.append((entry.file_id, trans_id))
                else:
                    file_trans_id[file_id] = new_by_entry(tt, entry, parent_id,
                                                          tree)
                if reparent:
                    new_trans_id = file_trans_id[file_id]
                    old_parent = tt.trans_id_tree_path(tree_path)
                    _reparent_children(tt, old_parent, new_trans_id)
            for num, (trans_id, bytes) in enumerate(
                _iter_files_bytes_accelerated(tree, accelerator_tree,
                                              deferred_contents)):
                tt.create_file(bytes, trans_id)
                pb.update('Adding file contents',
                          (num + len(tree.inventory) - len(deferred_contents)),
                          len(tree.inventory))
        finally:
            pb.finished()
        pp.next_phase()
        divert_trans = set(file_trans_id[f] for f in divert)
        resolver = lambda t, c: resolve_checkout(t, c, divert_trans)
        raw_conflicts = resolve_conflicts(tt, pass_func=resolver)
        conflicts = cook_conflicts(raw_conflicts, tt)
        for conflict in conflicts:
            warning(conflict)
        try:
            wt.add_conflicts(conflicts)
        except errors.UnsupportedOperation:
            pass
        result = tt.apply(no_conflicts=True)
    finally:
        tt.finalize()
        top_pb.finished()
    return result


def _iter_files_bytes_accelerated(tree, accelerator_tree, desired_files):
    if accelerator_tree is None:
        new_desired_files = desired_files
    else:
        new_desired_files = []
        for file_id, identifier in desired_files:
            want_new = True
            try:
                if (accelerator_tree.get_file_sha1(file_id) ==
                    tree.get_file_sha1(file_id)):
                    contents = accelerator_tree.get_file(file_id)
                    try:
                        yield identifier, (contents.read(),)
                        want_new = False
                    finally:
                        contents.close()
            except errors.NoSuchId:
                pass
            if want_new:
                new_desired_files.append((file_id, identifier))
    for result in tree.iter_files_bytes(new_desired_files):
        yield result


def _reparent_children(tt, old_parent, new_parent):
    for child in tt.iter_tree_children(old_parent):
        tt.adjust_path(tt.final_name(child), new_parent, child)


def _content_match(tree, entry, file_id, kind, target_path):
    if entry.kind != kind:
        return False
    if entry.kind == "directory":
        return True
    if entry.kind == "file":
        if tree.get_file(file_id).read() == file(target_path, 'rb').read():
            return True
    elif entry.kind == "symlink":
        if tree.get_symlink_target(file_id) == os.readlink(target_path):
            return True
    return False


def resolve_checkout(tt, conflicts, divert):
    new_conflicts = set()
    for c_type, conflict in ((c[0], c) for c in conflicts):
        # Anything but a 'duplicate' would indicate programmer error
        assert c_type == 'duplicate', c_type
        # Now figure out which is new and which is old
        if tt.new_contents(conflict[1]):
            new_file = conflict[1]
            old_file = conflict[2]
        else:
            new_file = conflict[2]
            old_file = conflict[1]

        # We should only get here if the conflict wasn't completely
        # resolved
        final_parent = tt.final_parent(old_file)
        if new_file in divert:
            new_name = tt.final_name(old_file)+'.diverted'
            tt.adjust_path(new_name, final_parent, new_file)
            new_conflicts.add((c_type, 'Diverted to',
                               new_file, old_file))
        else:
            new_name = tt.final_name(old_file)+'.moved'
            tt.adjust_path(new_name, final_parent, old_file)
            new_conflicts.add((c_type, 'Moved existing file to',
                               old_file, new_file))
    return new_conflicts


def new_by_entry(tt, entry, parent_id, tree):
    """Create a new file according to its inventory entry"""
    name = entry.name
    kind = entry.kind
    if kind == 'file':
        contents = tree.get_file(entry.file_id).readlines()
        executable = tree.is_executable(entry.file_id)
        return tt.new_file(name, parent_id, contents, entry.file_id, 
                           executable)
    elif kind in ('directory', 'tree-reference'):
        trans_id = tt.new_directory(name, parent_id, entry.file_id)
        if kind == 'tree-reference':
            tt.set_tree_reference(entry.reference_revision, trans_id)
        return trans_id 
    elif kind == 'symlink':
        target = tree.get_symlink_target(entry.file_id)
        return tt.new_symlink(name, parent_id, target, entry.file_id)
    else:
        raise errors.BadFileKindError(name, kind)


def create_by_entry(tt, entry, tree, trans_id, lines=None, mode_id=None):
    """Create new file contents according to an inventory entry."""
    if entry.kind == "file":
        if lines is None:
            lines = tree.get_file(entry.file_id).readlines()
        tt.create_file(lines, trans_id, mode_id=mode_id)
    elif entry.kind == "symlink":
        tt.create_symlink(tree.get_symlink_target(entry.file_id), trans_id)
    elif entry.kind == "directory":
        tt.create_directory(trans_id)


def create_entry_executability(tt, entry, trans_id):
    """Set the executability of a trans_id according to an inventory entry"""
    if entry.kind == "file":
        tt.set_executability(entry.executable, trans_id)


@deprecated_function(zero_fifteen)
def find_interesting(working_tree, target_tree, filenames):
    """Find the ids corresponding to specified filenames.
    
    Deprecated: Please use tree1.paths2ids(filenames, [tree2]).
    """
    working_tree.lock_read()
    try:
        target_tree.lock_read()
        try:
            return working_tree.paths2ids(filenames, [target_tree])
        finally:
            target_tree.unlock()
    finally:
        working_tree.unlock()


@deprecated_function(zero_ninety)
def change_entry(tt, file_id, working_tree, target_tree, 
                 trans_id_file_id, backups, trans_id, by_parent):
    """Replace a file_id's contents with those from a target tree."""
    if file_id is None and target_tree is None:
        # skip the logic altogether in the deprecation test
        return
    e_trans_id = trans_id_file_id(file_id)
    entry = target_tree.inventory[file_id]
    has_contents, contents_mod, meta_mod, = _entry_changes(file_id, entry, 
                                                           working_tree)
    if contents_mod:
        mode_id = e_trans_id
        if has_contents:
            if not backups:
                tt.delete_contents(e_trans_id)
            else:
                parent_trans_id = trans_id_file_id(entry.parent_id)
                backup_name = get_backup_name(entry, by_parent,
                                              parent_trans_id, tt)
                tt.adjust_path(backup_name, parent_trans_id, e_trans_id)
                tt.unversion_file(e_trans_id)
                e_trans_id = tt.create_path(entry.name, parent_trans_id)
                tt.version_file(file_id, e_trans_id)
                trans_id[file_id] = e_trans_id
        create_by_entry(tt, entry, target_tree, e_trans_id, mode_id=mode_id)
        create_entry_executability(tt, entry, e_trans_id)

    elif meta_mod:
        tt.set_executability(entry.executable, e_trans_id)
    if tt.final_name(e_trans_id) != entry.name:
        adjust_path  = True
    else:
        parent_id = tt.final_parent(e_trans_id)
        parent_file_id = tt.final_file_id(parent_id)
        if parent_file_id != entry.parent_id:
            adjust_path = True
        else:
            adjust_path = False
    if adjust_path:
        parent_trans_id = trans_id_file_id(entry.parent_id)
        tt.adjust_path(entry.name, parent_trans_id, e_trans_id)


def get_backup_name(entry, by_parent, parent_trans_id, tt):
    return _get_backup_name(entry.name, by_parent, parent_trans_id, tt)


def _get_backup_name(name, by_parent, parent_trans_id, tt):
    """Produce a backup-style name that appears to be available"""
    def name_gen():
        counter = 1
        while True:
            yield "%s.~%d~" % (name, counter)
            counter += 1
    for new_name in name_gen():
        if not tt.has_named_child(by_parent, parent_trans_id, new_name):
            return new_name


def _entry_changes(file_id, entry, working_tree):
    """Determine in which ways the inventory entry has changed.

    Returns booleans: has_contents, content_mod, meta_mod
    has_contents means there are currently contents, but they differ
    contents_mod means contents need to be modified
    meta_mod means the metadata needs to be modified
    """
    cur_entry = working_tree.inventory[file_id]
    try:
        working_kind = working_tree.kind(file_id)
        has_contents = True
    except NoSuchFile:
        has_contents = False
        contents_mod = True
        meta_mod = False
    if has_contents is True:
        if entry.kind != working_kind:
            contents_mod, meta_mod = True, False
        else:
            cur_entry._read_tree_state(working_tree.id2path(file_id), 
                                       working_tree)
            contents_mod, meta_mod = entry.detect_changes(cur_entry)
            cur_entry._forget_tree_state()
    return has_contents, contents_mod, meta_mod


def revert(working_tree, target_tree, filenames, backups=False,
           pb=DummyProgress(), change_reporter=None):
    """Revert a working tree's contents to those of a target tree."""
    target_tree.lock_read()
    tt = TreeTransform(working_tree, pb)
    try:
        pp = ProgressPhase("Revert phase", 3, pb)
        pp.next_phase()
        child_pb = bzrlib.ui.ui_factory.nested_progress_bar()
        try:
            merge_modified = _alter_files(working_tree, target_tree, tt,
                                          child_pb, filenames, backups)
        finally:
            child_pb.finished()
        pp.next_phase()
        child_pb = bzrlib.ui.ui_factory.nested_progress_bar()
        try:
            raw_conflicts = resolve_conflicts(tt, child_pb,
                lambda t, c: conflict_pass(t, c, target_tree))
        finally:
            child_pb.finished()
        conflicts = cook_conflicts(raw_conflicts, tt)
        if change_reporter:
            change_reporter = delta._ChangeReporter(
                unversioned_filter=working_tree.is_ignored)
            delta.report_changes(tt._iter_changes(), change_reporter)
        for conflict in conflicts:
            warning(conflict)
        pp.next_phase()
        tt.apply()
        working_tree.set_merge_modified(merge_modified)
    finally:
        target_tree.unlock()
        tt.finalize()
        pb.clear()
    return conflicts


def _alter_files(working_tree, target_tree, tt, pb, specific_files,
                 backups):
    merge_modified = working_tree.merge_modified()
    change_list = target_tree._iter_changes(working_tree,
        specific_files=specific_files, pb=pb)
    if target_tree.inventory.root is None:
        skip_root = True
    else:
        skip_root = False
    basis_tree = None
    try:
        deferred_files = []
        for id_num, (file_id, path, changed_content, versioned, parent, name,
                kind, executable) in enumerate(change_list):
            if skip_root and file_id[0] is not None and parent[0] is None:
                continue
            trans_id = tt.trans_id_file_id(file_id)
            mode_id = None
            if changed_content:
                keep_content = False
                if kind[0] == 'file' and (backups or kind[1] is None):
                    wt_sha1 = working_tree.get_file_sha1(file_id)
                    if merge_modified.get(file_id) != wt_sha1:
                        # acquire the basis tree lazily to prevent the
                        # expense of accessing it when it's not needed ?
                        # (Guessing, RBC, 200702)
                        if basis_tree is None:
                            basis_tree = working_tree.basis_tree()
                            basis_tree.lock_read()
                        if file_id in basis_tree:
                            if wt_sha1 != basis_tree.get_file_sha1(file_id):
                                keep_content = True
                        elif kind[1] is None and not versioned[1]:
                            keep_content = True
                if kind[0] is not None:
                    if not keep_content:
                        tt.delete_contents(trans_id)
                    elif kind[1] is not None:
                        parent_trans_id = tt.trans_id_file_id(parent[0])
                        by_parent = tt.by_parent()
                        backup_name = _get_backup_name(name[0], by_parent,
                                                       parent_trans_id, tt)
                        tt.adjust_path(backup_name, parent_trans_id, trans_id)
                        new_trans_id = tt.create_path(name[0], parent_trans_id)
                        if versioned == (True, True):
                            tt.unversion_file(trans_id)
                            tt.version_file(file_id, new_trans_id)
                        # New contents should have the same unix perms as old
                        # contents
                        mode_id = trans_id
                        trans_id = new_trans_id
                if kind[1] == 'directory':
                    tt.create_directory(trans_id)
                elif kind[1] == 'symlink':
                    tt.create_symlink(target_tree.get_symlink_target(file_id),
                                      trans_id)
                elif kind[1] == 'file':
                    deferred_files.append((file_id, (trans_id, mode_id)))
                    if basis_tree is None:
                        basis_tree = working_tree.basis_tree()
                        basis_tree.lock_read()
                    new_sha1 = target_tree.get_file_sha1(file_id)
                    if (file_id in basis_tree and new_sha1 ==
                        basis_tree.get_file_sha1(file_id)):
                        if file_id in merge_modified:
                            del merge_modified[file_id]
                    else:
                        merge_modified[file_id] = new_sha1

                    # preserve the execute bit when backing up
                    if keep_content and executable[0] == executable[1]:
                        tt.set_executability(executable[1], trans_id)
                else:
                    assert kind[1] is None
            if versioned == (False, True):
                tt.version_file(file_id, trans_id)
            if versioned == (True, False):
                tt.unversion_file(trans_id)
            if (name[1] is not None and 
                (name[0] != name[1] or parent[0] != parent[1])):
                tt.adjust_path(
                    name[1], tt.trans_id_file_id(parent[1]), trans_id)
            if executable[0] != executable[1] and kind[1] == "file":
                tt.set_executability(executable[1], trans_id)
        for (trans_id, mode_id), bytes in target_tree.iter_files_bytes(
            deferred_files):
            tt.create_file(bytes, trans_id, mode_id)
    finally:
        if basis_tree is not None:
            basis_tree.unlock()
    return merge_modified


def resolve_conflicts(tt, pb=DummyProgress(), pass_func=None):
    """Make many conflict-resolution attempts, but die if they fail"""
    if pass_func is None:
        pass_func = conflict_pass
    new_conflicts = set()
    try:
        for n in range(10):
            pb.update('Resolution pass', n+1, 10)
            conflicts = tt.find_conflicts()
            if len(conflicts) == 0:
                return new_conflicts
            new_conflicts.update(pass_func(tt, conflicts))
        raise MalformedTransform(conflicts=conflicts)
    finally:
        pb.clear()


def conflict_pass(tt, conflicts, path_tree=None):
    """Resolve some classes of conflicts.

    :param tt: The transform to resolve conflicts in
    :param conflicts: The conflicts to resolve
    :param path_tree: A Tree to get supplemental paths from
    """
    new_conflicts = set()
    for c_type, conflict in ((c[0], c) for c in conflicts):
        if c_type == 'duplicate id':
            tt.unversion_file(conflict[1])
            new_conflicts.add((c_type, 'Unversioned existing file',
                               conflict[1], conflict[2], ))
        elif c_type == 'duplicate':
            # files that were renamed take precedence
            final_parent = tt.final_parent(conflict[1])
            if tt.path_changed(conflict[1]):
                existing_file, new_file = conflict[2], conflict[1]
            else:
                existing_file, new_file = conflict[1], conflict[2]
            new_name = tt.final_name(existing_file)+'.moved'
            tt.adjust_path(new_name, final_parent, existing_file)
            new_conflicts.add((c_type, 'Moved existing file to', 
                               existing_file, new_file))
        elif c_type == 'parent loop':
            # break the loop by undoing one of the ops that caused the loop
            cur = conflict[1]
            while not tt.path_changed(cur):
                cur = tt.final_parent(cur)
            new_conflicts.add((c_type, 'Cancelled move', cur,
                               tt.final_parent(cur),))
            tt.adjust_path(tt.final_name(cur), tt.get_tree_parent(cur), cur)
            
        elif c_type == 'missing parent':
            trans_id = conflict[1]
            try:
                tt.cancel_deletion(trans_id)
                new_conflicts.add(('deleting parent', 'Not deleting', 
                                   trans_id))
            except KeyError:
                tt.create_directory(trans_id)
                new_conflicts.add((c_type, 'Created directory', trans_id))
                try:
                    tt.final_name(trans_id)
                except NoFinalPath:
                    if path_tree is not None:
                        file_id = tt.final_file_id(trans_id)
                        entry = path_tree.inventory[file_id]
                        parent_trans_id = tt.trans_id_file_id(entry.parent_id)
                        tt.adjust_path(entry.name, parent_trans_id, trans_id)
        elif c_type == 'unversioned parent':
            tt.version_file(tt.inactive_file_id(conflict[1]), conflict[1])
            new_conflicts.add((c_type, 'Versioned directory', conflict[1]))
    return new_conflicts


def cook_conflicts(raw_conflicts, tt):
    """Generate a list of cooked conflicts, sorted by file path"""
    from bzrlib.conflicts import Conflict
    conflict_iter = iter_cook_conflicts(raw_conflicts, tt)
    return sorted(conflict_iter, key=Conflict.sort_key)


def iter_cook_conflicts(raw_conflicts, tt):
    from bzrlib.conflicts import Conflict
    fp = FinalPaths(tt)
    for conflict in raw_conflicts:
        c_type = conflict[0]
        action = conflict[1]
        modified_path = fp.get_path(conflict[2])
        modified_id = tt.final_file_id(conflict[2])
        if len(conflict) == 3:
            yield Conflict.factory(c_type, action=action, path=modified_path,
                                     file_id=modified_id)
             
        else:
            conflicting_path = fp.get_path(conflict[3])
            conflicting_id = tt.final_file_id(conflict[3])
            yield Conflict.factory(c_type, action=action, path=modified_path,
                                   file_id=modified_id, 
                                   conflict_path=conflicting_path,
                                   conflict_file_id=conflicting_id)


class _FileMover(object):
    """Moves and deletes files for TreeTransform, tracking operations"""

    def __init__(self):
        self.past_renames = []
        self.pending_deletions = []

    def rename(self, from_, to):
        """Rename a file from one path to another.  Functions like os.rename"""
        try:
            os.rename(from_, to)
        except OSError, e:
            if e.errno in (errno.EEXIST, errno.ENOTEMPTY):
                raise errors.FileExists(to, str(e))
            raise
        self.past_renames.append((from_, to))

    def pre_delete(self, from_, to):
        """Rename a file out of the way and mark it for deletion.

        Unlike os.unlink, this works equally well for files and directories.
        :param from_: The current file path
        :param to: A temporary path for the file
        """
        self.rename(from_, to)
        self.pending_deletions.append(to)

    def rollback(self):
        """Reverse all renames that have been performed"""
        for from_, to in reversed(self.past_renames):
            os.rename(to, from_)
        # after rollback, don't reuse _FileMover
        past_renames = None
        pending_deletions = None

    def apply_deletions(self):
        """Apply all marked deletions"""
        for path in self.pending_deletions:
            delete_any(path)
        # after apply_deletions, don't reuse _FileMover
        past_renames = None
        pending_deletions = None
