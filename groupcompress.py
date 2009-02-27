# groupcompress, a bzr plugin providing new compression logic.
# Copyright (C) 2008 Canonical Limited.
# 
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as published
# by the Free Software Foundation.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA
# 

"""Core compression logic for compressing streams of related files."""

from itertools import izip
from cStringIO import StringIO
import zlib

from bzrlib import (
    annotate,
    debug,
    diff,
    errors,
    graph as _mod_graph,
    osutils,
    pack,
    patiencediff,
    )
from bzrlib.graph import Graph
from bzrlib.knit import _DirectPackAccess
from bzrlib.osutils import (
    contains_whitespace,
    contains_linebreaks,
    sha_string,
    sha_strings,
    split_lines,
    )
from bzrlib.btree_index import BTreeBuilder
from bzrlib.lru_cache import LRUSizeCache
from bzrlib.plugins.groupcompress import equivalence_table
from bzrlib.tsort import topo_sort
from bzrlib.versionedfile import (
    adapter_registry,
    AbsentContentFactory,
    ChunkedContentFactory,
    FulltextContentFactory,
    VersionedFiles,
    )


def sort_gc_optimal(parent_map):
    """Sort and group the keys in parent_map into gc-optimal order.

    gc-optimal is defined (currently) as reverse-topological order, grouped by
    the key prefix.

    :return: A sorted-list of keys
    """
    # gc-optimal ordering is approximately reverse topological,
    # properly grouped by file-id.
    per_prefix_map = {}
    present_keys = []
    for item in parent_map.iteritems():
        key = item[0]
        if isinstance(key, str) or len(key) == 1:
            prefix = ''
        else:
            prefix = key[0]
        try:
            per_prefix_map[prefix].append(item)
        except KeyError:
            per_prefix_map[prefix] = [item]

    for prefix in sorted(per_prefix_map):
        present_keys.extend(reversed(topo_sort(per_prefix_map[prefix])))
    return present_keys


