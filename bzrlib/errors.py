# Copyright (C) 2005, 2006 Canonical
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

"""Exceptions for bzr, and reporting of them.

There are 3 different classes of error:

 * KeyboardInterrupt, and OSError with EPIPE - the program terminates 
   with an appropriate short message

 * User errors, indicating a problem caused by the user such as a bad URL.
   These are printed in a short form.
 
 * Internal unexpected errors, including most Python builtin errors
   and some raised from inside bzr.  These are printed with a full 
   traceback and an invitation to report the bug.

Exceptions are caught at a high level to report errors to the user, and
might also be caught inside the program.  Therefore it needs to be
possible to convert them to a meaningful string, and also for them to be
interrogated by the program.

Exceptions are defined such that the arguments given to the constructor
are stored in the object as properties of the same name.  When the
object is printed as a string, the doc string of the class is used as
a format string with the property dictionary available to it.

This means that exceptions can used like this:

>>> import sys
>>> try:
...   raise NotBranchError(path='/foo/bar')
... except:
...   print sys.exc_type
...   print sys.exc_value
...   path = getattr(sys.exc_value, 'path', None)
...   if path is not None:
...     print path
bzrlib.errors.NotBranchError
Not a branch: /foo/bar
/foo/bar

Therefore:

 * create a new exception class for any class of error that can be
   usefully distinguished.  If no callers are likely to want to catch
   one but not another, don't worry about them.

 * the __str__ method should generate something useful; BzrError provides
   a good default implementation

Exception strings should start with a capital letter and should not have a
final fullstop.
"""

from warnings import warn

from bzrlib.patches import (PatchSyntax, 
                            PatchConflict, 
                            MalformedPatchHeader,
                            MalformedHunkHeader,
                            MalformedLine,)


# based on Scott James Remnant's hct error classes

# TODO: is there any value in providing the .args field used by standard
# python exceptions?   A list of values with no names seems less useful 
# to me.

# TODO: Perhaps convert the exception to a string at the moment it's 
# constructed to make sure it will succeed.  But that says nothing about
# exceptions that are never raised.

# TODO: Convert all the other error classes here to BzrNewError, and eliminate
# the old one.

# TODO: The pattern (from hct) of using classes docstrings as message
# templates is cute but maybe not such a great idea - perhaps should have a
# separate static message_template.


class BzrError(StandardError):
    
    is_user_error = True

    def __str__(self):
        # XXX: Should we show the exception class in 
        # exceptions that don't provide their own message?  
        # maybe it should be done at a higher level
        ## n = self.__class__.__name__ + ': '
        n = ''
        if len(self.args) == 1:
            return str(self.args[0])
        elif len(self.args) == 2:
            # further explanation or suggestions
            try:
                return n + '\n  '.join([self.args[0]] + self.args[1])
            except TypeError:
                return n + "%r" % self
        else:
            return n + `self.args`


class BzrNewError(BzrError):
    """bzr error"""
    # base classes should override the docstring with their human-
    # readable explanation

    def __init__(self, *args, **kwds):
        # XXX: Use the underlying BzrError to always generate the args attribute
        # if it doesn't exist.  We can't use super here, because exceptions are
        # old-style classes in python2.4 (but new in 2.5).  --bmc, 20060426
        BzrError.__init__(self, *args)
        for key, value in kwds.items():
            setattr(self, key, value)

    def __str__(self):
        try:
            # __str__() should always return a 'str' object
            # never a 'unicode' object.
            s = self.__doc__ % self.__dict__
            if isinstance(s, unicode):
                return s.encode('utf8')
            return s
        except (NameError, ValueError, KeyError), e:
            return 'Unprintable exception %s: %s' \
                % (self.__class__.__name__, str(e))


class BzrCheckError(BzrNewError):
    """Internal check failed: %(message)s"""

    is_user_error = False

    def __init__(self, message):
        BzrNewError.__init__(self)
        self.message = message


class InvalidEntryName(BzrNewError):
    """Invalid entry name: %(name)s"""

    is_user_error = False

    def __init__(self, name):
        BzrNewError.__init__(self)
        self.name = name


class InvalidRevisionNumber(BzrNewError):
    """Invalid revision number %(revno)d"""
    def __init__(self, revno):
        BzrNewError.__init__(self)
        self.revno = revno


