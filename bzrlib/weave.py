#! /usr/bin/python

# Copyright (C) 2005 Canonical Ltd
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

# Author: Martin Pool <mbp@canonical.com>


"""Weave - storage of related text file versions"""


# XXX: If we do weaves this way, will a merge still behave the same
# way if it's done in a different order?  That's a pretty desirable
# property.

# TODO: Nothing here so far assumes the lines are really \n newlines,
# rather than being split up in some other way.  We could accommodate
# binaries, perhaps by naively splitting on \n or perhaps using
# something like a rolling checksum.

# TODO: End marker for each version so we can stop reading?

# TODO: Check that no insertion occurs inside a deletion that was
# active in the version of the insertion.

# TODO: In addition to the SHA-1 check, perhaps have some code that
# checks structural constraints of the weave: ie that insertions are
# properly nested, that there is no text outside of an insertion, that
# insertions or deletions are not repeated, etc.

# TODO: Parallel-extract that passes back each line along with a
# description of which revisions include it.  Nice for checking all
# shas or calculating stats in parallel.

# TODO: Using a single _extract routine and then processing the output
# is probably inefficient.  It's simple enough that we can afford to
# have slight specializations for different ways its used: annotate,
# basis for add, get, etc.

# TODO: Probably the API should work only in names to hide the integer
# indexes from the user.

# TODO: Is there any potential performance win by having an add()
# variant that is passed a pre-cooked version of the single basis
# version?

# TODO: Reweave can possibly be made faster by remembering diffs
# where the basis and destination are unchanged.

# FIXME: Sometimes we will be given a parents list for a revision
# that includes some redundant parents (i.e. already a parent of 
# something in the list.)  We should eliminate them.  This can 
# be done fairly efficiently because the sequence numbers constrain
# the possible relationships.

# FIXME: the conflict markers should be *7* characters

from copy import copy
from cStringIO import StringIO
import os
import sha
import time
import warnings

from bzrlib import (
    progress,
    )
from bzrlib.errors import (WeaveError, WeaveFormatError, WeaveParentMismatch,
        RevisionAlreadyPresent,
        RevisionNotPresent,
        UnavailableRepresentation,
        WeaveRevisionAlreadyPresent,
        WeaveRevisionNotPresent,
        )
import bzrlib.errors as errors
from bzrlib.osutils import dirname, sha_strings, split_lines
import bzrlib.patiencediff
from bzrlib.revision import NULL_REVISION
from bzrlib.symbol_versioning import *
from bzrlib.trace import mutter
from bzrlib.tsort import topo_sort
from bzrlib.versionedfile import (
    AbsentContentFactory,
    adapter_registry,
    ContentFactory,
    VersionedFile,
    )
from bzrlib.weavefile import _read_weave_v5, write_weave_v5


class WeaveContentFactory(ContentFactory):
    """Content factory for streaming from weaves.

    :seealso ContentFactory:
    """

    def __init__(self, version, weave):
        """Create a WeaveContentFactory for version from weave."""
        ContentFactory.__init__(self)
        self.sha1 = weave.get_sha1s([version])[version]
        self.key = (version,)
        parents = weave.get_parent_map([version])[version]
        self.parents = tuple((parent,) for parent in parents)
        self.storage_kind = 'fulltext'
        self._weave = weave

    def get_bytes_as(self, storage_kind):
        if storage_kind == 'fulltext':
            return self._weave.get_text(self.key[-1])
        else:
            raise UnavailableRepresentation(self.key, storage_kind, 'fulltext')