class GroupCompressor(object):
    """Produce a serialised group of compressed texts.

    It contains code very similar to SequenceMatcher because of having a similar
    task. However some key differences apply:
     - there is no junk, we want a minimal edit not a human readable diff.
     - we don't filter very common lines (because we don't know where a good
       range will start, and after the first text we want to be emitting minmal
       edits only.
     - we chain the left side, not the right side
     - we incrementally update the adjacency matrix as new lines are provided.
     - we look for matches in all of the left side, so the routine which does
       the analagous task of find_longest_match does not need to filter on the
       left side.
    """

    def __init__(self, delta=True):
        """Create a GroupCompressor.

        :paeam delta: If False, do not compress records.
        """
        self._compressed = []
        self.endpoint = 0
        self.input_bytes = 0
        self.labels_deltas = {}

    def compress(self, key, chunks, expected_sha, soft=False):
        """Compress lines with label key.

        :param key: A key tuple. It is stored in the output
            for identification of the text during decompression. If the last
            element is 'None' it is replaced with the sha1 of the text -
            e.g. sha1:xxxxxxx.
        :param chunks: The chunks to be compressed
        :param expected_sha: If non-None, the sha the lines are blieved to
            have. During compression the sha is calculated; a mismatch will
            cause an error.
        :param soft: Do a 'soft' compression. This means that we require larger
            ranges to match to be considered for a copy command.
        :return: The sha1 of lines, and the number of bytes accumulated in
            the group output so far.
        """
        sha1 = sha_strings(chunks)
        if key[-1] is None:
            key = key[:-1] + ('sha1:' + sha1,)
        label = '\x00'.join(key)
        ## new_lines = []
        input_len = sum(map(len, chunks))
        new_lines = ['label: %s\n' % label,
                     'sha1: %s\n' % sha1,
                     'len: %s\n' % input_len,
                    ]
        delta_start = (self.endpoint, len(self.lines))
        self.output_lines(new_lines, index_lines)
        self.input_bytes += input_len
        delta_end = (self.endpoint, len(self.lines))
        self.labels_deltas[key] = (delta_start, delta_end)
        return sha1, self.endpoint

    def extract(self, key):
        """Extract a key previously added to the compressor.

        :param key: The key to extract.
        :return: An iterable over bytes and the sha1.
        """
        delta_details = self.labels_deltas[key]
        delta_lines = self.lines[delta_details[0][1]:delta_details[1][1]]
        label, sha1, delta = parse(delta_lines)
        ## delta = parse(delta_lines)
        if label != key:
            raise AssertionError("wrong key: %r, wanted %r" % (label, key))
        # Perhaps we want to keep the line offsets too in memory at least?
        chunks = apply_delta(''.join(self.lines), delta)
        sha1 = sha_strings(chunks)
        return chunks, sha1

    def flush_multi(self, instructions, lines, new_lines, index_lines):
        """Flush a bunch of different ranges out.

        This should only be called with data that are "pure" copies.
        """
        flush_range = self.flush_range
        if len(instructions) > 2:
            # This is the number of lines to be copied
            total_copy_range = sum(i[2] for i in instructions)
            if len(instructions) > 0.5 * total_copy_range:
                # We are copying N lines, but taking more than N/2
                # copy instructions to do so. We will go ahead and expand this
                # text so that other code is able to match against it
                flush_range(instructions[0][0], None, total_copy_range,
                            lines, new_lines, index_lines)
                return
        for ns, os, rl in instructions:
            flush_range(ns, os, rl, lines, new_lines, index_lines)

    def flush_range(self, range_start, copy_start, range_len, lines, new_lines, index_lines):
        insert_instruction = "i,%d\n" % range_len
        if copy_start is not None:
            # range stops, flush and start a new copy range
            stop_byte = self.line_offsets[copy_start + range_len - 1]
            if copy_start == 0:
                start_byte = 0
            else:
                start_byte = self.line_offsets[copy_start - 1]
            bytes = stop_byte - start_byte
            copy_control_instruction = "c,%d,%d\n" % (start_byte, bytes)
            if (bytes + len(insert_instruction) >
                len(copy_control_instruction)):
                new_lines.append(copy_control_instruction)
                index_lines.append(False)
                return
        # not copying, or inserting is shorter than copying, so insert.
        new_lines.append(insert_instruction)
        new_lines.extend(lines[range_start:range_start+range_len])
        index_lines.append(False)
        index_lines.extend([copy_start is None]*range_len)

    def output_lines(self, new_lines, index_lines):
        """Output some lines.

        :param new_lines: The lines to output.
        :param index_lines: A boolean flag for each line - when True, index
            that line.
        """
        # indexed_newlines = [idx for idx, val in enumerate(index_lines)
        #                          if val and new_lines[idx] == '\n']
        # if indexed_newlines:
        #     import pdb; pdb.set_trace()
        endpoint = self.endpoint
        self.line_locations.extend_lines(new_lines, index_lines)
        for line in new_lines:
            endpoint += len(line)
            self.line_offsets.append(endpoint)
        self.endpoint = endpoint

    def ratio(self):
        """Return the overall compression ratio."""
        return float(self.input_bytes) / float(self.endpoint)


def make_pack_factory(graph, delta, keylength):
    """Create a factory for creating a pack based groupcompress.

    This is only functional enough to run interface tests, it doesn't try to
    provide a full pack environment.
    
    :param graph: Store a graph.
    :param delta: Delta compress contents.
    :param keylength: How long should keys be.
    """
    def factory(transport):
        parents = graph or delta
        ref_length = 0
        if graph:
            ref_length += 1
        graph_index = BTreeBuilder(reference_lists=ref_length,
            key_elements=keylength)
        stream = transport.open_write_stream('newpack')
        writer = pack.ContainerWriter(stream.write)
        writer.begin()
        index = _GCGraphIndex(graph_index, lambda:True, parents=parents,
            add_callback=graph_index.add_nodes)
        access = _DirectPackAccess({})
        access.set_writer(writer, graph_index, (transport, 'newpack'))
        result = GroupCompressVersionedFiles(index, access, delta)
        result.stream = stream
        result.writer = writer
        return result
    return factory


def cleanup_pack_group(versioned_files):
    versioned_files.writer.end()
    versioned_files.stream.close()


