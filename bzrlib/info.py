# Copyright (C) 2005, 2006, 2007 Canonical Ltd
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

__all__ = ['show_bzrdir_info']

from cStringIO import StringIO
import os
import time
import sys

from bzrlib import (
    bzrdir,
    diff,
    errors,
    hooks as _mod_hooks,
    osutils,
    urlutils,
    )
from bzrlib.errors import (NoWorkingTree, NotBranchError,
                           NoRepositoryPresent, NotLocalUrl)
from bzrlib.missing import find_unmerged


def plural(n, base='', pl=None):
    if n == 1:
        return base
    elif pl is not None:
        return pl
    else:
        return 's'


class LocationList(object):

    def __init__(self, base_path):
        self.locs = []
        self.base_path = base_path

    def add_url(self, label, url):
        """Add a URL to the list, converting it to a path if possible"""
        if url is None:
            return
        try:
            path = urlutils.local_path_from_url(url)
        except errors.InvalidURL:
            self.locs.append((label, url))
        else:
            self.add_path(label, path)

    def add_path(self, label, path):
        """Add a path, converting it to a relative path if possible"""
        try:
            path = osutils.relpath(self.base_path, path)
        except errors.PathNotChild:
            pass
        else:
            if path == '':
                path = '.'
        if path != '/':
            path = path.rstrip('/')
        self.locs.append((label, path))

    def get_lines(self):
        max_len = max(len(l) for l, u in self.locs)
        return ["  %*s: %s\n" % (max_len, l, u) for l, u in self.locs ]


def gather_location_info(repository, branch=None, working=None):
    locs = {}
    repository_path = repository.bzrdir.root_transport.base
    if branch is not None:
        branch_path = branch.bzrdir.root_transport.base
        master_path = branch.get_bound_location()
        if master_path is None:
            master_path = branch_path
    else:
        branch_path = None
        master_path = None
    if working:
        working_path = working.bzrdir.root_transport.base
        if working_path != branch_path:
            locs['light checkout root'] = working_path
        if master_path != branch_path:
            if repository.is_shared():
                locs['repository checkout root'] = branch_path
            else:
                locs['checkout root'] = branch_path
        if working_path != master_path:
            locs['checkout of branch'] = master_path
        elif repository.is_shared():
            locs['repository branch'] = branch_path
        elif branch_path is not None:
            # standalone
            locs['branch root'] = branch_path
    else:
        working_path = None
        if repository.is_shared():
            # lightweight checkout of branch in shared repository
            if branch_path is not None:
                locs['repository branch'] = branch_path
        elif branch_path is not None:
            # standalone
            locs['branch root'] = branch_path
            if master_path != branch_path:
                locs['bound to branch'] = master_path
        else:
            locs['repository'] = repository_path
    if repository.is_shared():
        # lightweight checkout of branch in shared repository
        locs['shared repository'] = repository_path
    order = ['light checkout root', 'repository checkout root',
             'checkout root', 'checkout of branch', 'shared repository',
             'repository', 'repository branch', 'branch root',
             'bound to branch']
    return [(n, locs[n]) for n in order if n in locs]


def _show_location_info(locs, outfile):
    """Show known locations for working, branch and repository."""
    outfile.write('Location:\n')
    path_list = LocationList(osutils.getcwd())
    for name, loc in locs:
        path_list.add_url(name, loc)
    outfile.writelines(path_list.get_lines())


def _gather_related_branches(branch):
    locs = LocationList(osutils.getcwd())
    locs.add_url('public branch', branch.get_public_branch())
    locs.add_url('push branch', branch.get_push_location())
    locs.add_url('parent branch', branch.get_parent())
    locs.add_url('submit branch', branch.get_submit_branch())
    try:
        locs.add_url('stacked on', branch.get_stacked_on_url())
    except (errors.UnstackableBranchFormat, errors.UnstackableRepositoryFormat,
        errors.NotStacked):
        pass
    return locs


def _show_related_info(branch, outfile):
    """Show parent and push location of branch."""
    locs = _gather_related_branches(branch)
    if len(locs.locs) > 0:
        outfile.write('\n')
        outfile.write('Related branches:\n')
        outfile.writelines(locs.get_lines())


def _show_format_info(control=None, repository=None, branch=None,
                      working=None, outfile=None):
    """Show known formats for control, working, branch and repository."""
    outfile.write('\n')
    outfile.write('Format:\n')
    if control:
        outfile.write('       control: %s\n' %
            control._format.get_format_description())
    if working:
        outfile.write('  working tree: %s\n' %
            working._format.get_format_description())
    if branch:
        outfile.write('        branch: %s\n' %
            branch._format.get_format_description())
    if repository:
        outfile.write('    repository: %s\n' %
            repository._format.get_format_description())


