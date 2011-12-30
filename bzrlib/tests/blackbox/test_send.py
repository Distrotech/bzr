# Copyright (C) 2006-2011 Canonical Ltd
# Authors: Aaron Bentley
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


from cStringIO import StringIO

from bzrlib import (
    branch,
    bzrdir,
    merge_directive,
    tests,
    )
from bzrlib.bundle import serializer
from bzrlib.transport import memory
from bzrlib.tests import (
    scenarios,
    )
from bzrlib.tests.matchers import ContainsNoVfsCalls


load_tests = scenarios.load_tests_apply_scenarios


class TestSendMixin(object):

    _default_command = ['send', '-o-']
    _default_wd = 'branch'

    def run_send(self, args, cmd=None, rc=0, wd=None, err_re=None):
        if cmd is None: cmd = self._default_command
        if wd is None: wd = self._default_wd
        if err_re is None: err_re = []
        return self.run_bzr(cmd + args, retcode=rc,
                            working_dir=wd,
                            error_regexes=err_re)

    def get_MD(self, args, cmd=None, wd='branch'):
        out = StringIO(self.run_send(args, cmd=cmd, wd=wd)[0])
        return merge_directive.MergeDirective.from_lines(out)

    def assertBundleContains(self, revs, args, cmd=None, wd='branch'):
        md = self.get_MD(args, cmd=cmd, wd=wd)
        br = serializer.read_bundle(StringIO(md.get_raw_bundle()))
        self.assertEqual(set(revs), set(r.revision_id for r in br.revisions))


