# Copyright (C) 2007-2011 Canonical Ltd
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

"""Tag strategies.

These are contained within a branch and normally constructed
when the branch is opened.  Clients should typically do

  Branch.tags.add('name', 'value')
"""

# NOTE: I was going to call this tags.py, but vim seems to think all files
# called tags* are ctags files... mbp 20070220.

from bzrlib.registry import Registry
from bzrlib.lazy_import import lazy_import
lazy_import(globals(), """
import itertools
import re
import sys

from bzrlib import (
    bencode,
    cleanup,
    errors,
    symbol_versioning,
    trace,
    )
""")


class _Tags(object):

    def __init__(self, branch):
        self.branch = branch

    def has_tag(self, tag_name):
        return self.get_tag_dict().has_key(tag_name)


class DisabledTags(_Tags):
    """Tag storage that refuses to store anything.

    This is used by older formats that can't store tags.
    """

    def _not_supported(self, *a, **k):
        raise errors.TagsNotSupported(self.branch)

    set_tag = _not_supported
    get_tag_dict = _not_supported
    _set_tag_dict = _not_supported
    lookup_tag = _not_supported
    delete_tag = _not_supported

    def merge_to(self, to_tags, overwrite=False, ignore_master=False):
        # we never have anything to copy
        pass

    def rename_revisions(self, rename_map):
        # No tags, so nothing to rename
        pass

    def get_reverse_tag_dict(self):
        # There aren't any tags, so the reverse mapping is empty.
        return {}


class BasicTags(_Tags):
    """Tag storage in an unversioned branch control file.
    """

    def set_tag(self, tag_name, tag_target):
        """Add a tag definition to the branch.

        Behaviour if the tag is already present is not defined (yet).
        """
        # all done with a write lock held, so this looks atomic
        self.branch.lock_write()
        try:
            master = self.branch.get_master_branch()
            if master is not None:
                master.tags.set_tag(tag_name, tag_target)
            td = self.get_tag_dict()
            td[tag_name] = tag_target
            self._set_tag_dict(td)
        finally:
            self.branch.unlock()

    def lookup_tag(self, tag_name):
        """Return the referent string of a tag"""
        td = self.get_tag_dict()
        try:
            return td[tag_name]
        except KeyError:
            raise errors.NoSuchTag(tag_name)

    def get_tag_dict(self):
        self.branch.lock_read()
        try:
            try:
                tag_content = self.branch._get_tags_bytes()
            except errors.NoSuchFile, e:
                # ugly, but only abentley should see this :)
                trace.warning('No branch/tags file in %s.  '
                     'This branch was probably created by bzr 0.15pre.  '
                     'Create an empty file to silence this message.'
                     % (self.branch, ))
                return {}
            return self._deserialize_tag_dict(tag_content)
        finally:
            self.branch.unlock()

    def get_reverse_tag_dict(self):
        """Returns a dict with revisions as keys
           and a list of tags for that revision as value"""
        d = self.get_tag_dict()
        rev = {}
        for key in d:
            try:
                rev[d[key]].append(key)
            except KeyError:
                rev[d[key]] = [key]
        return rev

    def delete_tag(self, tag_name):
        """Delete a tag definition.
        """
        self.branch.lock_write()
        try:
            d = self.get_tag_dict()
            try:
                del d[tag_name]
            except KeyError:
                raise errors.NoSuchTag(tag_name)
            master = self.branch.get_master_branch()
            if master is not None:
                try:
                    master.tags.delete_tag(tag_name)
                except errors.NoSuchTag:
                    pass
            self._set_tag_dict(d)
        finally:
            self.branch.unlock()

    def _set_tag_dict(self, new_dict):
        """Replace all tag definitions

        WARNING: Calling this on an unlocked branch will lock it, and will
        replace the tags without warning on conflicts.

        :param new_dict: Dictionary from tag name to target.
        """
        return self.branch._set_tags_bytes(self._serialize_tag_dict(new_dict))

    def _serialize_tag_dict(self, tag_dict):
        td = dict((k.encode('utf-8'), v)
                  for k,v in tag_dict.items())
        return bencode.bencode(td)

    def _deserialize_tag_dict(self, tag_content):
        """Convert the tag file into a dictionary of tags"""
        # was a special case to make initialization easy, an empty definition
        # is an empty dictionary
        if tag_content == '':
            return {}
        try:
            r = {}
            for k, v in bencode.bdecode(tag_content).items():
                r[k.decode('utf-8')] = v
            return r
        except ValueError, e:
            raise ValueError("failed to deserialize tag dictionary %r: %s"
                % (tag_content, e))

    def merge_to(self, to_tags, overwrite=False, ignore_master=False):
        """Copy tags between repositories if necessary and possible.

        This method has common command-line behaviour about handling
        error cases.

        All new definitions are copied across, except that tags that already
        exist keep their existing definitions.

        :param to_tags: Branch to receive these tags
        :param overwrite: Overwrite conflicting tags in the target branch
        :param ignore_master: Do not modify the tags in the target's master
            branch (if any).  Default is false (so the master will be updated).
            New in bzr 2.3.

        :returns: A set of tags that conflicted, each of which is
            (tagname, source_target, dest_target), or None if no copying was
            done.
        """
        operation = cleanup.OperationWithCleanups(self._merge_to_operation)
        return operation.run(to_tags, overwrite, ignore_master)

    def _merge_to_operation(self, operation, to_tags, overwrite, ignore_master):
        add_cleanup = operation.add_cleanup
        if self.branch == to_tags.branch:
            return
        if not self.branch.supports_tags():
            # obviously nothing to copy
            return
        source_dict = self.get_tag_dict()
        if not source_dict:
            # no tags in the source, and we don't want to clobber anything
            # that's in the destination
            return
        # We merge_to both master and child individually.
        #
        # It's possible for master and child to have differing sets of
        # tags, in which case it's possible to have different sets of
        # conflicts.  We report the union of both conflict sets.  In
        # that case it's likely the child and master have accepted
        # different tags from the source, which may be a surprising result, but
        # the best we can do in the circumstances.
        #
        # Ideally we'd improve this API to report the different conflicts
        # more clearly to the caller, but we don't want to break plugins
        # such as bzr-builddeb that use this API.
        add_cleanup(to_tags.branch.lock_write().unlock)
        if ignore_master:
            master = None
        else:
            master = to_tags.branch.get_master_branch()
        if master is not None:
            add_cleanup(master.lock_write().unlock)
        conflicts = self._merge_to(to_tags, source_dict, overwrite)
        if master is not None:
            conflicts += self._merge_to(master.tags, source_dict,
                overwrite)
        # We use set() to remove any duplicate conflicts from the master
        # branch.
        return set(conflicts)

    def _merge_to(self, to_tags, source_dict, overwrite):
        dest_dict = to_tags.get_tag_dict()
        result, conflicts = self._reconcile_tags(source_dict, dest_dict,
                                                 overwrite)
        if result != dest_dict:
            to_tags._set_tag_dict(result)
        return conflicts

    def rename_revisions(self, rename_map):
        """Rename revisions in this tags dictionary.
        
        :param rename_map: Dictionary mapping old revids to new revids
        """
        reverse_tags = self.get_reverse_tag_dict()
        for revid, names in reverse_tags.iteritems():
            if revid in rename_map:
                for name in names:
                    self.set_tag(name, rename_map[revid])

    def _reconcile_tags(self, source_dict, dest_dict, overwrite):
        """Do a two-way merge of two tag dictionaries.

        only in source => source value
        only in destination => destination value
        same definitions => that
        different definitions => if overwrite is False, keep destination
            value and give a warning, otherwise use the source value

        :returns: (result_dict,
            [(conflicting_tag, source_target, dest_target)])
        """
        conflicts = []
        result = dict(dest_dict) # copy
        for name, target in source_dict.items():
            if name not in result or overwrite:
                result[name] = target
            elif result[name] == target:
                pass
            else:
                conflicts.append((name, target, result[name]))
        return result, conflicts


