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
"""\
This is an attempt to take the internal delta object, and represent
it as a single-file text-only changeset.
This should have commands for both generating a changeset,
and for applying a changeset.
"""

import sys

from bzrlib.lazy_import import lazy_import
lazy_import(globals(), """
from bzrlib import (
    branch,
    errors,
    revision as _mod_revision,
    urlutils,
    )
""")

from bzrlib.commands import Command
from bzrlib.option import Option
from bzrlib.trace import note


class cmd_send_changeset(Command):
    """Send a bundled up changset via mail.

    If no revision has been specified, the last commited change will
    be sent.

    Subject of the mail can be specified by the --message option,
    otherwise information from the changeset log will be used.

    A editor will be spawned where the user may enter a description
    of the changeset.  The description can be read from a file with
    the --file FILE option.
    """
    takes_options = ['revision', 'message', 'file']
    takes_args = ['to?']

    def run(self, to=None, message=None, revision=None, file=None):
        from bzrlib.errors import BzrCommandError
        from send_changeset import send_changeset
        
        if isinstance(revision, (list, tuple)):
            if len(revision) > 1:
                raise BzrCommandError('We do not support rollup-changesets yet.')
            revision = revision[0]

        b = branch.Branch.open_containing('.')

        if not to:
            try:
                to = b.controlfile('x-send-address', 'rb').read().strip('\n')
            except:
                raise BzrCommandError('destination address is not known')

        if not isinstance(revision, (list, tuple)):
            revision = [revision]

        send_changeset(b, revision, to, message, file)


class cmd_bundle_revisions(Command):
    """Generate a revision bundle.

    This bundle contains all of the meta-information of a
    diff, rather than just containing the patch information.

    You can apply it to another tree using 'bzr merge'.

    bzr bundle-revisions
        - Generate a bundle relative to a remembered location

    bzr bundle-revisions BASE
        - Bundle to apply the current tree into BASE

    bzr bundle-revisions --revision A
        - Bundle to apply revision A to remembered location 

    bzr bundle-revisions --revision A..B
        - Bundle to transform A into B
    """
    takes_options = ['revision', 'remember',
                     Option("output", help="write bundle to specified file",
                            type=unicode)]
    takes_args = ['base?']
    aliases = ['bundle']
    encoding_type = 'exact'

    def run(self, base=None, revision=None, output=None, remember=False):
        from bzrlib import user_encoding
        from bzrlib.bundle.serializer import write_bundle

        target_branch = branch.Branch.open_containing(u'.')[0]

        if base is None:
            base_specified = False
        else:
            base_specified = True

        if revision is None:
            target_revision = _mod_revision.ensure_null(
                target_branch.last_revision())
        elif len(revision) < 3:
            target_revision = revision[-1].in_history(target_branch).rev_id
            if len(revision) == 2:
                if base_specified:
                    raise errors.BzrCommandError('Cannot specify base as well'
                                                 ' as two revision arguments.')
                base_revision = revision[0].in_history(target_branch).rev_id
        else:
            raise errors.BzrCommandError('--revision takes 1 or 2 parameters')

        if revision is None or len(revision) < 2:
            submit_branch = target_branch.get_submit_branch()
            if base is None:
                base = submit_branch
            if base is None:
                base = target_branch.get_parent()
            if base is None:
                raise errors.BzrCommandError("No base branch known or"
                                             " specified.")
            elif not base_specified:
                # FIXME:
                # note() doesn't pay attention to terminal_encoding() so
                # we must format with 'ascii' to be safe
                note('Using saved location: %s',
                     urlutils.unescape_for_display(base, 'ascii'))
            base_branch = branch.Branch.open(base)

            # We don't want to lock the same branch across
            # 2 different branches
            if target_branch.base == base_branch.base:
                base_branch = target_branch 
            if submit_branch is None or remember:
                if base_specified:
                    target_branch.set_submit_branch(base_branch.base)
                elif remember:
                    raise errors.BzrCommandError('--remember requires a branch'
                                                 ' to be specified.')
            base_last_revision = _mod_revision.ensure_null(
                base_branch.last_revision())
            target_branch.repository.fetch(base_branch.repository, 
                base_last_revision)
            graph = target_branch.repository.get_graph()
            base_revision = graph.find_unique_lca(base_last_revision,
                                                  target_revision)

        if output is not None:
            fileobj = file(output, 'wb')
        else:
            fileobj = sys.stdout
        target_branch.repository.lock_read()
        try:
            write_bundle(target_branch.repository, target_revision,
                         base_revision, fileobj)
        finally:
            target_branch.repository.unlock()


class cmd_verify_changeset(Command):
    """Read a written changeset, and make sure it is valid.

    """
    takes_args = ['filename?']

    def run(self, filename=None):
        from read_changeset import read_changeset
        #from bzrlib.xml import serializer_v4

        b, relpath = branch.Branch.open_containing('.')

        if filename is None or filename == '-':
            f = sys.stdin
        else:
            f = open(filename, 'U')

        cset_info, cset_tree = read_changeset(f, b)
        # print cset_info
        # print cset_tree
        #serializer_v4.write(cset_tree.inventory, sys.stdout)