class Weave(VersionedFile):
    """weave - versioned text file storage.
    
    A Weave manages versions of line-based text files, keeping track
    of the originating version for each line.

    To clients the "lines" of the file are represented as a list of strings.
    These strings  will typically have terminal newline characters, but
    this is not required.  In particular files commonly do not have a newline
    at the end of the file.

    Texts can be identified in either of two ways:

    * a nonnegative index number.

    * a version-id string.

    Typically the index number will be valid only inside this weave and
    the version-id is used to reference it in the larger world.

    The weave is represented as a list mixing edit instructions and
    literal text.  Each entry in _weave can be either a string (or
    unicode), or a tuple.  If a string, it means that the given line
    should be output in the currently active revisions.

    If a tuple, it gives a processing instruction saying in which
    revisions the enclosed lines are active.  The tuple has the form
    (instruction, version).

    The instruction can be '{' or '}' for an insertion block, and '['
    and ']' for a deletion block respectively.  The version is the
    integer version index.  There is no replace operator, only deletes
    and inserts.  For '}', the end of an insertion, there is no
    version parameter because it always closes the most recently
    opened insertion.

    Constraints/notes:

    * A later version can delete lines that were introduced by any
      number of ancestor versions; this implies that deletion
      instructions can span insertion blocks without regard to the
      insertion block's nesting.

    * Similarly, deletions need not be properly nested with regard to
      each other, because they might have been generated by
      independent revisions.

    * Insertions are always made by inserting a new bracketed block
      into a single point in the previous weave.  This implies they
      can nest but not overlap, and the nesting must always have later
      insertions on the inside.

    * It doesn't seem very useful to have an active insertion
      inside an inactive insertion, but it might happen.
      
    * Therefore, all instructions are always"considered"; that
      is passed onto and off the stack.  An outer inactive block
      doesn't disable an inner block.

    * Lines are enabled if the most recent enclosing insertion is
      active and none of the enclosing deletions are active.

    * There is no point having a deletion directly inside its own
      insertion; you might as well just not write it.  And there
      should be no way to get an earlier version deleting a later
      version.

    _weave
        Text of the weave; list of control instruction tuples and strings.

    _parents
        List of parents, indexed by version number.
        It is only necessary to store the minimal set of parents for
        each version; the parent's parents are implied.

    _sha1s
        List of hex SHA-1 of each version.

    _names
        List of symbolic names for each version.  Each should be unique.

    _name_map
        For each name, the version number.

    _weave_name
        Descriptive name of this weave; typically the filename if known.
        Set by read_weave.
    """

    __slots__ = ['_weave', '_parents', '_sha1s', '_names', '_name_map',
                 '_weave_name', '_matcher', '_allow_reserved']

    def __init__(self, weave_name=None, access_mode='w', matcher=None,
                 get_scope=None, allow_reserved=False):
        """Create a weave.

        :param get_scope: A callable that returns an opaque object to be used
            for detecting when this weave goes out of scope (should stop
            answering requests or allowing mutation).
        """
        super(Weave, self).__init__()
        self._weave = []
        self._parents = []
        self._sha1s = []
        self._names = []
        self._name_map = {}
        self._weave_name = weave_name
        if matcher is None:
            self._matcher = bzrlib.patiencediff.PatienceSequenceMatcher
        else:
            self._matcher = matcher
        if get_scope is None:
            get_scope = lambda:None
        self._get_scope = get_scope
        self._scope = get_scope()
        self._access_mode = access_mode
        self._allow_reserved = allow_reserved

    def __repr__(self):
        return "Weave(%r)" % self._weave_name

    def _check_write_ok(self):
        """Is the versioned file marked as 'finished' ? Raise if it is."""
        if self._get_scope() != self._scope:
            raise errors.OutSideTransaction()
        if self._access_mode != 'w':
            raise errors.ReadOnlyObjectDirtiedError(self)

    def copy(self):
        """Return a deep copy of self.
        
        The copy can be modified without affecting the original weave."""
        other = Weave()
        other._weave = self._weave[:]
        other._parents = self._parents[:]
        other._sha1s = self._sha1s[:]
        other._names = self._names[:]
        other._name_map = self._name_map.copy()
        other._weave_name = self._weave_name
        return other

    def __eq__(self, other):
        if not isinstance(other, Weave):
            return False
        return self._parents == other._parents \
               and self._weave == other._weave \
               and self._sha1s == other._sha1s 
    
    def __ne__(self, other):
        return not self.__eq__(other)

    def _idx_to_name(self, version):
        return self._names[version]

    def _lookup(self, name):
        """Convert symbolic version name to index."""
        if not self._allow_reserved:
            self.check_not_reserved_id(name)
        try:
            return self._name_map[name]
        except KeyError:
            raise RevisionNotPresent(name, self._weave_name)

    def versions(self):
        """See VersionedFile.versions."""
        return self._names[:]

    def has_version(self, version_id):
        """See VersionedFile.has_version."""
        return (version_id in self._name_map)

    __contains__ = has_version

    def get_record_stream(self, versions, ordering, include_delta_closure):
        """Get a stream of records for versions.

        :param versions: The versions to include. Each version is a tuple
            (version,).
        :param ordering: Either 'unordered' or 'topological'. A topologically
            sorted stream has compression parents strictly before their
            children.
        :param include_delta_closure: If True then the closure across any
            compression parents will be included (in the opaque data).
        :return: An iterator of ContentFactory objects, each of which is only
            valid until the iterator is advanced.
        """
        versions = [version[-1] for version in versions]
        if ordering == 'topological':
            parents = self.get_parent_map(versions)
            new_versions = topo_sort(parents)
            new_versions.extend(set(versions).difference(set(parents)))
            versions = new_versions
        for version in versions:
            if version in self:
                yield WeaveContentFactory(version, self)
            else:
                yield AbsentContentFactory((version,))

    def get_parent_map(self, version_ids):
        """See VersionedFile.get_parent_map."""
        result = {}
        for version_id in version_ids:
            if version_id == NULL_REVISION:
                parents = ()
            else:
                try:
                    parents = tuple(
                        map(self._idx_to_name,
                            self._parents[self._lookup(version_id)]))
                except RevisionNotPresent:
                    continue
            result[version_id] = parents
        return result

    def get_parents_with_ghosts(self, version_id):
        raise NotImplementedError(self.get_parents_with_ghosts)

    def insert_record_stream(self, stream):
        """Insert a record stream into this versioned file.

        :param stream: A stream of records to insert. 
        :return: None
        :seealso VersionedFile.get_record_stream:
        """
        adapters = {}
        for record in stream:
            # Raise an error when a record is missing.
            if record.storage_kind == 'absent':
                raise RevisionNotPresent([record.key[0]], self)
            # adapt to non-tuple interface
            parents = [parent[0] for parent in record.parents]
            if record.storage_kind == 'fulltext':
                self.add_lines(record.key[0], parents,
                    split_lines(record.get_bytes_as('fulltext')))
            else:
                adapter_key = record.storage_kind, 'fulltext'
                try:
                    adapter = adapters[adapter_key]
                except KeyError:
                    adapter_factory = adapter_registry.get(adapter_key)
                    adapter = adapter_factory(self)
                    adapters[adapter_key] = adapter
                lines = split_lines(adapter.get_bytes(
                    record, record.get_bytes_as(record.storage_kind)))
                try:
                    self.add_lines(record.key[0], parents, lines)
                except RevisionAlreadyPresent:
                    pass

    def _check_repeated_add(self, name, parents, text, sha1):
        """Check that a duplicated add is OK.

        If it is, return the (old) index; otherwise raise an exception.
        """
        idx = self._lookup(name)
        if sorted(self._parents[idx]) != sorted(parents) \
            or sha1 != self._sha1s[idx]:
            raise RevisionAlreadyPresent(name, self._weave_name)
        return idx

    def _add_lines(self, version_id, parents, lines, parent_texts,
       left_matching_blocks, nostore_sha, random_id, check_content):
        """See VersionedFile.add_lines."""
        idx = self._add(version_id, lines, map(self._lookup, parents),
            nostore_sha=nostore_sha)
        return sha_strings(lines), sum(map(len, lines)), idx

    def _add(self, version_id, lines, parents, sha1=None, nostore_sha=None):
        """Add a single text on top of the weave.
  
        Returns the index number of the newly added version.

        version_id
            Symbolic name for this version.
            (Typically the revision-id of the revision that added it.)

        parents
            List or set of direct parent version numbers.
            
        lines
            Sequence of lines to be added in the new version.

        :param nostore_sha: See VersionedFile.add_lines.
        """
        self._check_lines_not_unicode(lines)
        self._check_lines_are_lines(lines)
        if not sha1:
            sha1 = sha_strings(lines)
        if sha1 == nostore_sha:
            raise errors.ExistingContent
        if version_id in self._name_map:
            return self._check_repeated_add(version_id, parents, lines, sha1)

        self._check_versions(parents)
        ## self._check_lines(lines)
        new_version = len(self._parents)

        # if we abort after here the (in-memory) weave will be corrupt because only
        # some fields are updated
        # XXX: FIXME implement a succeed-or-fail of the rest of this routine.
        #      - Robert Collins 20060226
        self._parents.append(parents[:])
        self._sha1s.append(sha1)
        self._names.append(version_id)
        self._name_map[version_id] = new_version

            
        if not parents:
            # special case; adding with no parents revision; can do
            # this more quickly by just appending unconditionally.
            # even more specially, if we're adding an empty text we
            # need do nothing at all.
            if lines:
                self._weave.append(('{', new_version))
                self._weave.extend(lines)
                self._weave.append(('}', None))
            return new_version

        if len(parents) == 1:
            pv = list(parents)[0]
            if sha1 == self._sha1s[pv]:
                # special case: same as the single parent
                return new_version
            

        ancestors = self._inclusions(parents)

        l = self._weave

        # basis a list of (origin, lineno, line)
        basis_lineno = []
        basis_lines = []
        for origin, lineno, line in self._extract(ancestors):
            basis_lineno.append(lineno)
            basis_lines.append(line)

        # another small special case: a merge, producing the same text
        # as auto-merge
        if lines == basis_lines:
            return new_version

        # add a sentinel, because we can also match against the final line
        basis_lineno.append(len(self._weave))

        # XXX: which line of the weave should we really consider
        # matches the end of the file?  the current code says it's the
        # last line of the weave?

        #print 'basis_lines:', basis_lines
        #print 'new_lines:  ', lines

        s = self._matcher(None, basis_lines, lines)

        # offset gives the number of lines that have been inserted
        # into the weave up to the current point; if the original edit instruction
        # says to change line A then we actually change (A+offset)
        offset = 0

        for tag, i1, i2, j1, j2 in s.get_opcodes():
            # i1,i2 are given in offsets within basis_lines; we need to map them
            # back to offsets within the entire weave
            #print 'raw match', tag, i1, i2, j1, j2
            if tag == 'equal':
                continue
            i1 = basis_lineno[i1]
            i2 = basis_lineno[i2]
            # the deletion and insertion are handled separately.
            # first delete the region.
            if i1 != i2:
                self._weave.insert(i1+offset, ('[', new_version))
                self._weave.insert(i2+offset+1, (']', new_version))
                offset += 2

            if j1 != j2:
                # there may have been a deletion spanning up to
                # i2; we want to insert after this region to make sure
                # we don't destroy ourselves
                i = i2 + offset
                self._weave[i:i] = ([('{', new_version)] 
                                    + lines[j1:j2] 
                                    + [('}', None)])
                offset += 2 + (j2 - j1)
        return new_version

    def _inclusions(self, versions):
        """Return set of all ancestors of given version(s)."""
        if not len(versions):
            return []
        i = set(versions)
        for v in xrange(max(versions), 0, -1):
            if v in i:
                # include all its parents
                i.update(self._parents[v])
        return i
        ## except IndexError:
        ##     raise ValueError("version %d not present in weave" % v)

    def get_ancestry(self, version_ids, topo_sorted=True):
        """See VersionedFile.get_ancestry."""
        if isinstance(version_ids, basestring):
            version_ids = [version_ids]
        i = self._inclusions([self._lookup(v) for v in version_ids])
        return [self._idx_to_name(v) for v in i]

    def _check_lines(self, text):
        if not isinstance(text, list):
            raise ValueError("text should be a list, not %s" % type(text))

        for l in text:
            if not isinstance(l, basestring):
                raise ValueError("text line should be a string or unicode, not %s"
                                 % type(l))
        


    def _check_versions(self, indexes):
        """Check everything in the sequence of indexes is valid"""
        for i in indexes:
            try:
                self._parents[i]
            except IndexError:
                raise IndexError("invalid version number %r" % i)

    def _compatible_parents(self, my_parents, other_parents):
        """During join check that other_parents are joinable with my_parents.

        Joinable is defined as 'is a subset of' - supersets may require 
        regeneration of diffs, but subsets do not.
        """
        return len(other_parents.difference(my_parents)) == 0

    def annotate(self, version_id):
        """Return a list of (version-id, line) tuples for version_id.

        The index indicates when the line originated in the weave."""
        incls = [self._lookup(version_id)]
        return [(self._idx_to_name(origin), text) for origin, lineno, text in
            self._extract(incls)]

    def iter_lines_added_or_present_in_versions(self, version_ids=None,
                                                pb=None):
        """See VersionedFile.iter_lines_added_or_present_in_versions()."""
        if version_ids is None:
            version_ids = self.versions()
        version_ids = set(version_ids)
        for lineno, inserted, deletes, line in self._walk_internal(version_ids):
            # if inserted not in version_ids then it was inserted before the
            # versions we care about, but because weaves cannot represent ghosts
            # properly, we do not filter down to that
            # if inserted not in version_ids: continue
            if line[-1] != '\n':
                yield line + '\n', inserted
            else:
                yield line, inserted

    def _walk_internal(self, version_ids=None):
        """Helper method for weave actions."""
        
        istack = []
        dset = set()

        lineno = 0         # line of weave, 0-based

        for l in self._weave:
            if l.__class__ == tuple:
                c, v = l
                isactive = None
                if c == '{':
                    istack.append(self._names[v])
                elif c == '}':
                    istack.pop()
                elif c == '[':
                    dset.add(self._names[v])
                elif c == ']':
                    dset.remove(self._names[v])
                else:
                    raise WeaveFormatError('unexpected instruction %r' % v)
            else:
                yield lineno, istack[-1], frozenset(dset), l
            lineno += 1

        if istack:
            raise WeaveFormatError("unclosed insertion blocks "
                    "at end of weave: %s" % istack)
        if dset:
            raise WeaveFormatError("unclosed deletion blocks at end of weave: %s"
                                   % dset)

    def plan_merge(self, ver_a, ver_b):
        """Return pseudo-annotation indicating how the two versions merge.

        This is computed between versions a and b and their common
        base.

        Weave lines present in none of them are skipped entirely.
        """
        inc_a = set(self.get_ancestry([ver_a]))
        inc_b = set(self.get_ancestry([ver_b]))
        inc_c = inc_a & inc_b

        for lineno, insert, deleteset, line in self._walk_internal([ver_a, ver_b]):
            if deleteset & inc_c:
                # killed in parent; can't be in either a or b
                # not relevant to our work
                yield 'killed-base', line
            elif insert in inc_c:
                # was inserted in base
                killed_a = bool(deleteset & inc_a)
                killed_b = bool(deleteset & inc_b)
                if killed_a and killed_b:
                    yield 'killed-both', line
                elif killed_a:
                    yield 'killed-a', line
                elif killed_b:
                    yield 'killed-b', line
                else:
                    yield 'unchanged', line
            elif insert in inc_a:
                if deleteset & inc_a:
                    yield 'ghost-a', line
                else:
                    # new in A; not in B
                    yield 'new-a', line
            elif insert in inc_b:
                if deleteset & inc_b:
                    yield 'ghost-b', line
                else:
                    yield 'new-b', line
            else:
                # not in either revision
                yield 'irrelevant', line

    def _extract(self, versions):
        """Yield annotation of lines in included set.

        Yields a sequence of tuples (origin, lineno, text), where
        origin is the origin version, lineno the index in the weave,
        and text the text of the line.

        The set typically but not necessarily corresponds to a version.
        """
        for i in versions:
            if not isinstance(i, int):
                raise ValueError(i)
            
        included = self._inclusions(versions)

        istack = []
        iset = set()
        dset = set()

        lineno = 0         # line of weave, 0-based

        isactive = None

        result = []

        WFE = WeaveFormatError

        # wow. 
        #  449       0   4474.6820   2356.5590   bzrlib.weave:556(_extract)
        #  +285282   0   1676.8040   1676.8040   +<isinstance>
        # 1.6 seconds in 'isinstance'.
        # changing the first isinstance:
        #  449       0   2814.2660   1577.1760   bzrlib.weave:556(_extract)
        #  +140414   0    762.8050    762.8050   +<isinstance>
        # note that the inline time actually dropped (less function calls)
        # and total processing time was halved.
        # we're still spending ~1/4 of the method in isinstance though.
        # so lets hard code the acceptable string classes we expect:
        #  449       0   1202.9420    786.2930   bzrlib.weave:556(_extract)
        # +71352     0    377.5560    377.5560   +<method 'append' of 'list' 
        #                                          objects>
        # yay, down to ~1/4 the initial extract time, and our inline time
        # has shrunk again, with isinstance no longer dominating.
        # tweaking the stack inclusion test to use a set gives:
        #  449       0   1122.8030    713.0080   bzrlib.weave:556(_extract)
        # +71352     0    354.9980    354.9980   +<method 'append' of 'list' 
        #                                          objects>
        # - a 5% win, or possibly just noise. However with large istacks that
        # 'in' test could dominate, so I'm leaving this change in place -
        # when its fast enough to consider profiling big datasets we can review.

              
             

        for l in self._weave:
            if l.__class__ == tuple:
                c, v = l
                isactive = None
                if c == '{':
                    istack.append(v)
                    iset.add(v)
                elif c == '}':
                    iset.remove(istack.pop())
                elif c == '[':
                    if v in included:
                        dset.add(v)
                elif c == ']':
                    if v in included:
                        dset.remove(v)
                else:
                    raise AssertionError()
            else:
                if isactive is None:
                    isactive = (not dset) and istack and (istack[-1] in included)
                if isactive:
                    result.append((istack[-1], lineno, l))
            lineno += 1
        if istack:
            raise WeaveFormatError("unclosed insertion blocks "
                    "at end of weave: %s" % istack)
        if dset:
            raise WeaveFormatError("unclosed deletion blocks at end of weave: %s"
                                   % dset)
        return result

    def _maybe_lookup(self, name_or_index):
        """Convert possible symbolic name to index, or pass through indexes.
        
        NOT FOR PUBLIC USE.
        """
        if isinstance(name_or_index, (int, long)):
            return name_or_index
        else:
            return self._lookup(name_or_index)

    def get_lines(self, version_id):
        """See VersionedFile.get_lines()."""
        int_index = self._maybe_lookup(version_id)
        result = [line for (origin, lineno, line) in self._extract([int_index])]
        expected_sha1 = self._sha1s[int_index]
        measured_sha1 = sha_strings(result)
        if measured_sha1 != expected_sha1:
            raise errors.WeaveInvalidChecksum(
                    'file %s, revision %s, expected: %s, measured %s' 
                    % (self._weave_name, version_id,
                       expected_sha1, measured_sha1))
        return result

    def get_sha1s(self, version_ids):
        """See VersionedFile.get_sha1s()."""
        result = {}
        for v in version_ids:
            result[v] = self._sha1s[self._lookup(v)]
        return result

    def num_versions(self):
        """How many versions are in this weave?"""
        l = len(self._parents)
        return l

    __len__ = num_versions

    def check(self, progress_bar=None):
        # TODO evaluate performance hit of using string sets in this routine.
        # TODO: check no circular inclusions
        # TODO: create a nested progress bar
        for version in range(self.num_versions()):
            inclusions = list(self._parents[version])
            if inclusions:
                inclusions.sort()
                if inclusions[-1] >= version:
                    raise WeaveFormatError("invalid included version %d for index %d"
                                           % (inclusions[-1], version))

        # try extracting all versions; parallel extraction is used
        nv = self.num_versions()
        sha1s = {}
        texts = {}
        inclusions = {}
        for i in range(nv):
            # For creating the ancestry, IntSet is much faster (3.7s vs 0.17s)
            # The problem is that set membership is much more expensive
            name = self._idx_to_name(i)
            sha1s[name] = sha.new()
            texts[name] = []
            new_inc = set([name])
            for p in self._parents[i]:
                new_inc.update(inclusions[self._idx_to_name(p)])

            if set(new_inc) != set(self.get_ancestry(name)):
                raise AssertionError(
                    'failed %s != %s' 
                    % (set(new_inc), set(self.get_ancestry(name))))
            inclusions[name] = new_inc

        nlines = len(self._weave)

        update_text = 'checking weave'
        if self._weave_name:
            short_name = os.path.basename(self._weave_name)
            update_text = 'checking %s' % (short_name,)
            update_text = update_text[:25]

        for lineno, insert, deleteset, line in self._walk_internal():
            if progress_bar:
                progress_bar.update(update_text, lineno, nlines)

            for name, name_inclusions in inclusions.items():
                # The active inclusion must be an ancestor,
                # and no ancestors must have deleted this line,
                # because we don't support resurrection.
                if (insert in name_inclusions) and not (deleteset & name_inclusions):
                    sha1s[name].update(line)

        for i in range(nv):
            version = self._idx_to_name(i)
            hd = sha1s[version].hexdigest()
            expected = self._sha1s[i]
            if hd != expected:
                raise errors.WeaveInvalidChecksum(
                        "mismatched sha1 for version %s: "
                        "got %s, expected %s"
                        % (version, hd, expected))

        # TODO: check insertions are properly nested, that there are
        # no lines outside of insertion blocks, that deletions are
        # properly paired, etc.

    def _imported_parents(self, other, other_idx):
        """Return list of parents in self corresponding to indexes in other."""
        new_parents = []
        for parent_idx in other._parents[other_idx]:
            parent_name = other._names[parent_idx]
            if parent_name not in self._name_map:
                # should not be possible
                raise WeaveError("missing parent {%s} of {%s} in %r" 
                                 % (parent_name, other._name_map[other_idx], self))
            new_parents.append(self._name_map[parent_name])
        return new_parents

    def _check_version_consistent(self, other, other_idx, name):
        """Check if a version in consistent in this and other.

        To be consistent it must have:

         * the same text
         * the same direct parents (by name, not index, and disregarding
           order)
        
        If present & correct return True;
        if not present in self return False; 
        if inconsistent raise error."""
        this_idx = self._name_map.get(name, -1)
        if this_idx != -1:
            if self._sha1s[this_idx] != other._sha1s[other_idx]:
                raise errors.WeaveTextDiffers(name, self, other)
            self_parents = self._parents[this_idx]
            other_parents = other._parents[other_idx]
            n1 = set([self._names[i] for i in self_parents])
            n2 = set([other._names[i] for i in other_parents])
            if not self._compatible_parents(n1, n2):
                raise WeaveParentMismatch("inconsistent parents "
                    "for version {%s}: %s vs %s" % (name, n1, n2))
            else:
                return True         # ok!
        else:
            return False

    def _reweave(self, other, pb, msg):
        """Reweave self with other - internal helper for join().

        :param other: The other weave to merge
        :param pb: An optional progress bar, indicating how far done we are
        :param msg: An optional message for the progress
        """
        new_weave = _reweave(self, other, pb=pb, msg=msg)
        self._copy_weave_content(new_weave)

    def _copy_weave_content(self, otherweave):
        """adsorb the content from otherweave."""
        for attr in self.__slots__:
            if attr != '_weave_name':
                setattr(self, attr, copy(getattr(otherweave, attr)))


