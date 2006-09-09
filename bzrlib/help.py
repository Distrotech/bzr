# Copyright (C) 2004, 2005, 2006 by Canonical Ltd
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

# TODO: Some way to get a list of external commands (defined by shell
# scripts) so that they can be included in the help listing as well.
# It should be enough to just list the plugin directory and look for
# executable files with reasonable names.

# TODO: `help commands --all` should show hidden commands
import textwrap

global_help = \
"""Bazaar -- a free distributed version-control tool
http://bazaar-vcs.org/

Basic commands:

  bzr init           makes this directory a versioned branch
  bzr branch         make a copy of another branch

  bzr add            make files or directories versioned
  bzr ignore         ignore a file or pattern
  bzr mv             move or rename a versioned file

  bzr status         summarize changes in working copy
  bzr diff           show detailed diffs

  bzr merge          pull in changes from another branch
  bzr commit         save some or all changes

  bzr log            show history of changes
  bzr check          validate storage

  bzr help init      more help on e.g. init command
  bzr help commands  list all commands
"""


import sys
from bzrlib.help_topics import (
    write_topic,
    is_topic,
    add_topic
)


add_topic("commands",( lambda name, outfile: help_commands(outfile) ),
    "List of commands") 

def help(topic=None, outfile = None):
    if outfile == None:
        outfile = sys.stdout
    if topic == None:
        write_topic("global_help", outfile)
    elif is_topic(topic):
        write_topic(topic, outfile)
    else:
        help_on_command(topic, outfile = outfile)


def command_usage(cmd_object):
    """Return single-line grammar for command.

    Only describes arguments, not options.
    """
    s = 'bzr ' + cmd_object.name() + ' '
    for aname in cmd_object.takes_args:
        aname = aname.upper()
        if aname[-1] in ['$', '+']:
            aname = aname[:-1] + '...'
        elif aname[-1] == '?':
            aname = '[' + aname[:-1] + ']'
        elif aname[-1] == '*':
            aname = '[' + aname[:-1] + '...]'
        s += aname + ' '
            
    assert s[-1] == ' '
    s = s[:-1]
    
    return s


def print_command_plugin(cmd_object, outfile, format):
    """Print the plugin that provides a command object, if any.

    If the cmd_object is provided by a plugin, prints the plugin name to
    outfile using the provided format string.
    """
    plugin_name = cmd_object.plugin_name()
    if plugin_name is not None:
        out_str = '(From plugin "%s")' % plugin_name
        outfile.write(format % out_str)


def help_on_command(cmdname, outfile=None):
    from bzrlib.commands import get_cmd_object

    cmdname = str(cmdname)

    if outfile == None:
        outfile = sys.stdout

    cmd_object = get_cmd_object(cmdname)

    doc = cmd_object.help()
    if doc == None:
        raise NotImplementedError("sorry, no detailed help yet for %r" % cmdname)

    print >>outfile, 'usage:', command_usage(cmd_object) 

    if cmd_object.aliases:
        print >>outfile, 'aliases:',
        print >>outfile, ', '.join(cmd_object.aliases)

    print >>outfile

    print_command_plugin(cmd_object, outfile, '%s\n\n')

    outfile.write(doc)
    if doc[-1] != '\n':
        outfile.write('\n')
    help_on_command_options(cmd_object, outfile)


def help_on_command_options(cmd, outfile=None):
    from bzrlib.option import Option, get_optparser
    if outfile is None:
        outfile = sys.stdout
    options = cmd.options()
    outfile.write('\n')
    outfile.write(get_optparser(options).format_option_help())


def help_commands(outfile=None):
    """List all commands"""
    from bzrlib.commands import (builtin_command_names,
                                 plugin_command_names,
                                 get_cmd_object)

    if outfile == None:
        outfile = sys.stdout

    names = set()                       # to eliminate duplicates
    names.update(builtin_command_names())
    names.update(plugin_command_names())
    names = list(names)
    names.sort()

    for cmd_name in names:
        cmd_object = get_cmd_object(cmd_name)
        if cmd_object.hidden:
            continue
        print >>outfile, command_usage(cmd_object)

        plugin_name = cmd_object.plugin_name()
        print_command_plugin(cmd_object, outfile, '        %s\n')

        cmd_help = cmd_object.help()
        if cmd_help:
            firstline = cmd_help.split('\n', 1)[0]
            print >>outfile, '        ' + firstline

