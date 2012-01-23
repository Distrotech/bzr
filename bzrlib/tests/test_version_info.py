# Copyright (C) 2005-2011 Canonical Ltd
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

"""Tests for version_info"""

from cStringIO import StringIO
import imp
import os
import sys

from bzrlib import (
    errors,
    registry,
    tests,
    version_info_formats,
    )
from bzrlib.tests import TestCaseWithTransport
from bzrlib.rio import read_stanzas

from bzrlib.version_info_formats.format_custom import CustomVersionInfoBuilder
from bzrlib.version_info_formats.format_rio import RioVersionInfoBuilder
from bzrlib.version_info_formats.format_python import PythonVersionInfoBuilder


class VersionInfoTestCase(TestCaseWithTransport):

    def create_branch(self):
        wt = self.make_branch_and_tree('branch')

        self.build_tree(['branch/a'])
        wt.add('a')
        wt.commit('a', rev_id='r1')

        self.build_tree(['branch/b'])
        wt.add('b')
        wt.commit('b', rev_id='r2')

        self.build_tree_contents([('branch/a', 'new contents\n')])
        wt.commit(u'\xe52', rev_id='r3')

        return wt

    def create_tree_with_dotted_revno(self):
        wt = self.make_branch_and_tree('branch')
        self.build_tree(['branch/a'])
        wt.add('a')
        wt.commit('a', rev_id='r1')

        other = wt.bzrdir.sprout('other').open_workingtree()
        self.build_tree(['other/b.a'])
        other.add(['b.a'])
        other.commit('b.a', rev_id='o2')

        os.chdir('branch')
        self.run_bzr('merge ../other')
        wt.commit('merge', rev_id='merge')

        wt.update(revision='o2')

        return wt


