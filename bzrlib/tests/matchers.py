# Copyright (C) 2010 Canonical Ltd
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

"""Matchers for bzrlib.

Primarily test support, Matchers are used by self.assertThat in the bzrlib
test suite. A matcher is a stateful test helper which can be used to determine
if a passed object 'matches', much like a regex. If the object does not match
the mismatch can be described in a human readable fashion. assertThat then
raises if a mismatch occurs, showing the description as the assertion error.

Matchers are designed to be more reusable and composable than layered
assertions in Test Case objects, so they are recommended for new testing work.
"""

__all__ = [
    'HasLayout',
    'MatchesAncestry',
    'ContainsNoVfsCalls',
    'ReturnsUnlockable',
    'RevisionHistoryMatches',
    ]

from bzrlib import (
    osutils,
    revision as _mod_revision,
    )
from bzrlib import lazy_import
lazy_import.lazy_import(globals(),
"""
from bzrlib.smart.request import request_handlers as smart_request_handlers
from bzrlib.smart import vfs
""")

from testtools.matchers import Equals, Mismatch, Matcher


class ReturnsUnlockable(Matcher):
    """A matcher that checks for the pattern we want lock* methods to have:

    They should return an object with an unlock() method.
    Calling that method should unlock the original object.

    :ivar lockable_thing: The object which can be locked that will be
        inspected.
    """

    def __init__(self, lockable_thing):
        Matcher.__init__(self)
        self.lockable_thing = lockable_thing

    def __str__(self):
        return ('ReturnsUnlockable(lockable_thing=%s)' %
            self.lockable_thing)

    def match(self, lock_method):
        lock_method().unlock()
        if self.lockable_thing.is_locked():
            return _IsLocked(self.lockable_thing)
        return None


class _IsLocked(Mismatch):
    """Something is locked."""

    def __init__(self, lockable_thing):
        self.lockable_thing = lockable_thing

    def describe(self):
        return "%s is locked" % self.lockable_thing


class _AncestryMismatch(Mismatch):
    """Ancestry matching mismatch."""

    def __init__(self, tip_revision, got, expected):
        self.tip_revision = tip_revision
        self.got = got
        self.expected = expected

    def describe(self):
        return "mismatched ancestry for revision %r was %r, expected %r" % (
            self.tip_revision, self.got, self.expected)


class MatchesAncestry(Matcher):
    """A matcher that checks the ancestry of a particular revision.

    :ivar graph: Graph in which to check the ancestry
    :ivar revision_id: Revision id of the revision
    """

    def __init__(self, repository, revision_id):
        Matcher.__init__(self)
        self.repository = repository
        self.revision_id = revision_id

    def __str__(self):
        return ('MatchesAncestry(repository=%r, revision_id=%r)' % (
            self.repository, self.revision_id))

    def match(self, expected):
        self.repository.lock_read()
        try:
            graph = self.repository.get_graph()
            got = [r for r, p in graph.iter_ancestry([self.revision_id])]
            if _mod_revision.NULL_REVISION in got:
                got.remove(_mod_revision.NULL_REVISION)
        finally:
            self.repository.unlock()
        if sorted(got) != sorted(expected):
            return _AncestryMismatch(self.revision_id, sorted(got),
                sorted(expected))


class HasLayout(Matcher):
    """A matcher that checks if a tree has a specific layout.

    :ivar entries: List of expected entries, as (path, file_id) pairs.
    """

    def __init__(self, entries):
        Matcher.__init__(self)
        self.entries = entries

    def get_tree_layout(self, tree):
        """Get the (path, file_id) pairs for the current tree."""
        tree.lock_read()
        try:
            for path, ie in tree.iter_entries_by_dir():
                if ie.parent_id is None:
                    yield (u"", ie.file_id)
                else:
                    yield (path+ie.kind_character(), ie.file_id)
        finally:
            tree.unlock()

    @staticmethod
    def _strip_unreferenced_directories(entries):
        """Strip all directories that don't (in)directly contain any files.

        :param entries: List of path strings or (path, ie) tuples to process
        """
        directories = []
        for entry in entries:
            if isinstance(entry, basestring):
                path = entry
            else:
                path = entry[0]
            if not path or path[-1] == "/":
                # directory
                directories.append((path, entry))
            else:
                # Yield the referenced parent directories
                for dirpath, direntry in directories:
                    if osutils.is_inside(dirpath, path):
                        yield direntry
                directories = []
                yield entry

    def __str__(self):
        return 'HasLayout(%r)' % self.entries

    def match(self, tree):
        actual = list(self.get_tree_layout(tree))
        if self.entries and isinstance(self.entries[0], basestring):
            actual = [path for (path, fileid) in actual]
        if not tree.has_versioned_directories():
            entries = list(self._strip_unreferenced_directories(self.entries))
        else:
            entries = self.entries
        return Equals(entries).match(actual)


class RevisionHistoryMatches(Matcher):
    """A matcher that checks if a branch has a specific revision history.

    :ivar history: Revision history, as list of revisions. Oldest first.
    """

    def __init__(self, history):
        Matcher.__init__(self)
        self.expected = history

    def __str__(self):
        return 'RevisionHistoryMatches(%r)' % self.expected

    def match(self, branch):
        branch.lock_read()
        try:
            graph = branch.repository.get_graph()
            history = list(graph.iter_lefthand_ancestry(
                branch.last_revision(), [_mod_revision.NULL_REVISION]))
            history.reverse()
        finally:
            branch.unlock()
        return Equals(self.expected).match(history)


class _NoVfsCallsMismatch(Mismatch):
    """Mismatch describing a list of HPSS calls which includes VFS requests."""

    def __init__(self, vfs_calls):
        self.vfs_calls = vfs_calls

    def describe(self):
        return "no VFS calls expected, got: %s" % ",".join([
            "%s(%s)" % (c.method,
                ", ".join([repr(a) for a in c.args])) for c in self.vfs_calls])


class ContainsNoVfsCalls(Matcher):
    """Ensure that none of the specified calls are HPSS calls."""

    def __str__(self):
        return 'ContainsNoVfsCalls()'

    @classmethod
    def match(cls, hpss_calls):
        vfs_calls = []
        for call in hpss_calls:
            try:
                request_method = smart_request_handlers.get(call.call.method)
            except KeyError:
                # A method we don't know about doesn't count as a VFS method.
                continue
            if issubclass(request_method, vfs.VfsRequest):
                vfs_calls.append(call.call)
        if len(vfs_calls) == 0:
            return None
        return _NoVfsCallsMismatch(vfs_calls)
