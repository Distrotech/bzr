# Copyright (C) 2008 Canonical Ltd
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

"""Tests for the win32 walkdir extension."""

from bzrlib import tests


class _WalkdirsWin32Feature(tests.Feature):

    def _probe(self):
        try:
            import bzrlib._walkdirs_win32
        except ImportError:
            return False
        else:
            return True

    def feature_name(self):
        return 'bzrlib._walkdirs_win32'

WalkdirsWin32Feature = _WalkdirsWin32Feature()


class TestWin32Finder(tests.TestCaseInTempDir):

    _test_needs_features = [WalkdirsWin32Feature]

    def assertWalkdirs(self, expected, top, prefix=''):
        from bzrlib._walkdirs_win32 import (
            Win32Finder,
            )
        finder = Win32Finder(top, prefix=prefix)
        result = list(finder)
        self.assertEqual(expected, result)

    def test_empty_directory(self):
        self.assertWalkdirs([(('', u'.'), [])], u'.')
