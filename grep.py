# Copyright (C) 2010 Canonical Ltd
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

from bzrlib.lazy_import import lazy_import
lazy_import(globals(), """
import codecs
import cStringIO
from fnmatch import fnmatch
import os
import re

from bzrlib import bzrdir
from bzrlib.workingtree import WorkingTree
from bzrlib.revisionspec import RevisionSpec, RevisionSpec_revid, RevisionSpec_revno
from bzrlib import (
    errors,
    lazy_regex,
    osutils,
    textfile,
    trace,
    )
""")

_terminal_encoding = osutils.get_terminal_encoding()
_user_encoding = osutils.get_user_encoding()

class _RevisionNotLinear(Exception):
    """Raised when a revision is not on left-hand history."""

def _rev_on_mainline(rev_tuple):
    """returns True is rev tuple is on mainline"""
    if len(rev_tuple) == 1:
        return True
    return rev_tuple[1] == 0 and rev_tuple[2] == 0

# NOTE: _linear_view_revisions is basided on
# bzrlib.log._linear_view_revisions.
# This should probably be a common public API
def _linear_view_revisions(branch, start_rev_id, end_rev_id):
    # requires that start is older than end
    repo = branch.repository
    for revision_id in repo.iter_reverse_revision_history(end_rev_id):
        revno = branch.revision_id_to_dotted_revno(revision_id)
        revno_str = '.'.join(str(n) for n in revno)
        if revision_id == start_rev_id:
            yield revision_id, revno_str, 0
            break
        yield revision_id, revno_str, 0

# NOTE: _graph_view_revisions is copied from
# bzrlib.log._graph_view_revisions.
# This should probably be a common public API
def _graph_view_revisions(branch, start_rev_id, end_rev_id,
                          rebase_initial_depths=True):
    """Calculate revisions to view including merges, newest to oldest.

    :param branch: the branch
    :param start_rev_id: the lower revision-id
    :param end_rev_id: the upper revision-id
    :param rebase_initial_depth: should depths be rebased until a mainline
      revision is found?
    :return: An iterator of (revision_id, dotted_revno, merge_depth) tuples.
    """
    # requires that start is older than end
    view_revisions = branch.iter_merge_sorted_revisions(
        start_revision_id=end_rev_id, stop_revision_id=start_rev_id,
        stop_rule="with-merges")
    if not rebase_initial_depths:
        for (rev_id, merge_depth, revno, end_of_merge
             ) in view_revisions:
            yield rev_id, '.'.join(map(str, revno)), merge_depth
    else:
        # We're following a development line starting at a merged revision.
        # We need to adjust depths down by the initial depth until we find
        # a depth less than it. Then we use that depth as the adjustment.
        # If and when we reach the mainline, depth adjustment ends.
        depth_adjustment = None
        for (rev_id, merge_depth, revno, end_of_merge
             ) in view_revisions:
            if depth_adjustment is None:
                depth_adjustment = merge_depth
            if depth_adjustment:
                if merge_depth < depth_adjustment:
                    # From now on we reduce the depth adjustement, this can be
                    # surprising for users. The alternative requires two passes
                    # which breaks the fast display of the first revision
                    # though.
                    depth_adjustment = merge_depth
                merge_depth -= depth_adjustment
            yield rev_id, '.'.join(map(str, revno)), merge_depth

def compile_pattern(pattern, flags=0):
    patternc = None
    try:
        # use python's re.compile as we need to catch re.error in case of bad pattern
        lazy_regex.reset_compile()
        patternc = re.compile(pattern, flags)
    except re.error, e:
        raise errors.BzrError("Invalid pattern: '%s'" % pattern)
    return patternc

def is_fixed_string(s):
    if re.match("^([A-Za-z0-9_]|\s)*$", s):
        return True
    return False