class InvalidRevisionId(BzrNewError):
    """Invalid revision-id {%(revision_id)s} in %(branch)s"""

    def __init__(self, revision_id, branch):
        # branch can be any string or object with __str__ defined
        BzrNewError.__init__(self)
        self.revision_id = revision_id
        self.branch = branch


class NoWorkingTree(BzrNewError):
    """No WorkingTree exists for %(base)s."""
    
    def __init__(self, base):
        BzrNewError.__init__(self)
        self.base = base


class NotLocalUrl(BzrNewError):
    """%(url)s is not a local path."""
    
    def __init__(self, url):
        BzrNewError.__init__(self)
        self.url = url


class BzrCommandError(BzrNewError):
    """Error from user command"""

    is_user_error = True

    # Error from malformed user command; please avoid raising this as a
    # generic exception not caused by user input.
    #
    # I think it's a waste of effort to differentiate between errors that
    # are not intended to be caught anyway.  UI code need not subclass
    # BzrCommandError, and non-UI code should not throw a subclass of
    # BzrCommandError.  ADHB 20051211
    def __init__(self, msg):
        # Object.__str__() must return a real string
        # returning a Unicode string is a python error.
        if isinstance(msg, unicode):
            self.msg = msg.encode('utf8')
        else:
            self.msg = msg

    def __str__(self):
        return self.msg


class BzrOptionError(BzrCommandError):
    """Error in command line options"""

    
class StrictCommitFailed(BzrNewError):
    """Commit refused because there are unknown files in the tree"""


# XXX: Should be unified with TransportError; they seem to represent the
# same thing
class PathError(BzrNewError):
    """Generic path error: %(path)r%(extra)s)"""

    def __init__(self, path, extra=None):
        BzrNewError.__init__(self)
        self.path = path
        if extra:
            self.extra = ': ' + str(extra)
        else:
            self.extra = ''


class NoSuchFile(PathError):
    """No such file: %(path)r%(extra)s"""


class FileExists(PathError):
    """File exists: %(path)r%(extra)s"""


class DirectoryNotEmpty(PathError):
    """Directory not empty: %(path)r%(extra)s"""


class ResourceBusy(PathError):
    """Device or resource busy: %(path)r%(extra)s"""


class PermissionDenied(PathError):
    """Permission denied: %(path)r%(extra)s"""


class InvalidURL(PathError):
    """Invalid url supplied to transport: %(path)r%(extra)s"""


class InvalidURLJoin(PathError):
    """Invalid URL join request: %(args)s%(extra)s"""

    def __init__(self, msg, base, args):
        PathError.__init__(self, base, msg)
        self.args = [base]
        self.args.extend(args)


class UnsupportedProtocol(PathError):
    """Unsupported protocol for url "%(path)s"%(extra)s"""

    def __init__(self, url, extra):
        PathError.__init__(self, url, extra=extra)


class PathNotChild(BzrNewError):
    """Path %(path)r is not a child of path %(base)r%(extra)s"""

    is_user_error = False

    def __init__(self, path, base, extra=None):
        BzrNewError.__init__(self)
        self.path = path
        self.base = base
        if extra:
            self.extra = ': ' + str(extra)
        else:
            self.extra = ''


class InvalidNormalization(PathError):
    """Path %(path)r is not unicode normalized"""


# TODO: This is given a URL; we try to unescape it but doing that from inside
# the exception object is a bit undesirable.
# TODO: Probably this behavior of should be a common superclass 
class NotBranchError(PathError):
    """Not a branch: %(path)s"""

    def __init__(self, path):
       import bzrlib.urlutils as urlutils
       self.path = urlutils.unescape_for_display(path, 'ascii')


class AlreadyBranchError(PathError):
    """Already a branch: %(path)s."""


class BranchExistsWithoutWorkingTree(PathError):
    """Directory contains a branch, but no working tree \
(use bzr checkout if you wish to build a working tree): %(path)s"""


class AtomicFileAlreadyClosed(PathError):
    """'%(function)s' called on an AtomicFile after it was closed: %(path)s"""

    def __init__(self, path, function):
        PathError.__init__(self, path=path, extra=None)
        self.function = function


class InaccessibleParent(PathError):
    """Parent not accessible given base %(base)s and relative path %(path)s"""

    def __init__(self, path, base):
        PathError.__init__(self, path)
        self.base = base


