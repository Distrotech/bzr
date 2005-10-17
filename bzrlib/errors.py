# -*- coding: UTF-8 -*-

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA


__copyright__ = "Copyright (C) 2005 Canonical Ltd."
__author__ = "Martin Pool <mbp@canonical.com>"

# TODO: Change to a standard exception pattern: 
#
# - docstring of exceptions is a template for formatting the exception
#   so the __str__ method can be defined only in the superclass
# - the arguments to the exception are interpolated into this string
#
# when printing the exception we'd then require special handling only
# for built-in exceptions with no decent __str__ method, such as 
# ValueError and AssertionError.  See 
# scott@canonical.com--2005/hct--devel--0.10 util/errors.py


######################################################################
# exceptions 
class BzrError(StandardError):
    def __str__(self):
        # XXX: Should we show the exception class in 
        # exceptions that don't provide their own message?  
        # maybe it should be done at a higher level
        ## n = self.__class__.__name__ + ': '
        n = ''
        if len(self.args) == 1:
            return n + self.args[0]
        elif len(self.args) == 2:
            # further explanation or suggestions
            try:
                return n + '\n  '.join([self.args[0]] + self.args[1])
            except TypeError:
                return n + "%r" % self
        else:
            return n + `self.args`


class BzrCheckError(BzrError):
    pass


class InvalidRevisionNumber(BzrError):
    def __str__(self):
        return 'invalid revision number: %r' % self.args[0]


class InvalidRevisionId(BzrError):
    pass


class BzrCommandError(BzrError):
    # Error from malformed user command
    def __str__(self):
        return self.args[0]


class NotBranchError(BzrError):
    """Specified path is not in a branch"""
    def __str__(self):
        return 'not a branch: %s' % self.args[0]


class UnsupportedFormatError(BzrError):
    """Specified path is a bzr branch that we cannot read."""
    def __str__(self):
        return 'unsupported branch format: %s' % self.args[0]


class NotVersionedError(BzrError):
    """Specified object is not versioned."""


class BadFileKindError(BzrError):
    """Specified file is of a kind that cannot be added.

    (For example a symlink or device file.)"""
    pass


class ForbiddenFileError(BzrError):
    """Cannot operate on a file because it is a control file."""
    pass


class LockError(Exception):
    """All exceptions from the lock/unlock functions should be from
    this exception class.  They will be translated as necessary. The
    original exception is available as e.original_error
    """
    def __init__(self, e=None):
        self.original_error = e
        if e:
            Exception.__init__(self, e)
        else:
            Exception.__init__(self)


class CommitNotPossible(LockError):
    """A commit was attempted but we do not have a write lock open."""


class AlreadyCommitted(LockError):
    """A rollback was requested, but is not able to be accomplished."""


class ReadOnlyError(LockError):
    """A write attempt was made in a read only transaction."""


class PointlessCommit(Exception):
    """Commit failed because nothing was changed."""


class NoSuchRevision(BzrError):
    def __init__(self, branch, revision):
        self.branch = branch
        self.revision = revision
        msg = "Branch %s has no revision %s" % (branch, revision)
        BzrError.__init__(self, msg)


class HistoryMissing(BzrError):
    def __init__(self, branch, object_type, object_id):
        self.branch = branch
        BzrError.__init__(self,
                          '%s is missing %s {%s}'
                          % (branch, object_type, object_id))


class DivergedBranches(BzrError):
    def __init__(self, branch1, branch2):
        BzrError.__init__(self, "These branches have diverged.")
        self.branch1 = branch1
        self.branch2 = branch2


class UnrelatedBranches(BzrCommandError):
    def __init__(self):
        msg = "Branches have no common ancestor, and no base revision"\
            " specified."
        BzrCommandError.__init__(self, msg)

class NoCommonAncestor(BzrError):
    def __init__(self, revision_a, revision_b):
        msg = "Revisions have no common ancestor: %s %s." \
            % (revision_a, revision_b) 
        BzrError.__init__(self, msg)

class NoCommonRoot(BzrError):
    def __init__(self, revision_a, revision_b):
        msg = "Revisions are not derived from the same root: %s %s." \
            % (revision_a, revision_b) 
        BzrError.__init__(self, msg)

class NotAncestor(BzrError):
    def __init__(self, rev_id, not_ancestor_id):
        msg = "Revision %s is not an ancestor of %s" % (not_ancestor_id, 
                                                        rev_id)
        BzrError.__init__(self, msg)
        self.rev_id = rev_id
        self.not_ancestor_id = not_ancestor_id


class NotAncestor(BzrError):
    def __init__(self, rev_id, not_ancestor_id):
        self.rev_id = rev_id
        self.not_ancestor_id = not_ancestor_id
        msg = "Revision %s is not an ancestor of %s" % (not_ancestor_id, 
                                                        rev_id)
        BzrError.__init__(self, msg)


class InstallFailed(BzrError):
    def __init__(self, revisions):
        msg = "Could not install revisions:\n%s" % " ,".join(revisions)
        BzrError.__init__(self, msg)
        self.revisions = revisions


class AmbiguousBase(BzrError):
    def __init__(self, bases):
        msg = "The correct base is unclear, becase %s are all equally close" %\
            ", ".join(bases)
        BzrError.__init__(self, msg)
        self.bases = bases

class NoCommits(BzrError):
    def __init__(self, branch):
        msg = "Branch %s has no commits." % branch
        BzrError.__init__(self, msg)

class UnlistableStore(BzrError):
    def __init__(self, store):
        BzrError.__init__(self, "Store %s is not listable" % store)

class UnlistableBranch(BzrError):
    def __init__(self, br):
        BzrError.__init__(self, "Stores for branch %s are not listable" % br)


from bzrlib.weave import WeaveError, WeaveParentMismatch

class TransportError(BzrError):
    """All errors thrown by Transport implementations should derive
    from this class.
    """
    def __init__(self, msg=None, orig_error=None):
        if msg is None and orig_error is not None:
            msg = str(orig_error)
        BzrError.__init__(self, msg)
        self.msg = msg
        self.orig_error = orig_error

# A set of semi-meaningful errors which can be thrown
class TransportNotPossible(TransportError):
    """This is for transports where a specific function is explicitly not
    possible. Such as pushing files to an HTTP server.
    """
    pass

class NonRelativePath(TransportError):
    """An absolute path was supplied, that could not be decoded into
    a relative path.
    """
    pass

class NoSuchFile(TransportError, IOError):
    """A get() was issued for a file that doesn't exist."""

    # XXX: Is multiple inheritance for exceptions really needed?

    def __str__(self):
        return 'no such file: ' + self.msg

    def __init__(self, msg=None, orig_error=None):
        import errno
        TransportError.__init__(self, msg=msg, orig_error=orig_error)
        IOError.__init__(self, errno.ENOENT, self.msg)

class FileExists(TransportError, OSError):
    """An operation was attempted, which would overwrite an entry,
    but overwritting is not supported.

    mkdir() can throw this, but put() just overwites existing files.
    """
    # XXX: Is multiple inheritance for exceptions really needed?
    def __init__(self, msg=None, orig_error=None):
        import errno
        TransportError.__init__(self, msg=msg, orig_error=orig_error)
        OSError.__init__(self, errno.EEXIST, self.msg)

class PermissionDenied(TransportError):
    """An operation cannot succeed because of a lack of permissions."""
    pass

class ConnectionReset(TransportError):
    """The connection has been closed."""
    pass

class ConflictsInTree(BzrError):
    def __init__(self):
        BzrError.__init__(self, "Working tree has conflicts.")
