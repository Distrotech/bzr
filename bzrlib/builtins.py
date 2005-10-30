# Copyright (C) 2004, 2005 by Canonical Ltd

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

# DO NOT change this to cStringIO - it results in control files 
# written as UCS4
# FIXIT! (Only deal with byte streams OR unicode at any one layer.)
# RBC 20051018
from StringIO import StringIO
import sys
import os

import bzrlib
from bzrlib import BZRDIR
from bzrlib.commands import Command, display_command
from bzrlib.branch import Branch
from bzrlib.errors import BzrError, BzrCheckError, BzrCommandError, NotBranchError
from bzrlib.errors import DivergedBranches
from bzrlib.option import Option
from bzrlib.revisionspec import RevisionSpec
import bzrlib.trace
from bzrlib.trace import mutter, note, log_error, warning
from bzrlib.workingtree import WorkingTree


def branch_files(file_list, default_branch='.'):
    """\
    Return a branch and list of branch-relative paths.
    If supplied file_list is empty or None, the branch default will be used,
    and returned file_list will match the original.
    """
    if file_list is None or len(file_list) == 0:
        return Branch.open_containing(default_branch)[0], file_list
    b = Branch.open_containing(file_list[0])[0]
    
    # note that if this is a remote branch, we would want
    # relpath against the transport. RBC 20051018
    # Most branch ops can't meaningfully operate on files in remote branches;
    # the above comment was in cmd_status.  ADHB 20051026
    tree = WorkingTree(b.base, b)
    new_list = []
    for filename in file_list:
        try:
            new_list.append(tree.relpath(filename))
        except NotBranchError:
            raise BzrCommandError("%s is not in the same branch as %s" % 
                                  (filename, file_list[0]))
    return b, new_list


# TODO: Make sure no commands unconditionally use the working directory as a
# branch.  If a filename argument is used, the first of them should be used to
# specify the branch.  (Perhaps this can be factored out into some kind of
# Argument class, representing a file in a branch, where the first occurrence
# opens the branch?)

class cmd_status(Command):
    """Display status summary.

    This reports on versioned and unknown files, reporting them
    grouped by state.  Possible states are:

    added
        Versioned in the working copy but not in the previous revision.

    removed
        Versioned in the previous revision but removed or deleted
        in the working copy.

    renamed
        Path of this file changed from the previous revision;
        the text may also have changed.  This includes files whose
        parent directory was renamed.

    modified
        Text has changed since the previous revision.

    unchanged
        Nothing about this file has changed since the previous revision.
        Only shown with --all.

    unknown
        Not versioned and not matching an ignore pattern.

    To see ignored files use 'bzr ignored'.  For details in the
    changes to file texts, use 'bzr diff'.

    If no arguments are specified, the status of the entire working
    directory is shown.  Otherwise, only the status of the specified
    files or directories is reported.  If a directory is given, status
    is reported for everything inside that directory.

    If a revision argument is given, the status is calculated against
    that revision, or between two revisions if two are provided.
    """
    
    # XXX: FIXME: bzr status should accept a -r option to show changes
    # relative to a revision, or between revisions

    # TODO: --no-recurse, --recurse options
    
    takes_args = ['file*']
    takes_options = ['all', 'show-ids']
    aliases = ['st', 'stat']
    
    @display_command
    def run(self, all=False, show_ids=False, file_list=None, revision=None):
        b, file_list = branch_files(file_list)
            
        from bzrlib.status import show_status
        show_status(b, show_unchanged=all, show_ids=show_ids,
                    specific_files=file_list, revision=revision)


class cmd_cat_revision(Command):
    """Write out metadata for a revision.
    
    The revision to print can either be specified by a specific
    revision identifier, or you can use --revision.
    """

    hidden = True
    takes_args = ['revision_id?']
    takes_options = ['revision']
    
    @display_command
    def run(self, revision_id=None, revision=None):

        if revision_id is not None and revision is not None:
            raise BzrCommandError('You can only supply one of revision_id or --revision')
        if revision_id is None and revision is None:
            raise BzrCommandError('You must supply either --revision or a revision_id')
        b = Branch.open_containing('.')[0]
        if revision_id is not None:
            sys.stdout.write(b.get_revision_xml_file(revision_id).read())
        elif revision is not None:
            for rev in revision:
                if rev is None:
                    raise BzrCommandError('You cannot specify a NULL revision.')
                revno, rev_id = rev.in_history(b)
                sys.stdout.write(b.get_revision_xml_file(rev_id).read())
    

class cmd_revno(Command):
    """Show current revision number.

    This is equal to the number of revisions on this branch."""
    @display_command
    def run(self):
        print Branch.open_containing('.')[0].revno()


class cmd_revision_info(Command):
    """Show revision number and revision id for a given revision identifier.
    """
    hidden = True
    takes_args = ['revision_info*']
    takes_options = ['revision']
    @display_command
    def run(self, revision=None, revision_info_list=[]):

        revs = []
        if revision is not None:
            revs.extend(revision)
        if revision_info_list is not None:
            for rev in revision_info_list:
                revs.append(RevisionSpec(rev))
        if len(revs) == 0:
            raise BzrCommandError('You must supply a revision identifier')

        b = Branch.open_containing('.')[0]

        for rev in revs:
            revinfo = rev.in_history(b)
            if revinfo.revno is None:
                print '     %s' % revinfo.rev_id
            else:
                print '%4d %s' % (revinfo.revno, revinfo.rev_id)

    
class cmd_add(Command):
    """Add specified files or directories.

    In non-recursive mode, all the named items are added, regardless
    of whether they were previously ignored.  A warning is given if
    any of the named files are already versioned.

    In recursive mode (the default), files are treated the same way
    but the behaviour for directories is different.  Directories that
    are already versioned do not give a warning.  All directories,
    whether already versioned or not, are searched for files or
    subdirectories that are neither versioned or ignored, and these
    are added.  This search proceeds recursively into versioned
    directories.  If no names are given '.' is assumed.

    Therefore simply saying 'bzr add' will version all files that
    are currently unknown.

    Adding a file whose parent directory is not versioned will
    implicitly add the parent, and so on up to the root. This means
    you should never need to explictly add a directory, they'll just
    get added when you add a file in the directory.
    """
    takes_args = ['file*']
    takes_options = ['no-recurse', 'quiet']
    
    def run(self, file_list, no_recurse=False, quiet=False):
        from bzrlib.add import smart_add, add_reporter_print, add_reporter_null
        if quiet:
            reporter = add_reporter_null
        else:
            reporter = add_reporter_print
        smart_add(file_list, not no_recurse, reporter)