class NoRepositoryPresent(BzrNewError):
    """No repository present: %(path)r"""
    def __init__(self, bzrdir):
        BzrNewError.__init__(self)
        self.path = bzrdir.transport.clone('..').base


class FileInWrongBranch(BzrNewError):
    """File %(path)s in not in branch %(branch_base)s."""

    def __init__(self, branch, path):
        BzrNewError.__init__(self)
        self.branch = branch
        self.branch_base = branch.base
        self.path = path


class UnsupportedFormatError(BzrNewError):
    """Unsupported branch format: %(format)s"""


class UnknownFormatError(BzrNewError):
    """Unknown branch format: %(format)r"""


class IncompatibleFormat(BzrNewError):
    """Format %(format)s is not compatible with .bzr version %(bzrdir)s."""

    def __init__(self, format, bzrdir_format):
        BzrNewError.__init__(self)
        self.format = format
        self.bzrdir = bzrdir_format


class NotVersionedError(BzrNewError):
    """%(path)s is not versioned"""
    def __init__(self, path):
        BzrNewError.__init__(self)
        self.path = path


class PathsNotVersionedError(BzrNewError):
    # used when reporting several paths are not versioned
    """Path(s) are not versioned: %(paths_as_string)s"""

    def __init__(self, paths):
        from bzrlib.osutils import quotefn
        BzrNewError.__init__(self)
        self.paths = paths
        self.paths_as_string = ' '.join([quotefn(p) for p in paths])


class PathsDoNotExist(BzrNewError):
    """Path(s) do not exist: %(paths_as_string)s"""

    # used when reporting that paths are neither versioned nor in the working
    # tree

    def __init__(self, paths):
        # circular import
        from bzrlib.osutils import quotefn
        BzrNewError.__init__(self)
        self.paths = paths
        self.paths_as_string = ' '.join([quotefn(p) for p in paths])


class BadFileKindError(BzrNewError):
    """Cannot operate on %(filename)s of unsupported kind %(kind)s"""


class ForbiddenControlFileError(BzrNewError):
    """Cannot operate on %(filename)s because it is a control file"""


class LockError(BzrNewError):
    """Lock error: %(message)s"""
    # All exceptions from the lock/unlock functions should be from
    # this exception class.  They will be translated as necessary. The
    # original exception is available as e.original_error
    #
    # New code should prefer to raise specific subclasses
    def __init__(self, message):
        self.message = message


class CommitNotPossible(LockError):
    """A commit was attempted but we do not have a write lock open."""
    def __init__(self):
        pass


class AlreadyCommitted(LockError):
    """A rollback was requested, but is not able to be accomplished."""
    def __init__(self):
        pass


class ReadOnlyError(LockError):
    """A write attempt was made in a read only transaction on %(obj)s"""
    def __init__(self, obj):
        self.obj = obj


class OutSideTransaction(BzrNewError):
    """A transaction related operation was attempted after the transaction finished."""


class ObjectNotLocked(LockError):
    """%(obj)r is not locked"""

    is_user_error = False

    # this can indicate that any particular object is not locked; see also
    # LockNotHeld which means that a particular *lock* object is not held by
    # the caller -- perhaps they should be unified.
    def __init__(self, obj):
        self.obj = obj


class ReadOnlyObjectDirtiedError(ReadOnlyError):
    """Cannot change object %(obj)r in read only transaction"""
    def __init__(self, obj):
        self.obj = obj


class UnlockableTransport(LockError):
    """Cannot lock: transport is read only: %(transport)s"""
    def __init__(self, transport):
        self.transport = transport


class LockContention(LockError):
    """Could not acquire lock %(lock)s"""
    # TODO: show full url for lock, combining the transport and relative bits?
    def __init__(self, lock):
        self.lock = lock


class LockBroken(LockError):
    """Lock was broken while still open: %(lock)s - check storage consistency!"""
    def __init__(self, lock):
        self.lock = lock


class LockBreakMismatch(LockError):
    """Lock was released and re-acquired before being broken: %(lock)s: held by %(holder)r, wanted to break %(target)r"""
    def __init__(self, lock, holder, target):
        self.lock = lock
        self.holder = holder
        self.target = target


class LockNotHeld(LockError):
    """Lock not held: %(lock)s"""
    def __init__(self, lock):
        self.lock = lock


class PointlessCommit(BzrNewError):
    """No changes to commit"""


class UpgradeReadonly(BzrNewError):
    """Upgrade URL cannot work with readonly URL's."""


