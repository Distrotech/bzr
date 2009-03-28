# Copyright (C) 2007 Canonical Ltd
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


from bzrlib import tests
from bzrlib.builtins import cmd_push
from bzrlib.tests.transport_util import TestCaseWithConnectionHookedTransport


class TestPush(TestCaseWithConnectionHookedTransport):

    def test_push(self):
        self.make_branch_and_tree('branch')

        self.start_logging_connections()

        cmd = cmd_push()
        # We don't care about the ouput but 'outf' should be defined
        cmd.outf = tests.StringIOWrapper()
        cmd.run(self.get_url('remote'), directory='branch')
        self.assertEquals(1, len(self.connections))

    def test_push_onto_stacked(self):
        self.make_branch_and_tree('base', format='1.9')
        self.make_branch_and_tree('source', format='1.9')

        self.start_logging_connections()

        cmd = cmd_push()
        cmd.outf = tests.StringIOWrapper()
        cmd.run(self.get_url('remote'), directory='source',
                stacked_on=self.get_url('base'))
        self.assertEqual(1, len(self.connections))