class WeaveFile(Weave):
    """A WeaveFile represents a Weave on disk and writes on change."""

    WEAVE_SUFFIX = '.weave'
    
    def __init__(self, name, transport, filemode=None, create=False, access_mode='w', get_scope=None):
        """Create a WeaveFile.
        
        :param create: If not True, only open an existing knit.
        """
        super(WeaveFile, self).__init__(name, access_mode, get_scope=get_scope,
            allow_reserved=False)
        self._transport = transport
        self._filemode = filemode
        try:
            _read_weave_v5(self._transport.get(name + WeaveFile.WEAVE_SUFFIX), self)
        except errors.NoSuchFile:
            if not create:
                raise
            # new file, save it
            self._save()

    def _add_lines(self, version_id, parents, lines, parent_texts,
        left_matching_blocks, nostore_sha, random_id, check_content):
        """Add a version and save the weave."""
        self.check_not_reserved_id(version_id)
        result = super(WeaveFile, self)._add_lines(version_id, parents, lines,
            parent_texts, left_matching_blocks, nostore_sha, random_id,
            check_content)
        self._save()
        return result

    def copy_to(self, name, transport):
        """See VersionedFile.copy_to()."""
        # as we are all in memory always, just serialise to the new place.
        sio = StringIO()
        write_weave_v5(self, sio)
        sio.seek(0)
        transport.put_file(name + WeaveFile.WEAVE_SUFFIX, sio, self._filemode)

    def _save(self):
        """Save the weave."""
        self._check_write_ok()
        sio = StringIO()
        write_weave_v5(self, sio)
        sio.seek(0)
        bytes = sio.getvalue()
        path = self._weave_name + WeaveFile.WEAVE_SUFFIX
        try:
            self._transport.put_bytes(path, bytes, self._filemode)
        except errors.NoSuchFile:
            self._transport.mkdir(dirname(path))
            self._transport.put_bytes(path, bytes, self._filemode)

    @staticmethod
    def get_suffixes():
        """See VersionedFile.get_suffixes()."""
        return [WeaveFile.WEAVE_SUFFIX]

    def insert_record_stream(self, stream):
        super(WeaveFile, self).insert_record_stream(stream)
        self._save()

    @deprecated_method(one_five)
    def join(self, other, pb=None, msg=None, version_ids=None,
             ignore_missing=False):
        """Join other into self and save."""
        super(WeaveFile, self).join(other, pb, msg, version_ids, ignore_missing)
        self._save()


