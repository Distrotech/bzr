# Copyright (C) 2006-2011 Canonical Ltd
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

"""Tests for the osutils wrapper."""

import codecs
import errno
import locale
import os
import sys

from bzrlib import (
    osutils,
    )
from bzrlib.tests import (
        StringIOWrapper,
        TestCase,
        )


class FakeCodec(object):
    """Special class that helps testing over several non-existed encodings.

    Clients can add new encoding names, but because of how codecs is
    implemented they cannot be removed. Be careful with naming to avoid
    collisions between tests.
    """
    _registered = False
    _enabled_encodings = set()

    def add(self, encoding_name):
        """Adding encoding name to fake.

        :type   encoding_name:  lowercase plain string
        """
        if not self._registered:
            codecs.register(self)
            self._registered = True
        if encoding_name is not None:
            self._enabled_encodings.add(encoding_name)

    def __call__(self, encoding_name):
        """Called indirectly by codecs module during lookup"""
        if encoding_name in self._enabled_encodings:
            return codecs.lookup('latin-1')


fake_codec = FakeCodec()


class TestFakeCodec(TestCase):

    def test_fake_codec(self):
        self.assertRaises(LookupError, codecs.lookup, 'fake')

        fake_codec.add('fake')
        codecs.lookup('fake')


class TestTerminalEncoding(TestCase):
    """Test the auto-detection of proper terminal encoding."""

    def setUp(self):
        TestCase.setUp(self)
        self.overrideAttr(sys, 'stdin')
        self.overrideAttr(sys, 'stdout')
        self.overrideAttr(sys, 'stderr')
        self.overrideAttr(osutils, '_cached_user_encoding')

    def make_wrapped_streams(self,
                             stdout_encoding,
                             stderr_encoding,
                             stdin_encoding,
                             user_encoding='user_encoding',
                             enable_fake_encodings=True):
        sys.stdout = StringIOWrapper()
        sys.stdout.encoding = stdout_encoding
        sys.stderr = StringIOWrapper()
        sys.stderr.encoding = stderr_encoding
        sys.stdin = StringIOWrapper()
        sys.stdin.encoding = stdin_encoding
        osutils._cached_user_encoding = user_encoding
        if enable_fake_encodings:
            fake_codec.add(stdout_encoding)
            fake_codec.add(stderr_encoding)
            fake_codec.add(stdin_encoding)

    def test_get_terminal_encoding(self):
        self.make_wrapped_streams('stdout_encoding',
                                  'stderr_encoding',
                                  'stdin_encoding')

        # first preference is stdout encoding
        self.assertEqual('stdout_encoding', osutils.get_terminal_encoding())

        sys.stdout.encoding = None
        # if sys.stdout is None, fall back to sys.stdin
        self.assertEqual('stdin_encoding', osutils.get_terminal_encoding())

        sys.stdin.encoding = None
        # and in the worst case, use osutils.get_user_encoding()
        self.assertEqual('user_encoding', osutils.get_terminal_encoding())

    def test_get_terminal_encoding_silent(self):
        self.make_wrapped_streams('stdout_encoding',
                                  'stderr_encoding',
                                  'stdin_encoding')
        # Calling get_terminal_encoding should not mutter when silent=True is
        # passed.
        log = self.get_log()
        osutils.get_terminal_encoding()
        self.assertEqual(log, self.get_log())

    def test_get_terminal_encoding_trace(self):
        self.make_wrapped_streams('stdout_encoding',
                                  'stderr_encoding',
                                  'stdin_encoding')
        # Calling get_terminal_encoding should not mutter when silent=True is
        # passed.
        log = self.get_log()
        osutils.get_terminal_encoding(trace=True)
        self.assertNotEqual(log, self.get_log())

    def test_terminal_cp0(self):
        # test cp0 encoding (Windows returns cp0 when there is no encoding)
        self.make_wrapped_streams('cp0',
                                  'cp0',
                                  'cp0',
                                  user_encoding='latin-1',
                                  enable_fake_encodings=False)

        # cp0 is invalid encoding. We should fall back to user_encoding
        self.assertEqual('latin-1', osutils.get_terminal_encoding())

        # check stderr
        self.assertEquals('', sys.stderr.getvalue())

    def test_terminal_cp_unknown(self):
        # test against really unknown encoding
        # catch warning at stderr
        self.make_wrapped_streams('cp-unknown',
                                  'cp-unknown',
                                  'cp-unknown',
                                  user_encoding='latin-1',
                                  enable_fake_encodings=False)

        self.assertEqual('latin-1', osutils.get_terminal_encoding())

        # check stderr
        self.assertEquals('bzr: warning: unknown terminal encoding cp-unknown.\n'
                          '  Using encoding latin-1 instead.\n',
                          sys.stderr.getvalue())


class TestUserEncoding(TestCase):
    """Test detection of default user encoding."""

    def setUp(self):
        TestCase.setUp(self)
        self.overrideAttr(locale, 'getpreferredencoding')
        self.overrideAttr(locale, 'CODESET', None)
        self.overrideAttr(sys, 'stderr', StringIOWrapper())

    def test_get_user_encoding(self):
        def f():
            return 'user_encoding'

        locale.getpreferredencoding = f
        fake_codec.add('user_encoding')
        self.assertEquals('iso8859-1', # fake_codec maps to latin-1
                          osutils.get_user_encoding(use_cache=False))
        self.assertEquals('', sys.stderr.getvalue())

    def test_user_cp0(self):
        def f():
            return 'cp0'

        locale.getpreferredencoding = f
        self.assertEquals('ascii', osutils.get_user_encoding(use_cache=False))
        self.assertEquals('', sys.stderr.getvalue())

    def test_user_cp_unknown(self):
        def f():
            return 'cp-unknown'

        locale.getpreferredencoding = f
        self.assertEquals('ascii', osutils.get_user_encoding(use_cache=False))
        self.assertEquals('bzr: warning: unknown encoding cp-unknown.'
                          ' Continuing with ascii encoding.\n',
                          sys.stderr.getvalue())

    def test_user_empty(self):
        """Running bzr from a vim script gives '' for a preferred locale"""
        def f():
            return ''

        locale.getpreferredencoding = f
        self.assertEquals('ascii', osutils.get_user_encoding(use_cache=False))
        self.assertEquals('', sys.stderr.getvalue())


class TestMessageEncoding(TestCase):
    """Tests for getting the encoding used by system messages"""

    def test_get_message_encoding(self):
        encoding_name = osutils.get_message_encoding()
        "".decode(encoding_name) # should be a valid encoding name

    def test_get_message_encoding_decodes_strerror(self):
        encoding_name = osutils.get_message_encoding()
        for number, name in errno.errorcode.iteritems():
            string = os.strerror(number)
            string.decode(encoding_name)
