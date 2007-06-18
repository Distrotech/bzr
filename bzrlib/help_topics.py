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

"""A collection of extra help information for using bzr.

Help topics are meant to be help for items that aren't commands, but will
help bzr become fully learnable without referring to a tutorial.
"""

from bzrlib import registry


class HelpTopicRegistry(registry.Registry):
    """A Registry customized for handling help topics."""

    def register(self, topic, detail, summary):
        """Register a new help topic.

        :param topic: Name of documentation entry
        :param detail: Function or string object providing detailed
            documentation for topic.  Function interface is detail(topic).
            This should return a text string of the detailed information.
        :param summary: String providing single-line documentation for topic.
        """
        # The detail is stored as the 'object' and the 
        super(HelpTopicRegistry, self).register(topic, detail, info=summary)

    def register_lazy(self, topic, module_name, member_name, summary):
        """Register a new help topic, and import the details on demand.

        :param topic: Name of documentation entry
        :param module_name: The module to find the detailed help.
        :param member_name: The member of the module to use for detailed help.
        :param summary: String providing single-line documentation for topic.
        """
        super(HelpTopicRegistry, self).register_lazy(topic, module_name,
                                                     member_name, info=summary)

    def get_detail(self, topic):
        """Get the detailed help on a given topic."""
        obj = self.get(topic)
        if callable(obj):
            return obj(topic)
        else:
            return obj

    def get_summary(self, topic):
        """Get the single line summary for the topic."""
        return self.get_info(topic)


topic_registry = HelpTopicRegistry()


#----------------------------------------------------

def _help_on_topics(dummy):
    """Write out the help for topics to outfile"""

    topics = topic_registry.keys()
    lmax = max(len(topic) for topic in topics)
        
    out = []
    for topic in topics:
        summary = topic_registry.get_summary(topic)
        out.append("%-*s %s\n" % (lmax, topic, summary))
    return ''.join(out)


def _help_on_revisionspec(name):
    """Write the summary help for all documented topics to outfile."""
    import bzrlib.revisionspec

    out = []
    out.append("\nRevision prefix specifier:"
               "\n--------------------------\n")

    for i in bzrlib.revisionspec.SPEC_TYPES:
        doc = i.help_txt
        if doc == bzrlib.revisionspec.RevisionSpec.help_txt:
            doc = "N/A\n"
        while (doc[-2:] == '\n\n' or doc[-1:] == ' '):
            doc = doc[:-1]

        out.append("  %s %s\n\n" % (i.prefix, doc))

    return ''.join(out)


def _help_on_transport(name):
    from bzrlib.transport import (
        transport_list_registry,
    )
    import textwrap

    def add_string(proto, help, maxl, prefix_width=20):
       help_lines = textwrap.wrap(help, maxl - prefix_width)
       line_with_indent = '\n' + ' ' * prefix_width
       help_text = line_with_indent.join(help_lines)
       return "%-20s%s\n" % (proto, help_text)

    def sort_func(a,b):
        a1 = a[:a.rfind("://")]
        b1 = b[:b.rfind("://")]
        if a1>b1:
            return +1
        elif a1<b1:
            return -1
        else:
            return 0

    out = []
    protl = []
    decl = []
    protos = transport_list_registry.keys( )
    protos.sort(sort_func)
    for proto in protos:
        shorthelp = transport_list_registry.get_help(proto)
        if not shorthelp:
            continue
        if proto.endswith("://"):
            protl.extend(add_string(proto, shorthelp, 79))
        else:
            decl.extend(add_string(proto, shorthelp, 79))


    out = "\nSupported URL prefix\n--------------------\n" + \
            ''.join(protl)

    if len(decl):
        out += "\nSupported modifiers\n-------------------\n" + \
            ''.join(decl)

    return out


_basic_help= \
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
  bzr help topics    list all help topics
"""


_global_options =\
"""Global Options

These options may be used with any command, and may appear in front of any
command.  (e.g. "bzr --quiet help").

--quiet        Suppress informational output; only print errors and warnings
--version      Print the version number

--no-aliases   Do not process command aliases when running this command
--builtin      Use the built-in version of a command, not the plugin version.
               This does not suppress other plugin effects
--no-plugins   Do not process any plugins

-Derror        Instead of normal error handling, always print a traceback on
               error.