def _reweave(wa, wb, pb=None, msg=None):
    """Combine two weaves and return the result.

    This works even if a revision R has different parents in 
    wa and wb.  In the resulting weave all the parents are given.

    This is done by just building up a new weave, maintaining ordering 
    of the versions in the two inputs.  More efficient approaches
    might be possible but it should only be necessary to do 
    this operation rarely, when a new previously ghost version is 
    inserted.

    :param pb: An optional progress bar, indicating how far done we are
    :param msg: An optional message for the progress
    """
    wr = Weave()
    ia = ib = 0
    queue_a = range(wa.num_versions())
    queue_b = range(wb.num_versions())
    # first determine combined parents of all versions
    # map from version name -> all parent names
    combined_parents = _reweave_parent_graphs(wa, wb)
    mutter("combined parents: %r", combined_parents)
    order = topo_sort(combined_parents.iteritems())
    mutter("order to reweave: %r", order)

    if pb and not msg:
        msg = 'reweave'

    for idx, name in enumerate(order):
        if pb:
            pb.update(msg, idx, len(order))
        if name in wa._name_map:
            lines = wa.get_lines(name)
            if name in wb._name_map:
                lines_b = wb.get_lines(name)
                if lines != lines_b:
                    mutter('Weaves differ on content. rev_id {%s}', name)
                    mutter('weaves: %s, %s', wa._weave_name, wb._weave_name)
                    import difflib
                    lines = list(difflib.unified_diff(lines, lines_b,
                            wa._weave_name, wb._weave_name))
                    mutter('lines:\n%s', ''.join(lines))
                    raise errors.WeaveTextDiffers(name, wa, wb)
        else:
            lines = wb.get_lines(name)
        wr._add(name, lines, [wr._lookup(i) for i in combined_parents[name]])
    return wr