def _show_locking_info(repository, branch=None, working=None, outfile=None):
    """Show locking status of working, branch and repository."""
    if (repository.get_physical_lock_status() or
        (branch and branch.get_physical_lock_status()) or
        (working and working.get_physical_lock_status())):
        outfile.write('\n')
        outfile.write('Lock status:\n')
        if working:
            if working.get_physical_lock_status():
                status = 'locked'
            else:
                status = 'unlocked'
            outfile.write('  working tree: %s\n' % status)
        if branch:
            if branch.get_physical_lock_status():
                status = 'locked'
            else:
                status = 'unlocked'
            outfile.write('        branch: %s\n' % status)
        if repository:
            if repository.get_physical_lock_status():
                status = 'locked'
            else:
                status = 'unlocked'
            outfile.write('    repository: %s\n' % status)


def _show_missing_revisions_branch(branch, outfile):
    """Show missing master revisions in branch."""
    # Try with inaccessible branch ?
    master = branch.get_master_branch()
    if master:
        local_extra, remote_extra = find_unmerged(branch, master)
        if remote_extra:
            outfile.write('\n')
            outfile.write(('Branch is out of date: missing %d '
                'revision%s.\n') % (len(remote_extra),
                plural(len(remote_extra))))


def _show_missing_revisions_working(working, outfile):
    """Show missing revisions in working tree."""
    branch = working.branch
    basis = working.basis_tree()
    work_inv = working.inventory
    branch_revno, branch_last_revision = branch.last_revision_info()
    try:
        tree_last_id = working.get_parent_ids()[0]
    except IndexError:
        tree_last_id = None

    if branch_revno and tree_last_id != branch_last_revision:
        tree_last_revno = branch.revision_id_to_revno(tree_last_id)
        missing_count = branch_revno - tree_last_revno
        outfile.write('\n')
        outfile.write(('Working tree is out of date: missing %d '
            'revision%s.\n') % (missing_count, plural(missing_count)))


def _show_working_stats(working, outfile):
    """Show statistics about a working tree."""
    basis = working.basis_tree()
    work_inv = working.inventory
    delta = working.changes_from(basis, want_unchanged=True)

    outfile.write('\n')
    outfile.write('In the working tree:\n')
    outfile.write('  %8s unchanged\n' % len(delta.unchanged))
    outfile.write('  %8d modified\n' % len(delta.modified))
    outfile.write('  %8d added\n' % len(delta.added))
    outfile.write('  %8d removed\n' % len(delta.removed))
    outfile.write('  %8d renamed\n' % len(delta.renamed))

    ignore_cnt = unknown_cnt = 0
    for path in working.extras():
        if working.is_ignored(path):
            ignore_cnt += 1
        else:
            unknown_cnt += 1
    outfile.write('  %8d unknown\n' % unknown_cnt)
    outfile.write('  %8d ignored\n' % ignore_cnt)

    dir_cnt = 0
    for file_id in work_inv:
        if (work_inv.get_file_kind(file_id) == 'directory' and
            not work_inv.is_root(file_id)):
            dir_cnt += 1
    outfile.write('  %8d versioned %s\n' % (dir_cnt,
        plural(dir_cnt, 'subdirectory', 'subdirectories')))


def _show_branch_stats(branch, verbose, outfile):
    """Show statistics about a branch."""
    revno, head = branch.last_revision_info()
    outfile.write('\n')
    outfile.write('Branch history:\n')
    outfile.write('  %8d revision%s\n' % (revno, plural(revno)))
    stats = branch.repository.gather_stats(head, committers=verbose)
    if verbose:
        committers = stats['committers']
        outfile.write('  %8d committer%s\n' % (committers,
            plural(committers)))
    if revno:
        timestamp, timezone = stats['firstrev']
        age = int((time.time() - timestamp) / 3600 / 24)
        outfile.write('  %8d day%s old\n' % (age, plural(age)))
        outfile.write('   first revision: %s\n' %
            osutils.format_date(timestamp, timezone))
        timestamp, timezone = stats['latestrev']
        outfile.write('  latest revision: %s\n' %
            osutils.format_date(timestamp, timezone))
    return stats


def _show_repository_info(repository, outfile):
    """Show settings of a repository."""
    if repository.make_working_trees():
        outfile.write('\n')
        outfile.write('Create working tree for new branches inside '
            'the repository.\n')


def _show_repository_stats(repository, stats, outfile):
    """Show statistics about a repository."""
    f = StringIO()
    if 'revisions' in stats:
        revisions = stats['revisions']
        f.write('  %8d revision%s\n' % (revisions, plural(revisions)))
    if 'size' in stats:
        f.write('  %8d KiB\n' % (stats['size']/1024))
    for hook in hooks['repository']:
        hook(repository, stats, f)
    if f.getvalue() != "":
        outfile.write('\n')
        outfile.write('Repository:\n')
        outfile.write(f.getvalue())