class GroupCompressVersionedFiles(VersionedFiles):
    """A group-compress based VersionedFiles implementation."""

    def __init__(self, index, access, delta=True):
        """Create a GroupCompressVersionedFiles object.

        :param index: The index object storing access and graph data.
        :param access: The access object storing raw data.
        :param delta: Whether to delta compress or just entropy compress.
        """
        self._index = index
        self._access = access
        self._delta = delta
        self._unadded_refs = {}
        self._group_cache = LRUSizeCache(max_size=50*1024*1024)

    def add_lines(self, key, parents, lines, parent_texts=None,
        left_matching_blocks=None, nostore_sha=None, random_id=False,
        check_content=True):
        """Add a text to the store.

        :param key: The key tuple of the text to add.
        :param parents: The parents key tuples of the text to add.
        :param lines: A list of lines. Each line must be a bytestring. And all
            of them except the last must be terminated with \n and contain no
            other \n's. The last line may either contain no \n's or a single
            terminating \n. If the lines list does meet this constraint the add
            routine may error or may succeed - but you will be unable to read
            the data back accurately. (Checking the lines have been split
            correctly is expensive and extremely unlikely to catch bugs so it
            is not done at runtime unless check_content is True.)
        :param parent_texts: An optional dictionary containing the opaque 
            representations of some or all of the parents of version_id to
            allow delta optimisations.  VERY IMPORTANT: the texts must be those
            returned by add_lines or data corruption can be caused.
        :param left_matching_blocks: a hint about which areas are common
            between the text and its left-hand-parent.  The format is
            the SequenceMatcher.get_matching_blocks format.
        :param nostore_sha: Raise ExistingContent and do not add the lines to
            the versioned file if the digest of the lines matches this.
        :param random_id: If True a random id has been selected rather than
            an id determined by some deterministic process such as a converter
            from a foreign VCS. When True the backend may choose not to check
            for uniqueness of the resulting key within the versioned file, so
            this should only be done when the result is expected to be unique
            anyway.
        :param check_content: If True, the lines supplied are verified to be
            bytestrings that are correctly formed lines.
        :return: The text sha1, the number of bytes in the text, and an opaque
                 representation of the inserted version which can be provided
                 back to future add_lines calls in the parent_texts dictionary.
        """
        self._index._check_write_ok()
        self._check_add(key, lines, random_id, check_content)
        if parents is None:
            # The caller might pass None if there is no graph data, but kndx
            # indexes can't directly store that, so we give them
            # an empty tuple instead.
            parents = ()
        # double handling for now. Make it work until then.
        length = sum(map(len, lines))
        record = ChunkedContentFactory(key, parents, None, lines)
        sha1 = list(self._insert_record_stream([record], random_id=random_id))[0]
        return sha1, length, None

    def annotate(self, key):
        """See VersionedFiles.annotate."""
        graph = Graph(self)
        parent_map = self.get_parent_map([key])
        if not parent_map:
            raise errors.RevisionNotPresent(key, self)
        if parent_map[key] is not None:
            search = graph._make_breadth_first_searcher([key])
            keys = set()
            while True:
                try:
                    present, ghosts = search.next_with_ghosts()
                except StopIteration:
                    break
                keys.update(present)
            parent_map = self.get_parent_map(keys)
        else:
            keys = [key]
            parent_map = {key:()}
        head_cache = _mod_graph.FrozenHeadsCache(graph)
        parent_cache = {}
        reannotate = annotate.reannotate
        for record in self.get_record_stream(keys, 'topological', True):
            key = record.key
            chunks = osutils.chunks_to_lines(record.get_bytes_as('chunked'))
            parent_lines = [parent_cache[parent] for parent in parent_map[key]]
            parent_cache[key] = list(
                reannotate(parent_lines, chunks, key, None, head_cache))
        return parent_cache[key]

    def check(self, progress_bar=None):
        """See VersionedFiles.check()."""
        keys = self.keys()
        for record in self.get_record_stream(keys, 'unordered', True):
            record.get_bytes_as('fulltext')

    def _check_add(self, key, lines, random_id, check_content):
        """check that version_id and lines are safe to add."""
        version_id = key[-1]
        if version_id is not None:
            if contains_whitespace(version_id):
                raise InvalidRevisionId(version_id, self)
        self.check_not_reserved_id(version_id)
        # TODO: If random_id==False and the key is already present, we should
        # probably check that the existing content is identical to what is
        # being inserted, and otherwise raise an exception.  This would make
        # the bundle code simpler.
        if check_content:
            self._check_lines_not_unicode(lines)
            self._check_lines_are_lines(lines)

    def get_parent_map(self, keys):
        """Get a map of the parents of keys.

        :param keys: The keys to look up parents for.
        :return: A mapping from keys to parents. Absent keys are absent from
            the mapping.
        """
        result = {}
        sources = [self._index]
        source_results = []
        missing = set(keys)
        for source in sources:
            if not missing:
                break
            new_result = source.get_parent_map(missing)
            source_results.append(new_result)
            result.update(new_result)
            missing.difference_update(set(new_result))
        if self._unadded_refs:
            for key in missing:
                if key in self._unadded_refs:
                    result[key] = self._unadded_refs[key]
        return result

    def _get_delta_lines(self, key):
        """Helper function for manual debugging.

        This is a convenience function that shouldn't be used in production
        code.
        """
        build_details = self._index.get_build_details([key])[key]
        index_memo = build_details[0]
        group, delta_lines = self._get_group_and_delta_lines(index_memo)
        return delta_lines

    def _get_group_and_delta_lines(self, index_memo):
        read_memo = index_memo[0:3]
        # get the group:
        try:
            plain = self._group_cache[read_memo]
        except KeyError:
            # read the group
            zdata = self._access.get_raw_records([read_memo]).next()
            # decompress - whole thing - this is not a bug, as it
            # permits caching. We might want to store the partially
            # decompresed group and decompress object, so that recent
            # texts are not penalised by big groups.
            plain = zlib.decompress(zdata) #, index_memo[4])
            self._group_cache[read_memo] = plain
        # cheapo debugging:
        # print len(zdata), len(plain)
        # parse - requires split_lines, better to have byte offsets
        # here (but not by much - we only split the region for the
        # recipe, and we often want to end up with lines anyway.
        return plain, split_lines(plain[index_memo[3]:index_memo[4]])

    def get_missing_compression_parent_keys(self):
        """Return the keys of missing compression parents.

        Missing compression parents occur when a record stream was missing
        basis texts, or a index was scanned that had missing basis texts.
        """
        # GroupCompress cannot currently reference texts that are not in the
        # group, so this is valid for now
        return frozenset()

    def get_record_stream(self, keys, ordering, include_delta_closure):
        """Get a stream of records for keys.

        :param keys: The keys to include.
        :param ordering: Either 'unordered' or 'topological'. A topologically
            sorted stream has compression parents strictly before their
            children.
        :param include_delta_closure: If True then the closure across any
            compression parents will be included (in the opaque data).
        :return: An iterator of ContentFactory objects, each of which is only
            valid until the iterator is advanced.
        """
        # keys might be a generator
        orig_keys = list(keys)
        keys = set(orig_keys)
        if not keys:
            return
        if (not self._index.has_graph
            and ordering in ('topological', 'gc-optimal')):
            # Cannot topological order when no graph has been stored.
            ordering = 'unordered'
        # Cheap: iterate
        locations = self._index.get_build_details(keys)
        local_keys = frozenset(keys).intersection(set(self._unadded_refs))
        locations.update((key, None) for key in local_keys)
        if ordering == 'topological':
            # would be better to not globally sort initially but instead
            # start with one key, recurse to its oldest parent, then grab
            # everything in the same group, etc.
            parent_map = dict((key, details[2]) for key, details in
                locations.iteritems())
            for key in local_keys:
                parent_map[key] = self._unadded_refs[key]
            present_keys = topo_sort(parent_map)
            # Now group by source:
        elif ordering == 'gc-optimal':
            parent_map = dict((key, details[2]) for key, details in
                              locations.iteritems())
            for key in local_keys:
                parent_map[key] = self._unadded_refs[key]
            # XXX: This only optimizes for the target ordering. We may need to
            #      balance that with the time it takes to extract ordering, by
            #      somehow grouping based on locations[key][0:3]
            present_keys = sort_gc_optimal(parent_map)
        elif ordering == 'as-requested':
            present_keys = [key for key in orig_keys if key in locations]
        else:
            # We want to yield the keys in a semi-optimal (read-wise) ordering.
            # Otherwise we thrash the _group_cache and destroy performance
            def get_group(key):
                # This is the group the bytes are stored in, followed by the
                # location in the group
                return locations[key][0]
            present_keys = sorted(locations.iterkeys(), key=get_group)
            # We don't have an ordering for keys in the in-memory object, but
            # lets process the in-memory ones first.
            local = list(local_keys)
            present_keys = list(local_keys) + present_keys
        absent_keys = keys.difference(set(locations))
        for key in absent_keys:
            yield AbsentContentFactory(key)
        for key in present_keys:
            if key in self._unadded_refs:
                lines, sha1 = self._compressor.extract(key)
                parents = self._unadded_refs[key]
            else:
                index_memo, _, parents, (method, _) = locations[key]
                plain, delta_lines = self._get_group_and_delta_lines(index_memo)
                ## delta = parse(delta_lines)
                label, sha1, delta = parse(delta_lines)
                if label != key:
                    raise AssertionError("wrong key: %r, wanted %r" % (label, key))
                chunks = apply_delta(plain, delta)
                ## sha1 = sha_strings(lines)
                if sha_strings(chunks) != sha1:
                    raise AssertionError('sha1 sum did not match')
            yield ChunkedContentFactory(key, parents, sha1, chunks)

    def get_sha1s(self, keys):
        """See VersionedFiles.get_sha1s()."""
        result = {}
        for record in self.get_record_stream(keys, 'unordered', True):
            if record.sha1 != None:
                result[record.key] = record.sha1
            else:
                if record.storage_kind != 'absent':
                    result[record.key] == sha_string(record.get_bytes_as(
                        'fulltext'))
        return result

    def insert_record_stream(self, stream):
        """Insert a record stream into this container.

        :param stream: A stream of records to insert. 
        :return: None
        :seealso VersionedFiles.get_record_stream:
        """
        for _ in self._insert_record_stream(stream):
            pass

    def _insert_record_stream(self, stream, random_id=False):
        """Internal core to insert a record stream into this container.

        This helper function has a different interface than insert_record_stream
        to allow add_lines to be minimal, but still return the needed data.

        :param stream: A stream of records to insert. 
        :return: An iterator over the sha1 of the inserted records.
        :seealso insert_record_stream:
        :seealso add_lines:
        """
        def get_adapter(adapter_key):
            try:
                return adapters[adapter_key]
            except KeyError:
                adapter_factory = adapter_registry.get(adapter_key)
                adapter = adapter_factory(self)
                adapters[adapter_key] = adapter
                return adapter
        adapters = {}
        # This will go up to fulltexts for gc to gc fetching, which isn't
        # ideal.
        self._compressor = GroupCompressor(self._delta)
        self._unadded_refs = {}
        keys_to_add = []
        basis_end = 0
        groups = 1
        def flush():
            compressed = zlib.compress(''.join(self._compressor.lines))
            index, start, length = self._access.add_raw_records(
                [(None, len(compressed))], compressed)[0]
            nodes = []
            for key, reads, refs in keys_to_add:
                nodes.append((key, "%d %d %s" % (start, length, reads), refs))
            self._index.add_records(nodes, random_id=random_id)
        last_prefix = None
        for record in stream:
            # Raise an error when a record is missing.
            if record.storage_kind == 'absent':
                raise errors.RevisionNotPresent([record.key], self)
            try:
                lines = osutils.chunks_to_lines(record.get_bytes_as('chunked'))
            except errors.UnavailableRepresentation:
                adapter_key = record.storage_kind, 'fulltext'
                adapter = get_adapter(adapter_key)
                bytes = adapter.get_bytes(record)
                lines = osutils.split_lines(bytes)
            soft = False
            if len(record.key) > 1:
                prefix = record.key[0]
                if (last_prefix is not None and prefix != last_prefix):
                    soft = True
                    if basis_end > 1024 * 1024 * 4:
                        flush()
                        self._compressor = GroupCompressor(self._delta)
                        self._unadded_refs = {}
                        keys_to_add = []
                        basis_end = 0
                        groups += 1
                last_prefix = prefix
            found_sha1, end_point = self._compressor.compress(record.key,
                lines, record.sha1, soft=soft)
            if record.key[-1] is None:
                key = record.key[:-1] + ('sha1:' + found_sha1,)
            else:
                key = record.key
            self._unadded_refs[key] = record.parents
            yield found_sha1
            keys_to_add.append((key, '%d %d' % (basis_end, end_point),
                (record.parents,)))
            basis_end = end_point
            if basis_end > 1024 * 1024 * 8:
                flush()
                self._compressor = GroupCompressor(self._delta)
                self._unadded_refs = {}
                keys_to_add = []
                basis_end = 0
                groups += 1
        if len(keys_to_add):
            flush()
        self._compressor = None
        self._unadded_refs = {}

    def iter_lines_added_or_present_in_keys(self, keys, pb=None):
        """Iterate over the lines in the versioned files from keys.

        This may return lines from other keys. Each item the returned
        iterator yields is a tuple of a line and a text version that that line
        is present in (not introduced in).

        Ordering of results is in whatever order is most suitable for the
        underlying storage format.

        If a progress bar is supplied, it may be used to indicate progress.
        The caller is responsible for cleaning up progress bars (because this
        is an iterator).

        NOTES:
         * Lines are normalised by the underlying store: they will all have \n
           terminators.
         * Lines are returned in arbitrary order.

        :return: An iterator over (line, key).
        """
        if pb is None:
            pb = progress.DummyProgress()
        keys = set(keys)
        total = len(keys)
        # we don't care about inclusions, the caller cares.
        # but we need to setup a list of records to visit.
        # we need key, position, length
        for key_idx, record in enumerate(self.get_record_stream(keys,
            'unordered', True)):
            # XXX: todo - optimise to use less than full texts.
            key = record.key
            pb.update('Walking content.', key_idx, total)
            if record.storage_kind == 'absent':
                raise errors.RevisionNotPresent(record.key, self)
            lines = split_lines(record.get_bytes_as('fulltext'))
            for line in lines:
                yield line, key
        pb.update('Walking content.', total, total)

    def keys(self):
        """See VersionedFiles.keys."""
        if 'evil' in debug.debug_flags:
            trace.mutter_callsite(2, "keys scales with size of history")
        sources = [self._index]
        result = set()
        for source in sources:
            result.update(source.keys())
        return result