def _reweave_parent_graphs(wa, wb):
    """Return combined parent ancestry for two weaves.
    
    Returned as a list of (version_name, set(parent_names))"""
    combined = {}
    for weave in [wa, wb]:
        for idx, name in enumerate(weave._names):
            p = combined.setdefault(name, set())
            p.update(map(weave._idx_to_name, weave._parents[idx]))
    return combined


def weave_toc(w):
    """Show the weave's table-of-contents"""
    print '%6s %50s %10s %10s' % ('ver', 'name', 'sha1', 'parents')
    for i in (6, 50, 10, 10):
        print '-' * i,
    print
    for i in range(w.num_versions()):
        sha1 = w._sha1s[i]
        name = w._names[i]
        parent_str = ' '.join(map(str, w._parents[i]))
        print '%6d %-50.50s %10.10s %s' % (i, name, sha1, parent_str)



def weave_stats(weave_file, pb):
    from bzrlib.weavefile import read_weave

    wf = file(weave_file, 'rb')
    w = read_weave(wf)
    # FIXME: doesn't work on pipes
    weave_size = wf.tell()

    total = 0
    vers = len(w)
    for i in range(vers):
        pb.update('checking sizes', i, vers)
        for origin, lineno, line in w._extract([i]):
            total += len(line)

    pb.clear()

    print 'versions          %9d' % vers
    print 'weave file        %9d bytes' % weave_size
    print 'total contents    %9d bytes' % total
    print 'compression ratio %9.2fx' % (float(total) / float(weave_size))
    if vers:
        avg = total/vers
        print 'average size      %9d bytes' % avg
        print 'relative size     %9.2fx' % (float(weave_size) / float(avg))