class cmd_mkdir(Command):
    """Create a new versioned directory.

    This is equivalent to creating the directory and then adding it.
    """
    takes_args = ['dir+']

    def run(self, dir_list):
        b = None
        
        for d in dir_list:
            os.mkdir(d)
            if not b:
                b = Branch.open_containing(d)[0]
            b.add([d])
            print 'added', d


class cmd_relpath(Command):
    """Show path of a file relative to root"""
    takes_args = ['filename']
    hidden = True
    
    @display_command
    def run(self, filename):
        branch, relpath = Branch.open_containing(filename)
        print relpath


class cmd_inventory(Command):
    """Show inventory of the current working copy or a revision."""
    takes_options = ['revision', 'show-ids']
    
    @display_command
    def run(self, revision=None, show_ids=False):
        b = Branch.open_containing('.')[0]
        if revision is None:
            inv = b.read_working_inventory()
        else:
            if len(revision) > 1:
                raise BzrCommandError('bzr inventory --revision takes'
                    ' exactly one revision identifier')
            inv = b.get_revision_inventory(revision[0].in_history(b).rev_id)

        for path, entry in inv.entries():
            if show_ids:
                print '%-50s %s' % (path, entry.file_id)
            else:
                print path


class cmd_move(Command):
    """Move files to a different directory.

    examples:
        bzr move *.txt doc

    The destination must be a versioned directory in the same branch.
    """
    takes_args = ['source$', 'dest']
    def run(self, source_list, dest):
        b, source_list = branch_files(source_list)

        # TODO: glob expansion on windows?
        tree = WorkingTree(b.base, b)
        b.move(source_list, tree.relpath(dest))


class cmd_rename(Command):
    """Change the name of an entry.

    examples:
      bzr rename frob.c frobber.c
      bzr rename src/frob.c lib/frob.c

    It is an error if the destination name exists.

    See also the 'move' command, which moves files into a different
    directory without changing their name.
    """
    # TODO: Some way to rename multiple files without invoking 
    # bzr for each one?"""
    takes_args = ['from_name', 'to_name']
    
    def run(self, from_name, to_name):
        b, (from_name, to_name) = branch_files((from_name, to_name))
        b.rename_one(from_name, to_name)


class cmd_mv(Command):
    """Move or rename a file.

    usage:
        bzr mv OLDNAME NEWNAME
        bzr mv SOURCE... DESTINATION

    If the last argument is a versioned directory, all the other names
    are moved into it.  Otherwise, there must be exactly two arguments
    and the file is changed to a new name, which must not already exist.

    Files cannot be moved between branches.
    """
    takes_args = ['names*']
    def run(self, names_list):
        if len(names_list) < 2:
            raise BzrCommandError("missing file argument")
        b, rel_names = branch_files(names_list)
        
        if os.path.isdir(names_list[-1]):
            # move into existing directory
            for pair in b.move(rel_names[:-1], rel_names[-1]):
                print "%s => %s" % pair
        else:
            if len(names_list) != 2:
                raise BzrCommandError('to mv multiple files the destination '
                                      'must be a versioned directory')
            b.rename_one(rel_names[0], rel_names[1])
            print "%s => %s" % (rel_names[0], rel_names[1])
            
    
class cmd_pull(Command):
    """Pull any changes from another branch into the current one.

    If there is no default location set, the first pull will set it.  After
    that, you can omit the location to use the default.  To change the
    default, use --remember.

    This command only works on branches that have not diverged.  Branches are
    considered diverged if both branches have had commits without first
    pulling from the other.

    If branches have diverged, you can use 'bzr merge' to pull the text changes
    from one into the other.  Once one branch has merged, the other should
    be able to pull it again.

    If you want to forget your local changes and just update your branch to
    match the remote one, use --overwrite.
    """
    takes_options = ['remember', 'overwrite']
    takes_args = ['location?']

    def run(self, location=None, remember=False, overwrite=False):
        from bzrlib.merge import merge
        from shutil import rmtree
        import errno
        
        br_to = Branch.open_containing('.')[0]
        stored_loc = br_to.get_parent()
        if location is None:
            if stored_loc is None:
                raise BzrCommandError("No pull location known or specified.")
            else:
                print "Using saved location: %s" % stored_loc
                location = stored_loc
        if br_to.get_parent() is None or remember:
            br_to.set_parent(location)
        br_from = Branch.open(location)
        try:
            br_to.working_tree().pull(br_from, overwrite)
        except DivergedBranches:
            raise BzrCommandError("These branches have diverged."
                                  "  Try merge.")


class cmd_push(Command):
    """Push this branch into another branch.
    
    The remote branch will not have its working tree populated because this
    is both expensive, and may not be supported on the remote file system.
    
    Some smart servers or protocols *may* put the working tree in place.

    If there is no default push location set, the first push will set it.
    After that, you can omit the location to use the default.  To change the
    default, use --remember.

    This command only works on branches that have not diverged.  Branches are
    considered diverged if the branch being pushed to is not an older version
    of this branch.

    If branches have diverged, you can use 'bzr push --overwrite' to replace
    the other branch completely.
    
    If you want to ensure you have the different changes in the other branch,
    do a merge (see bzr help merge) from the other branch, and commit that
    before doing a 'push --overwrite'.
    """
    takes_options = ['remember', 'overwrite']
    takes_args = ['location?']

    def run(self, location=None, remember=False, overwrite=False):
        import errno
        from shutil import rmtree
        from bzrlib.transport import get_transport
        
        br_from = Branch.open_containing('.')[0]
        stored_loc = br_from.get_push_location()
        if location is None:
            if stored_loc is None:
                raise BzrCommandError("No push location known or specified.")
            else:
                print "Using saved location: %s" % stored_loc
                location = stored_loc
        if br_from.get_push_location() is None or remember:
            br_from.set_push_location(location)
        try:
            br_to = Branch.open(location)
        except NotBranchError:
            # create a branch.
            transport = get_transport(location).clone('..')
            transport.mkdir(transport.relpath(location))
            br_to = Branch.initialize(location)
        try:
            br_to.pull(br_from, overwrite)
        except DivergedBranches:
            raise BzrCommandError("These branches have diverged."
                                  "  Try a merge then push with overwrite.")


