# Copyright (C) 2005 by Canonical Ltd

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA


def selftest():
    import unittest
    from unittest import TestLoader
    import bzrlib
    from doctest import DocTestSuite
    
    tr = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestSuite()
    import bzrlib.whitebox

    suite.addTest(TestLoader().loadTestsFromModule(bzrlib.whitebox))
    
    for m in bzrlib.store, bzrlib.inventory, bzrlib.branch, bzrlib.osutils, \
            bzrlib.tree, bzrlib.commands, bzrlib.add:
        suite.addTest(DocTestSuite(m))

    result = tr.run(suite)
    return result.wasSuccessful()