def usage():
    print """bzr weave tool

Experimental tool for weave algorithm.

usage:
    weave init WEAVEFILE
        Create an empty weave file
    weave get WEAVEFILE VERSION
        Write out specified version.
    weave check WEAVEFILE
        Check consistency of all versions.
    weave toc WEAVEFILE
        Display table of contents.
    weave add WEAVEFILE NAME [BASE...] < NEWTEXT
        Add NEWTEXT, with specified parent versions.
    weave annotate WEAVEFILE VERSION
        Display origin of each line.
    weave merge WEAVEFILE VERSION1 VERSION2 > OUT
        Auto-merge two versions and display conflicts.
    weave diff WEAVEFILE VERSION1 VERSION2 
        Show differences between two versions.

example:

    % weave init foo.weave
    % vi foo.txt
    % weave add foo.weave ver0 < foo.txt
    added version 0

    (create updated version)
    % vi foo.txt
    % weave get foo.weave 0 | diff -u - foo.txt
    % weave add foo.weave ver1 0 < foo.txt
    added version 1

    % weave get foo.weave 0 > foo.txt       (create forked version)
    % vi foo.txt
    % weave add foo.weave ver2 0 < foo.txt
    added version 2

    % weave merge foo.weave 1 2 > foo.txt   (merge them)
    % vi foo.txt                            (resolve conflicts)
    % weave add foo.weave merged 1 2 < foo.txt     (commit merged version)     
    
"""
    