class UpToDateFormat(BzrNewError):
    """The branch format %(format)s is already at the most recent format."""

    def __init__(self, format):
        BzrNewError.__init__(self)
        self.format = format


class StrictCommitFailed(Exception):
    """Commit refused because there are unknowns in the tree."""


class NoSuchRevision(BzrNewError):
    """Branch %(branch)s has no revision %(revision)s"""

    is_user_error = False

    def __init__(self, branch, revision):
        BzrNewError.__init__(self, branch=branch, revision=revision)


class NoSuchRevisionSpec(BzrNewError):
    """No namespace registered for string: %(spec)r"""

    def __init__(self, spec):
        BzrNewError.__init__(self, spec=spec)


class InvalidRevisionSpec(BzrNewError):
    """Requested revision: '%(spec)s' does not exist in branch:
%(branch)s%(extra)s"""

    def __init__(self, spec, branch, extra=None):
        BzrNewError.__init__(self, branch=branch, spec=spec)
        if extra:
            self.extra = '\n' + str(extra)
        else:
            self.extra = ''


class HistoryMissing(BzrError):
    def __init__(self, branch, object_type, object_id):
        self.branch = branch
        BzrError.__init__(self,
                          '%s is missing %s {%s}'
                          % (branch, object_type, object_id))


class DivergedBranches(BzrNewError):
    "These branches have diverged.  Use the merge command to reconcile them."""

    is_user_error = True

    def __init__(self, branch1, branch2):
        self.branch1 = branch1
        self.branch2 = branch2


class UnrelatedBranches(BzrNewError):
    "Branches have no common ancestor, and no merge base revision was specified."

    is_user_error = True


class NoCommonAncestor(BzrNewError):
    "Revisions have no common ancestor: %(revision_a)s %(revision_b)s"

    def __init__(self, revision_a, revision_b):
        self.revision_a = revision_a
        self.revision_b = revision_b


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


class InstallFailed(BzrError):
    def __init__(self, revisions):
        msg = "Could not install revisions:\n%s" % " ,".join(revisions)
        BzrError.__init__(self, msg)
        self.revisions = revisions


class AmbiguousBase(BzrError):
    def __init__(self, bases):
        warn("BzrError AmbiguousBase has been deprecated as of bzrlib 0.8.",
                DeprecationWarning)
        msg = "The correct base is unclear, because %s are all equally close" %\
            ", ".join(bases)
        BzrError.__init__(self, msg)
        self.bases = bases


class NoCommits(BzrNewError):
    """Branch %(branch)s has no commits."""

    def __init__(self, branch):
        BzrNewError.__init__(self, branch=branch)


class UnlistableStore(BzrError):
    def __init__(self, store):
        BzrError.__init__(self, "Store %s is not listable" % store)



class UnlistableBranch(BzrError):
    def __init__(self, br):
        BzrError.__init__(self, "Stores for branch %s are not listable" % br)


class BoundBranchOutOfDate(BzrNewError):
    """Bound branch %(branch)s is out of date with master branch %(master)s."""
    def __init__(self, branch, master):
        BzrNewError.__init__(self)
        self.branch = branch
        self.master = master

        
class CommitToDoubleBoundBranch(BzrNewError):
    """Cannot commit to branch %(branch)s. It is bound to %(master)s, which is bound to %(remote)s."""
    def __init__(self, branch, master, remote):
        BzrNewError.__init__(self)
        self.branch = branch
        self.master = master
        self.remote = remote


class OverwriteBoundBranch(BzrNewError):
    """Cannot pull --overwrite to a branch which is bound %(branch)s"""
    def __init__(self, branch):
        BzrNewError.__init__(self)
        self.branch = branch


class BoundBranchConnectionFailure(BzrNewError):
    """Unable to connect to target of bound branch %(branch)s => %(target)s: %(error)s"""
    def __init__(self, branch, target, error):
        BzrNewError.__init__(self)
        self.branch = branch
        self.target = target
        self.error = error


class WeaveError(BzrNewError):
    """Error in processing weave: %(message)s"""

    def __init__(self, message=None):
        BzrNewError.__init__(self)
        self.message = message


class WeaveRevisionAlreadyPresent(WeaveError):
    """Revision {%(revision_id)s} already present in %(weave)s"""
    def __init__(self, revision_id, weave):

        WeaveError.__init__(self)
        self.revision_id = revision_id
        self.weave = weave