class _GCGraphIndex(object):
    """Mapper from GroupCompressVersionedFiles needs into GraphIndex storage."""

    def __init__(self, graph_index, is_locked, parents=True,
        add_callback=None):
        """Construct a _GCGraphIndex on a graph_index.

        :param graph_index: An implementation of bzrlib.index.GraphIndex.
        :param is_locked: A callback to check whether the object should answer
            queries.
        :param parents: If True, record knits parents, if not do not record 
            parents.
        :param add_callback: If not None, allow additions to the index and call
            this callback with a list of added GraphIndex nodes:
            [(node, value, node_refs), ...]
        :param is_locked: A callback, returns True if the index is locked and
            thus usable.
        """
        self._add_callback = add_callback
        self._graph_index = graph_index
        self._parents = parents
        self.has_graph = parents
        self._is_locked = is_locked

    def add_records(self, records, random_id=False):
        """Add multiple records to the index.
        
        This function does not insert data into the Immutable GraphIndex
        backing the KnitGraphIndex, instead it prepares data for insertion by
        the caller and checks that it is safe to insert then calls
        self._add_callback with the prepared GraphIndex nodes.

        :param records: a list of tuples:
                         (key, options, access_memo, parents).
        :param random_id: If True the ids being added were randomly generated
            and no check for existence will be performed.
        """
        if not self._add_callback:
            raise errors.ReadOnlyError(self)
        # we hope there are no repositories with inconsistent parentage
        # anymore.

        changed = False
        keys = {}
        for (key, value, refs) in records:
            if not self._parents:
                if refs:
                    for ref in refs:
                        if ref:
                            raise KnitCorrupt(self,
                                "attempt to add node with parents "
                                "in parentless index.")
                    refs = ()
                    changed = True
            keys[key] = (value, refs)
        # check for dups
        if not random_id:
            present_nodes = self._get_entries(keys)
            for (index, key, value, node_refs) in present_nodes:
                if node_refs != keys[key][1]:
                    raise errors.KnitCorrupt(self, "inconsistent details in add_records"
                        ": %s %s" % ((value, node_refs), keys[key]))
                del keys[key]
                changed = True
        if changed:
            result = []
            if self._parents:
                for key, (value, node_refs) in keys.iteritems():
                    result.append((key, value, node_refs))
            else:
                for key, (value, node_refs) in keys.iteritems():
                    result.append((key, value))
            records = result
        self._add_callback(records)
        
    def _check_read(self):
        """raise if reads are not permitted."""
        if not self._is_locked():
            raise errors.ObjectNotLocked(self)

    def _check_write_ok(self):
        """Assert if writes are not permitted."""
        if not self._is_locked():
            raise errors.ObjectNotLocked(self)

    def _get_entries(self, keys, check_present=False):
        """Get the entries for keys.
        
        :param keys: An iterable of index key tuples.
        """
        keys = set(keys)
        found_keys = set()
        if self._parents:
            for node in self._graph_index.iter_entries(keys):
                yield node
                found_keys.add(node[1])
        else:
            # adapt parentless index to the rest of the code.
            for node in self._graph_index.iter_entries(keys):
                yield node[0], node[1], node[2], ()
                found_keys.add(node[1])
        if check_present:
            missing_keys = keys.difference(found_keys)
            if missing_keys:
                raise RevisionNotPresent(missing_keys.pop(), self)

    def get_parent_map(self, keys):
        """Get a map of the parents of keys.

        :param keys: The keys to look up parents for.
        :return: A mapping from keys to parents. Absent keys are absent from
            the mapping.
        """
        self._check_read()
        nodes = self._get_entries(keys)
        result = {}
        if self._parents:
            for node in nodes:
                result[node[1]] = node[3][0]
        else:
            for node in nodes:
                result[node[1]] = None
        return result

    def get_build_details(self, keys):
        """Get the various build details for keys.

        Ghosts are omitted from the result.

        :param keys: An iterable of keys.
        :return: A dict of key:
            (index_memo, compression_parent, parents, record_details).
            index_memo
                opaque structure to pass to read_records to extract the raw
                data
            compression_parent
                Content that this record is built upon, may be None
            parents
                Logical parents of this node
            record_details
                extra information about the content which needs to be passed to
                Factory.parse_record
        """
        self._check_read()
        result = {}
        entries = self._get_entries(keys, False)
        for entry in entries:
            key = entry[1]
            if not self._parents:
                parents = None
            else:
                parents = entry[3][0]
            value = entry[2]
            method = 'group'
            result[key] = (self._node_to_position(entry),
                                  None, parents, (method, None))
        return result
    
    def keys(self):
        """Get all the keys in the collection.
        
        The keys are not ordered.
        """
        self._check_read()
        return [node[1] for node in self._graph_index.iter_all_entries()]
    
    def _node_to_position(self, node):
        """Convert an index value to position details."""
        bits = node[2].split(' ')
        # It would be nice not to read the entire gzip.
        start = int(bits[0])
        stop = int(bits[1])
        basis_end = int(bits[2])
        delta_end = int(bits[3])
        return node[0], start, stop, basis_end, delta_end


