# Copyright (C) 2005 by Canonical Ltd
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

"""Test that various operations work in a non-ASCII environment."""

import os
import sys
from unicodedata import normalize

from bzrlib.osutils import pathjoin, normalizes_filenames, unicode_filename
from bzrlib.tests import TestCaseWithTransport, TestSkipped
from bzrlib.workingtree import WorkingTree


class NonAsciiTest(TestCaseWithTransport):

    def test_add_in_nonascii_branch(self):
        """Test adding in a non-ASCII branch."""
        br_dir = u"\u1234"
        try:
            wt = self.make_branch_and_tree(br_dir)
        except UnicodeEncodeError:
            raise TestSkipped("filesystem can't accomodate nonascii names")
            return
        file(pathjoin(br_dir, "a"), "w").write("hello")
        wt.add(["a"], ["a-id"])


a_circle_c = u'\xe5'
a_dots_c = u'\xe4'
a_circle_d = u'a\u030a'
a_dots_d = u'a\u0308'
z_umlat_c = u'\u017d'
z_umlat_d = u'Z\u030c'


class UnicodeFilename(TestCaseWithTransport):
    """Test that UnicodeFilename returns the expected values."""

    def test_a_circle(self):
        self.assertEqual(a_circle_d, normalize('NFKD', a_circle_c))
        self.assertEqual(a_circle_c, normalize('NFKC', a_circle_d))

        self.assertEqual((a_circle_c, True), unicode_filename(a_circle_c))
        if normalizes_filenames():
            self.assertEqual((a_circle_c, True), unicode_filename(a_circle_d))
        else:
            self.assertEqual((a_circle_d, False), unicode_filename(a_circle_d))

    def test_platform(self):
        # With FAT32 and certain encodings on win32
        # a_circle_c and a_dots_c actually map to the same file
        # adding a suffix kicks in the 'preserving but insensitive'
        # route, and maintains the right files
        files = [a_circle_c+'.1', a_dots_c+'.2', z_umlat_c+'.3']
        try:
            self.build_tree(files)
        except UnicodeError:
            raise TestSkipped("filesystem cannot create unicode files")

        if sys.platform == 'darwin':
            expected = sorted([a_circle_d+'.1', a_dots_d+'.2', z_umlat_d+'.3'])
        else:
            expected = sorted(files)

        present = sorted(os.listdir(u'.'))
        self.assertEqual(expected, present)

    def test_access(self):
        # We should always be able to access files by the path returned
        # from unicode_filename
        # With FAT32 and certain encodings on win32
        # a_circle_c and a_dots_c actually map to the same file
        # adding a suffix kicks in the 'preserving but insensitive'
        # route, and maintains the right files
        files = [a_circle_c+'.1', a_dots_c+'.2', z_umlat_c+'.3']
        try:
            self.build_tree(files)
        except UnicodeError:
            raise TestSkipped("filesystem cannot create unicode files")

        for fname in files:
            path = unicode_filename(fname)[0]
            # We should get an exception if we can't open the file at
            # this location.
            f = open(path, 'rb')
            try:
                # Check the contents
                shouldbe = 'contents of %s%s' % (path.encode('utf8'),
                                                 os.linesep)
                actual = f.read()
            finally:
                f.close()
            self.assertEqual(shouldbe, actual, 
                             'contents of %s is incorrect: %r != %r'
                             % (path, shouldbe, actual))