def _merge_tags_if_possible(from_branch, to_branch, ignore_master=False):
    # Try hard to support merge_to implementations that don't expect
    # 'ignore_master' (new in bzr 2.3).  First, if the flag isn't set then we
    # can safely avoid passing ignore_master at all.
    if not ignore_master:
        from_branch.tags.merge_to(to_branch.tags)
        return
    # If the flag is set, try to pass it, but be ready to catch TypeError.
    try:
        from_branch.tags.merge_to(to_branch.tags, ignore_master=ignore_master)
    except TypeError:
        # Probably this implementation of 'merge_to' is from a plugin that
        # doesn't expect the 'ignore_master' keyword argument (e.g. bzr-svn
        # 1.0.4).  There's a small risk that the TypeError is actually caused
        # by a completely different problem (which is why we don't catch it for
        # the ignore_master=False case), but even then there's probably no harm
        # in calling a second time.
        symbol_versioning.warn(
            symbol_versioning.deprecated_in((2,3)) % (
                "Tags.merge_to (of %r) that doesn't accept ignore_master kwarg"
                % (from_branch.tags,),),
            DeprecationWarning)
        from_branch.tags.merge_to(to_branch.tags)


def sort_natural(branch, tags):
    """Sort tags, with numeric substrings as numbers.

    :param branch: Branch
    :param tags: List of tuples with tag name and revision id.
    """
    def natural_sort_key(tag):
        return [f(s) for f,s in
                zip(itertools.cycle((unicode.lower,int)),
                                    re.split('([0-9]+)', tag[0]))]
    tags.sort(key=natural_sort_key)


def sort_alpha(branch, tags):
    """Sort tags lexicographically, in place.

    :param branch: Branch
    :param tags: List of tuples with tag name and revision id.
    """
    tags.sort()


def sort_time(branch, tags):
    """Sort tags by time inline.

    :param branch: Branch
    :param tags: List of tuples with tag name and revision id.
    """
    timestamps = {}
    for tag, revid in tags:
        try:
            revobj = branch.repository.get_revision(revid)
        except errors.NoSuchRevision:
            timestamp = sys.maxint # place them at the end
        else:
            timestamp = revobj.timestamp
        timestamps[revid] = timestamp
    tags.sort(key=lambda x: timestamps[x[1]])


tag_sort_methods = Registry()
tag_sort_methods.register("natural", sort_natural,
    'Sort numeric substrings as numbers. (default)')
tag_sort_methods.register("alpha", sort_alpha, 'Sort tags lexicographically.')
tag_sort_methods.register("time", sort_time, 'Sort tags chronologically.')
tag_sort_methods.default_key = "natural"