class cmd_branch(Command):
    """Create a new copy of a branch.

    If the TO_LOCATION is omitted, the last component of the FROM_LOCATION will
    be used.  In other words, "branch ../foo/bar" will attempt to create ./bar.

    To retrieve the branch as of a particular revision, supply the --revision
    parameter, as in "branch foo/bar -r 5".

    --basis is to speed up branching from remote branches.  When specified, it
    copies all the file-contents, inventory and revision data from the basis
    branch before copying anything from the remote branch.
    """
    takes_args = ['from_location', 'to_location?']
    takes_options = ['revision', 'basis']
    aliases = ['get', 'clone']

    def run(self, from_location, to_location=None, revision=None, basis=None):
        from bzrlib.clone import copy_branch
        import errno
        from shutil import rmtree
        if revision is None:
            revision = [None]
        elif len(revision) > 1:
            raise BzrCommandError(
                'bzr branch --revision takes exactly 1 revision value')
        try:
            br_from = Branch.open(from_location)
        except OSError, e:
            if e.errno == errno.ENOENT:
                raise BzrCommandError('Source location "%s" does not'
                                      ' exist.' % to_location)
            else:
                raise
        br_from.lock_read()
        try:
            if basis is not None:
                basis_branch = Branch.open_containing(basis)[0]
            else:
                basis_branch = None
            if len(revision) == 1 and revision[0] is not None:
                revision_id = revision[0].in_history(br_from)[1]
            else:
                revision_id = None
            if to_location is None:
                to_location = os.path.basename(from_location.rstrip("/\\"))
                name = None
            else:
                name = os.path.basename(to_location) + '\n'
            try:
                os.mkdir(to_location)
            except OSError, e:
                if e.errno == errno.EEXIST:
                    raise BzrCommandError('Target directory "%s" already'
                                          ' exists.' % to_location)
                if e.errno == errno.ENOENT:
                    raise BzrCommandError('Parent of "%s" does not exist.' %
                                          to_location)
                else:
                    raise
            try:
                copy_branch(br_from, to_location, revision_id, basis_branch)
            except bzrlib.errors.NoSuchRevision:
                rmtree(to_location)
                msg = "The branch %s has no revision %s." % (from_location, revision[0])
                raise BzrCommandError(msg)
            except bzrlib.errors.UnlistableBranch:
                rmtree(to_location)
                msg = "The branch %s cannot be used as a --basis"
                raise BzrCommandError(msg)
            if name:
                branch = Branch.open(to_location)
                name = StringIO(name)
                branch.put_controlfile('branch-name', name)
        finally:
            br_from.unlock()


class cmd_renames(Command):
    """Show list of renamed files.
    """
    # TODO: Option to show renames between two historical versions.

    # TODO: Only show renames under dir, rather than in the whole branch.
    takes_args = ['dir?']

    @display_command
    def run(self, dir='.'):
        b = Branch.open_containing(dir)[0]
        old_inv = b.basis_tree().inventory
        new_inv = b.read_working_inventory()

        renames = list(bzrlib.tree.find_renames(old_inv, new_inv))
        renames.sort()
        for old_name, new_name in renames:
            print "%s => %s" % (old_name, new_name)        


class cmd_info(Command):
    """Show statistical information about a branch."""
    takes_args = ['branch?']
    
    @display_command
    def run(self, branch=None):
        import info
        b = Branch.open_containing(branch)[0]
        info.show_info(b)


class cmd_remove(Command):
    """Make a file unversioned.

    This makes bzr stop tracking changes to a versioned file.  It does
    not delete the working copy.
    """
    takes_args = ['file+']
    takes_options = ['verbose']
    aliases = ['rm']
    
    def run(self, file_list, verbose=False):
        b, file_list = branch_files(file_list)
        tree = b.working_tree()
        tree.remove(file_list, verbose=verbose)


class cmd_file_id(Command):
    """Print file_id of a particular file or directory.

    The file_id is assigned when the file is first added and remains the
    same through all revisions where the file exists, even when it is
    moved or renamed.
    """
    hidden = True
    takes_args = ['filename']
    @display_command
    def run(self, filename):
        b, relpath = Branch.open_containing(filename)
        i = b.inventory.path2id(relpath)
        if i == None:
            raise BzrError("%r is not a versioned file" % filename)
        else:
            print i


class cmd_file_path(Command):
    """Print path of file_ids to a file or directory.

    This prints one line for each directory down to the target,
    starting at the branch root."""
    hidden = True
    takes_args = ['filename']
    @display_command
    def run(self, filename):
        b, relpath = Branch.open_containing(filename)
        inv = b.inventory
        fid = inv.path2id(relpath)
        if fid == None:
            raise BzrError("%r is not a versioned file" % filename)
        for fip in inv.get_idpath(fid):
            print fip


class cmd_revision_history(Command):
    """Display list of revision ids on this branch."""
    hidden = True
    @display_command
    def run(self):
        for patchid in Branch.open_containing('.')[0].revision_history():
            print patchid


class cmd_ancestry(Command):
    """List all revisions merged into this branch."""
    hidden = True
    @display_command
    def run(self):
        b = Branch.open_containing('.')[0]
        for revision_id in b.get_ancestry(b.last_revision()):
            print revision_id


class cmd_directories(Command):
    """Display list of versioned directories in this branch."""
    @display_command
    def run(self):
        for name, ie in Branch.open_containing('.')[0].read_working_inventory().directories():
            if name == '':
                print '.'
            else:
                print name


class cmd_init(Command):
    """Make a directory into a versioned branch.

    Use this to create an empty branch, or before importing an
    existing project.

    Recipe for importing a tree of files:
        cd ~/project
        bzr init
        bzr add .
        bzr status
        bzr commit -m 'imported project'
    """
    def run(self):
        Branch.initialize('.')