class TestSend(tests.TestCaseWithTransport, TestSendMixin):

    def setUp(self):
        super(TestSend, self).setUp()
        grandparent_tree = bzrdir.BzrDir.create_standalone_workingtree(
            'grandparent')
        self.build_tree_contents([('grandparent/file1', 'grandparent')])
        grandparent_tree.add('file1')
        grandparent_tree.commit('initial commit', rev_id='rev1')

        parent_bzrdir = grandparent_tree.bzrdir.sprout('parent')
        parent_tree = parent_bzrdir.open_workingtree()
        parent_tree.commit('next commit', rev_id='rev2')

        branch_tree = parent_tree.bzrdir.sprout('branch').open_workingtree()
        self.build_tree_contents([('branch/file1', 'branch')])
        branch_tree.commit('last commit', rev_id='rev3')

    def assertFormatIs(self, fmt_string, md):
        self.assertEqual(fmt_string, md.get_raw_bundle().splitlines()[0])

    def test_uses_parent(self):
        """Parent location is used as a basis by default"""
        errmsg = self.run_send([], rc=3, wd='grandparent')[1]
        self.assertContainsRe(errmsg, 'No submit branch known or specified')
        stdout, stderr = self.run_send([])
        self.assertEqual(stderr.count('Using saved parent location'), 1)
        self.assertBundleContains(['rev3'], [])

    def test_bundle(self):
        """Bundle works like send, except -o is not required"""
        errmsg = self.run_send([], cmd=['bundle'], rc=3, wd='grandparent')[1]
        self.assertContainsRe(errmsg, 'No submit branch known or specified')
        stdout, stderr = self.run_send([], cmd=['bundle'])
        self.assertEqual(stderr.count('Using saved parent location'), 1)
        self.assertBundleContains(['rev3'], [], cmd=['bundle'])

    def test_uses_submit(self):
        """Submit location can be used and set"""
        self.assertBundleContains(['rev3'], [])
        self.assertBundleContains(['rev3', 'rev2'], ['../grandparent'])
        # submit location should be auto-remembered
        self.assertBundleContains(['rev3', 'rev2'], [])

        self.run_send(['../parent'])
        # We still point to ../grandparent
        self.assertBundleContains(['rev3', 'rev2'], [])
        # Remember parent now
        self.run_send(['../parent', '--remember'])
        # Now we point to parent
        self.assertBundleContains(['rev3'], [])

        err = self.run_send(['--remember'], rc=3)[1]
        self.assertContainsRe(err,
                              '--remember requires a branch to be specified.')

    def test_revision_branch_interaction(self):
        self.assertBundleContains(['rev3', 'rev2'], ['../grandparent'])
        self.assertBundleContains(['rev2'], ['../grandparent', '-r-2'])
        self.assertBundleContains(['rev3', 'rev2'],
                                  ['../grandparent', '-r-2..-1'])
        md = self.get_MD(['-r-2..-1'])
        self.assertEqual('rev2', md.base_revision_id)
        self.assertEqual('rev3', md.revision_id)

    def test_output(self):
        # check output for consistency
        # win32 stdout converts LF to CRLF,
        # which would break patch-based bundles
        self.assertBundleContains(['rev3'], [])

    def test_no_common_ancestor(self):
        foo = self.make_branch_and_tree('foo')
        foo.commit('rev a')
        bar = self.make_branch_and_tree('bar')
        bar.commit('rev b')
        self.run_send(['--from', 'foo', '../bar'], wd='foo')

    def test_content_options(self):
        """--no-patch and --no-bundle should work and be independant"""
        md = self.get_MD([])
        self.assertIsNot(None, md.bundle)
        self.assertIsNot(None, md.patch)

        md = self.get_MD(['--format=0.9'])
        self.assertIsNot(None, md.bundle)
        self.assertIsNot(None, md.patch)

        md = self.get_MD(['--no-patch'])
        self.assertIsNot(None, md.bundle)
        self.assertIs(None, md.patch)
        self.run_bzr_error(['Format 0.9 does not permit bundle with no patch'],
                           ['send', '--no-patch', '--format=0.9', '-o-'],
                           working_dir='branch')
        md = self.get_MD(['--no-bundle', '.', '.'])
        self.assertIs(None, md.bundle)
        self.assertIsNot(None, md.patch)

        md = self.get_MD(['--no-bundle', '--format=0.9', '../parent',
                                  '.'])
        self.assertIs(None, md.bundle)
        self.assertIsNot(None, md.patch)

        md = self.get_MD(['--no-bundle', '--no-patch', '.', '.'])
        self.assertIs(None, md.bundle)
        self.assertIs(None, md.patch)

        md = self.get_MD(['--no-bundle', '--no-patch', '--format=0.9',
                          '../parent', '.'])
        self.assertIs(None, md.bundle)
        self.assertIs(None, md.patch)

    def test_from_option(self):
        self.run_bzr('send', retcode=3)
        md = self.get_MD(['--from', 'branch'])
        self.assertEqual('rev3', md.revision_id)
        md = self.get_MD(['-f', 'branch'])
        self.assertEqual('rev3', md.revision_id)

    def test_output_option(self):
        stdout = self.run_bzr('send -f branch --output file1')[0]
        self.assertEqual('', stdout)
        md_file = open('file1', 'rb')
        self.addCleanup(md_file.close)
        self.assertContainsRe(md_file.read(), 'rev3')
        stdout = self.run_bzr('send -f branch --output -')[0]
        self.assertContainsRe(stdout, 'rev3')

    def test_note_revisions(self):
        stderr = self.run_send([])[1]
        self.assertEndsWith(stderr, '\nBundling 1 revision.\n')

    def test_mailto_option(self):
        b = branch.Branch.open('branch')
        b.get_config().set_user_option('mail_client', 'editor')
        self.run_bzr_error(
            ('No mail-to address \\(--mail-to\\) or output \\(-o\\) specified',
            ), 'send -f branch')
        b.get_config().set_user_option('mail_client', 'bogus')
        self.run_send([])
        self.run_bzr_error(('Unknown mail client: bogus',),
                           'send -f branch --mail-to jrandom@example.org')
        b.get_config().set_user_option('submit_to', 'jrandom@example.org')
        self.run_bzr_error(('Unknown mail client: bogus',),
                           'send -f branch')

    def test_mailto_child_option(self):
        """Make sure that child_submit_to is used."""
        b = branch.Branch.open('branch')
        b.get_config().set_user_option('mail_client', 'bogus')
        parent = branch.Branch.open('parent')
        parent.get_config().set_user_option('child_submit_to',
                           'somebody@example.org')
        self.run_bzr_error(('Unknown mail client: bogus',),
                           'send -f branch')

    def test_format(self):
        md = self.get_MD(['--format=4'])
        self.assertIs(merge_directive.MergeDirective2, md.__class__)
        self.assertFormatIs('# Bazaar revision bundle v4', md)

        md = self.get_MD(['--format=0.9'])
        self.assertFormatIs('# Bazaar revision bundle v0.9', md)

        md = self.get_MD(['--format=0.9'], cmd=['bundle'])
        self.assertFormatIs('# Bazaar revision bundle v0.9', md)
        self.assertIs(merge_directive.MergeDirective, md.__class__)

        self.run_bzr_error(['Bad value .* for option .format.'],
                            'send -f branch -o- --format=0.999')[0]

    def test_format_child_option(self):
        parent_config = branch.Branch.open('parent').get_config()
        parent_config.set_user_option('child_submit_format', '4')
        md = self.get_MD([])
        self.assertIs(merge_directive.MergeDirective2, md.__class__)

        parent_config.set_user_option('child_submit_format', '0.9')
        md = self.get_MD([])
        self.assertFormatIs('# Bazaar revision bundle v0.9', md)

        md = self.get_MD([], cmd=['bundle'])
        self.assertFormatIs('# Bazaar revision bundle v0.9', md)
        self.assertIs(merge_directive.MergeDirective, md.__class__)

        parent_config.set_user_option('child_submit_format', '0.999')
        self.run_bzr_error(["No such send format '0.999'"],
                            'send -f branch -o-')[0]

    def test_message_option(self):
        self.run_bzr('send', retcode=3)
        md = self.get_MD([])
        self.assertIs(None, md.message)
        md = self.get_MD(['-m', 'my message'])
        self.assertEqual('my message', md.message)

    def test_omitted_revision(self):
        md = self.get_MD(['-r-2..'])
        self.assertEqual('rev2', md.base_revision_id)
        self.assertEqual('rev3', md.revision_id)
        md = self.get_MD(['-r..3', '--from', 'branch', 'grandparent'], wd='.')
        self.assertEqual('rev1', md.base_revision_id)
        self.assertEqual('rev3', md.revision_id)

    def test_nonexistant_branch(self):
        self.vfs_transport_factory = memory.MemoryServer
        location = self.get_url('absentdir/')
        out, err = self.run_bzr(["send", "--from", location], retcode=3)
        self.assertEqual(out, '')
        self.assertEqual(err, 'bzr: ERROR: Not a branch: "%s".\n' % location)


