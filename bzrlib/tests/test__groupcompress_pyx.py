# Copyright (C) 2008, 2009 Canonical Ltd
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

"""Tests for the pyrex extension of groupcompress"""

from bzrlib import tests

from bzrlib import groupcompress


class _CompiledGroupCompress(tests.Feature):

    def _probe(self):
        try:
            import bzrlib._groupcompress_pyx
        except ImportError:
            return False
        else:
            return True

    def feature_name(self):
        return 'bzrlib._groupcompress_pyx'

CompiledGroupCompress = _CompiledGroupCompress()

_text1 = """\
This is a bit
of source text
which is meant to be matched
against other text
"""

_text2 = """\
This is a bit
of source text
which is meant to differ from
against other text
"""

_text3 = """\
This is a bit
of source text
which is meant to be matched
against other text
except it also
has a lot more data
at the end of the file
"""

_first_text = """\
a bit of text, that
does not have much in
common with the next text
"""

_second_text = """\
some more bit of text, that
does not have much in
common with the previous text
and has some extra text
"""


_third_text = """\
a bit of text, that
has some in common with the previous text
and has some extra text
and not have much in
common with the next text
"""

_fourth_text = """\
123456789012345
same rabin hash
123456789012345
same rabin hash
123456789012345
same rabin hash
123456789012345
same rabin hash
"""

class Test_GroupCompress(tests.TestCase):
    """Direct tests for the compiled extension."""

    def setUp(self):
        super(Test_GroupCompress, self).setUp()
        self.requireFeature(CompiledGroupCompress)
        from bzrlib import _groupcompress_pyx
        self._gc_module = _groupcompress_pyx


class TestMakeAndApplyDelta(Test_GroupCompress):

    def setUp(self):
        super(TestMakeAndApplyDelta, self).setUp()
        self.make_delta = self._gc_module.make_delta
        self.apply_delta = self._gc_module.apply_delta

    def test_make_delta_is_typesafe(self):
        self.make_delta('a string', 'another string')
        self.assertRaises(TypeError,
            self.make_delta, 'a string', object())
        self.assertRaises(TypeError,
            self.make_delta, 'a string', u'not a string')
        self.assertRaises(TypeError,
            self.make_delta, object(), 'a string')
        self.assertRaises(TypeError,
            self.make_delta, u'not a string', 'a string')

    def test_make_noop_delta(self):
        ident_delta = self.make_delta(_text1, _text1)
        self.assertEqual('MM\x90M', ident_delta)
        ident_delta = self.make_delta(_text2, _text2)
        self.assertEqual('NN\x90N', ident_delta)
        ident_delta = self.make_delta(_text3, _text3)
        self.assertEqual('\x87\x01\x87\x01\x90\x87', ident_delta)

    def test_make_delta(self):
        delta = self.make_delta(_text1, _text2)
        self.assertEqual('MN\x90/\x1fdiffer from\nagainst other text\n', delta)
        delta = self.make_delta(_text2, _text1)
        self.assertEqual('NM\x90/\x1ebe matched\nagainst other text\n', delta)
        delta = self.make_delta(_text3, _text1)
        self.assertEqual('\x87\x01M\x90M', delta)
        delta = self.make_delta(_text3, _text2)
        self.assertEqual('\x87\x01N\x90/\x1fdiffer from\nagainst other text\n',
                         delta)

    def test_apply_delta_is_typesafe(self):
        self.apply_delta(_text1, 'MM\x90M')
        self.assertRaises(TypeError,
            self.apply_delta, object(), 'MM\x90M')
        self.assertRaises(TypeError,
            self.apply_delta, unicode(_text1), 'MM\x90M')
        self.assertRaises(TypeError,
            self.apply_delta, _text1, u'MM\x90M')
        self.assertRaises(TypeError,
            self.apply_delta, _text1, object())

    def test_apply_delta(self):
        target = self.apply_delta(_text1,
                    'MN\x90/\x1fdiffer from\nagainst other text\n')
        self.assertEqual(_text2, target)
        target = self.apply_delta(_text2,
                    'NM\x90/\x1ebe matched\nagainst other text\n')
        self.assertEqual(_text1, target)