def versioned_grep(revision, pattern, compiled_pattern, path_list, recursive,
        line_number, from_root, eol_marker, print_revno, levels,
        include, exclude, verbose, fixed_string, ignore_case, files_with_matches,
        outf):

    wt, relpath = WorkingTree.open_containing('.')
    wt.lock_read()
    try:
        # res_cache is used to cache results for dir grep based on fid.
        # If the fid is does not change between results, it means that
        # the result will be the same apart from revno. In such a case
        # we avoid getting file chunks from repo and grepping. The result
        # is just printed by replacing old revno with new one.
        res_cache = {}

        start_rev = revision[0]
        start_revid = start_rev.as_revision_id(wt.branch)
        if start_revid == None:
            start_rev = RevisionSpec_revno.from_string("revno:1")
            start_revid = start_rev.as_revision_id(wt.branch)
        srevno_tuple = wt.branch.revision_id_to_dotted_revno(start_revid)

        if len(revision) == 2:
            end_rev = revision[1]
            end_revid = end_rev.as_revision_id(wt.branch)
            if end_revid == None:
                end_revno, end_revid = wt.branch.last_revision_info()
            erevno_tuple = wt.branch.revision_id_to_dotted_revno(end_revid)

            grep_mainline = (_rev_on_mainline(srevno_tuple) and
                _rev_on_mainline(erevno_tuple))

            # ensure that we go in reverse order
            if srevno_tuple > erevno_tuple:
                srevno_tuple, erevno_tuple = erevno_tuple, srevno_tuple
                start_revid, end_revid = end_revid, start_revid

            # Optimization: Traversing the mainline in reverse order is much
            # faster when we don't want to look at merged revs. We try this
            # with _linear_view_revisions. If all revs are to be grepped we
            # use the slower _graph_view_revisions
            if levels==1 and grep_mainline:
                given_revs = _linear_view_revisions(wt.branch, start_revid, end_revid)
            else:
                given_revs = _graph_view_revisions(wt.branch, start_revid, end_revid)
        else:
            # We do an optimization below. For grepping a specific revison
            # We don't need to call _graph_view_revisions which is slow.
            # We create the start_rev_tuple for only that specific revision.
            # _graph_view_revisions is used only for revision range.
            start_revno = '.'.join(map(str, srevno_tuple))
            start_rev_tuple = (start_revid, start_revno, 0)
            given_revs = [start_rev_tuple]

        for revid, revno, merge_depth in given_revs:
            if levels == 1 and merge_depth != 0:
                # with level=1 show only top level
                continue

            rev = RevisionSpec_revid.from_string("revid:"+revid)
            tree = rev.as_tree(wt.branch)
            for path in path_list:
                path_for_id = osutils.pathjoin(relpath, path)
                id = tree.path2id(path_for_id)
                if not id:
                    trace.warning("Skipped unknown file '%s'." % path)
                    continue

                if osutils.isdir(path):
                    path_prefix = path
                    res_cache = dir_grep(tree, path, relpath, recursive,
                        line_number, pattern, compiled_pattern,
                        from_root, eol_marker, revno, print_revno,
                        include, exclude, verbose, fixed_string,
                        ignore_case, files_with_matches,
                        outf, path_prefix, res_cache)
                else:
                    versioned_file_grep(tree, id, '.', path,
                        pattern, compiled_pattern, eol_marker, line_number,
                        revno, print_revno, include, exclude, verbose,
                        fixed_string, ignore_case, files_with_matches,
                        outf)
    finally:
        wt.unlock()

def workingtree_grep(pattern, compiled_pattern, path_list, recursive,
        line_number, from_root, eol_marker, include, exclude, verbose,
        fixed_string, ignore_case, files_with_matches, outf):
    revno = print_revno = None # for working tree set revno to None

    tree, branch, relpath = \
        bzrdir.BzrDir.open_containing_tree_or_branch('.')
    tree.lock_read()
    try:
        for path in path_list:
            if osutils.isdir(path):
                path_prefix = path
                dir_grep(tree, path, relpath, recursive, line_number,
                    pattern, compiled_pattern, from_root, eol_marker, revno,
                    print_revno, include, exclude, verbose, fixed_string,
                    ignore_case, files_with_matches, outf, path_prefix)
            else:
                _file_grep(open(path).read(), '.', path, pattern,
                    compiled_pattern, eol_marker, line_number, revno,
                    print_revno, include, exclude, verbose,
                    fixed_string, ignore_case, files_with_matches, outf)
    finally:
        tree.unlock()