class TestSendStrictMixin(TestSendMixin):

    def make_parent_and_local_branches(self):
        # Create a 'parent' branch as the base
        self.parent_tree = bzrdir.BzrDir.create_standalone_workingtree('parent')
        self.build_tree_contents([('parent/file', 'parent')])
        self.parent_tree.add('file')
        self.parent_tree.commit('first commit', rev_id='parent')
        # Branch 'local' from parent and do a change
        local_bzrdir = self.parent_tree.bzrdir.sprout('local')
        self.local_tree = local_bzrdir.open_workingtree()
        self.build_tree_contents([('local/file', 'local')])
        self.local_tree.commit('second commit', rev_id='local')

    _default_command = ['send', '-o-', '../parent']
    _default_wd = 'local'
    _default_sent_revs = ['local']
    _default_errors = ['Working tree ".*/local/" has uncommitted '
                       'changes \(See bzr status\)\.',]
    _default_additional_error = 'Use --no-strict to force the send.\n'
    _default_additional_warning = 'Uncommitted changes will not be sent.'

    def set_config_send_strict(self, value):
        # set config var (any of bazaar.conf, locations.conf, branch.conf
        # should do)
        conf = self.local_tree.branch.get_config_stack()
        conf.set('send_strict', value)

    def assertSendFails(self, args):
        out, err = self.run_send(args, rc=3, err_re=self._default_errors)
        self.assertContainsRe(err, self._default_additional_error)

    def assertSendSucceeds(self, args, revs=None, with_warning=False):
        if with_warning:
            err_re = self._default_errors
        else:
            err_re = []
        if revs is None:
            revs = self._default_sent_revs
        out, err = self.run_send(args, err_re=err_re)
        if len(revs) == 1:
            bundling_revs = 'Bundling %d revision.\n'% len(revs)
        else:
            bundling_revs = 'Bundling %d revisions.\n' % len(revs)
        if with_warning:
            self.assertContainsRe(err, self._default_additional_warning)
            self.assertEndsWith(err, bundling_revs)
        else:
            self.assertEquals(bundling_revs, err)
        md = merge_directive.MergeDirective.from_lines(StringIO(out))
        self.assertEqual('parent', md.base_revision_id)
        br = serializer.read_bundle(StringIO(md.get_raw_bundle()))
        self.assertEqual(set(revs), set(r.revision_id for r in br.revisions))


class TestSendStrictWithoutChanges(tests.TestCaseWithTransport,
                                   TestSendStrictMixin):

    def setUp(self):
        super(TestSendStrictWithoutChanges, self).setUp()
        self.make_parent_and_local_branches()

    def test_send_default(self):
        self.assertSendSucceeds([])

    def test_send_strict(self):
        self.assertSendSucceeds(['--strict'])

    def test_send_no_strict(self):
        self.assertSendSucceeds(['--no-strict'])

    def test_send_config_var_strict(self):
        self.set_config_send_strict('true')
        self.assertSendSucceeds([])

    def test_send_config_var_no_strict(self):
        self.set_config_send_strict('false')
        self.assertSendSucceeds([])