def show_bzrdir_info(a_bzrdir, verbose=False, outfile=None):
    """Output to stdout the 'info' for a_bzrdir."""
    if outfile is None:
        outfile = sys.stdout
    try:
        tree = a_bzrdir.open_workingtree(
            recommend_upgrade=False)
    except (NoWorkingTree, NotLocalUrl):
        tree = None
        try:
            branch = a_bzrdir.open_branch()
        except NotBranchError:
            branch = None
            try:
                repository = a_bzrdir.open_repository()
            except NoRepositoryPresent:
                # Return silently; cmd_info already returned NotBranchError
                # if no bzrdir could be opened.
                return
            else:
                lockable = repository
        else:
            repository = branch.repository
            lockable = branch
    else:
        branch = tree.branch
        repository = branch.repository
        lockable = tree

    lockable.lock_read()
    try:
        show_component_info(a_bzrdir, repository, branch, tree, verbose,
                            outfile)
    finally:
        lockable.unlock()


def show_component_info(control, repository, branch=None, working=None,
    verbose=1, outfile=None):
    """Write info about all bzrdir components to stdout"""
    if outfile is None:
        outfile = sys.stdout
    if verbose is False:
        verbose = 1
    if verbose is True:
        verbose = 2
    layout = describe_layout(repository, branch, working)
    format = describe_format(control, repository, branch, working)
    outfile.write("%s (format: %s)\n" % (layout, format))
    _show_location_info(gather_location_info(repository, branch, working),
                        outfile)
    if branch is not None:
        _show_related_info(branch, outfile)
    if verbose == 0:
        return
    _show_format_info(control, repository, branch, working, outfile)
    _show_locking_info(repository, branch, working, outfile)
    if branch is not None:
        _show_missing_revisions_branch(branch, outfile)
    if working is not None:
        _show_missing_revisions_working(working, outfile)
        _show_working_stats(working, outfile)
    elif branch is not None:
        _show_missing_revisions_branch(branch, outfile)
    if branch is not None:
        show_committers = verbose >= 2
        stats = _show_branch_stats(branch, show_committers, outfile)
    else:
        stats = repository.gather_stats()
    if branch is None and working is None:
        _show_repository_info(repository, outfile)
    _show_repository_stats(repository, stats, outfile)


def describe_layout(repository=None, branch=None, tree=None):
    """Convert a control directory layout into a user-understandable term

    Common outputs include "Standalone tree", "Repository branch" and
    "Checkout".  Uncommon outputs include "Unshared repository with trees"
    and "Empty control directory"
    """
    if repository is None:
        return 'Empty control directory'
    if branch is None and tree is None:
        if repository.is_shared():
            phrase = 'Shared repository'
        else:
            phrase = 'Unshared repository'
        if repository.make_working_trees():
            phrase += ' with trees'
        return phrase
    else:
        if repository.is_shared():
            independence = "Repository "
        else:
            independence = "Standalone "
        if tree is not None:
            phrase = "tree"
        else:
            phrase = "branch"
        if branch is None and tree is not None:
            phrase = "branchless tree"
        else:
            if (tree is not None and tree.bzrdir.root_transport.base !=
                branch.bzrdir.root_transport.base):
                independence = ''
                phrase = "Lightweight checkout"
            elif branch.get_bound_location() is not None:
                if independence == 'Standalone ':
                    independence = ''
                if tree is None:
                    phrase = "Bound branch"
                else:
                    phrase = "Checkout"
        if independence != "":
            phrase = phrase.lower()
        return "%s%s" % (independence, phrase)


def describe_format(control, repository, branch, tree):
    """Determine the format of an existing control directory

    Several candidates may be found.  If so, the names are returned as a
    single string, separated by ' or '.

    If no matching candidate is found, "unnamed" is returned.
    """
    candidates  = []
    if (branch is not None and tree is not None and
        branch.bzrdir.root_transport.base !=
        tree.bzrdir.root_transport.base):
        branch = None
        repository = None
    non_aliases = set(bzrdir.format_registry.keys())
    non_aliases.difference_update(bzrdir.format_registry.aliases())
    for key in non_aliases:
        format = bzrdir.format_registry.make_bzrdir(key)
        if isinstance(format, bzrdir.BzrDirMetaFormat1):
            if (tree and format.workingtree_format !=
                tree._format):
                continue
            if (branch and format.get_branch_format() !=
                branch._format):
                continue
            if (repository and format.repository_format !=
                repository._format):
                continue
        if format.__class__ is not control._format.__class__:
            continue
        candidates.append(key)
    if len(candidates) == 0:
        return 'unnamed'
    candidates.sort()
    new_candidates = [c for c in candidates if not
        bzrdir.format_registry.get_info(c).hidden]
    if len(new_candidates) > 0:
        # If there are any non-hidden formats that match, only return those to
        # avoid listing hidden formats except when only a hidden format will
        # do.
        candidates = new_candidates
    return ' or '.join(candidates)


class InfoHooks(_mod_hooks.Hooks):
    """Hooks for the info command."""

    def __init__(self):
        self.create_hook(_mod_hooks.HookPoint('repository',
            "Invoked when displaying the statistics for a repository. "
            "repository is called with a statistics dictionary as returned "
            "by the repository and a file-like object to write to.", (1, 15), 
            None))


hooks = InfoHooks()
