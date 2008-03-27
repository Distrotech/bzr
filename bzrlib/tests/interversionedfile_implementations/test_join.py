# Copyright (C) 2006 Canonical Ltd
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

"""Tests for join between versioned files."""


import bzrlib.errors as errors
from bzrlib.tests import TestCaseWithTransport
from bzrlib.transport import get_transport
import bzrlib.versionedfile as versionedfile


class TestJoin(TestCaseWithTransport):
    #Tests have self.versionedfile_factory and self.versionedfile_factory_to
    #available to create source and target versioned files respectively.

    def get_source(self, name='source'):
        """Get a versioned file we will be joining from."""
        return self.versionedfile_factory(name,
                                          get_transport(self.get_url()),
                                          create=True)

    def get_target(self, name='target', create=True):
        """"Get an empty versioned file to join into."""
        return self.versionedfile_factory_to(name,
                                             get_transport(self.get_url()),
                                             create=create)

    def test_join(self):
        f1 = self.get_source()
        f1.add_lines('r0', [], ['a\n', 'b\n'])
        f1.add_lines('r1', ['r0'], ['c\n', 'b\n'])
        f2 = self.get_target()
        f2.join(f1, None)
        def verify_file(f):
            self.assertTrue(f.has_version('r0'))
            self.assertTrue(f.has_version('r1'))
        verify_file(f2)
        verify_file(self.get_target())

        self.assertRaises(errors.RevisionNotPresent,
            f2.join, f1, version_ids=['r3'])

    def test_gets_expected_inter_worker(self):
        source = self.get_source()
        target = self.get_target()
        inter = versionedfile.InterVersionedFile.get(source, target)
        self.assertTrue(isinstance(inter, self.interversionedfile_class))

    def test_join_versions_joins_ancestors_not_siblings(self):
        # joining with a version list should bring in ancestors of the
        # named versions but not siblings thereof.
        target = self.get_target()
        target.add_lines('base', [], [])
        source = self.get_source()
        source.add_lines('base', [], [])
        source.add_lines('sibling', ['base'], [])
        source.add_lines('ancestorleft', ['base'], [])
        source.add_lines('ancestorright', ['base'], [])
        source.add_lines('namedleft', ['ancestorleft'], [])
        source.add_lines('namedright', ['ancestorright'], [])
        target.join(source, version_ids=['namedleft', 'namedright'])
        self.assertFalse(target.has_version('sibling'))
        self.assertTrue(target.has_version('ancestorleft'))
        self.assertTrue(target.has_version('ancestorright'))
        self.assertTrue(target.has_version('namedleft'))
        self.assertTrue(target.has_version('namedright'))

    def test_join_different_parents_existing_version(self):
        """This may either ignore or error."""
        w1 = self.get_target('w1')
        w2 = self.get_source('w2')
        w1.add_lines('v-1', [], ['line 1\n'])
        w2.add_lines('v-2', [], ['line 2\n'])
        w1.add_lines('v-3', ['v-1'], ['line 1\n'])
        w2.add_lines('v-3', ['v-2'], ['line 1\n'])
        try:
            w1.join(w2)
        except errors.WeaveParentMismatch:
            # Acceptable behaviour:
            return
        self.assertEqual(sorted(w1.versions()),
                         'v-1 v-2 v-3'.split())
        self.assertEqualDiff(w1.get_text('v-3'),
                'line 1\n')
        self.assertEqual({'v-3':('v-1',)}, w1.get_parent_map(['v-3']))
        ann = list(w1.annotate('v-3'))
        self.assertEqual(len(ann), 1)
        self.assertEqual(ann[0][0], 'v-1')
        self.assertEqual(ann[0][1], 'line 1\n')
        
    def build_weave1(self):
        weave1 = self.get_source()
        self.lines1 = ['hello\n']
        self.lines3 = ['hello\n', 'cruel\n', 'world\n']
        weave1.add_lines('v1', [], self.lines1)
        weave1.add_lines('v2', ['v1'], ['hello\n', 'world\n'])
        weave1.add_lines('v3', ['v2'], self.lines3)
        return weave1
        
    def test_join_with_empty(self):
        """Reweave adding empty weave"""
        wb = self.get_target()
        w1 = self.build_weave1()
        w1.join(wb)
        self.verify_weave1(w1)

    def verify_weave1(self, w1):
        self.assertEqual(sorted(w1.versions()), ['v1', 'v2', 'v3'])
        self.assertEqual(w1.get_lines('v1'), ['hello\n'])
        self.assertEqual({'v1':()}, w1.get_parent_map(['v1']))
        self.assertEqual(w1.get_lines('v2'), ['hello\n', 'world\n'])
        self.assertEqual({'v2':('v1',)}, w1.get_parent_map(['v2']))
        self.assertEqual(w1.get_lines('v3'), ['hello\n', 'cruel\n', 'world\n'])
        self.assertEqual({'v3':('v2',)}, w1.get_parent_map(['v3']))

    def test_join_source_has_less_parents_preserves_parents(self):
        # when the target has a text with more parent info, join 
        # preserves that.
        s = self.get_source()
        s.add_lines('base', [], [])
        s.add_lines('text', [], [])
        t = self.get_target()
        t.add_lines('base', [], [])
        t.add_lines('text', ['base'], [])
        t.join(s)
        self.assertEqual({'text':('base',)}, t.get_parent_map(['text']))

    def test_join_with_ghosts(self):
        """Join that inserts parents of an existing revision.

        This can happen when merging from another branch who knows about
        revisions the destination does not, and the destinations index is
        incorrect because it was or is using a ghost-unaware format to
        represent the index. In this test the second weave knows of an
        additional parent of v2.
        
        However v2 must not be changed because we consider indexes immutable:
        instead a check or reconcile operation locally should pickup that v2 is
        wrong and regenerate the index at a later time. So either this errors,
        or leaves v2 unaltered.
        """
        w1 = self.build_weave1()
        wb = self.get_target()
        wb.add_lines('x1', [], ['line from x1\n'])
        wb.add_lines('v1', [], ['hello\n'])
        wb.add_lines('v2', ['v1', 'x1'], ['hello\n', 'world\n'])
        try:
            w1.join(wb)
        except errors.WeaveParentMismatch:
            # Acceptable behaviour:
            return
        self.assertEqual(['v1', 'v2', 'v3', 'x1',], sorted(w1.versions()))
        self.assertEqual('line from x1\n', w1.get_text('x1'))
        self.assertEqual(['hello\n', 'world\n'], w1.get_lines('v2'))
        self.assertEqual({'v2':('v1',)}, w1.get_parent_map(['v2']))

    def test_join_with_ignore_missing_versions(self):
        # test that ignore_missing=True makes a listed but absent version id
        # be ignored, and that unlisted version_ids are not integrated.
        w1 = self.build_weave1()
        wb = self.get_target()
        wb.add_lines('x1', [], ['line from x1\n'])
        wb.add_lines('v1', [], ['hello\n'])
        wb.add_lines('v2', ['v1', 'x1'], ['hello\n', 'world\n'])
        w1.join(wb, version_ids=['x1', 'z1'], ignore_missing=True)
        eq = self.assertEquals
        eq(sorted(w1.versions()), ['v1', 'v2', 'v3', 'x1'])
        eq(w1.get_text('x1'), 'line from x1\n')
        eq(w1.get_lines('v2'), ['hello\n', 'world\n'])
        self.assertEqual({'v2':('v1',)}, w1.get_parent_map(['v2']))
    
    def build_source_weave(self, name, *pattern):
        w = self.get_source(name)
        for version, parents in pattern:
            w.add_lines(version, parents, [])
        return w

    def build_target_weave(self, name, *pattern):
        w = self.get_target(name)
        for version, parents in pattern:
            w.add_lines(version, parents, [])
        return w
        
    def test_joining_ghosts(self):
        # some versioned file formats allow lines to be added with parent
        # information that is > than that in the format. Formats that do
        # not support this need to raise NotImplementedError on the
        # add_lines_with_ghosts api.
        # files with ghost information when joined into a file which
        # supports that must preserve it, when joined into a file which
        # does not must discard it, and when filling a ghost for a listed
        # ghost must reconcile it
        source = self.get_source()
        target = self.get_target()
        # try filling target with ghosts and filling in reverse -  
        try:
            target.add_lines_with_ghosts('notbase', ['base'], [])
        except NotImplementedError:
            # The target does not support ghosts; the test is irrelevant.
            return
        try:
            source.join(target)
        except errors.RevisionNotPresent:
            return
        # legacy apis should behave
        self.assertEqual(['notbase'], source.get_ancestry(['notbase']))
        self.assertFalse(source.has_version('base'))
        # ghost data should have been preserved
        self.assertEqual(['base', 'notbase'], source.get_ancestry_with_ghosts(['notbase']))
        self.assertEqual(['base'], source.get_parents_with_ghosts('notbase'))
        self.assertEqual({'notbase':('base',)}, source.get_parent_map(['notbase']))
        self.assertEqual({'notbase':('base',)}, source.get_graph_with_ghosts())
        self.assertTrue(source.has_ghost('base'))

        # if we add something that is fills out what is a ghost, then 
        # when joining into a ghost aware join it should flesh out the ghosts.
        source.add_lines('base', [], [])
        target.join(source, version_ids=['base'])
        self.assertEqual(['base', 'notbase'], target.get_ancestry(['notbase']))
        self.assertEqual({'base':(),
                          'notbase':('base', ),
                          },
                         target.get_parent_map(target.versions()))
        self.assertTrue(target.has_version('base'))
        # we have _with_ghost apis to give us ghost information.
        self.assertEqual(['base', 'notbase'], target.get_ancestry_with_ghosts(['notbase']))
        self.assertEqual(['base'], target.get_parents_with_ghosts('notbase'))
        self.assertEqual({'base':(),
                          'notbase':('base',),
                          },
                         target.get_graph_with_ghosts())
        self.assertFalse(target.has_ghost('base'))

    def test_restricted_join_into_empty(self):
        # joining into an empty versioned file with a version_ids list
        # should only grab the selected versions.
        source = self.get_source()
        source.add_lines('skip_me', [], ['a\n'])
        source.add_lines('inherit_me', [], ['b\n'])
        source.add_lines('select_me', ['inherit_me'], ['b\n'])
        target = self.get_target()
        target.join(source, version_ids=['select_me'])
        self.assertEqual(['inherit_me', 'select_me'], target.versions())

    def test_join_odd_records(self):
        # this tests that joining the 1st, 3rd and 5th records and not the
        # 2nd and 4th works, particularly after reopening the file.
        # this test is designed to test versioned files that perform
        # optimisations on the join. Grabbing partial data and reopening the
        # file make it likely to trigger a fault.
        source = self.get_source()
        source.add_lines('1', [], ['1st\n'])
        source.add_lines('2', [], ['2nd\n'])
        source.add_lines('3', ['1'], ['1st\n', '2nd\n'])
        source.add_lines('4', ['2'], ['1st\n'])
        source.add_lines('5', ['3'], ['1st\n', '2nd\n', '3rd\n'])
        target = self.get_target()
        target.join(source, version_ids=['1', '3', '5'])
        target = self.get_target(create=False)
        self.assertEqual(set(['1', '3', '5']), set(target.versions()))
        self.assertEqual(3, len(target.versions()))
        self.assertEqual(['1st\n'], target.get_lines('1'))
        self.assertEqual(['1st\n', '2nd\n'], target.get_lines('3'))
        self.assertEqual(['1st\n', '2nd\n', '3rd\n'], target.get_lines('5'))