--profile      Profile execution using the hotshot profiler
--lsprof       Profile execution using the lsprof profiler
--lsprof-file  Profile execution using the lsprof profiler, and write the
               results to a specified file.  If the filename ends with ".txt",
               text format will be used.  If the filename ends with
               ".callgrind", output will be formatted for use with KCacheGrind.
               Otherwise, the output will be a pickle.

See doc/developers/profiling.txt for more information on profiling.

Note: --version must be supplied before any command.
"""

_checkouts = \
"""Checkouts

Checkouts are source trees that are connected to a branch, so that when
you commit in the source tree, the commit goes into that branch.  They
allow you to use a simpler, more centralized workflow, ignoring some of
Bazaar's decentralized features until you want them. Using checkouts
with shared repositories is very similar to working with SVN or CVS, but
doesn't have the same restrictions.  And using checkouts still allows
others working on the project to use whatever workflow they like.

A checkout is created with the bzr checkout command (see "help checkout").
You pass it a reference to another branch, and it will create a local copy
for you that still contains a reference to the branch you created the
checkout from (the master branch). Then if you make any commits they will be
made on the other branch first. This creates an instant mirror of your work, or
facilitates lockstep development, where each developer is working together,
continuously integrating the changes of others.

However the checkout is still a first class branch in Bazaar terms, so that
you have the full history locally.  As you have a first class branch you can
also commit locally if you want, for instance due to the temporary loss af a
network connection. Use the --local option to commit to do this. All the local
commits will then be made on the master branch the next time you do a non-local
commit.

If you are using a checkout from a shared branch you will periodically want to
pull in all the changes made by others. This is done using the "update"
command. The changes need to be applied before any non-local commit, but
Bazaar will tell you if there are any changes and suggest that you use this
command when needed.

It is also possible to create a "lightweight" checkout by passing the
--lightweight flag to checkout. A lightweight checkout is even closer to an
SVN checkout in that it is not a first class branch, it mainly consists of the
working tree. This means that any history operations must query the master
branch, which could be slow if a network connection is involved. Also, as you
don't have a local branch, then you cannot commit locally.

Lightweight checkouts work best when you have fast reliable access to the
master branch. This means that if the master branch is on the same disk or LAN
a lightweight checkout will be faster than a heavyweight one for any commands
that modify the revision history (as only one copy branch needs to be updated).
Heavyweight checkouts will generally be faster for any command that uses the
history but does not change it, but if the master branch is on the same disk
then there wont be a noticeable difference.

Another possible use for a checkout is to use it with a treeless repository
containing your branches, where you maintain only one working tree by
switching the master branch that the checkout points to when you want to 
work on a different branch.

Obviously to commit on a checkout you need to be able to write to the master
branch. This means that the master branch must be accessible over a writeable
protocol , such as sftp://, and that you have write permissions at the other
end. Checkouts also work on the local file system, so that all that matters is
file permissions.

