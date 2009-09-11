# Copyright (C) 2006-2007 Canonical Ltd
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

"""Generate ReStructuredText source for the User Reference Manual.
Loosely based on the manpage generator autodoc_man.py.

Written by the Bazaar community.
"""

import os
import sys
import time

import bzrlib
import bzrlib.help
import bzrlib.help_topics
import bzrlib.commands
import bzrlib.osutils


# Set this to True to generate a file per topic.
# This probably ought to be an option. The files probably
# ought to be prefixed with their section name as well so
# there's zero risk of clashing with a standard sphinx
# topic (like search.html).
FILE_PER_TOPIC = False


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
    nominated_filename = getattr(options, 'filename', None)
    if nominated_filename is None:
        topic_dir = None
    else:
        topic_dir = bzrlib.osutils.dirname(nominated_filename)
    outfile.write(rstx_preamble % params)
    outfile.write(rstx_head % params)
    outfile.write(_get_body(params, topic_dir))
    outfile.write(rstx_foot % params)


def _get_body(params, topic_dir):
    """Build the manual content."""
    from bzrlib.help_topics import SECT_CONCEPT, SECT_LIST, SECT_PLUGIN
    registry = bzrlib.help_topics.topic_registry
    result = []
    result.append(_get_section(registry, SECT_CONCEPT, "Concepts",
        output_dir=topic_dir))
    result.append(_get_section(registry, SECT_LIST, "Lists",
        output_dir=topic_dir))
    result.append(_get_commands_section(registry))
    #result.append(_get_section(registry, SECT_PLUGIN, "Standard Plug-ins"))
    return "\n".join(result)


def _get_section(registry, section, title, hdg_level1="#", hdg_level2="=",
        output_dir=None):
    """Build the manual part from topics matching that section.
    
    If output_dir is not None, topics are dumped into text files there
    during processing, as well as being included in the return result.
    """
    topics = sorted(registry.get_topics_for_section(section))
    lines = [title, hdg_level1 * len(title), ""]

    # docutils treats section heading as implicit link target.
    # But in some cases topic and heading are different, e.g.:
    #
    # `bugs' vs. `Bug Trackers'
    # `working-tree' vs. `Working Trees'
    #
    # So for building proper cross-reference between topic names
    # and corresponding sections in document, we need provide
    # simple glue in the form:
    #
    # .. _topic: `heading`_
    links_glue = []

    for topic in topics:
        help = registry.get_detail(topic)
        heading,text = help.split("\n", 1)
        lines.append(heading)
        if not text.startswith(hdg_level2):
            lines.append(hdg_level2 * len(heading))
        lines.append(text)
        lines.append('')
        # check that topic match heading
        if topic != heading.lower():
            links_glue.append((topic, heading))
        # dump the text if requested
        if output_dir is not None:
            out_file = bzrlib.osutils.pathjoin(output_dir, topic + ".txt")
            _dump_text(out_file, help)

    # provide links glue for topics that don't match headings
    lines.extend([".. _%s: `%s`_" % i for i in links_glue])
    lines.append('')

    return "\n" + "\n".join(lines) + "\n"


def _dump_text(filename, text):
    """Dump text to filename."""
    if not FILE_PER_TOPIC:
        return
    f =  open(filename, "w")
    f.writelines(text)
    f.close()


def _get_commands_section(registry, title="Commands", hdg_level1="#",
                          hdg_level2="="):
    """Build the commands reference section of the manual."""
    lines = [title, hdg_level1 * len(title), ""]
    cmds = sorted(bzrlib.commands.builtin_command_names())
    for cmd_name in cmds:
        cmd_object = bzrlib.commands.get_cmd_object(cmd_name)
        if cmd_object.hidden:
            continue
        heading = cmd_name
        text = cmd_object.get_help_text(plain=False, see_also_as_links=True)
        lines.append(heading)
        lines.append(hdg_level2 * len(heading))
        lines.append(text)
        lines.append('')
    return "\n" + "\n".join(lines) + "\n"


##
# TEMPLATES

rstx_preamble = """.. This file is autogenerated from the output of
..     %(bzrcmd)s help topics
..     %(bzrcmd)s help commands
..     %(bzrcmd)s help <cmd>
..
.. Generation time: %(timestamp)s

"""


rstx_head = """\
#####################
Bazaar User Reference
#####################

About This Manual
#################

This manual is generated from Bazaar's online help. To use
the online help system, try the following commands.

    Introduction including a list of commonly used commands::

        bzr help

    List of topics and a summary of each::

        bzr help topics

    List of commands and a summary of each::

        bzr help commands

    More information about a particular topic or command::

        bzr help topic-or-command-name

The following web sites provide further information on Bazaar:

:Home page:                     http://www.bazaar-vcs.org/
:Official docs:                 http://doc.bazaar-vcs.org/
:Launchpad:                     https://launchpad.net/bzr/
"""


rstx_foot = """
"""
