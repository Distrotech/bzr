# Copyright 2006 Canonical Ltd.
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

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""Generate ReStructuredText source for manual.
Based on manpage generator autodoc_man.py

Written by Alexander Belchenko
"""

import os
import sys
import textwrap
import time

import bzrlib
import bzrlib.help
import bzrlib.commands


def get_filename(options):
    """Provides name of manual"""
    return "%s_man.txt" % (options.bzr_name)


def infogen(options, outfile):
    """Create manual in RSTX format"""
    t = time.time()
    tt = time.gmtime(t)
    params = \
           { "bzrcmd": options.bzr_name,
             "datestamp": time.strftime("%Y-%m-%d",tt),
             "timestamp": time.strftime("%Y-%m-%d %H:%M:%S +0000",tt),
             "version": bzrlib.__version__,
             }
    outfile.write(rstx_preamble % params)
    outfile.write(rstx_head % params)
    outfile.write(getcommand_list(params))
    outfile.write(getcommand_help(params))
    outfile.write(rstx_foot % params)


def command_name_list():
    """Builds a list of command names from bzrlib"""
    command_names = bzrlib.commands.builtin_command_names()
    command_names.sort()
    return command_names


def getcommand_list (params):
    """Builds summary help for command names in RSTX format"""
    bzrcmd = params["bzrcmd"]
    output = """
Command overview
================
"""
    for cmd_name in command_name_list():
        cmd_object = bzrlib.commands.get_cmd_object(cmd_name)
        if cmd_object.hidden:
            continue
        cmd_help = cmd_object.help()
        if cmd_help:
            firstline = cmd_help.split('\n', 1)[0]
            usage = bzrlib.help.command_usage(cmd_object)
            tmp = '**%s**\n\t%s\n\n' % (usage, firstline)
            output = output + tmp
        else:
            raise RuntimeError, "Command '%s' has no help text" % (cmd_name)
    return output


def getcommand_help(params):
    """Shows individual options for a bzr command"""
    output="""
Command reference
=================
"""
    for cmd_name in command_name_list():
        cmd_object = bzrlib.commands.get_cmd_object(cmd_name)
        if cmd_object.hidden:
            continue
        output = output + format_command(params, cmd_object, cmd_name)
    return output


def format_command (params, cmd, name):
    """Provides long help for each public command"""
    usage = bzrlib.help.command_usage(cmd)
    subsection_header = """
%s
%s
::
""" % (usage, '-'*len(usage))

    docsplit = cmd.__doc__.split('\n')
    doc = '\n'.join([' '*4 + docsplit[0]] + docsplit[1:])
        
    option_str = ""
    options = cmd.options()
    if options:
        option_str = "\n    Options:\n"
        for option_name, option in sorted(options.items()):
            l = '        --' + option_name
            if option.type is not None:
                l += ' ' + option.argname.upper()
            short_name = option.short_name()
            if short_name:
                assert len(short_name) == 1
                l += ', -' + short_name
            l += (30 - len(l)) * ' ' + option.help
            # TODO: Split help over multiple lines with
            # correct indenting and wrapping.
            wrapped = textwrap.fill(l, initial_indent='',
                                    subsequent_indent=30*' ')
            option_str = option_str + wrapped + '\n'       
    return subsection_header + option_str + "\n" + doc + "\n"


##
# TEMPLATES

rstx_preamble = """.. Large parts of this file are autogenerated from the output of
..     %(bzrcmd)s help commands
..     %(bzrcmd)s help <cmd>
..
.. Generation time: %(timestamp)s

=============================================
Man page for %(bzrcmd)s (bazaar-ng)
=============================================

:Date: %(datestamp)s

`Index <#id1>`_

-----

"""


rstx_head = """\
Name
====
%(bzrcmd)s - bazaar-ng next-generation distributed version control

Synopsis
========
**%(bzrcmd)s** *command* [ *command_options* ]

**%(bzrcmd)s help**

**%(bzrcmd)s help** *command*


Description
===========
bazaar-ng (or **%(bzrcmd)s**) is a project of Canonical to develop
an open source distributed version control system that is powerful,
friendly, and scalable. Version control means a system
that keeps track of previous revisions of software source code
or similar information and helps people work on it in teams.
"""


rstx_foot = """
Environment
===========
**BZRPATH**
                Path where **%(bzrcmd)s** is to look for external command.

**BZREMAIL**
                E-Mail address of the user. Overrides default user config.

**EMAIL**
                E-Mail address of the user. Overriddes default user config.

Files
=====

**On Linux**:  ``~/.bazaar/bazaar.conf/``

**On Windows**: ``C:\\Documents and Settings\\username\\Application Data\\bazaar\\2.0\\bazaar.conf``

Contains the default user config. Only one section, ``[DEFAULT]`` is allowed.
A typical default config file may be similiar to::

    [DEFAULT]
    email=John Doe <jdoe@isp.com>


See also
========
http://www.bazaar-vcs.org/

--------------------

.. Contents::
	**Index**
"""