class TestVersionInfoRio(VersionInfoTestCase):

    def test_rio_null(self):
        wt = self.make_branch_and_tree('branch')

        sio = StringIO()
        builder = RioVersionInfoBuilder(wt.branch, working_tree=wt)
        builder.generate(sio)
        val = sio.getvalue()
        self.assertContainsRe(val, 'build-date:')
        self.assertContainsRe(val, 'revno: 0')

    def test_rio_dotted_revno(self):
        wt = self.create_tree_with_dotted_revno()

        sio = StringIO()
        builder = RioVersionInfoBuilder(wt.branch, working_tree=wt)
        builder.generate(sio)
        val = sio.getvalue()
        self.assertContainsRe(val, 'revno: 1.1.1')

    def regen_text(self, wt, **kwargs):
        sio = StringIO()
        builder = RioVersionInfoBuilder(wt.branch, working_tree=wt, **kwargs)
        builder.generate(sio)
        val = sio.getvalue()
        return val

    def test_simple(self):
        wt = self.create_branch()

        val = self.regen_text(wt)
        self.assertContainsRe(val, 'build-date:')
        self.assertContainsRe(val, 'date:')
        self.assertContainsRe(val, 'revno: 3')
        self.assertContainsRe(val, 'revision-id: r3')

    def test_clean(self):
        wt = self.create_branch()
        val = self.regen_text(wt, check_for_clean=True)
        self.assertContainsRe(val, 'clean: True')

    def test_no_clean(self):
        wt = self.create_branch()
        self.build_tree(['branch/c'])
        val = self.regen_text(wt, check_for_clean=True)
        self.assertContainsRe(val, 'clean: False')

    def test_history(self):
        wt = self.create_branch()

        val = self.regen_text(wt, include_revision_history=True)
        self.assertContainsRe(val, 'id: r1')
        self.assertContainsRe(val, 'message: a')
        self.assertContainsRe(val, 'id: r2')
        self.assertContainsRe(val, 'message: b')
        self.assertContainsRe(val, 'id: r3')
        self.assertContainsRe(val, 'message: \xc3\xa52') # utf8 encoding '\xe5'

    def regen(self, wt, **kwargs):
        sio = StringIO()
        builder = RioVersionInfoBuilder(wt.branch, working_tree=wt, **kwargs)
        builder.generate(sio)
        sio.seek(0)
        stanzas = list(read_stanzas(sio))
        self.assertEqual(1, len(stanzas))
        return stanzas[0]

    def test_rio_version_hook(self):
        def update_stanza(rev, stanza):
            stanza.add('bla', 'bloe')
        RioVersionInfoBuilder.hooks.install_named_hook(
            'revision', update_stanza, None)
        wt = self.create_branch()

        stanza = self.regen(wt)
        self.assertEqual(['bloe'], stanza.get_all('bla'))

    def get_one_stanza(self, stanza, key):
        new_stanzas = list(read_stanzas(StringIO(stanza[key].encode('utf8'))))
        self.assertEqual(1, len(new_stanzas))
        return new_stanzas[0]

    def test_build_date(self):
        wt = self.create_branch()
        stanza = self.regen(wt)
        self.assertTrue('date' in stanza)
        self.assertTrue('build-date' in stanza)
        self.assertEqual(['3'], stanza.get_all('revno'))
        self.assertEqual(['r3'], stanza.get_all('revision-id'))

    def test_not_clean(self):
        wt = self.create_branch()
        self.build_tree(['branch/c'])
        stanza = self.regen(wt, check_for_clean=True, include_file_revisions=True)
        self.assertEqual(['False'], stanza.get_all('clean'))

    def test_file_revisions(self):
        wt = self.create_branch()
        self.build_tree(['branch/c'])
        stanza = self.regen(wt, check_for_clean=True, include_file_revisions=True)
        # This assumes it's being run against a tree that does not update the
        # root revision on every commit.
        file_rev_stanza = self.get_one_stanza(stanza, 'file-revisions')
        self.assertEqual(['', 'a', 'b', 'c'], file_rev_stanza.get_all('path'))
        self.assertEqual(['r1', 'r3', 'r2', 'unversioned'],
            file_rev_stanza.get_all('revision'))

    def test_revision_history(self):
        wt = self.create_branch()
        stanza = self.regen(wt, include_revision_history=True)
        revision_stanza = self.get_one_stanza(stanza, 'revisions')
        self.assertEqual(['r1', 'r2', 'r3'], revision_stanza.get_all('id'))
        self.assertEqual(['a', 'b', u'\xe52'], revision_stanza.get_all('message'))
        self.assertEqual(3, len(revision_stanza.get_all('date')))

    def test_file_revisions_with_rename(self):
        # a was modified, so it should show up modified again
        wt = self.create_branch()
        self.build_tree(['branch/a', 'branch/c'])
        wt.add('c')
        wt.rename_one('b', 'd')
        stanza = self.regen(wt, check_for_clean=True, include_file_revisions=True)
        file_rev_stanza = self.get_one_stanza(stanza, 'file-revisions')
        self.assertEqual(['', 'a', 'b', 'c', 'd'],
                          file_rev_stanza.get_all('path'))
        self.assertEqual(['r1', 'modified', 'renamed to d', 'new',
                          'renamed from b'],
                         file_rev_stanza.get_all('revision'))

    def test_file_revisions_with_removal(self):
        wt = self.create_branch()
        self.build_tree(['branch/a', 'branch/c'])
        wt.add('c')
        wt.rename_one('b', 'd')

        wt.commit('modified', rev_id='r4')

        wt.remove(['c', 'd'])
        os.remove('branch/d')
        stanza = self.regen(wt, check_for_clean=True, include_file_revisions=True)
        file_rev_stanza = self.get_one_stanza(stanza, 'file-revisions')
        self.assertEqual(['', 'a', 'c', 'd'], file_rev_stanza.get_all('path'))
        self.assertEqual(['r1', 'r4', 'unversioned', 'removed'],
                         file_rev_stanza.get_all('revision'))

    def test_revision(self):
        wt = self.create_branch()
        self.build_tree(['branch/a', 'branch/c'])
        wt.add('c')
        wt.rename_one('b', 'd')

        stanza = self.regen(wt, check_for_clean=True,
            include_file_revisions=True, revision_id=wt.last_revision())
        file_rev_stanza = self.get_one_stanza(stanza, 'file-revisions')
        self.assertEqual(['', 'a', 'b'], file_rev_stanza.get_all('path'))
        self.assertEqual(['r1', 'r3', 'r2'],
                         file_rev_stanza.get_all('revision'))


