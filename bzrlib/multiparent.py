# Copyright (C) 2007 Canonical Ltd
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
import errno
import itertools
import os
from StringIO import StringIO

from bzrlib import (
    patiencediff,
    trace,
    ui,
    )
from bzrlib.util import bencode
""")
from bzrlib.tuned_gzip import GzipFile


def topo_iter(vf, versions=None):
    seen = set()
    descendants = {}
    if versions is None:
        versions = vf.versions()
    def pending_parents(version):
        return [v for v in vf.get_parents(version) if v in versions and
                v not in seen]
    for version_id in versions:
        for parent_id in vf.get_parents(version_id):
            descendants.setdefault(parent_id, []).append(version_id)
    cur = [v for v in versions if len(pending_parents(v)) == 0]
    while len(cur) > 0:
        next = []
        for version_id in cur:
            if version_id in seen:
                continue
            if len(pending_parents(version_id)) != 0:
                continue
            next.extend(descendants.get(version_id, []))
            yield version_id
            seen.add(version_id)
        cur = next
    assert len(seen) == len(versions)


class MultiParent(object):

    def __init__(self, hunks=None):
        if hunks is not None:
            self.hunks = hunks
        else:
            self.hunks = []

    def __repr__(self):
        return "MultiParent(%r)" % self.hunks

    def __eq__(self, other):
        if self.__class__ is not other.__class__:
            return False
        return (self.hunks == other.hunks)

    @staticmethod
    def from_lines(text, parents=(), left_blocks=None):
        """Produce a MultiParent from a list of lines and parents"""
        def compare(parent):
            matcher = patiencediff.PatienceSequenceMatcher(None, parent,
                                                           text)
            return matcher.get_matching_blocks()
        if len(parents) > 0:
            if left_blocks is None:
                left_blocks = compare(parents[0])
            parent_comparisons = [left_blocks] + [compare(p) for p in
                                                  parents[1:]]
        else:
            parent_comparisons = []
        cur_line = 0
        new_text = NewText([])
        parent_text = []
        block_iter = [iter(i) for i in parent_comparisons]
        diff = MultiParent([])
        def next_block(p):
            try:
                return block_iter[p].next()
            except StopIteration:
                return None
        cur_block = [next_block(p) for p, i in enumerate(block_iter)]
        while cur_line < len(text):
            best_match = None
            for p, block in enumerate(cur_block):
                if block is None:
                    continue
                i, j, n = block
                while j + n < cur_line:
                    block = cur_block[p] = next_block(p)
                    if block is None:
                        break
                    i, j, n = block
                if block is None:
                    continue
                if j > cur_line:
                    continue
                offset = cur_line - j
                i += offset
                j = cur_line
                n -= offset
                if n == 0:
                    continue
                if best_match is None or n > best_match.num_lines:
                    best_match = ParentText(p, i, j, n)
            if best_match is None:
                new_text.lines.append(text[cur_line])
                cur_line += 1
            else:
                if len(new_text.lines) > 0:
                    diff.hunks.append(new_text)
                    new_text = NewText([])
                diff.hunks.append(best_match)
                cur_line += best_match.num_lines
        if len(new_text.lines) > 0:
            diff.hunks.append(new_text)
        return diff

    @classmethod
    def from_texts(cls, text, parents=()):
        """Produce a MultiParent from a text and list of parent text"""
        return cls.from_lines(StringIO(text).readlines(),
                              [StringIO(p).readlines() for p in parents])

    def to_patch(self):
        """Yield text lines for a patch"""
        for hunk in self.hunks:
            for line in hunk.to_patch():
                yield line

    def patch_len(self):
        return len(''.join(self.to_patch()))

    def zipped_patch_len(self):
        return len(gzip_string(self.to_patch()))

    @classmethod
    def from_patch(cls, text):
        return cls._from_patch(StringIO(text))

    @staticmethod
    def _from_patch(lines):
        """This is private because it is essential to split lines on \n only"""
        line_iter = iter(lines)
        hunks = []
        cur_line = None
        while(True):
            try:
                cur_line = line_iter.next()
            except StopIteration:
                break
            if cur_line[0] == 'i':
                num_lines = int(cur_line.split(' ')[1])
                hunk_lines = [line_iter.next() for x in xrange(num_lines)]
                hunk_lines[-1] = hunk_lines[-1][:-1]
                hunks.append(NewText(hunk_lines))
            elif cur_line[0] == '\n':
                hunks[-1].lines[-1] += '\n'
            else:
                assert cur_line[0] == 'c', cur_line[0]
                parent, parent_pos, child_pos, num_lines =\
                    [int(v) for v in cur_line.split(' ')[1:]]
                hunks.append(ParentText(parent, parent_pos, child_pos,
                                        num_lines))
        return MultiParent(hunks)

    def range_iterator(self):
        """Iterate through the hunks, with range indicated

        kind is "new" or "parent".
        for "new", data is a list of lines.
        for "parent", data is (parent, parent_start, parent_end)
        :return: a generator of (start, end, kind, data)
        """
        start = 0
        for hunk in self.hunks:
            if isinstance(hunk, NewText):
                kind = 'new'
                end = start + len(hunk.lines)
                data = hunk.lines
            else:
                kind = 'parent'
                start = hunk.child_pos
                end = start + hunk.num_lines
                data = (hunk.parent, hunk.parent_pos, hunk.parent_pos +
                        hunk.num_lines)
            yield start, end, kind, data
            start = end

    def num_lines(self):
        extra_n = 0
        for hunk in reversed(self.hunks):
            if isinstance(hunk, ParentText):
               return hunk.child_pos + hunk.num_lines + extra_n
            extra_n += len(hunk.lines)
        return extra_n

    def is_snapshot(self):
        if len(self.hunks) != 1:
            return False
        return (isinstance(self.hunks[0], NewText))


class NewText(object):
    """The contents of text that is introduced by this text"""

    def __init__(self, lines):
        self.lines = lines

    def __eq__(self, other):
        if self.__class__ is not other.__class__:
            return False
        return (other.lines == self.lines)

    def __repr__(self):
        return 'NewText(%r)' % self.lines

    def to_patch(self):
        yield 'i %d\n' % len(self.lines)
        for line in self.lines:
            yield line
        yield '\n'


class ParentText(object):
    """A reference to text present in a parent text"""

    def __init__(self, parent, parent_pos, child_pos, num_lines):
        self.parent = parent
        self.parent_pos = parent_pos
        self.child_pos = child_pos
        self.num_lines = num_lines

    def __repr__(self):
        return 'ParentText(%(parent)r, %(parent_pos)r, %(child_pos)r,'\
            ' %(num_lines)r)' % self.__dict__

    def __eq__(self, other):
        if self.__class__ != other.__class__:
            return False
        return (self.__dict__ == other.__dict__)

    def to_patch(self):
        yield 'c %(parent)d %(parent_pos)d %(child_pos)d %(num_lines)d\n'\
            % self.__dict__


class BaseVersionedFile(object):
    """VersionedFile skeleton for MultiParent"""

    def __init__(self, snapshot_interval=25, max_snapshots=None):
        self._lines = {}
        self._parents = {}
        self._snapshots = set()
        self.snapshot_interval = snapshot_interval
        self.max_snapshots = max_snapshots

    def versions(self):
        return iter(self._parents)

    def has_version(self, version):
        return version in self._parents

    def do_snapshot(self, version_id, parent_ids):
        if self.snapshot_interval is None:
            return False
        if self.max_snapshots is not None and\
            len(self._snapshots) == self.max_snapshots:
            return False
        if len(parent_ids) == 0:
            return True
        for ignored in xrange(self.snapshot_interval):
            if len(parent_ids) == 0:
                return False
            version_ids = parent_ids
            parent_ids = []
            for version_id in version_ids:
                if version_id not in self._snapshots:
                    parent_ids.extend(self._parents[version_id])
        else:
            return True

    def add_version(self, lines, version_id, parent_ids,
                    force_snapshot=None, single_parent=False):
        if force_snapshot is None:
            do_snapshot = self.do_snapshot(version_id, parent_ids)
        else:
            do_snapshot = force_snapshot
        if do_snapshot:
            self._snapshots.add(version_id)
            diff = MultiParent([NewText(lines)])
        else:
            if single_parent:
                parent_lines = self.get_line_list(parent_ids[:1])
            else:
                parent_lines = self.get_line_list(parent_ids)
            diff = MultiParent.from_lines(lines, parent_lines)
            if diff.is_snapshot():
                self._snapshots.add(version_id)
        self.add_diff(diff, version_id, parent_ids)
        self._lines[version_id] = lines

    def get_parents(self, version_id):
        return self._parents[version_id]

    def make_snapshot(self, version_id):
        snapdiff = MultiParent([NewText(self.cache_version(version_id))])
        self.add_diff(snapdiff, version_id, self._parents[version_id])
        self._snapshots.add(version_id)

    def import_versionedfile(self, vf, snapshots, no_cache=True,
                             single_parent=False, verify=False):
        """Import all revisions of a versionedfile

        :param vf: The versionedfile to import
        :param snapshots: If provided, the revisions to make snapshots of.
            Otherwise, this will be auto-determined
        :param no_cache: If true, clear the cache after every add.
        :param single_parent: If true, omit all but one parent text, (but
            retain parent metadata).
        """
        assert no_cache or not verify
        revisions = set(vf.versions())
        total = len(revisions)
        pb = ui.ui_factory.nested_progress_bar()
        try:
            while len(revisions) > 0:
                added = set()
                for revision in revisions:
                    parents = vf.get_parents(revision)
                    if [p for p in parents if p not in self._parents] != []:
                        continue
                    lines = [a + ' ' + l for a, l in
                             vf.annotate_iter(revision)]
                    if snapshots is None:
                        force_snapshot = None
                    else:
                        force_snapshot = (revision in snapshots)
                    self.add_version(lines, revision, parents, force_snapshot,
                                     single_parent)
                    added.add(revision)
                    if no_cache:
                        self.clear_cache()
                        vf.clear_cache()
                        if verify:
                            assert lines == self.get_line_list([revision])[0]
                            self.clear_cache()
                    pb.update('Importing revisions',
                              (total - len(revisions)) + len(added), total)
                revisions = [r for r in revisions if r not in added]
        finally:
            pb.finished()

    def select_snapshots(self, vf):
        build_ancestors = {}
        descendants = {}
        snapshots = set()
        for version_id in topo_iter(vf):
            potential_build_ancestors = set(vf.get_parents(version_id))
            parents = vf.get_parents(version_id)
            if len(parents) == 0:
                snapshots.add(version_id)
                build_ancestors[version_id] = set()
            else:
                for parent in vf.get_parents(version_id):
                    potential_build_ancestors.update(build_ancestors[parent])
                if len(potential_build_ancestors) > self.snapshot_interval:
                    snapshots.add(version_id)
                    build_ancestors[version_id] = set()
                else:
                    build_ancestors[version_id] = potential_build_ancestors
        return snapshots

    def select_by_size(self, num):
        """Select snapshots for minimum output size"""
        num -= len(self._snapshots)
        new_snapshots = self.get_size_ranking()[-num:]
        return [v for n, v in new_snapshots]

    def get_size_ranking(self):
        versions = []
        new_snapshots = set()
        for version_id in self.versions():
            if version_id in self._snapshots:
                continue
            diff_len = self.get_diff(version_id).patch_len()
            snapshot_len = MultiParent([NewText(
                self.cache_version(version_id))]).patch_len()
            versions.append((snapshot_len - diff_len, version_id))
        versions.sort()
        return versions
        return [v for n, v in versions]

    def import_diffs(self, vf):
        for version_id in vf.versions():
            self.add_diff(vf.get_diff(version_id), version_id,
                          vf._parents[version_id])

    def get_build_ranking(self):
        could_avoid = {}
        referenced_by = {}
        for version_id in topo_iter(self):
            could_avoid[version_id] = set()
            if version_id not in self._snapshots:
                for parent_id in self._parents[version_id]:
                    could_avoid[version_id].update(could_avoid[parent_id])
                could_avoid[version_id].update(self._parents)
                could_avoid[version_id].discard(version_id)
            for avoid_id in could_avoid[version_id]:
                referenced_by.setdefault(avoid_id, set()).add(version_id)
        available_versions = list(self.versions())
        ranking = []
        while len(available_versions) > 0:
            available_versions.sort(key=lambda x:
                len(could_avoid[x]) *
                len(referenced_by.get(x, [])))
            selected = available_versions.pop()
            ranking.append(selected)
            for version_id in referenced_by[selected]:
                could_avoid[version_id].difference_update(
                    could_avoid[selected])
            for version_id in could_avoid[selected]:
                referenced_by[version_id].difference_update(
                    referenced_by[selected]
                )
        return ranking

    def clear_cache(self):
        self._lines.clear()

    def get_line_list(self, version_ids):
        return [self.cache_version(v) for v in version_ids]

    def cache_version(self, version_id):
        try:
            return self._lines[version_id]
        except KeyError:
            pass
        diff = self.get_diff(version_id)
        lines = []
        reconstructor = _Reconstructor(self, self._lines,
                                       self._parents)
        reconstructor.reconstruct_version(lines, version_id)
        self._lines[version_id] = lines
        return lines


class MultiMemoryVersionedFile(BaseVersionedFile):

    def __init__(self, snapshot_interval=25, max_snapshots=None):
        BaseVersionedFile.__init__(self, snapshot_interval, max_snapshots)
        self._diffs = {}

    def add_diff(self, diff, version_id, parent_ids):
        self._diffs[version_id] = diff
        self._parents[version_id] = parent_ids

    def get_diff(self, version_id):
        return self._diffs[version_id]

    def destroy(self):
        self._diffs = {}


class MultiVersionedFile(BaseVersionedFile):

    def __init__(self, filename, snapshot_interval=25, max_snapshots=None):
        BaseVersionedFile.__init__(self, snapshot_interval, max_snapshots)
        self._filename = filename
        self._diff_offset = {}

    def get_diff(self, version_id):
        start, count = self._diff_offset[version_id]
        infile = open(self._filename + '.mpknit', 'rb')
        try:
            infile.seek(start)
            sio = StringIO(infile.read(count))
        finally:
            infile.close()
        zip_file = GzipFile(None, mode='rb', fileobj=sio)
        try:
            file_version_id = zip_file.readline()
            return MultiParent.from_patch(zip_file.read())
        finally:
            zip_file.close()

    def add_diff(self, diff, version_id, parent_ids):
        outfile = open(self._filename + '.mpknit', 'ab')
        try:
            start = outfile.tell()
            try:
                zipfile = GzipFile(None, mode='ab', fileobj=outfile)
                zipfile.writelines(itertools.chain(
                    ['version %s\n' % version_id], diff.to_patch()))
            finally:
                zipfile.close()
            end = outfile.tell()
        finally:
            outfile.close()
        self._diff_offset[version_id] = (start, end-start)
        self._parents[version_id] = parent_ids

    def destroy(self):
        try:
            os.unlink(self._filename + '.mpknit')
        except OSError, e:
            if e.errno != errno.ENOENT:
                raise
        try:
            os.unlink(self._filename + '.mpidx')
        except OSError, e:
            if e.errno != errno.ENOENT:
                raise

    def save(self):
        open(self._filename + '.mpidx', 'wb').write(bencode.bencode(
            (self._parents, list(self._snapshots), self._diff_offset)))

    def load(self):
        self._parents, snapshots, self._diff_offset = bencode.bdecode(
            open(self._filename + '.mpidx', 'rb').read())
        self._snapshots = set(snapshots)


class _Reconstructor(object):
    """Build a text from the diffs, ancestry graph and cached lines"""

    def __init__(self, diffs, lines, parents):
        self.diffs = diffs
        self.lines = lines
        self.parents = parents
        self.cursor = {}

    def reconstruct(self, lines, parent_text, version_id):
        """Append the lines referred to by a ParentText to lines"""
        parent_id = self.parents[version_id][parent_text.parent]
        end = parent_text.parent_pos + parent_text.num_lines
        return self._reconstruct(lines, parent_id, parent_text.parent_pos,
                                 end)

    def _reconstruct(self, lines, req_version_id, req_start, req_end):
        """Append lines for the requested version_id range"""
        # stack of pending range requests
        if req_start == req_end:
            return
        pending_reqs = [(req_version_id, req_start, req_end)]
        while len(pending_reqs) > 0:
            req_version_id, req_start, req_end = pending_reqs.pop()
            # lazily allocate cursors for versions
            try:
                start, end, kind, data, iterator = self.cursor[req_version_id]
            except KeyError:
                iterator = self.diffs.get_diff(req_version_id).range_iterator()
                start, end, kind, data = iterator.next()
            if start > req_start:
                iterator = self.diffs.get_diff(req_version_id).range_iterator()
                start, end, kind, data = iterator.next()

            # find the first hunk relevant to the request
            while end <= req_start:
                start, end, kind, data = iterator.next()
            self.cursor[req_version_id] = start, end, kind, data, iterator
            # if the hunk can't satisfy the whole request, split it in two,
            # and leave the second half for later.
            if req_end > end:
                pending_reqs.append((req_version_id, end, req_end))
                req_end = end
            if kind == 'new':
                lines.extend(data[req_start - start: (req_end - start)])
            else:
                # If the hunk is a ParentText, rewrite it as a range request
                # for the parent, and make it the next pending request.
                parent, parent_start, parent_end = data
                new_version_id = self.parents[req_version_id][parent]
                new_start = parent_start + req_start - start
                new_end = parent_end + req_end - end
                pending_reqs.append((new_version_id, new_start, new_end))

    def reconstruct_version(self, lines, version_id):
        length = self.diffs.get_diff(version_id).num_lines()
        return self._reconstruct(lines, version_id, 0, length)


def gzip_string(lines):
    sio = StringIO()
    data_file = GzipFile(None, mode='wb', fileobj=sio)
    data_file.writelines(lines)
    data_file.close()
    return sio.getvalue()
