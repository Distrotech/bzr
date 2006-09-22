# Copyright (C) 2006 by Canonical Ltd
#   Authors: Robert Collins <robert.collins@canonical.com>
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

"""Tests for the formatting and construction of errors."""

from bzrlib import (
    bzrdir,
    errors,
    )
from bzrlib.tests import TestCase, TestCaseWithTransport


class TestErrors(TestCaseWithTransport):

    def test_medium_not_connected(self):
        error = errors.MediumNotConnected("a medium")
        self.assertEqualDiff(
            "The medium 'a medium' is not connected.", str(error))
        
    def test_no_repo(self):
        dir = bzrdir.BzrDir.create(self.get_url())
        error = errors.NoRepositoryPresent(dir)
        self.assertNotEqual(-1, str(error).find((dir.transport.clone('..').base)))
        self.assertEqual(-1, str(error).find((dir.transport.base)))
        
    def test_no_smart_medium(self):
        error = errors.NoSmartMedium("a transport")
        self.assertEqualDiff("The transport 'a transport' cannot tunnel the "
            "smart protocol.",
            str(error))

    def test_no_such_id(self):
        error = errors.NoSuchId("atree", "anid")
        self.assertEqualDiff("The file id anid is not present in the tree "
            "atree.",
            str(error))

    def test_too_many_concurrent_requests(self):
        error = errors.TooManyConcurrentRequests("a medium")
        self.assertEqualDiff("The medium 'a medium' has reached its concurrent "
            "request limit. Be sure to finish_writing and finish_reading on "
            "the current request that is open.",
            str(error))

    def test_up_to_date(self):
        error = errors.UpToDateFormat(bzrdir.BzrDirFormat4())
        self.assertEqualDiff("The branch format Bazaar-NG branch, "
                             "format 0.0.4 is already at the most "
                             "recent format.",
                             str(error))

    def test_corrupt_repository(self):
        repo = self.make_repository('.')
        error = errors.CorruptRepository(repo)
        self.assertEqualDiff("An error has been detected in the repository %s.\n"
                             "Please run bzr reconcile on this repository." %
                             repo.bzrdir.root_transport.base,
                             str(error))

    def test_reading_completed(self):
        error = errors.ReadingCompleted("a request")
        self.assertEqualDiff("The MediumRequest 'a request' has already had "
            "finish_reading called upon it - the request has been completed and"
            " no more data may be read.",
            str(error))

    def test_writing_completed(self):
        error = errors.WritingCompleted("a request")
        self.assertEqualDiff("The MediumRequest 'a request' has already had "
            "finish_writing called upon it - accept bytes may not be called "
            "anymore.",
            str(error))

    def test_writing_not_completed(self):
        error = errors.WritingNotComplete("a request")
        self.assertEqualDiff("The MediumRequest 'a request' has not has "
            "finish_writing called upon it - until the write phase is complete"
            " no data may be read.",
            str(error))


class PassThroughError(errors.BzrNewError):
    """Pass through %(foo)s and %(bar)s"""

    def __init__(self, foo, bar):
        errors.BzrNewError.__init__(self, foo=foo, bar=bar)


class ErrorWithBadFormat(errors.BzrNewError):
    """One format specifier: %(thing)s"""


class TestErrorFormatting(TestCase):
    
    def test_always_str(self):
        e = PassThroughError(u'\xb5', 'bar')
        self.assertIsInstance(e.__str__(), str)
        # In Python str(foo) *must* return a real byte string
        # not a Unicode string. The following line would raise a
        # Unicode error, because it tries to call str() on the string
        # returned from e.__str__(), and it has non ascii characters
        s = str(e)
        self.assertEqual('Pass through \xc2\xb5 and bar', s)

    def test_mismatched_format_args(self):
        # Even though ErrorWithBadFormat's format string does not match the
        # arguments we constructing it with, we can still stringify an instance
        # of this exception. The resulting string will say its unprintable.
        e = ErrorWithBadFormat(not_thing='x')
        self.assertStartsWith(
            str(e), 'Unprintable exception ErrorWithBadFormat(')

