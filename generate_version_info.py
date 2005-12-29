# Copyright (C) 2005 Canonical Ltd

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""\
Routines for extracting all version information from a bzr branch.
"""

import time
import pprint

from StringIO import StringIO

from bzrlib.errors import NoWorkingTree
from bzrlib.log import show_log, log_formatter
from bzrlib.rio import RioReader, RioWriter, Stanza


def is_clean(branch):
    """Check if a branch is clean.

    :param branch: The branch to check for changes
    TODO: jam 20051228 This might be better to ask for a WorkingTree
            instead of a Branch.
    :return: (is_clean, message)
    """
    try:
        new_tree = branch.working_tree()
    except NoWorkingTree:
        # Trees without a working tree can't be dirty :)
        return True, ''

    # Look for unknown files in the new tree
    for info in new_tree.list_files():
        path = info[0]
        file_class = info[1]
        if file_class == '?':
            return False, 'path %s is unknown' % (path,)

    from bzrlib.diff import compare_trees
    # See if there is anything that has been changed
    old_tree = branch.basis_tree()
    delta = compare_trees(old_tree, new_tree, want_unchanged=False)
    if len(delta.added) > 0:
        return False, 'have added files: %r' % (delta.added,)
    if len(delta.removed) > 0:
        return False, 'have removed files: %r' % (delta.removed,)
    if len(delta.modified) > 0:
        return False, 'have modified files: %r' % (delta.modified,)
    if len(delta.renamed) > 0:
        return False, 'have renamed files: %r' % (delta.renamed,)

    return True, ''


# This contains a map of format id => formatter
# None is considered the default formatter
version_formats = {}


def generate_rio_version(branch, to_file,
        check_for_clean=False,
        include_revision_history=False):
    """Create the version file for this project.

    :param branch: The branch to write information about
    :param to_file: The file to write the information
    :param check_for_clean: If true, check if the branch is clean.
        This can be expensive for large trees. This is also only
        valid for branches with working trees.
    :param include_revision_history: Write out the list of revisions, and
        the commit message associated with each
    """
    info = Stanza()
    # TODO: jam 20051228 This might be better as the datestamp 
    #       of the last commit
    info.add('date', time.strftime('%Y-%m-%d %H:%M:%S (%A, %B %d, %Y, %Z)'))
    info.add('revno', str(branch.revno()))

    last_rev = branch.last_revision()
    if last_rev is not None:
        info.add('revision_id', last_rev)

    if branch.nick is not None:
        info.add('branch_nick', branch.nick)

    if check_for_clean:
        clean, message = is_clean(branch)
        if clean:
            info.add('clean', 'True')
        else:
            info.add('clean', 'False')

    if include_revision_history:
        revs = branch.revision_history()
        log = Stanza()
        for rev_id in revs:
            rev = branch.get_revision(rev_id)
            log.add('id', rev_id)
            log.add('message', rev.message)
        sio = StringIO()
        log_writer = RioWriter(to_file=sio)
        log_writer.write_stanza(log)
        info.add('revisions', sio.getvalue())

    writer = RioWriter(to_file=to_file)
    writer.write_stanza(info)


version_formats['rio'] = generate_rio_version
# Default format is rio
version_formats[None] = generate_rio_version


# Header and footer for the python format
_py_version_header = '''#!/usr/bin/env python
"""\\
This file is automatically generated by generate_version_info
It uses the current working tree to determine the revision.
So don't edit it. :)
"""

'''


_py_version_footer = '''

if __name__ == '__main__':
    print 'revision: %(revno)d' % version_info
    print 'nick: %(branch_nick)s' % version_info
    print 'revision id: %(revision_id)s' % version_info
'''


def generate_python_version(branch, to_file,
        check_for_clean=False,
        include_revision_history=False):
    """Create a python version file for this project.

    :param branch: The branch to write information about
    :param to_file: The file to write the information
    :param check_for_clean: If true, check if the branch is clean.
        This can be expensive for large trees. This is also only
        valid for branches with working trees.
    :param include_revision_history: Write out the list of revisions, and
        the commit message associated with each
    """
    # TODO: jam 20051228 The python output doesn't actually need to be
    #       encoded, because it should only generate ascii safe output.
    info = {'date':time.strftime('%Y-%m-%d %H:%M:%S (%A, %B %d, %Y, %Z)')
              , 'revno':branch.revno()
              , 'revision_id':branch.last_revision()
              , 'revisions':None
              , 'branch_nick':branch.nick
              , 'clean':None
    }

    if include_revision_history:
        revs = branch.revision_history()
        log = []
        for rev_id in revs:
            rev = branch.get_revision(rev_id)
            log.append((rev_id, rev.message))
        info['revisions'] = log

    if check_for_clean:
        clean, message = is_clean(branch)
        if clean:
            info['clean'] = True
        else:
            info['clean'] = False

    info_str = pprint.pformat(info)
    to_file.write(_py_version_header)
    to_file.write('version_info =')
    to_file.write(info_str)
    to_file.write('\n')
    to_file.write(_py_version_footer)

version_formats['python'] = generate_python_version