class TestDeltaIndex(Test_GroupCompress):

    def test_repr(self):
        di = self._gc_module.DeltaIndex('test text\n')
        self.assertEqual('DeltaIndex(1, 10)', repr(di))

    def test_make_delta(self):
        di = self._gc_module.DeltaIndex(_text1)
        delta = di.make_delta(_text2)
        self.assertEqual('MN\x90/\x1fdiffer from\nagainst other text\n', delta)

    def test_delta_against_multiple_sources(self):
        di = self._gc_module.DeltaIndex()
        di.add_source(_first_text, 0)
        self.assertEqual(len(_first_text), di._source_offset)
        di.add_source(_second_text, 0)
        self.assertEqual(len(_first_text) + len(_second_text), di._source_offset)
        delta = di.make_delta(_third_text)
        result = self._gc_module.apply_delta(_first_text + _second_text, delta)
        self.assertEqualDiff(_third_text, result)
        self.assertEqual('\xac\x01\x85\x01\x90\x14\x0chas some in '
                         '\x91v6\x03and\x91d"\x91:\n', delta)

    def test_delta_with_offsets(self):
        di = self._gc_module.DeltaIndex()
        di.add_source(_first_text, 5)
        self.assertEqual(len(_first_text) + 5, di._source_offset)
        di.add_source(_second_text, 10)
        self.assertEqual(len(_first_text) + len(_second_text) + 15,
                         di._source_offset)
        delta = di.make_delta(_third_text)
        self.assertIsNot(None, delta)
        result = self._gc_module.apply_delta(
            '12345' + _first_text + '1234567890' + _second_text, delta)
        self.assertIsNot(None, result)
        self.assertEqualDiff(_third_text, result)
        self.assertEqual('\xbb\x01\x85\x01\x91\x05\x14\x0chas some in '
                         '\x91\x856\x03and\x91s"\x91?\n', delta)

    def test_delta_with_delta_bytes(self):
        di = self._gc_module.DeltaIndex()
        source = _first_text
        di.add_source(_first_text, 0)
        self.assertEqual(len(_first_text), di._source_offset)
        delta = di.make_delta(_second_text)
        self.assertEqual('Dh\tsome more\x91\x019'
                         '&previous text\nand has some extra text\n', delta)
        di.add_delta_source(delta, 0)
        source += delta
        self.assertEqual(len(_first_text) + len(delta), di._source_offset)
        second_delta = di.make_delta(_third_text)
        result = self._gc_module.apply_delta(source, second_delta)
        self.assertEqualDiff(_third_text, result)
        # We should be able to match against the 'previous text\nand has some...'
        # that was part of the delta bytes
        # Note that we don't match the 'common with the', because it isn't long
        # enough to match in the original text, and those bytes are not present
        # in the delta for the second text.
        self.assertEqual('z\x85\x01\x90\x14\x1chas some in common with the '
                         '\x91T&\x03and\x91\x18,', second_delta)
        # Add this delta, and create a new delta for the same text. We should
        # find the remaining text, and only insert the short 'and' text.
        di.add_delta_source(second_delta, 0)
        source += second_delta
        third_delta = di.make_delta(_third_text)
        result = self._gc_module.apply_delta(source, third_delta)
        self.assertEqualDiff(_third_text, result)
        self.assertEqual('\xa6\x01\x85\x01\x90\x14\x91\x80\x1c'
                         '\x91T&\x03and\x91\x18,', third_delta)
        # Now create a delta, which we know won't be able to be 'fit' into the
        # existing index
        fourth_delta = di.make_delta(_fourth_text)
        self.assertEqual(_fourth_text,
                         self._gc_module.apply_delta(source, fourth_delta))
        self.assertEqual('\xa6\x01\x80\x01'
                         '\x7f123456789012345\nsame rabin hash\n'
                         '123456789012345\nsame rabin hash\n'
                         '123456789012345\nsame rabin hash\n'
                         '123456789012345\nsame rabin hash'
                         '\x01\n', fourth_delta)
        di.add_delta_source(fourth_delta, 0)
        source += fourth_delta
        # With the next delta, everything should be found
        fifth_delta = di.make_delta(_fourth_text)
        self.assertEqual(_fourth_text,
                         self._gc_module.apply_delta(source, fifth_delta))
        self.assertEqual('\xac\x02\x80\x01\x91\xab\x7f\x01\n', fifth_delta)