def _skip_file(include, exclude, path):
    if include and not _path_in_glob_list(path, include):
        return True
    if exclude and _path_in_glob_list(path, exclude):
        return True
    return False


def dir_grep(tree, path, relpath, recursive, line_number, pattern,
        compiled_pattern, from_root, eol_marker, revno, print_revno,
        include, exclude, verbose, fixed_string, ignore_case,
        files_with_matches, outf, path_prefix, res_cache={}):
    _revno_pattern = re.compile("\~[0-9.]+:")
    dir_res = {}

    # setup relpath to open files relative to cwd
    rpath = relpath
    if relpath:
        rpath = osutils.pathjoin('..',relpath)

    from_dir = osutils.pathjoin(relpath, path)
    if from_root:
        # start searching recursively from root
        from_dir=None
        recursive=True

    to_grep = []
    to_grep_append = to_grep.append
    outf_write = outf.write
    for fp, fc, fkind, fid, entry in tree.list_files(include_root=False,
        from_dir=from_dir, recursive=recursive):

        if _skip_file(include, exclude, fp):
            continue

        if fc == 'V' and fkind == 'file':
            if revno != None:
                # If old result is valid, print results immediately.
                # Otherwise, add file info to to_grep so that the
                # loop later will get chunks and grep them
                file_rev = tree.inventory[fid].revision
                old_res = res_cache.get(file_rev)
                if old_res != None:
                    res = []
                    res_append = res.append
                    new_rev = ('~%s:' % (revno,))
                    for line in old_res:
                        s = _revno_pattern.sub(new_rev, line)
                        res_append(s)
                        outf_write(s)
                    dir_res[file_rev] = res
                else:
                    to_grep_append((fid, (fp, fid)))
            else:
                # we are grepping working tree.
                if from_dir == None:
                    from_dir = '.'

                path_for_file = osutils.pathjoin(tree.basedir, from_dir, fp)
                file_text = codecs.open(path_for_file, 'r').read()
                _file_grep(file_text, rpath, fp,
                    pattern, compiled_pattern, eol_marker, line_number, revno,
                    print_revno, include, exclude, verbose, fixed_string,
                    ignore_case, files_with_matches, outf, path_prefix)

    if revno != None: # grep versioned files
        for (path, fid), chunks in tree.iter_files_bytes(to_grep):
            path = _make_display_path(relpath, path)
            res = _file_grep(chunks[0], rpath, path, pattern,
                compiled_pattern, eol_marker, line_number, revno,
                print_revno, include, exclude, verbose, fixed_string,
                ignore_case, files_with_matches, outf, path_prefix)
            file_rev = tree.inventory[fid].revision
            dir_res[file_rev] = res
    return dir_res

def _make_display_path(relpath, path):
    """Return path string relative to user cwd.

    Take tree's 'relpath' and user supplied 'path', and return path
    that can be displayed to the user.
    """
    if relpath:
        # update path so to display it w.r.t cwd
        # handle windows slash separator
        path = osutils.normpath(osutils.pathjoin(relpath, path))
        path = path.replace('\\', '/')
        path = path.replace(relpath + '/', '', 1)
    return path


def versioned_file_grep(tree, id, relpath, path, pattern, patternc,
        eol_marker, line_number, revno, print_revno, include, exclude,
        verbose, fixed_string, ignore_case, files_with_matches,
        outf, path_prefix = None):
    """Create a file object for the specified id and pass it on to _file_grep.
    """

    path = _make_display_path(relpath, path)
    file_text = tree.get_file_text(id)
    _file_grep(file_text, relpath, path, pattern, patternc, eol_marker,
        line_number, revno, print_revno, include, exclude, verbose,
        fixed_string, ignore_case, files_with_matches, outf, path_prefix)

def _path_in_glob_list(path, glob_list):
    present = False
    for glob in glob_list:
        if fnmatch(path, glob):
            present = True
            break
    return present