class PythonVersionInfoTests(VersionInfoTestCase):

    def test_python_null(self):
        wt = self.make_branch_and_tree('branch')

        sio = StringIO()
        builder = PythonVersionInfoBuilder(wt.branch, working_tree=wt)
        builder.generate(sio)
        val = sio.getvalue()
        self.assertContainsRe(val, "'revision_id': None")
        self.assertContainsRe(val, "'revno': '0'")
        self.assertNotContainsString(val, '\n\n\n\n')

    def test_python_dotted_revno(self):
        wt = self.create_tree_with_dotted_revno()

        sio = StringIO()
        builder = PythonVersionInfoBuilder(wt.branch, working_tree=wt)
        builder.generate(sio)
        val = sio.getvalue()
        self.assertContainsRe(val, "'revno': '1.1.1'")

    def regen(self, wt, **kwargs):
        """Create a test module, import and return it"""
        outf = open('test_version_information.py', 'wb')
        try:
            builder = PythonVersionInfoBuilder(wt.branch, working_tree=wt,
                                               **kwargs)
            builder.generate(outf)
        finally:
            outf.close()
        module_info = imp.find_module('test_version_information',
                                      [os.getcwdu()])
        tvi = imp.load_module('tvi', *module_info)
        # Make sure the module isn't cached
        sys.modules.pop('tvi', None)
        sys.modules.pop('test_version_information', None)
        # Delete the compiled versions, because we are generating
        # a new file fast enough that python doesn't detect it
        # needs to recompile, and using sleep() just makes the
        # test slow
        if os.path.exists('test_version_information.pyc'):
            os.remove('test_version_information.pyc')
        if os.path.exists('test_version_information.pyo'):
            os.remove('test_version_information.pyo')
        return tvi

    def test_python_version(self):
        wt = self.create_branch()

        tvi = self.regen(wt)
        self.assertEqual('3', tvi.version_info['revno'])
        self.assertEqual('r3', tvi.version_info['revision_id'])
        self.assertTrue(tvi.version_info.has_key('date'))
        self.assertEqual(None, tvi.version_info['clean'])

        tvi = self.regen(wt, check_for_clean=True)
        self.assertEqual(True, tvi.version_info['clean'])

        self.build_tree(['branch/c'])
        tvi = self.regen(wt, check_for_clean=True, include_file_revisions=True)
        self.assertEqual(False, tvi.version_info['clean'])
        self.assertEqual(['', 'a', 'b', 'c'],
                         sorted(tvi.file_revisions.keys()))
        self.assertEqual('r3', tvi.file_revisions['a'])
        self.assertEqual('r2', tvi.file_revisions['b'])
        self.assertEqual('unversioned', tvi.file_revisions['c'])
        os.remove('branch/c')

        tvi = self.regen(wt, include_revision_history=True)

        rev_info = [(rev, message) for rev, message, timestamp, timezone
                                   in tvi.revisions]
        self.assertEqual([('r1', 'a'), ('r2', 'b'), ('r3', u'\xe52')], rev_info)

        # a was modified, so it should show up modified again
        self.build_tree(['branch/a', 'branch/c'])
        wt.add('c')
        wt.rename_one('b', 'd')
        tvi = self.regen(wt, check_for_clean=True, include_file_revisions=True)
        self.assertEqual(['', 'a', 'b', 'c', 'd'],
                          sorted(tvi.file_revisions.keys()))
        self.assertEqual('modified', tvi.file_revisions['a'])
        self.assertEqual('renamed to d', tvi.file_revisions['b'])
        self.assertEqual('new', tvi.file_revisions['c'])
        self.assertEqual('renamed from b', tvi.file_revisions['d'])

        wt.commit('modified', rev_id='r4')
        wt.remove(['c', 'd'])
        os.remove('branch/d')
        tvi = self.regen(wt, check_for_clean=True, include_file_revisions=True)
        self.assertEqual(['', 'a', 'c', 'd'],
                          sorted(tvi.file_revisions.keys()))
        self.assertEqual('r4', tvi.file_revisions['a'])
        self.assertEqual('unversioned', tvi.file_revisions['c'])
        self.assertEqual('removed', tvi.file_revisions['d'])