class cmd_diff(Command):
    """Show differences in working tree.
    
    If files are listed, only the changes in those files are listed.
    Otherwise, all changes for the tree are listed.

    examples:
        bzr diff
        bzr diff -r1
        bzr diff -r1..2
    """
    # TODO: Allow diff across branches.
    # TODO: Option to use external diff command; could be GNU diff, wdiff,
    #       or a graphical diff.

    # TODO: Python difflib is not exactly the same as unidiff; should
    #       either fix it up or prefer to use an external diff.

    # TODO: If a directory is given, diff everything under that.

    # TODO: Selected-file diff is inefficient and doesn't show you
    #       deleted files.

    # TODO: This probably handles non-Unix newlines poorly.
    
    takes_args = ['file*']
    takes_options = ['revision', 'diff-options']
    aliases = ['di', 'dif']

    @display_command
    def run(self, revision=None, file_list=None, diff_options=None):
        from bzrlib.diff import show_diff
        
        b, file_list = branch_files(file_list)
        if revision is not None:
            if len(revision) == 1:
                return show_diff(b, revision[0], specific_files=file_list,
                                 external_diff_options=diff_options)
            elif len(revision) == 2:
                return show_diff(b, revision[0], specific_files=file_list,
                                 external_diff_options=diff_options,
                                 revision2=revision[1])
            else:
                raise BzrCommandError('bzr diff --revision takes exactly one or two revision identifiers')
        else:
            return show_diff(b, None, specific_files=file_list,
                             external_diff_options=diff_options)


class cmd_deleted(Command):
    """List files deleted in the working tree.
    """
    # TODO: Show files deleted since a previous revision, or
    # between two revisions.
    # TODO: Much more efficient way to do this: read in new
    # directories with readdir, rather than stating each one.  Same
    # level of effort but possibly much less IO.  (Or possibly not,
    # if the directories are very large...)
    @display_command
    def run(self, show_ids=False):
        b = Branch.open_containing('.')[0]
        old = b.basis_tree()
        new = b.working_tree()
        for path, ie in old.inventory.iter_entries():
            if not new.has_id(ie.file_id):
                if show_ids:
                    print '%-50s %s' % (path, ie.file_id)
                else:
                    print path


class cmd_modified(Command):
    """List files modified in working tree."""
    hidden = True
    @display_command
    def run(self):
        from bzrlib.delta import compare_trees

        b = Branch.open_containing('.')[0]
        td = compare_trees(b.basis_tree(), b.working_tree())

        for path, id, kind, text_modified, meta_modified in td.modified:
            print path



class cmd_added(Command):
    """List files added in working tree."""
    hidden = True
    @display_command
    def run(self):
        b = Branch.open_containing('.')[0]
        wt = b.working_tree()
        basis_inv = b.basis_tree().inventory
        inv = wt.inventory
        for file_id in inv:
            if file_id in basis_inv:
                continue
            path = inv.id2path(file_id)
            if not os.access(b.abspath(path), os.F_OK):
                continue
            print path
                
        

class cmd_root(Command):
    """Show the tree root directory.

    The root is the nearest enclosing directory with a .bzr control
    directory."""
    takes_args = ['filename?']
    @display_command
    def run(self, filename=None):
        """Print the branch root."""
        b = Branch.open_containing(filename)[0]
        print b.base


class cmd_log(Command):
    """Show log of this branch.

    To request a range of logs, you can use the command -r begin:end
    -r revision requests a specific revision, -r :end or -r begin: are
    also valid.
    """

    # TODO: Make --revision support uuid: and hash: [future tag:] notation.

    takes_args = ['filename?']
    takes_options = [Option('forward', 
                            help='show from oldest to newest'),
                     'timezone', 'verbose', 
                     'show-ids', 'revision',
                     Option('line', help='format with one line per revision'),
                     'long', 
                     Option('message',
                            help='show revisions whose message matches this regexp',
                            type=str),
                     Option('short', help='use moderately short format'),
                     ]
    @display_command
    def run(self, filename=None, timezone='original',
            verbose=False,
            show_ids=False,
            forward=False,
            revision=None,
            message=None,
            long=False,
            short=False,
            line=False):
        from bzrlib.log import log_formatter, show_log
        import codecs
        assert message is None or isinstance(message, basestring), \
            "invalid message argument %r" % message
        direction = (forward and 'forward') or 'reverse'
        
        if filename:
            b, fp = Branch.open_containing(filename)
            if fp != '':
                file_id = b.read_working_inventory().path2id(fp)
            else:
                file_id = None  # points to branch root
        else:
            b, relpath = Branch.open_containing('.')
            file_id = None

        if revision is None:
            rev1 = None
            rev2 = None
        elif len(revision) == 1:
            rev1 = rev2 = revision[0].in_history(b).revno
        elif len(revision) == 2:
            rev1 = revision[0].in_history(b).revno
            rev2 = revision[1].in_history(b).revno
        else:
            raise BzrCommandError('bzr log --revision takes one or two values.')

        if rev1 == 0:
            rev1 = None
        if rev2 == 0:
            rev2 = None

        mutter('encoding log as %r' % bzrlib.user_encoding)

        # use 'replace' so that we don't abort if trying to write out
        # in e.g. the default C locale.
        outf = codecs.getwriter(bzrlib.user_encoding)(sys.stdout, errors='replace')

        log_format = 'long'
        if short:
            log_format = 'short'
        if line:
            log_format = 'line'
        lf = log_formatter(log_format,
                           show_ids=show_ids,
                           to_file=outf,
                           show_timezone=timezone)

        show_log(b,
                 lf,
                 file_id,
                 verbose=verbose,
                 direction=direction,
                 start_revision=rev1,
                 end_revision=rev2,
                 search=message)



class cmd_touching_revisions(Command):
    """Return revision-ids which affected a particular file.

    A more user-friendly interface is "bzr log FILE"."""
    hidden = True
    takes_args = ["filename"]
    @display_command
    def run(self, filename):
        b, relpath = Branch.open_containing(filename)[0]
        inv = b.read_working_inventory()
        file_id = inv.path2id(relpath)
        for revno, revision_id, what in bzrlib.log.find_touching_revisions(b, file_id):
            print "%6d %s" % (revno, what)