def main(argv):
    import sys
    import os
    try:
        import bzrlib
    except ImportError:
        # in case we're run directly from the subdirectory
        sys.path.append('..')
        import bzrlib
    from bzrlib.weavefile import write_weave, read_weave
    from bzrlib.progress import ProgressBar

    try:
        import psyco
        psyco.full()
    except ImportError:
        pass

    if len(argv) < 2:
        usage()
        return 0

    cmd = argv[1]

    def readit():
        return read_weave(file(argv[2], 'rb'))
    
    if cmd == 'help':
        usage()
    elif cmd == 'add':
        w = readit()
        # at the moment, based on everything in the file
        name = argv[3]
        parents = map(int, argv[4:])
        lines = sys.stdin.readlines()
        ver = w.add(name, parents, lines)
        write_weave(w, file(argv[2], 'wb'))
        print 'added version %r %d' % (name, ver)
    elif cmd == 'init':
        fn = argv[2]
        if os.path.exists(fn):
            raise IOError("file exists")
        w = Weave()
        write_weave(w, file(fn, 'wb'))
    elif cmd == 'get': # get one version
        w = readit()
        sys.stdout.writelines(w.get_iter(int(argv[3])))
        
    elif cmd == 'diff':
        w = readit()
        fn = argv[2]
        v1, v2 = map(int, argv[3:5])
        lines1 = w.get(v1)
        lines2 = w.get(v2)
        diff_gen = bzrlib.patiencediff.unified_diff(lines1, lines2,
                                '%s version %d' % (fn, v1),
                                '%s version %d' % (fn, v2))
        sys.stdout.writelines(diff_gen)
            
    elif cmd == 'annotate':
        w = readit()
        # newline is added to all lines regardless; too hard to get
        # reasonable formatting otherwise
        lasto = None
        for origin, text in w.annotate(int(argv[3])):
            text = text.rstrip('\r\n')
            if origin == lasto:
                print '      | %s' % (text)
            else:
                print '%5d | %s' % (origin, text)
                lasto = origin
                
    elif cmd == 'toc':
        weave_toc(readit())

    elif cmd == 'stats':
        weave_stats(argv[2], ProgressBar())
        
    elif cmd == 'check':
        w = readit()
        pb = ProgressBar()
        w.check(pb)
        pb.clear()
        print '%d versions ok' % w.num_versions()

    elif cmd == 'inclusions':
        w = readit()
        print ' '.join(map(str, w.inclusions([int(argv[3])])))

    elif cmd == 'parents':
        w = readit()
        print ' '.join(map(str, w._parents[int(argv[3])]))

    elif cmd == 'plan-merge':
        # replaced by 'bzr weave-plan-merge'
        w = readit()
        for state, line in w.plan_merge(int(argv[3]), int(argv[4])):
            if line:
                print '%14s | %s' % (state, line),
    elif cmd == 'merge':
        # replaced by 'bzr weave-merge-text'
        w = readit()
        p = w.plan_merge(int(argv[3]), int(argv[4]))
        sys.stdout.writelines(w.weave_merge(p))
    else:
        raise ValueError('unknown command %r' % cmd)
    

if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