class WeaveRevisionNotPresent(WeaveError):
    """Revision {%(revision_id)s} not present in %(weave)s"""

    def __init__(self, revision_id, weave):
        WeaveError.__init__(self)
        self.revision_id = revision_id
        self.weave = weave


class WeaveFormatError(WeaveError):
    """Weave invariant violated: %(what)s"""

    def __init__(self, what):
        WeaveError.__init__(self)
        self.what = what


class WeaveParentMismatch(WeaveError):
    """Parents are mismatched between two revisions."""
    

class WeaveInvalidChecksum(WeaveError):
    """Text did not match it's checksum: %(message)s"""


class WeaveTextDiffers(WeaveError):
    """Weaves differ on text content. Revision: {%(revision_id)s}, %(weave_a)s, %(weave_b)s"""

    def __init__(self, revision_id, weave_a, weave_b):
        WeaveError.__init__(self)
        self.revision_id = revision_id
        self.weave_a = weave_a
        self.weave_b = weave_b


class WeaveTextDiffers(WeaveError):
    """Weaves differ on text content. Revision: {%(revision_id)s}, %(weave_a)s, %(weave_b)s"""

    def __init__(self, revision_id, weave_a, weave_b):
        WeaveError.__init__(self)
        self.revision_id = revision_id
        self.weave_a = weave_a
        self.weave_b = weave_b


class VersionedFileError(BzrNewError):
    """Versioned file error."""


class RevisionNotPresent(VersionedFileError):
    """Revision {%(revision_id)s} not present in %(file_id)s."""

    def __init__(self, revision_id, file_id):
        VersionedFileError.__init__(self)
        self.revision_id = revision_id
        self.file_id = file_id


class RevisionAlreadyPresent(VersionedFileError):
    """Revision {%(revision_id)s} already present in %(file_id)s."""

    def __init__(self, revision_id, file_id):
        VersionedFileError.__init__(self)
        self.revision_id = revision_id
        self.file_id = file_id


class KnitError(BzrNewError):
    """Knit error"""


class KnitHeaderError(KnitError):
    """Knit header error: %(badline)r unexpected"""

    def __init__(self, badline):
        KnitError.__init__(self)
        self.badline = badline


class KnitCorrupt(KnitError):
    """Knit %(filename)s corrupt: %(how)s"""

    def __init__(self, filename, how):
        KnitError.__init__(self)
        self.filename = filename
        self.how = how


class NoSuchExportFormat(BzrNewError):
    """Export format %(format)r not supported"""
    def __init__(self, format):
        BzrNewError.__init__(self)
        self.format = format


class TransportError(BzrNewError):
    """Transport error: %(msg)s %(orig_error)s"""

    def __init__(self, msg=None, orig_error=None):
        if msg is None and orig_error is not None:
            msg = str(orig_error)
        if orig_error is None:
            orig_error = ''
        if msg is None:
            msg =  ''
        self.msg = msg
        self.orig_error = orig_error
        BzrNewError.__init__(self)


# A set of semi-meaningful errors which can be thrown
class TransportNotPossible(TransportError):
    """Transport operation not possible: %(msg)s %(orig_error)%"""


class ConnectionError(TransportError):
    """Connection error: %(msg)s %(orig_error)s"""


class ConnectionReset(TransportError):
    """Connection closed: %(msg)s %(orig_error)s"""


class InvalidRange(TransportError):
    """Invalid range access."""
    
    def __init__(self, path, offset):
        TransportError.__init__(self, ("Invalid range access in %s at %d"
                                       % (path, offset)))


class InvalidHttpResponse(TransportError):
    """Invalid http response for %(path)s: %(msg)s"""

    def __init__(self, path, msg, orig_error=None):
        self.path = path
        TransportError.__init__(self, msg, orig_error=orig_error)


class InvalidHttpRange(InvalidHttpResponse):
    """Invalid http range "%(range)s" for %(path)s: %(msg)s"""
    
    def __init__(self, path, range, msg):
        self.range = range
        InvalidHttpResponse.__init__(self, path, msg)


class InvalidHttpContentType(InvalidHttpResponse):
    """Invalid http Content-type "%(ctype)s" for %(path)s: %(msg)s"""
    
    def __init__(self, path, ctype, msg):
        self.ctype = ctype
        InvalidHttpResponse.__init__(self, path, msg)