class cmd_ls(Command):
    """List files in a tree.
    """
    # TODO: Take a revision or remote path and list that tree instead.
    hidden = True
    takes_options = ['verbose', 'revision',
                     Option('non-recursive',
                            help='don\'t recurse into sub-directories'),
                     Option('from-root',
                            help='Print all paths from the root of the branch.'),
                     Option('unknown', help='Print unknown files'),
                     Option('versioned', help='Print versioned files'),
                     Option('ignored', help='Print ignored files'),

                     Option('null', help='Null separate the files'),
                    ]
    @display_command
    def run(self, revision=None, verbose=False, 
            non_recursive=False, from_root=False,
            unknown=False, versioned=False, ignored=False,
            null=False):

        if verbose and null:
            raise BzrCommandError('Cannot set both --verbose and --null')
        all = not (unknown or versioned or ignored)

        selection = {'I':ignored, '?':unknown, 'V':versioned}

        b, relpath = Branch.open_containing('.')
        if from_root:
            relpath = ''
        elif relpath:
            relpath += '/'
        if revision == None:
            tree = b.working_tree()
        else:
            tree = b.revision_tree(revision[0].in_history(b).rev_id)
        for fp, fc, kind, fid, entry in tree.list_files():
            if fp.startswith(relpath):
                fp = fp[len(relpath):]
                if non_recursive and '/' in fp:
                    continue
                if not all and not selection[fc]:
                    continue
                if verbose:
                    kindch = entry.kind_character()
                    print '%-8s %s%s' % (fc, fp, kindch)
                elif null:
                    sys.stdout.write(fp)
                    sys.stdout.write('\0')
                    sys.stdout.flush()
                else:
                    print fp



class cmd_unknowns(Command):
    """List unknown files."""
    @display_command
    def run(self):
        from bzrlib.osutils import quotefn
        for f in Branch.open_containing('.')[0].unknowns():
            print quotefn(f)



class cmd_ignore(Command):
    """Ignore a command or pattern.

    To remove patterns from the ignore list, edit the .bzrignore file.

    If the pattern contains a slash, it is compared to the whole path
    from the branch root.  Otherwise, it is compared to only the last
    component of the path.  To match a file only in the root directory,
    prepend './'.

    Ignore patterns are case-insensitive on case-insensitive systems.

    Note: wildcards must be quoted from the shell on Unix.

    examples:
        bzr ignore ./Makefile
        bzr ignore '*.class'
    """
    # TODO: Complain if the filename is absolute
    takes_args = ['name_pattern']
    
    def run(self, name_pattern):
        from bzrlib.atomicfile import AtomicFile
        import os.path

        b, relpath = Branch.open_containing('.')
        ifn = b.abspath('.bzrignore')

        if os.path.exists(ifn):
            f = open(ifn, 'rt')
            try:
                igns = f.read().decode('utf-8')
            finally:
                f.close()
        else:
            igns = ''

        # TODO: If the file already uses crlf-style termination, maybe
        # we should use that for the newly added lines?

        if igns and igns[-1] != '\n':
            igns += '\n'
        igns += name_pattern + '\n'

        try:
            f = AtomicFile(ifn, 'wt')
            f.write(igns.encode('utf-8'))
            f.commit()
        finally:
            f.close()

        inv = b.working_tree().inventory
        if inv.path2id('.bzrignore'):
            mutter('.bzrignore is already versioned')
        else:
            mutter('need to make new .bzrignore file versioned')
            b.add(['.bzrignore'])



class cmd_ignored(Command):
    """List ignored files and the patterns that matched them.

    See also: bzr ignore"""
    @display_command
    def run(self):
        tree = Branch.open_containing('.')[0].working_tree()
        for path, file_class, kind, file_id, entry in tree.list_files():
            if file_class != 'I':
                continue
            ## XXX: Slightly inefficient since this was already calculated
            pat = tree.is_ignored(path)
            print '%-50s %s' % (path, pat)


class cmd_lookup_revision(Command):
    """Lookup the revision-id from a revision-number

    example:
        bzr lookup-revision 33
    """
    hidden = True
    takes_args = ['revno']
    
    @display_command
    def run(self, revno):
        try:
            revno = int(revno)
        except ValueError:
            raise BzrCommandError("not a valid revision-number: %r" % revno)

        print Branch.open_containing('.')[0].get_rev_id(revno)


class cmd_export(Command):
    """Export past revision to destination directory.

    If no revision is specified this exports the last committed revision.

    Format may be an "exporter" name, such as tar, tgz, tbz2.  If none is
    given, try to find the format with the extension. If no extension
    is found exports to a directory (equivalent to --format=dir).

    Root may be the top directory for tar, tgz and tbz2 formats. If none
    is given, the top directory will be the root name of the file."""
    # TODO: list known exporters
    takes_args = ['dest']
    takes_options = ['revision', 'format', 'root']
    def run(self, dest, revision=None, format=None, root=None):
        import os.path
        b = Branch.open_containing('.')[0]
        if revision is None:
            rev_id = b.last_revision()
        else:
            if len(revision) != 1:
                raise BzrError('bzr export --revision takes exactly 1 argument')
            rev_id = revision[0].in_history(b).rev_id
        t = b.revision_tree(rev_id)
        arg_root, ext = os.path.splitext(os.path.basename(dest))
        if ext in ('.gz', '.bz2'):
            new_root, new_ext = os.path.splitext(arg_root)
            if new_ext == '.tar':
                arg_root = new_root
                ext = new_ext + ext
        if root is None:
            root = arg_root
        if not format:
            if ext in (".tar",):
                format = "tar"
            elif ext in (".tar.gz", ".tgz"):
                format = "tgz"
            elif ext in (".tar.bz2", ".tbz2"):
                format = "tbz2"
            else:
                format = "dir"
        t.export(dest, format, root)