def _get_longest_match(equivalence_table, pos, max_pos, locations):
    """Get the longest possible match for the current position."""
    range_start = pos
    range_len = 0
    copy_ends = None
    while pos < max_pos:
        if locations is None:
            locations = equivalence_table.get_idx_matches(pos)
        if locations is None:
            # No more matches, just return whatever we have, but we know that
            # this last position is not going to match anything
            pos += 1
            break
        else:
            if copy_ends is None:
                # We are starting a new range
                copy_ends = [loc + 1 for loc in locations]
                range_len = 1
                locations = None # Consumed
            else:
                # We are currently in the middle of a match
                next_locations = set(copy_ends).intersection(locations)
                if len(next_locations):
                    # range continues
                    copy_ends = [loc + 1 for loc in next_locations]
                    range_len += 1
                    locations = None # Consumed
                else:
                    # But we are done with this match, we should be
                    # starting a new one, though. We will pass back 'locations'
                    # so that we don't have to do another lookup.
                    break
        pos += 1
    if copy_ends is None:
        return None, pos, locations
    return ((min(copy_ends) - range_len, range_start, range_len)), pos, locations


try:
    from bzrlib.plugins.groupcompress import _groupcompress_c
except ImportError:
    pass
else:
    GroupCompressor._equivalence_table_class = _groupcompress_c.EquivalenceTable
    _get_longest_match = _groupcompress_c._get_longest_match
