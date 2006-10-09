# Copyright (C) 2006 Canonical Ltd
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

"""MutableTree object.

See MutableTree for more details.
"""


from bzrlib import tree
from bzrlib.decorators import needs_read_lock, needs_write_lock
from bzrlib.osutils import splitpath
from bzrlib.symbol_versioning import DEPRECATED_PARAMETER


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

    @needs_write_lock
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
            assert(ids is None or isinstance(ids, basestring))
            assert(kinds is None or isinstance(kinds, basestring))
            files = [files]
            if ids is not None:
                ids = [ids]
            if kinds is not None:
                kinds = [kinds]

        if ids is None:
            ids = [None] * len(files)
        else:
            assert(len(ids) == len(files))

        if kinds is None:
            kinds = [None] * len(files)
        else:
            assert(len(kinds) == len(files))
        for f in files:
            # generic constraint checks:
            if self.is_control_filename(f):
                raise errors.ForbiddenControlFileError(filename=f)
            fp = splitpath(f)
            if len(fp) == 0:
                raise BzrError("cannot add top-level %r" % f)
        # fill out file kinds for all files [not needed when we stop 
        # caring about the instantaneous file kind within a uncommmitted tree
        #
        self._gather_kinds(files, kinds)
        self._add(files, ids, kinds)

    def _add(self, files, ids, kinds):
        """Helper function for add - updates the inventory."""
        raise NotImplementedError(self._add)

    @needs_write_lock
    def commit(self, message=None, revprops=None, *args, **kwargs):
        # avoid circular imports
        from bzrlib import commit
        if revprops is None:
            revprops = {}
        if not 'branch-nick' in revprops:
            revprops['branch-nick'] = self.branch.nick
        # args for wt.commit start at message from the Commit.commit method,
        # but with branch a kwarg now, passing in args as is results in the
        #message being used for the branch
        args = (DEPRECATED_PARAMETER, message, ) + args
        committed_id = commit.Commit().commit(working_tree=self,
            revprops=revprops, *args, **kwargs)
        return committed_id

    def _gather_kinds(self, files, kinds):
        """Helper function for add - sets the entries of kinds."""
        raise NotImplementedError(self._gather_kinds)

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

    def set_parent_trees(self, parents_list, allow_leftmost_as_ghost=False):
        """Set the parents of the working tree.

        :param parents_list: A list of (revision_id, tree) tuples. 
            If tree is None, then that element is treated as an unreachable
            parent tree - i.e. a ghost.
        """
        raise NotImplementedError(self.set_parent_trees)