class cmd_cat(Command):
    """Write a file's text from a previous revision."""

    takes_options = ['revision']
    takes_args = ['filename']

    @display_command
    def run(self, filename, revision=None):
        if revision is None:
            raise BzrCommandError("bzr cat requires a revision number")
        elif len(revision) != 1:
            raise BzrCommandError("bzr cat --revision takes exactly one number")
        b, relpath = Branch.open_containing(filename)
        b.print_file(relpath, revision[0].in_history(b).revno)


class cmd_local_time_offset(Command):
    """Show the offset in seconds from GMT to local time."""
    hidden = True    
    @display_command
    def run(self):
        print bzrlib.osutils.local_time_offset()



class cmd_commit(Command):
    """Commit changes into a new revision.
    
    If no arguments are given, the entire tree is committed.

    If selected files are specified, only changes to those files are
    committed.  If a directory is specified then the directory and everything 
    within it is committed.

    A selected-file commit may fail in some cases where the committed
    tree would be invalid, such as trying to commit a file in a
    newly-added directory that is not itself committed.
    """
    # TODO: Run hooks on tree to-be-committed, and after commit.

    # TODO: Strict commit that fails if there are deleted files.
    #       (what does "deleted files" mean ??)

    # TODO: Give better message for -s, --summary, used by tla people

    # XXX: verbose currently does nothing

    takes_args = ['selected*']
    takes_options = ['message', 'verbose', 
                     Option('unchanged',
                            help='commit even if nothing has changed'),
                     Option('file', type=str, 
                            argname='msgfile',
                            help='file containing commit message'),
                     Option('strict',
                            help="refuse to commit if there are unknown "
                            "files in the working tree."),
                     ]
    aliases = ['ci', 'checkin']

    def run(self, message=None, file=None, verbose=True, selected_list=None,
            unchanged=False, strict=False):
        from bzrlib.errors import (PointlessCommit, ConflictsInTree,
                StrictCommitFailed)
        from bzrlib.msgeditor import edit_commit_message
        from bzrlib.status import show_status
        from cStringIO import StringIO

        b, selected_list = branch_files(selected_list)
        if message is None and not file:
            catcher = StringIO()
            show_status(b, specific_files=selected_list,
                        to_file=catcher)
            message = edit_commit_message(catcher.getvalue())

            if message is None:
                raise BzrCommandError("please specify a commit message"
                                      " with either --message or --file")
        elif message and file:
            raise BzrCommandError("please specify either --message or --file")
        
        if file:
            import codecs
            message = codecs.open(file, 'rt', bzrlib.user_encoding).read()

        if message == "":
                raise BzrCommandError("empty commit message specified")
            
        try:
            b.commit(message, specific_files=selected_list,
                     allow_pointless=unchanged, strict=strict)
        except PointlessCommit:
            # FIXME: This should really happen before the file is read in;
            # perhaps prepare the commit; get the message; then actually commit
            raise BzrCommandError("no changes to commit",
                                  ["use --unchanged to commit anyhow"])
        except ConflictsInTree:
            raise BzrCommandError("Conflicts detected in working tree.  "
                'Use "bzr conflicts" to list, "bzr resolve FILE" to resolve.')
        except StrictCommitFailed:
            raise BzrCommandError("Commit refused because there are unknown "
                                  "files in the working tree.")


class cmd_check(Command):
    """Validate consistency of branch history.

    This command checks various invariants about the branch storage to
    detect data corruption or bzr bugs.
    """
    takes_args = ['dir?']
    takes_options = ['verbose']

    def run(self, dir='.', verbose=False):
        from bzrlib.check import check
        check(Branch.open_containing(dir)[0], verbose)


class cmd_scan_cache(Command):
    hidden = True
    def run(self):
        from bzrlib.hashcache import HashCache

        c = HashCache('.')
        c.read()
        c.scan()
            
        print '%6d stats' % c.stat_count
        print '%6d in hashcache' % len(c._cache)
        print '%6d files removed from cache' % c.removed_count
        print '%6d hashes updated' % c.update_count
        print '%6d files changed too recently to cache' % c.danger_count

        if c.needs_write:
            c.write()
            


class cmd_upgrade(Command):
    """Upgrade branch storage to current format.

    The check command or bzr developers may sometimes advise you to run
    this command.

    This version of this command upgrades from the full-text storage
    used by bzr 0.0.8 and earlier to the weave format (v5).
    """
    takes_args = ['dir?']

    def run(self, dir='.'):
        from bzrlib.upgrade import upgrade
        upgrade(dir)


class cmd_whoami(Command):
    """Show bzr user id."""
    takes_options = ['email']
    
    @display_command
    def run(self, email=False):
        try:
            b = bzrlib.branch.Branch.open_containing('.')[0]
            config = bzrlib.config.BranchConfig(b)
        except NotBranchError:
            config = bzrlib.config.GlobalConfig()
        
        if email:
            print config.user_email()
        else:
            print config.username()


class cmd_selftest(Command):
    """Run internal test suite.
    
    This creates temporary test directories in the working directory,
    but not existing data is affected.  These directories are deleted
    if the tests pass, or left behind to help in debugging if they
    fail.
    
    If arguments are given, they are regular expressions that say
    which tests should run.
    """
    # TODO: --list should give a list of all available tests
    hidden = True
    takes_args = ['testspecs*']
    takes_options = ['verbose', 
                     Option('one', help='stop when one test fails'),
                    ]

    def run(self, testspecs_list=None, verbose=False, one=False):
        import bzrlib.ui
        from bzrlib.selftest import selftest
        # we don't want progress meters from the tests to go to the
        # real output; and we don't want log messages cluttering up
        # the real logs.
        save_ui = bzrlib.ui.ui_factory
        bzrlib.trace.info('running tests...')
        try:
            bzrlib.ui.ui_factory = bzrlib.ui.SilentUIFactory()
            if testspecs_list is not None:
                pattern = '|'.join(testspecs_list)
            else:
                pattern = ".*"
            result = selftest(verbose=verbose, 
                              pattern=pattern,
                              stop_on_failure=one)
            if result:
                bzrlib.trace.info('tests passed')
            else:
                bzrlib.trace.info('tests failed')
            return int(not result)
        finally:
            bzrlib.ui.ui_factory = save_ui


