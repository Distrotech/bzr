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
#

"""Tests for writing fixed size chunks with compression."""

import zlib

from bzrlib import chunk_writer
from bzrlib.tests import TestCaseWithTransport


class TestWriter(TestCaseWithTransport):

    def check_chunk(self, bytes_list, size):
        bytes = ''.join(bytes_list)
        self.assertEqual(size, len(bytes))
        return zlib.decompress(bytes)

    def test_chunk_writer_empty(self):
        writer = chunk_writer.ChunkWriter(4096)
        bytes_list, unused, padding = writer.finish()
        node_bytes = self.check_chunk(bytes_list, 4096)
        self.assertEqual("", node_bytes)
        self.assertEqual(None, unused)
        # Only a zlib header.
        self.assertEqual(4088, padding)

    def test_some_data(self):
        writer = chunk_writer.ChunkWriter(4096)
        writer.write("foo bar baz quux\n")
        bytes_list, unused, padding = writer.finish()
        node_bytes = self.check_chunk(bytes_list, 4096)
        self.assertEqual("foo bar baz quux\n", node_bytes)
        self.assertEqual(None, unused)
        # More than just the header..
        self.assertEqual(4073, padding)

    def test_too_much_data_does_not_exceed_size(self):
        # Generate enough data to exceed 4K
        lines = []
        for group in range(48):
            offset = group * 50
            numbers = range(offset, offset + 50)
            # Create a line with this group
            lines.append(''.join(map(str, numbers)) + '\n')
        writer = chunk_writer.ChunkWriter(4096)
        for idx, line in enumerate(lines):
            if idx >= 45:
                import pdb; pdb.set_trace()
            if writer.write(line):
                self.assertEqual(47, idx)
                break
        bytes_list, unused, _ = writer.finish()
        node_bytes = self.check_chunk(bytes_list, 4096)
        # the first 46 lines should have been added
        expected_bytes = ''.join(lines[:46])
        self.assertEqualDiff(expected_bytes, node_bytes)
        # And the line that failed should have been saved for us
        self.assertEqual(lines[46], unused)

    def test_too_much_data_preserves_reserve_space(self):
        # Generate enough data to exceed 4K
        lines = []
        for group in range(48):
            offset = group * 50
            numbers = range(offset, offset + 50)
            # Create a line with this group
            lines.append(''.join(map(str, numbers)) + '\n')
        writer = chunk_writer.ChunkWriter(4096, 256)
        for idx, line in enumerate(lines):
            if writer.write(line):
                self.assertEqual(44, idx)
                break
        else:
            self.fail('We were able to write all lines')
        self.assertFalse(writer.write("A"*256, reserved=True))
        bytes_list, unused, _ = writer.finish()
        node_bytes = self.check_chunk(bytes_list, 4096)
        # the first 44 lines should have been added
        expected_bytes = ''.join(lines[:44]) + "A"*256
        self.assertEqualDiff(expected_bytes, node_bytes)
        # And the line that failed should have been saved for us
        self.assertEqual(lines[44], unused)
