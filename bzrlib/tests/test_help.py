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
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""Unit tests for the bzrlib.help module."""

from cStringIO import StringIO

from bzrlib import (
    commands,
    help,
    help_topics,
    tests,
    )


class TestCommandHelp(tests.TestCase):
    """Tests for help on commands."""

    def test_command_help_includes_see_also(self):
        class cmd_WithSeeAlso(commands.Command):
            """A sample command."""
            _see_also = ['foo', 'bar']
        cmd = cmd_WithSeeAlso()
        helpfile = StringIO()
        help.help_on_command_object(cmd, 'cmd_sample', helpfile)
        self.assertEndsWith(
            helpfile.getvalue(),
            '  -h, --help  show help message\n'
            '\n'
            'See also: bar, foo\n')


class TestTopicContext(tests.TestCase):
    """Tests for the HelpTopicContext class."""

    def test_default_constructable(self):
        context = help_topics.HelpTopicContext()


class TestCommandContext(tests.TestCase):
    """Tests for the HelpCommandContext class."""

    def test_default_constructable(self):
        context = commands.HelpCommandContext()


class TestHelpContexts(tests.TestCase):
    """Tests for the HelpContexts class."""

    def test_default_search_path(self):
        """The default search path should include internal contexts."""
        contexts = help.HelpContexts()
        self.assertEqual(2, len(contexts.search_path))
        # help topics should be searched in first.
        self.assertIsInstance(contexts.search_path[0],
            help_topics.HelpTopicContext)
        # with commands being search second.
        self.assertIsInstance(contexts.search_path[1],
            commands.HelpCommandContext)