class ConflictsInTree(BzrError):
    def __init__(self):
        BzrError.__init__(self, "Working tree has conflicts.")


class ParseConfigError(BzrError):
    def __init__(self, errors, filename):
        if filename is None:
            filename = ""
        message = "Error(s) parsing config file %s:\n%s" % \
            (filename, ('\n'.join(e.message for e in errors)))
        BzrError.__init__(self, message)


class SigningFailed(BzrError):
    def __init__(self, command_line):
        BzrError.__init__(self, "Failed to gpg sign data with command '%s'"
                               % command_line)


class WorkingTreeNotRevision(BzrError):
    def __init__(self, tree):
        BzrError.__init__(self, "The working tree for %s has changed since"
                          " last commit, but weave merge requires that it be"
                          " unchanged." % tree.basedir)


class CantReprocessAndShowBase(BzrNewError):
    """Can't reprocess and show base.
Reprocessing obscures relationship of conflicting lines to base."""


class GraphCycleError(BzrNewError):
    """Cycle in graph %(graph)r"""
    def __init__(self, graph):
        BzrNewError.__init__(self)
        self.graph = graph


class NotConflicted(BzrNewError):
    """File %(filename)s is not conflicted."""

    def __init__(self, filename):
        BzrNewError.__init__(self)
        self.filename = filename


class MustUseDecorated(Exception):
    """A decorating function has requested its original command be used.
    
    This should never escape bzr, so does not need to be printable.
    """


class NoBundleFound(BzrNewError):
    """No bundle was found in %(filename)s"""
    def __init__(self, filename):
        BzrNewError.__init__(self)
        self.filename = filename


class BundleNotSupported(BzrNewError):
    """Unable to handle bundle version %(version)s: %(msg)s"""
    def __init__(self, version, msg):
        BzrNewError.__init__(self)
        self.version = version
        self.msg = msg


class MissingText(BzrNewError):
    """Branch %(base)s is missing revision %(text_revision)s of %(file_id)s"""

    def __init__(self, branch, text_revision, file_id):
        BzrNewError.__init__(self)
        self.branch = branch
        self.base = branch.base
        self.text_revision = text_revision
        self.file_id = file_id


class DuplicateKey(BzrNewError):
    """Key %(key)s is already present in map"""


class MalformedTransform(BzrNewError):
    """Tree transform is malformed %(conflicts)r"""


class BzrBadParameter(BzrNewError):
    """A bad parameter : %(param)s is not usable.
    
    This exception should never be thrown, but it is a base class for all
    parameter-to-function errors.
    """
    def __init__(self, param):
        BzrNewError.__init__(self)
        self.param = param


class BzrBadParameterNotUnicode(BzrBadParameter):
    """Parameter %(param)s is neither unicode nor utf8."""


class ReusingTransform(BzrNewError):
    """Attempt to reuse a transform that has already been applied."""


class CantMoveRoot(BzrNewError):
    """Moving the root directory is not supported at this time"""


class BzrBadParameterNotString(BzrBadParameter):
    """Parameter %(param)s is not a string or unicode string."""


class BzrBadParameterMissing(BzrBadParameter):
    """Parameter $(param)s is required but not present."""


class BzrBadParameterUnicode(BzrBadParameter):
    """Parameter %(param)s is unicode but only byte-strings are permitted."""


class BzrBadParameterContainsNewline(BzrBadParameter):
    """Parameter %(param)s contains a newline."""


class DependencyNotPresent(BzrNewError):
    """Unable to import library "%(library)s": %(error)s"""

    def __init__(self, library, error):
        BzrNewError.__init__(self, library=library, error=error)


class ParamikoNotPresent(DependencyNotPresent):
    """Unable to import paramiko (required for sftp support): %(error)s"""

    def __init__(self, error):
        DependencyNotPresent.__init__(self, 'paramiko', error)


class UninitializableFormat(BzrNewError):
    """Format %(format)s cannot be initialised by this version of bzr."""

    def __init__(self, format):
        BzrNewError.__init__(self)
        self.format = format


class NoDiff(BzrNewError):
    """Diff is not installed on this machine: %(msg)s"""

    def __init__(self, msg):
        BzrNewError.__init__(self, msg=msg)


class NoDiff3(BzrNewError):
    """Diff3 is not installed on this machine."""