class TestSendStrictWithChanges(tests.TestCaseWithTransport,
                                TestSendStrictMixin):

    # These are textually the same as test_push.strict_push_change_scenarios,
    # but since the functions are reimplemented here, the definitions are left
    # here too.
    scenarios = [
        ('uncommitted',
         dict(_changes_type='_uncommitted_changes')),
        ('pending_merges',
         dict(_changes_type='_pending_merges')),
        ('out-of-sync-trees',
         dict(_changes_type='_out_of_sync_trees')),
        ]

    _changes_type = None # Set by load_tests

    def setUp(self):
        super(TestSendStrictWithChanges, self).setUp()
        # load tests set _changes_types to the name of the method we want to
        # call now
        do_changes_func = getattr(self, self._changes_type)
        do_changes_func()

    def _uncommitted_changes(self):
        self.make_parent_and_local_branches()
        # Make a change without committing it
        self.build_tree_contents([('local/file', 'modified')])

    def _pending_merges(self):
        self.make_parent_and_local_branches()
        # Create 'other' branch containing a new file
        other_bzrdir = self.parent_tree.bzrdir.sprout('other')
        other_tree = other_bzrdir.open_workingtree()
        self.build_tree_contents([('other/other-file', 'other')])
        other_tree.add('other-file')
        other_tree.commit('other commit', rev_id='other')
        # Merge and revert, leaving a pending merge
        self.local_tree.merge_from_branch(other_tree.branch)
        self.local_tree.revert(filenames=['other-file'], backups=False)

    def _out_of_sync_trees(self):
        self.make_parent_and_local_branches()
        self.run_bzr(['checkout', '--lightweight', 'local', 'checkout'])
        # Make a change and commit it
        self.build_tree_contents([('local/file', 'modified in local')])
        self.local_tree.commit('modify file', rev_id='modified-in-local')
        # Exercise commands from the checkout directory
        self._default_wd = 'checkout'
        self._default_errors = ["Working tree is out of date, please run"
                                " 'bzr update'\.",]
        self._default_sent_revs = ['modified-in-local', 'local']

    def test_send_default(self):
        self.assertSendSucceeds([], with_warning=True)

    def test_send_with_revision(self):
        self.assertSendSucceeds(['-r', 'revid:local'], revs=['local'])

    def test_send_no_strict(self):
        self.assertSendSucceeds(['--no-strict'])

    def test_send_strict_with_changes(self):
        self.assertSendFails(['--strict'])

    def test_send_respect_config_var_strict(self):
        self.set_config_send_strict('true')
        self.assertSendFails([])
        self.assertSendSucceeds(['--no-strict'])

    def test_send_bogus_config_var_ignored(self):
        self.set_config_send_strict("I'm unsure")
        self.assertSendSucceeds([], with_warning=True)

    def test_send_no_strict_command_line_override_config(self):
        self.set_config_send_strict('true')
        self.assertSendFails([])
        self.assertSendSucceeds(['--no-strict'])

    def test_send_strict_command_line_override_config(self):
        self.set_config_send_strict('false')
        self.assertSendSucceeds([])
        self.assertSendFails(['--strict'])


class TestBundleStrictWithoutChanges(TestSendStrictWithoutChanges):

    _default_command = ['bundle-revisions', '../parent']


class TestSmartServerSend(tests.TestCaseWithTransport):

    def test_send(self):
        self.setup_smart_server_with_call_log()
        t = self.make_branch_and_tree('branch')
        self.build_tree_contents([('branch/foo', 'thecontents')])
        t.add("foo")
        t.commit("message")
        local = t.bzrdir.sprout('local-branch').open_workingtree()
        self.build_tree_contents([('branch/foo', 'thenewcontents')])
        local.commit("anothermessage")
        self.reset_smart_call_log()
        out, err = self.run_bzr(
            ['send', '-o', 'x.diff', self.get_url('branch')], working_dir='local-branch')
        # This figure represent the amount of work to perform this use case. It
        # is entirely ok to reduce this number if a test fails due to rpc_count
        # being too low. If rpc_count increases, more network roundtrips have
        # become necessary for this use case. Please do not adjust this number
        # upwards without agreement from bzr's network support maintainers.
        self.assertLength(9, self.hpss_calls)
        self.assertLength(1, self.hpss_connections)
        self.assertThat(self.hpss_calls, ContainsNoVfsCalls)