class CustomVersionInfoTests(VersionInfoTestCase):

    def test_custom_null(self):
        sio = StringIO()
        wt = self.make_branch_and_tree('branch')
        builder = CustomVersionInfoBuilder(wt.branch, working_tree=wt,
            template='revno: {revno}')
        builder.generate(sio)
        self.assertEquals("revno: 0", sio.getvalue())

        builder = CustomVersionInfoBuilder(wt.branch, working_tree=wt, 
            template='{revno} revid: {revision_id}')
        # revision_id is not available yet
        self.assertRaises(errors.MissingTemplateVariable, 
            builder.generate, sio)

    def test_custom_dotted_revno(self):
        sio = StringIO()
        wt = self.create_tree_with_dotted_revno()
        builder = CustomVersionInfoBuilder(wt.branch, working_tree=wt, 
            template='{revno} revid: {revision_id}')
        builder.generate(sio)
        self.assertEquals("1.1.1 revid: o2", sio.getvalue())

    def regen(self, wt, tpl, **kwargs):
        sio = StringIO()
        builder = CustomVersionInfoBuilder(wt.branch, working_tree=wt,
                                           template=tpl, **kwargs)
        builder.generate(sio)
        val = sio.getvalue()
        return val

    def test_build_date(self):
        wt = self.create_branch()

        val = self.regen(wt, 'build-date: "{build_date}"\ndate: "{date}"')
        self.assertContainsRe(val, 'build-date: "[0-9-+: ]+"')
        self.assertContainsRe(val, 'date: "[0-9-+: ]+"')

    def test_revno(self):
        wt = self.create_branch()
        val = self.regen(wt, 'revno: {revno}')
        self.assertEqual(val, 'revno: 3')

    def test_revision_id(self):
        wt = self.create_branch()
        val = self.regen(wt, 'revision-id: {revision_id}')
        self.assertEqual(val, 'revision-id: r3')

    def test_clean(self):
        wt = self.create_branch()
        val = self.regen(wt, 'clean: {clean}', check_for_clean=True)
        self.assertEqual(val, 'clean: 1')

    def test_not_clean(self):
        wt = self.create_branch()

        self.build_tree(['branch/c'])
        val = self.regen(wt, 'clean: {clean}', check_for_clean=True)
        self.assertEqual(val, 'clean: 0')
        os.remove('branch/c')

    def test_custom_without_template(self):
        builder = CustomVersionInfoBuilder(None)
        sio = StringIO()
        self.assertRaises(errors.NoTemplate, builder.generate, sio)


class TestBuilder(version_info_formats.VersionInfoBuilder):
    pass


class TestVersionInfoFormatRegistry(tests.TestCase):

    def setUp(self):
        super(TestVersionInfoFormatRegistry, self).setUp()
        self.overrideAttr(version_info_formats,
                          'format_registry', registry.Registry())

    def test_register_remove(self):
        registry = version_info_formats.format_registry
        registry.register('testbuilder',
            TestBuilder, 'a simple test builder')
        self.assertIs(TestBuilder, registry.get('testbuilder'))
        self.assertEqual('a simple test builder',
                         registry.get_help('testbuilder'))
        registry.remove('testbuilder')
        self.assertRaises(KeyError, registry.get, 'testbuilder')