def show_version():
    print "bzr (bazaar-ng) %s" % bzrlib.__version__
    # is bzrlib itself in a branch?
    bzrrev = bzrlib.get_bzr_revision()
    if bzrrev:
        print "  (bzr checkout, revision %d {%s})" % bzrrev
    print bzrlib.__copyright__
    print "http://bazaar-ng.org/"
    print
    print "bzr comes with ABSOLUTELY NO WARRANTY.  bzr is free software, and"
    print "you may use, modify and redistribute it under the terms of the GNU"
    print "General Public License version 2 or later."


class cmd_version(Command):
    """Show version of bzr."""
    @display_command
    def run(self):
        show_version()

class cmd_rocks(Command):
    """Statement of optimism."""
    hidden = True
    @display_command
    def run(self):
        print "it sure does!"


class cmd_find_merge_base(Command):
    """Find and print a base revision for merging two branches.
    """
    # TODO: Options to specify revisions on either side, as if
    #       merging only part of the history.
    takes_args = ['branch', 'other']
    hidden = True
    
    @display_command
    def run(self, branch, other):
        from bzrlib.revision import common_ancestor, MultipleRevisionSources
        
        branch1 = Branch.open_containing(branch)[0]
        branch2 = Branch.open_containing(other)[0]

        history_1 = branch1.revision_history()
        history_2 = branch2.revision_history()

        last1 = branch1.last_revision()
        last2 = branch2.last_revision()

        source = MultipleRevisionSources(branch1, branch2)
        
        base_rev_id = common_ancestor(last1, last2, source)

        print 'merge base is revision %s' % base_rev_id
        
        return

        if base_revno is None:
            raise bzrlib.errors.UnrelatedBranches()

        print ' r%-6d in %s' % (base_revno, branch)

        other_revno = branch2.revision_id_to_revno(base_revid)
        
        print ' r%-6d in %s' % (other_revno, other)



class cmd_merge(Command):
    """Perform a three-way merge.
    
    The branch is the branch you will merge from.  By default, it will
    merge the latest revision.  If you specify a revision, that
    revision will be merged.  If you specify two revisions, the first
    will be used as a BASE, and the second one as OTHER.  Revision
    numbers are always relative to the specified branch.

    By default bzr will try to merge in all new work from the other
    branch, automatically determining an appropriate base.  If this
    fails, you may need to give an explicit base.
    
    Examples:

    To merge the latest revision from bzr.dev
    bzr merge ../bzr.dev

    To merge changes up to and including revision 82 from bzr.dev
    bzr merge -r 82 ../bzr.dev

    To merge the changes introduced by 82, without previous changes:
    bzr merge -r 81..82 ../bzr.dev
    
    merge refuses to run if there are any uncommitted changes, unless
    --force is given.
    """
    takes_args = ['branch?']
    takes_options = ['revision', 'force', 'merge-type', 'reprocess',
                     Option('show-base', help="Show base revision text in "
                            "conflicts")]

    def run(self, branch=None, revision=None, force=False, merge_type=None,
            show_base=False, reprocess=False):
        from bzrlib.merge import merge
        from bzrlib.merge_core import ApplyMerge3
        if merge_type is None:
            merge_type = ApplyMerge3
        if branch is None:
            branch = Branch.open_containing('.')[0].get_parent()
            if branch is None:
                raise BzrCommandError("No merge location known or specified.")
            else:
                print "Using saved location: %s" % branch 
        if revision is None or len(revision) < 1:
            base = [None, None]
            other = [branch, -1]
        else:
            if len(revision) == 1:
                base = [None, None]
                other_branch = Branch.open_containing(branch)[0]
                revno = revision[0].in_history(other_branch).revno
                other = [branch, revno]
            else:
                assert len(revision) == 2
                if None in revision:
                    raise BzrCommandError(
                        "Merge doesn't permit that revision specifier.")
                b = Branch.open_containing(branch)[0]

                base = [branch, revision[0].in_history(b).revno]
                other = [branch, revision[1].in_history(b).revno]

        try:
            conflict_count = merge(other, base, check_clean=(not force),
                                   merge_type=merge_type, reprocess=reprocess,
                                   show_base=show_base)
            if conflict_count != 0:
                return 1
            else:
                return 0
        except bzrlib.errors.AmbiguousBase, e:
            m = ("sorry, bzr can't determine the right merge base yet\n"
                 "candidates are:\n  "
                 + "\n  ".join(e.bases)
                 + "\n"
                 "please specify an explicit base with -r,\n"
                 "and (if you want) report this to the bzr developers\n")
            log_error(m)


class cmd_revert(Command):
    """Reverse all changes since the last commit.

    Only versioned files are affected.  Specify filenames to revert only 
    those files.  By default, any files that are changed will be backed up
    first.  Backup files have a '~' appended to their name.
    """
    takes_options = ['revision', 'no-backup']
    takes_args = ['file*']
    aliases = ['merge-revert']

    def run(self, revision=None, no_backup=False, file_list=None):
        from bzrlib.merge import merge
        from bzrlib.commands import parse_spec

        if file_list is not None:
            if len(file_list) == 0:
                raise BzrCommandError("No files specified")
        if revision is None:
            revno = -1
        elif len(revision) != 1:
            raise BzrCommandError('bzr revert --revision takes exactly 1 argument')
        else:
            b, file_list = branch_files(file_list)
            revno = revision[0].in_history(b).revno
        merge(('.', revno), parse_spec('.'),
              check_clean=False,
              ignore_zero=True,
              backup_files=not no_backup,
              file_list=file_list)
        if not file_list:
            Branch.open_containing('.')[0].set_pending_merges([])


class cmd_assert_fail(Command):
    """Test reporting of assertion failures"""
    hidden = True
    def run(self):
        assert False, "always fails"


class cmd_help(Command):
    """Show help on a command or other topic.

    For a list of all available commands, say 'bzr help commands'."""
    takes_options = ['long']
    takes_args = ['topic?']
    aliases = ['?']
    
    @display_command
    def run(self, topic=None, long=False):
        import help
        if topic is None and long:
            topic = "commands"
        help.help(topic)


