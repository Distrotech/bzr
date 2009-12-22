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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#

"""Tests for chunks_to_lines."""

from bzrlib import tests


def load_tests(standard_tests, module, loader):
    suite, _ = tests.permute_tests_for_extension(standard_tests, loader,
        'bzrlib._chunks_to_lines_py', 'bzrlib._chunks_to_lines_pyx')
    return suite


class TestChunksToLines(tests.TestCase):

    module = None # Filled in by test parameterization

    def assertChunksToLines(self, lines, chunks, alreadly_lines=False):
        result = self.module.chunks_to_lines(chunks)
        self.assertEqual(lines, result)
        if alreadly_lines:
            self.assertIs(chunks, result)

    def test_fulltext_chunk_to_lines(self):
        self.assertChunksToLines(['foo\n', 'bar\r\n', 'ba\rz\n'],
                                 ['foo\nbar\r\nba\rz\n'])
        self.assertChunksToLines(['foobarbaz\n'], ['foobarbaz\n'],
                                 alreadly_lines=True)
        self.assertChunksToLines(['foo\n', 'bar\n', '\n', 'baz\n', '\n', '\n'],
                                 ['foo\nbar\n\nbaz\n\n\n'])
        self.assertChunksToLines(['foobarbaz'], ['foobarbaz'],
                                 alreadly_lines=True)
        self.assertChunksToLines(['foobarbaz'], ['foo', 'bar', 'baz'])

    def test_newlines(self):
        self.assertChunksToLines(['\n'], ['\n'], alreadly_lines=True)
        self.assertChunksToLines(['\n'], ['', '\n', ''])
        self.assertChunksToLines(['\n'], ['\n', ''])
        self.assertChunksToLines(['\n'], ['', '\n'])
        self.assertChunksToLines(['\n', '\n', '\n'], ['\n\n\n'])
        self.assertChunksToLines(['\n', '\n', '\n'], ['\n', '\n', '\n'],
                                 alreadly_lines=True)

    def test_lines_to_lines(self):
        self.assertChunksToLines(['foo\n', 'bar\r\n', 'ba\rz\n'],
                                 ['foo\n', 'bar\r\n', 'ba\rz\n'],
                                 alreadly_lines=True)

    def test_no_final_newline(self):
        self.assertChunksToLines(['foo\n', 'bar\r\n', 'ba\rz'],
                                 ['foo\nbar\r\nba\rz'])
        self.assertChunksToLines(['foo\n', 'bar\r\n', 'ba\rz'],
                                 ['foo\n', 'bar\r\n', 'ba\rz'],
                                 alreadly_lines=True)
        self.assertChunksToLines(('foo\n', 'bar\r\n', 'ba\rz'),
                                 ('foo\n', 'bar\r\n', 'ba\rz'),
                                 alreadly_lines=True)
        self.assertChunksToLines([], [], alreadly_lines=True)
        self.assertChunksToLines(['foobarbaz'], ['foobarbaz'],
                                 alreadly_lines=True)
        self.assertChunksToLines([], [''])

    def test_mixed(self):
        self.assertChunksToLines(['foo\n', 'bar\r\n', 'ba\rz'],
                                 ['foo\n', 'bar\r\nba\r', 'z'])
        self.assertChunksToLines(['foo\n', 'bar\r\n', 'ba\rz'],
                                 ['foo\nb', 'a', 'r\r\nba\r', 'z'])
        self.assertChunksToLines(['foo\n', 'bar\r\n', 'ba\rz'],
                                 ['foo\nbar\r\nba', '\r', 'z'])

        self.assertChunksToLines(['foo\n', 'bar\r\n', 'ba\rz'],
                                 ['foo\n', '', 'bar\r\nba', '\r', 'z'])
        self.assertChunksToLines(['foo\n', 'bar\r\n', 'ba\rz\n'],
                                 ['foo\n', 'bar\r\n', 'ba\rz\n', ''])
        self.assertChunksToLines(['foo\n', 'bar\r\n', 'ba\rz\n'],
                                 ['foo\n', 'bar', '\r\n', 'ba\rz\n'])

    def test_not_lines(self):
        # We should raise a TypeError, not crash
        self.assertRaises(TypeError, self.module.chunks_to_lines,
                          object())
        self.assertRaises(TypeError, self.module.chunks_to_lines,
                          [object()])
        self.assertRaises(TypeError, self.module.chunks_to_lines,
                          ['foo', object()])