class ExistingLimbo(BzrNewError):
    """This tree contains left-over files from a failed operation.
    Please examine %(limbo_dir)s to see if it contains any files you wish to
    keep, and delete it when you are done.
    """
    def __init__(self, limbo_dir):
       BzrNewError.__init__(self)
       self.limbo_dir = limbo_dir


class ImmortalLimbo(BzrNewError):
    """Unable to delete transform temporary directory $(limbo_dir)s.
    Please examine %(limbo_dir)s to see if it contains any files you wish to
    keep, and delete it when you are done.
    """
    def __init__(self, limbo_dir):
       BzrNewError.__init__(self)
       self.limbo_dir = limbo_dir


class OutOfDateTree(BzrNewError):
    """Working tree is out of date, please run 'bzr update'."""

    def __init__(self, tree):
        BzrNewError.__init__(self)
        self.tree = tree


class MergeModifiedFormatError(BzrNewError):
    """Error in merge modified format"""


class ConflictFormatError(BzrNewError):
    """Format error in conflict listings"""


class CorruptRepository(BzrNewError):
    """An error has been detected in the repository %(repo_path)s.
Please run bzr reconcile on this repository."""

    def __init__(self, repo):
        BzrNewError.__init__(self)
        self.repo_path = repo.bzrdir.root_transport.base


class UpgradeRequired(BzrNewError):
    """To use this feature you must upgrade your branch at %(path)s."""

    def __init__(self, path):
        BzrNewError.__init__(self)
        self.path = path


class LocalRequiresBoundBranch(BzrNewError):
    """Cannot perform local-only commits on unbound branches."""


class MissingProgressBarFinish(BzrNewError):
    """A nested progress bar was not 'finished' correctly."""


class InvalidProgressBarType(BzrNewError):
    """Environment variable BZR_PROGRESS_BAR='%(bar_type)s is not a supported type
Select one of: %(valid_types)s"""

    def __init__(self, bar_type, valid_types):
        BzrNewError.__init__(self, bar_type=bar_type, valid_types=valid_types)


class UnsupportedOperation(BzrNewError):
    """The method %(mname)s is not supported on objects of type %(tname)s."""
    def __init__(self, method, method_self):
        self.method = method
        self.mname = method.__name__
        self.tname = type(method_self).__name__


class BinaryFile(BzrNewError):
    """File is binary but should be text."""


class IllegalPath(BzrNewError):
    """The path %(path)s is not permitted on this platform"""

    def __init__(self, path):
        BzrNewError.__init__(self)
        self.path = path


class TestamentMismatch(BzrNewError):
    """Testament did not match expected value.  
       For revision_id {%(revision_id)s}, expected {%(expected)s}, measured 
       {%(measured)s}
    """
    def __init__(self, revision_id, expected, measured):
        self.revision_id = revision_id
        self.expected = expected
        self.measured = measured


class NotABundle(BzrNewError):
    """Not a bzr revision-bundle: %(text)r"""

    def __init__(self, text):
        BzrNewError.__init__(self)
        self.text = text


class BadBundle(BzrNewError): 
    """Bad bzr revision-bundle: %(text)r"""

    def __init__(self, text):
        BzrNewError.__init__(self)
        self.text = text


class MalformedHeader(BadBundle): 
    """Malformed bzr revision-bundle header: %(text)r"""

    def __init__(self, text):
        BzrNewError.__init__(self)
        self.text = text


class MalformedPatches(BadBundle): 
    """Malformed patches in bzr revision-bundle: %(text)r"""

    def __init__(self, text):
        BzrNewError.__init__(self)
        self.text = text


class MalformedFooter(BadBundle): 
    """Malformed footer in bzr revision-bundle: %(text)r"""

    def __init__(self, text):
        BzrNewError.__init__(self)
        self.text = text


class UnsupportedEOLMarker(BadBundle):
    """End of line marker was not \\n in bzr revision-bundle"""    

    def __init__(self):
        BzrNewError.__init__(self)


class UnknownSSH(BzrNewError):
    """Unrecognised value for BZR_SSH environment variable: %(vendor)s"""

    def __init__(self, vendor):
        BzrNewError.__init__(self)
        self.vendor = vendor


class GhostRevisionUnusableHere(BzrNewError):
    """Ghost revision {%(revision_id)s} cannot be used here."""

    def __init__(self, revision_id):
        BzrNewError.__init__(self)
        self.revision_id = revision_id