class cmd_shell_complete(Command):
    """Show appropriate completions for context.

    For a list of all available commands, say 'bzr shell-complete'."""
    takes_args = ['context?']
    aliases = ['s-c']
    hidden = True
    
    @display_command
    def run(self, context=None):
        import shellcomplete
        shellcomplete.shellcomplete(context)


class cmd_fetch(Command):
    """Copy in history from another branch but don't merge it.

    This is an internal method used for pull and merge."""
    hidden = True
    takes_args = ['from_branch', 'to_branch']
    def run(self, from_branch, to_branch):
        from bzrlib.fetch import Fetcher
        from bzrlib.branch import Branch
        from_b = Branch.open(from_branch)
        to_b = Branch.open(to_branch)
        from_b.lock_read()
        try:
            to_b.lock_write()
            try:
                Fetcher(to_b, from_b)
            finally:
                to_b.unlock()
        finally:
            from_b.unlock()


class cmd_missing(Command):
    """What is missing in this branch relative to other branch.
    """
    # TODO: rewrite this in terms of ancestry so that it shows only
    # unmerged things
    
    takes_args = ['remote?']
    aliases = ['mis', 'miss']
    # We don't have to add quiet to the list, because 
    # unknown options are parsed as booleans
    takes_options = ['verbose', 'quiet']

    @display_command
    def run(self, remote=None, verbose=False, quiet=False):
        from bzrlib.errors import BzrCommandError
        from bzrlib.missing import show_missing

        if verbose and quiet:
            raise BzrCommandError('Cannot pass both quiet and verbose')

        b = Branch.open_containing('.')[0]
        parent = b.get_parent()
        if remote is None:
            if parent is None:
                raise BzrCommandError("No missing location known or specified.")
            else:
                if not quiet:
                    print "Using last location: %s" % parent
                remote = parent
        elif parent is None:
            # We only update parent if it did not exist, missing
            # should not change the parent
            b.set_parent(remote)
        br_remote = Branch.open_containing(remote)[0]
        return show_missing(b, br_remote, verbose=verbose, quiet=quiet)


class cmd_plugins(Command):
    """List plugins"""
    hidden = True
    @display_command
    def run(self):
        import bzrlib.plugin
        from inspect import getdoc
        for plugin in bzrlib.plugin.all_plugins:
            if hasattr(plugin, '__path__'):
                print plugin.__path__[0]
            elif hasattr(plugin, '__file__'):
                print plugin.__file__
            else:
                print `plugin`
                
            d = getdoc(plugin)
            if d:
                print '\t', d.split('\n')[0]


class cmd_testament(Command):
    """Show testament (signing-form) of a revision."""
    takes_options = ['revision', 'long']
    takes_args = ['branch?']
    @display_command
    def run(self, branch='.', revision=None, long=False):
        from bzrlib.testament import Testament
        b = Branch.open_containing(branch)[0]
        b.lock_read()
        try:
            if revision is None:
                rev_id = b.last_revision()
            else:
                rev_id = revision[0].in_history(b).rev_id
            t = Testament.from_revision(b, rev_id)
            if long:
                sys.stdout.writelines(t.as_text_lines())
            else:
                sys.stdout.write(t.as_short_text())
        finally:
            b.unlock()


class cmd_annotate(Command):
    """Show the origin of each line in a file.

    This prints out the given file with an annotation on the left side
    indicating which revision, author and date introduced the change.

    If the origin is the same for a run of consecutive lines, it is 
    shown only at the top, unless the --all option is given.
    """
    # TODO: annotate directories; showing when each file was last changed
    # TODO: annotate a previous version of a file
    # TODO: if the working copy is modified, show annotations on that 
    #       with new uncommitted lines marked
    aliases = ['blame', 'praise']
    takes_args = ['filename']
    takes_options = [Option('all', help='show annotations on all lines'),
                     Option('long', help='show date in annotations'),
                     ]

    @display_command
    def run(self, filename, all=False, long=False):
        from bzrlib.annotate import annotate_file
        b, relpath = Branch.open_containing(filename)
        b.lock_read()
        try:
            tree = WorkingTree(b.base, b)
            tree = b.revision_tree(b.last_revision())
            file_id = tree.inventory.path2id(relpath)
            file_version = tree.inventory[file_id].revision
            annotate_file(b, file_version, file_id, long, all, sys.stdout)
        finally:
            b.unlock()


class cmd_re_sign(Command):
    """Create a digital signature for an existing revision."""
    # TODO be able to replace existing ones.

    hidden = True # is this right ?
    takes_args = ['revision_id?']
    takes_options = ['revision']
    
    def run(self, revision_id=None, revision=None):
        import bzrlib.config as config
        import bzrlib.gpg as gpg
        if revision_id is not None and revision is not None:
            raise BzrCommandError('You can only supply one of revision_id or --revision')
        if revision_id is None and revision is None:
            raise BzrCommandError('You must supply either --revision or a revision_id')
        b = Branch.open_containing('.')[0]
        gpg_strategy = gpg.GPGStrategy(config.BranchConfig(b))
        if revision_id is not None:
            b.sign_revision(revision_id, gpg_strategy)
        elif revision is not None:
            if len(revision) == 1:
                revno, rev_id = revision[0].in_history(b)
                b.sign_revision(rev_id, gpg_strategy)
            elif len(revision) == 2:
                # are they both on rh- if so we can walk between them
                # might be nice to have a range helper for arbitrary
                # revision paths. hmm.
                from_revno, from_revid = revision[0].in_history(b)
                to_revno, to_revid = revision[1].in_history(b)
                if to_revid is None:
                    to_revno = b.revno()
                if from_revno is None or to_revno is None:
                    raise BzrCommandError('Cannot sign a range of non-revision-history revisions')
                for revno in range(from_revno, to_revno + 1):
                    b.sign_revision(b.get_rev_id(revno), gpg_strategy)
            else:
                raise BzrCommandError('Please supply either one revision, or a range.')


# these get imported and then picked up by the scan for cmd_*
# TODO: Some more consistent way to split command definitions across files;
# we do need to load at least some information about them to know of 
# aliases.
from bzrlib.conflicts import cmd_resolve, cmd_conflicts