def _file_grep(file_text, relpath, path, pattern, patternc, eol_marker,
        line_number, revno, print_revno, include, exclude, verbose,
        fixed_string, ignore_case, files_with_matches, outf, path_prefix=None):
    res = []

    _te = _terminal_encoding
    _ue = _user_encoding

    pattern = pattern.encode(_ue, 'replace')
    if fixed_string and ignore_case:
        pattern = pattern.lower()

    # test and skip binary files
    if '\x00' in file_text[:1024]:
        if verbose:
            trace.warning("Binary file '%s' skipped." % path)
        return res

    if path_prefix and path_prefix != '.':
        # user has passed a dir arg, show that as result prefix
        path = osutils.pathjoin(path_prefix, path)

    path = path.encode(_te, 'replace')

    # for better performance we moved formatting conditionals out
    # of the core loop. hence, the core loop is somewhat duplicated
    # for various combinations of formatting options.

    if files_with_matches:
        # While printing files with matches we only have two case
        # print file name or print file name with revno.
        if print_revno:
            pfmt = "~%s".encode(_te, 'replace')
            if fixed_string:
                for line in file_text.splitlines():
                    if ignore_case:
                        line = line.lower()
                    if pattern in line:
                        s = path + (pfmt % (revno,)) + eol_marker
                        res.append(s)
                        outf.write(s)
                        break
            else:
                for line in file_text.splitlines():
                    if patternc.search(line):
                        s = path + (pfmt % (revno,)) + eol_marker
                        res.append(s)
                        outf.write(s)
                        break
        else:
            if fixed_string:
                for line in file_text.splitlines():
                    if ignore_case:
                        line = line.lower()
                    if pattern in line:
                        s = path + eol_marker
                        res.append(s)
                        outf.write(s)
                        break
            else:
                for line in file_text.splitlines():
                    if patternc.search(line):
                        s = path + eol_marker
                        res.append(s)
                        outf.write(s)
                        break
        return res


    if print_revno and line_number:

        pfmt = "~%s:%d:%s".encode(_te)
        if fixed_string:
            for index, line in enumerate(file_text.splitlines()):
                if ignore_case:
                    line = line.lower()
                if pattern in line:
                    line = line.decode(_te, 'replace')
                    s = path + (pfmt % (revno, index+1, line)) + eol_marker
                    res.append(s)
                    outf.write(s)
        else:
            for index, line in enumerate(file_text.splitlines()):
                if patternc.search(line):
                    line = line.decode(_te, 'replace')
                    s = path + (pfmt % (revno, index+1, line)) + eol_marker
                    res.append(s)
                    outf.write(s)

    elif print_revno and not line_number:

        pfmt = "~%s:%s".encode(_te, 'replace')
        if fixed_string:
            for line in file_text.splitlines():
                if ignore_case:
                    line = line.lower()
                if pattern in line:
                    line = line.decode(_te, 'replace')
                    s = path + (pfmt % (revno, line)) + eol_marker
                    res.append(s)
                    outf.write(s)
        else:
            for line in file_text.splitlines():
                if patternc.search(line):
                    line = line.decode(_te, 'replace')
                    s = path + (pfmt % (revno, line)) + eol_marker
                    res.append(s)
                    outf.write(s)

    elif not print_revno and line_number:

        pfmt = ":%d:%s".encode(_te)
        if fixed_string:
            for index, line in enumerate(file_text.splitlines()):
                if ignore_case:
                    line = line.lower()
                if pattern in line:
                    line = line.decode(_te, 'replace')
                    s = path + (pfmt % (index+1, line)) + eol_marker
                    res.append(s)
                    outf.write(s)
        else:
            for index, line in enumerate(file_text.splitlines()):
                if patternc.search(line):
                    line = line.decode(_te, 'replace')
                    s = path + (pfmt % (index+1, line)) + eol_marker
                    res.append(s)
                    outf.write(s)

    else:

        pfmt = ":%s".encode(_te)
        if fixed_string:
            for line in file_text.splitlines():
                if ignore_case:
                    line = line.lower()
                if pattern in line:
                    line = line.decode(_te, 'replace')
                    s = path + (pfmt % (line,)) + eol_marker
                    res.append(s)
                    outf.write(s)
        else:
            for line in file_text.splitlines():
                if patternc.search(line):
                    line = line.decode(_te, 'replace')
                    s = path + (pfmt % (line,)) + eol_marker
                    res.append(s)
                    outf.write(s)

    return res

