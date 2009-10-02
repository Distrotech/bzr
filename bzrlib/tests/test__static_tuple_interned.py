# Copyright (C) 2009 Canonical Ltd
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

"""Tests for the StaticTupleInterned type."""

import sys

from bzrlib import (
    # _static_tuple_py,
    errors,
    osutils,
    tests,
    )

from bzrlib.tests import (
    test__static_tuple,
    )
try:
    from bzrlib import _static_tuple_interned_pyx as _module
except ImportError:
    _module = None
try:
    from bzrlib._static_tuple_c import StaticTuple
except ImportError:
    pass


# Even though this is an extension, we don't permute the tests for a python
# version. As the plain python version is just a dict.

class _CompiledStaticTupleInterned(tests.Feature):

    def _probe(self):
        if _module is None:
            return False
        return True

    def feature_name(self):
        return 'bzrlib._static_tuple_interned_pyx'

CompiledStaticTupleInterned = _CompiledStaticTupleInterned()


class TestStaticTupleInterned(tests.TestCase):

    _test_needs_features = [CompiledStaticTupleInterned, 
                            test__static_tuple.CompiledStaticTuple]

    def assertIn(self, obj, container):
        self.assertTrue(obj in container,
            '%s not found in %s' % (obj, container))

    def assertNotIn(self, obj, container):
        self.assertTrue(obj not in container,
            'We found %s in %s' % (obj, container))

    def assertFillState(self, used, fill, mask, obj):
        self.assertEqual((used, fill, mask), (obj.used, obj.fill, obj.mask))

    def assertRefcount(self, count, obj):
        """Assert that the refcount for obj is what we expect.

        Note that this automatically adjusts for the fact that calling
        assertRefcount actually creates a new pointer, as does calling
        sys.getrefcount. So pass the expected value *before* the call.
        """
        # I don't understand why it is count+3 here, but it seems to be
        # correct. If I check in the calling function, with:
        # self.assertEqual(count+1, sys.getrefcount(obj))
        # Then it works fine. Something about passing it to assertRefcount is
        # actually double-incrementing (and decrementing) the refcount
        self.assertEqual(count+3, sys.getrefcount(obj))

    def test_initial(self):
        obj = _module.StaticTupleInterner()
        self.assertEqual(0, len(obj))
        st = StaticTuple('foo', 'bar')
        self.assertFillState(0, 0, 0x3ff, obj)

    def test__lookup(self):
        # The tuple hash function is rather good at entropy. For all integers
        # 0=>1023, hash((i,)) & 1023 maps to a unique output, and hash((i,j))
        # maps to all 1024 fields evenly.
        # However, hash((c,d))& 1023 for characters has an uneven distribution
        # of collisions, for example:
        #  ('a', 'a'), ('f', '4'), ('p', 'r'), ('q', '1'), ('F', 'T'),
        #  ('Q', 'Q'), ('V', 'd'), ('7', 'C')
        # all collide @ 643
        obj = _module.StaticTupleInterner()
        offset, val = obj._test_lookup(StaticTuple('a', 'a'))
        self.assertEqual(643, offset)
        self.assertEqual('<null>', val)
        offset, val = obj._test_lookup(StaticTuple('f', '4'))
        self.assertEqual(643, offset)
        self.assertEqual('<null>', val)
        offset, val = obj._test_lookup(StaticTuple('p', 'r'))
        self.assertEqual(643, offset)
        self.assertEqual('<null>', val)

    def test_get_set_del_with_collisions(self):
        obj = _module.StaticTupleInterner()
        k1 = StaticTuple('a', 'a')
        k2 = StaticTuple('f', '4') # collides
        k3 = StaticTuple('p', 'r')
        k4 = StaticTuple('q', '1')
        self.assertEqual((643, '<null>'), obj._test_lookup(k1))
        self.assertEqual((643, '<null>'), obj._test_lookup(k2))
        self.assertEqual((643, '<null>'), obj._test_lookup(k3))
        self.assertEqual((643, '<null>'), obj._test_lookup(k4))
        obj.add(k1)
        self.assertIn(k1, obj)
        self.assertNotIn(k2, obj)
        self.assertNotIn(k3, obj)
        self.assertNotIn(k4, obj)
        self.assertEqual((643, k1), obj._test_lookup(k1))
        self.assertEqual((787, '<null>'), obj._test_lookup(k2))
        self.assertEqual((787, '<null>'), obj._test_lookup(k3))
        self.assertEqual((787, '<null>'), obj._test_lookup(k4))
        self.assertIs(k1, obj[k1])
        obj.add(k2)
        self.assertIs(k2, obj[k2])
        self.assertEqual((643, k1), obj._test_lookup(k1))
        self.assertEqual((787, k2), obj._test_lookup(k2))
        self.assertEqual((660, '<null>'), obj._test_lookup(k3))
        # Even though k4 collides for the first couple of iterations, the hash
        # perturbation uses the full width hash (not just the masked value), so
        # it now diverges
        self.assertEqual((180, '<null>'), obj._test_lookup(k4))
        self.assertEqual((643, k1), obj._test_lookup(('a', 'a')))
        self.assertEqual((787, k2), obj._test_lookup(('f', '4')))
        self.assertEqual((660, '<null>'), obj._test_lookup(('p', 'r')))
        self.assertEqual((180, '<null>'), obj._test_lookup(('q', '1')))
        obj.add(k3)
        self.assertIs(k3, obj[k3])
        self.assertIn(k1, obj)
        self.assertIn(k2, obj)
        self.assertIn(k3, obj)
        self.assertNotIn(k4, obj)

        del obj[k1]
        self.assertEqual((643, '<dummy>'), obj._test_lookup(k1))
        self.assertEqual((787, k2), obj._test_lookup(k2))
        self.assertEqual((660, k3), obj._test_lookup(k3))
        self.assertEqual((643, '<dummy>'), obj._test_lookup(k4))
        self.assertNotIn(k1, obj)
        self.assertIn(k2, obj)
        self.assertIn(k3, obj)
        self.assertNotIn(k4, obj)

    def test_add(self):
        obj = _module.StaticTupleInterner()
        self.assertFillState(0, 0, 0x3ff, obj)
        k1 = StaticTuple('foo')
        self.assertRefcount(1, k1)
        self.assertIs(k1, obj.add(k1))
        self.assertFillState(1, 1, 0x3ff, obj)
        self.assertRefcount(2, k1)
        ktest = obj[k1]
        self.assertRefcount(3, k1)
        self.assertIs(k1, ktest)
        del ktest
        self.assertRefcount(2, k1)
        k2 = StaticTuple('foo')
        self.assertRefcount(1, k2)
        self.assertIsNot(k1, k2)
        # doesn't add anything, so the counters shouldn't be adjusted
        self.assertIs(k1, obj.add(k2))
        self.assertFillState(1, 1, 0x3ff, obj)
        self.assertRefcount(2, k1) # not changed
        self.assertRefcount(1, k2) # not incremented
        self.assertIs(k1, obj[k1])
        self.assertIs(k1, obj[k2])
        self.assertRefcount(2, k1)
        self.assertRefcount(1, k2)
        # Deleting an entry should remove the fill, but not the used
        del obj[k1]
        self.assertFillState(0, 1, 0x3ff, obj)
        self.assertRefcount(1, k1)
        k3 = StaticTuple('bar')
        self.assertRefcount(1, k3)
        self.assertIs(k3, obj.add(k3))
        self.assertFillState(1, 2, 0x3ff, obj)
        self.assertRefcount(2, k3)
        self.assertIs(k2, obj.add(k2))
        self.assertFillState(2, 2, 0x3ff, obj)
        self.assertRefcount(1, k1)
        self.assertRefcount(2, k2)
        self.assertRefcount(2, k3)

    def test_discard(self):
        obj = _module.StaticTupleInterner()
        k1 = StaticTuple('foo')
        k2 = StaticTuple('foo')
        k3 = StaticTuple('bar')
        self.assertRefcount(1, k1)
        self.assertRefcount(1, k2)
        self.assertRefcount(1, k3)
        obj.add(k1)
        self.assertRefcount(2, k1)
        self.assertEqual(0, obj.discard(k3))
        self.assertRefcount(1, k3)
        obj.add(k3)
        self.assertRefcount(2, k3)
        self.assertEqual(1, obj.discard(k3))
        self.assertRefcount(1, k3)

    def test__delitem__(self):
        obj = _module.StaticTupleInterner()
        k1 = StaticTuple('foo')
        k2 = StaticTuple('foo')
        k3 = StaticTuple('bar')
        self.assertRefcount(1, k1)
        self.assertRefcount(1, k2)
        self.assertRefcount(1, k3)
        obj.add(k1)
        self.assertRefcount(2, k1)
        self.assertRaises(KeyError, obj.__delitem__, k3)
        self.assertRefcount(1, k3)
        obj.add(k3)
        self.assertRefcount(2, k3)
        del obj[k3]
        self.assertRefcount(1, k3)
