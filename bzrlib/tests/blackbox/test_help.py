# Copyright (C) 2006, 2007, 2009, 2010 Canonical Ltd
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


"""Black-box tests for bzr help.
"""


import bzrlib
from bzrlib import config
from bzrlib.tests import TestCaseWithTransport


class TestHelp(TestCaseWithTransport):

    def test_help_basic(self):
        for cmd in ['--help', 'help', '-h', '-?']:
            output = self.run_bzr(cmd)[0]
            line1 = output.split('\n')[0]
            if not line1.startswith('Bazaar'):
                self.fail("bad output from bzr %s:\n%r" % (cmd, output))
        # see https://launchpad.net/products/bzr/+bug/35940, -h doesn't work

    def test_help_topics(self):
        """Smoketest for 'bzr help topics'"""
        out, err = self.run_bzr('help topics')
        self.assertContainsRe(out, 'basic')
        self.assertContainsRe(out, 'topics')
        self.assertContainsRe(out, 'commands')
        self.assertContainsRe(out, 'revisionspec')

    def test_help_revisionspec(self):
        """Smoke test for 'bzr help revisionspec'"""
        out, err = self.run_bzr('help revisionspec')
        self.assertContainsRe(out, 'revno:')
        self.assertContainsRe(out, 'date:')
        self.assertContainsRe(out, 'revid:')
        self.assertContainsRe(out, 'last:')
        self.assertContainsRe(out, 'before:')
        self.assertContainsRe(out, 'ancestor:')
        self.assertContainsRe(out, 'branch:')

    def test_help_checkouts(self):
        """Smoke test for 'bzr help checkouts'"""
        out, err = self.run_bzr('help checkouts')
        self.assertContainsRe(out, 'checkout')
        self.assertContainsRe(out, 'lightweight')

    def test_help_urlspec(self):
        """Smoke test for 'bzr help urlspec'"""
        out, err = self.run_bzr('help urlspec')
        self.assertContainsRe(out, 'aftp://')
        self.assertContainsRe(out, 'bzr://')
        self.assertContainsRe(out, 'bzr\+ssh://')
        self.assertContainsRe(out, 'file://')
        self.assertContainsRe(out, 'ftp://')
        self.assertContainsRe(out, 'http://')
        self.assertContainsRe(out, 'https://')
        self.assertContainsRe(out, 'sftp://')

    def test_help_repositories(self):
        """Smoke test for 'bzr help repositories'"""
        out, err = self.run_bzr('help repositories')
        from bzrlib.help_topics import help_as_plain_text, _repositories
        expected = help_as_plain_text(_repositories)
        self.assertEqual(expected, out)

    def test_help_working_trees(self):
        """Smoke test for 'bzr help working-trees'"""
        out, err = self.run_bzr('help working-trees')
        from bzrlib.help_topics import help_as_plain_text, _working_trees
        expected = help_as_plain_text(_working_trees)
        self.assertEqual(expected, out)

    def test_help_status_flags(self):
        """Smoke test for 'bzr help status-flags'"""
        out, err = self.run_bzr('help status-flags')
        from bzrlib.help_topics import help_as_plain_text, _status_flags
        expected = help_as_plain_text(_status_flags)
        self.assertEqual(expected, out)

    def test_help_commands(self):
        dash_help  = self.run_bzr('--help commands')[0]
        commands   = self.run_bzr('help commands')[0]
        hidden = self.run_bzr('help hidden-commands')[0]
        long_help  = self.run_bzr('help --long')[0]
        qmark_long = self.run_bzr('? --long')[0]
        qmark_cmds = self.run_bzr('? commands')[0]
        self.assertEquals(dash_help, commands)
        self.assertEquals(dash_help, long_help)
        self.assertEquals(dash_help, qmark_long)
        self.assertEquals(dash_help, qmark_cmds)

    def test_hidden(self):
        help_commands = self.run_bzr('help commands')[0]
        help_hidden = self.run_bzr('help hidden-commands')[0]

        def extract_cmd_names(help_output):
            # keep only the command names to avoid matching on help text (there
            # is a high risk to fail a test when a plugin get installed
            # otherwise)
            cmds = []
            for line in help_output.split('\n'):
                if line.startswith(' '):
                    continue # help on more than one line
                cmd = line.split(' ')[0]
                if line:
                    cmds.append(cmd)
            return cmds
        commands = extract_cmd_names(help_commands)
        hidden = extract_cmd_names(help_hidden)
        self.assertTrue('commit' in commands)
        self.assertTrue('commit' not in hidden)
        self.assertTrue('rocks' in hidden)
        self.assertTrue('rocks' not in commands)

    def test_help_detail(self):
        dash_h  = self.run_bzr('diff -h')[0]
        help_x  = self.run_bzr('help diff')[0]
        self.assertEquals(dash_h, help_x)
        self.assertContainsRe(help_x, "Purpose:")
        self.assertContainsRe(help_x, "Usage:")
        self.assertContainsRe(help_x, "Options:")
        self.assertContainsRe(help_x, "Description:")
        self.assertContainsRe(help_x, "Examples:")
        self.assertContainsRe(help_x, "See also:")
        self.assertContainsRe(help_x, "Aliases:")

    def test_help_usage(self):
        usage  = self.run_bzr('diff --usage')[0]
        self.assertContainsRe(usage, "Purpose:")
        self.assertContainsRe(usage, "Usage:")
        self.assertContainsRe(usage, "Options:")
        self.assertNotContainsRe(usage, "Description:")
        self.assertNotContainsRe(usage, "Examples:")
        self.assertContainsRe(usage, "See also:")
        self.assertContainsRe(usage, "Aliases:")

    def test_help_help(self):
        help = self.run_bzr('help help')[0]
        qmark = self.run_bzr('? ?')[0]
        self.assertEquals(help, qmark)
        for line in help.split('\n'):
            if '--long' in line:
                self.assertContainsRe(line,
                    r'Show help on all commands\.')

    def test_help_with_aliases(self):
        original = self.run_bzr('help cat')[0]

        content = '''[ALIASES]
c=cat
cat=cat
'''
        conf = config.GlobalConfig(_content=content)
        conf._write_config_file()

        expected = original + "'bzr cat' is an alias for 'bzr cat'.\n"
        self.assertEqual(expected, self.run_bzr('help cat')[0])

        self.assertEqual("'bzr c' is an alias for 'bzr cat'.\n",
                         self.run_bzr('help c')[0])