You can change the master of a checkout by using the "bind" command (see "help
bind"). This will change the location that the commits are sent to. The bind
command can also be used to turn a branch into a heavy checkout. If you
would like to convert your heavy checkout into a normal branch so that every
commit is local, you can use the "unbind" command.

Related commands:

  checkout    Create a checkout. Pass --lightweight to get a lightweight
              checkout
  update      Pull any changes in the master branch in to your checkout
  commit      Make a commit that is sent to the master branch. If you have
              a heavy checkout then the --local option will commit to the 
              checkout without sending the commit to the master
  bind        Change the master branch that the commits in the checkout will
              be sent to
  unbind      Turn a heavy checkout into a standalone branch so that any
              commits are only made locally
"""

_repositories = \
"""Repositories

Repositories in Bazaar are where committed information is stored. It is
possible to create a shared repository which allows multiple branches to
share their information in the same location. When a new branch is
created it will first look to see if there is a containing repository it
can share.

When two branches of the same project share a repository, there is
generally a large space saving. For some operations (e.g. branching
within the repository) this translates in to a large time saving.

To create a shared repository use the init-repository command (or the alias
init-repo). This command takes the location of the repository to create. This
means that 'bzr init-repository repo' will create a directory named 'repo',
which contains a shared repository. Any new branches that are created in this
directory will then use it for storage.

It is a good idea to create a repository whenever you might create more
than one branch of a project. This is true for both working areas where you
are doing the development, and any server areas that you use for hosting
projects. In the latter case, it is common to want branches without working
trees. Since the files in the branch will not be edited directly there is no
need to use up disk space for a working tree. To create a repository in which
the branches will not have working trees pass the '--no-trees' option to
'init-repository'.

Related commands:

  init-repository   Create a shared repository. Use --no-trees to create one
                    in which new branches won't get a working tree.
"""


_working_trees = \
"""Working Trees

A working tree is the contents of a branch placed on disk so that you can
see the files and edit them. The working tree is where you make changes to a
branch, and when you commit the current state of the working tree is the
snapshot that is recorded in the commit.

When you push a branch to a remote system, a working tree will not be
created. If one is already present the files will not be updated. The
branch information will be updated and the working tree will be marked
as out-of-date. Updating a working tree remotely is difficult, as there
may be uncommitted changes or the update may cause content conflicts that are
difficult to deal with remotely.

If you have a branch with no working tree you can use the 'checkout' command
to create a working tree. If you run 'bzr checkout .' from the branch it will
create the working tree. If the branch is updated remotely, you can update the
working tree by running 'bzr update' in that directory.

If you have a branch with a working tree that you do not want the 'remove-tree'
command will remove the tree if it is safe. This can be done to avoid the
warning about the remote working tree not being updated when pushing to the
branch. It can also be useful when working with a '--no-trees' repository
(see 'bzr help repositories').

If you want to have a working tree on a remote machine that you push to you
can either run 'bzr update' in the remote branch after each push, or use some
other method to update the tree during the push. There is an 'rspush' plugin
that will update the working tree using rsync as well as doing a push. There
is also a 'push-and-update' plugin that automates running 'bzr update' via SSH
after each push.

Useful commands:

  checkout     Create a working tree when a branch does not have one.
  remove-tree  Removes the working tree from a branch when it is safe to do so.
  update       When a working tree is out of sync with it's associated branch
               this will update the tree to match the branch.
"""


topic_registry.register("revisionspec", _help_on_revisionspec,
                        "Explain how to use --revision")
topic_registry.register('basic', _basic_help, "Basic commands")
topic_registry.register('topics', _help_on_topics, "Topics list")
def get_format_topic(topic):
    from bzrlib import bzrdir
    return bzrdir.format_registry.help_topic(topic)
topic_registry.register('formats', get_format_topic, 'Directory formats')
topic_registry.register('global-options', _global_options,
                        'Options that can be used with any command')
topic_registry.register('checkouts', _checkouts,
                        'Information on what a checkout is')
topic_registry.register('urlspec', _help_on_transport,
                        "Supported transport protocols")
def get_bugs_topic(topic):
    from bzrlib import bugtracker
    return bugtracker.tracker_registry.help_topic(topic)
topic_registry.register('bugs', get_bugs_topic, 'Bug tracker support')
topic_registry.register('repositories', _repositories,
                        'Basic information on shared repositories.')
topic_registry.register('working-trees', _working_trees,
                        'Information on working trees')


class HelpTopicIndex(object):
    """A index for bzr help that returns topics."""

    def __init__(self):
        self.prefix = ''

    def get_topics(self, topic):
        """Search for topic in the HelpTopicRegistry.

        :param topic: A topic to search for. None is treated as 'basic'.
        :return: A list which is either empty or contains a single
            RegisteredTopic entry.
        """
        if topic is None:
            topic = 'basic'
        if topic in topic_registry:
            return [RegisteredTopic(topic)]
        else:
            return []


class RegisteredTopic(object):
    """A help topic which has been registered in the HelpTopicRegistry.

    These topics consist of nothing more than the name of the topic - all
    data is retrieved on demand from the registry.
    """

    def __init__(self, topic):
        """Constructor.

        :param topic: The name of the topic that this represents.
        """
        self.topic = topic

    def get_help_text(self, additional_see_also=None):
        """Return a string with the help for this topic.

        :param additional_see_also: Additional help topics to be
            cross-referenced.
        """
        result = topic_registry.get_detail(self.topic)
        # there is code duplicated here and in bzrlib/plugin.py's 
        # matching Topic code. This should probably be factored in
        # to a helper function and a common base class.
        if additional_see_also is not None:
            see_also = sorted(set(additional_see_also))
        else:
            see_also = None
        if see_also:
            result += '\nSee also: '
            result += ', '.join(see_also)
            result += '\n'
        return result

    def get_help_topic(self):
        """Return the help topic this can be found under."""
        return self.topic

