# Copyright (C) 2006 by Canonical Ltd
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

"""Black-box tests for 'bzr inventory'."""

from bzrlib.tests import TestCaseWithTransport


class TestInventory(TestCaseWithTransport):

    def setUp(self):
        TestCaseWithTransport.setUp(self)

        tree = self.make_branch_and_tree('.')
        self.build_tree(['a', 'b/', 'b/c'])

        tree.add(['a', 'b', 'b/c'], ['a-id', 'b-id', 'c-id'])
        tree.commit('init', rev_id='one')

    def assertInventoryEqual(self, expected, *args, **kwargs):
        """Test that the output of 'bzr inventory' is as expected.

        Any arguments supplied will be passed to run_bzr.
        """
        out, err = self.run_bzr('inventory', *args, **kwargs)
        self.assertEqual(expected, out)
        self.assertEqual('', err)
        
    def test_inventory(self):
        self.assertInventoryEqual('a\nb\nb/c\n')

    def test_inventory_kind(self):
        self.assertInventoryEqual('a\nb/c\n', '--kind', 'file')
        self.assertInventoryEqual('b\n', '--kind', 'directory')

    def test_inventory_show_ids(self):
        expected = ''.join(('%-50s %s\n' % (path, file_id))
                                for path, file_id in
                                [('a', 'a-id'),
                                 ('b', 'b-id'),
                                 ('b/c', 'c-id')
                                ]
                          )
        self.assertInventoryEqual(expected, '--show-ids')

    def test_inventory_specific_files(self):
        self.assertInventoryEqual('a\n', 'a')
        self.assertInventoryEqual('b\nb/c\n', 'b', 'b/c')

    def test_mixed(self):
        """Test that we get expected results when mixing parameters"""
        a_line = '%-50s %s\n' % ('a', 'a-id')
        b_line = '%-50s %s\n' % ('b', 'b-id')
        c_line = '%-50s %s\n' % ('b/c', 'c-id')

        self.assertInventoryEqual('', '--kind', 'directory', 'a')
        self.assertInventoryEqual(a_line + c_line, '--kind', 'file',
                                                   '--show-ids')
        self.assertInventoryEqual(c_line, '--kind', 'file', '--show-ids',
                                          'b', 'b/c')
