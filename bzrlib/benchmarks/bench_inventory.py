# Copyright (C) 2006 by Canonical Ltd
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as published by
# the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""Tests for bzr add performance."""


import bzrlib
from bzrlib.benchmarks import Benchmark


class InvBenchmark(Benchmark):

    def test_make_10824_inv_entries(self):
        """Making 10824 inv entries should be quick."""
        entries = []
        def make_10824_entries():
            for counter in xrange(10000):
                bzrlib.inventory.make_entry('file', 'foo',
                    bzrlib.inventory.ROOT_ID)
            for counter in xrange(824):
                bzrlib.inventory.make_entry('directory', 'foo',
                    bzrlib.inventory.ROOT_ID)
        # on roberts machine: this originally took:  533ms/  600ms
        # fixing slots to be vaguely accurate :      365ms/  419ms
        self.time(make_10824_entries)
