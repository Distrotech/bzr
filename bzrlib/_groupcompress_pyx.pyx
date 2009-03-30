# Copyright (C) 2009 Canonical Ltd
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

"""Compiled extensions for doing compression."""

cdef extern from *:
    ctypedef unsigned long size_t
    void * malloc(size_t)
    void * realloc(void *, size_t)
    void free(void *)
    void memcpy(void *, void *, size_t)

cdef extern from "delta.h":
    struct source_info:
        void *buf
        unsigned long size
        unsigned long agg_offset
    struct delta_index:
        pass
    delta_index * create_delta_index(source_info *src, delta_index *old)
    delta_index * create_delta_index_from_delta(source_info *delta,
                                                delta_index *old)
    void free_delta_index(delta_index *index)
    void *create_delta(delta_index *indexes,
             void *buf, unsigned long bufsize,
             unsigned long *delta_size, unsigned long max_delta_size)
    unsigned long get_delta_hdr_size(unsigned char **datap,
                                     unsigned char *top)
    Py_ssize_t DELTA_SIZE_MIN
    void *patch_delta(void *src_buf, unsigned long src_size,
                      void *delta_buf, unsigned long delta_size,
                      unsigned long *dst_size)

cdef extern from "Python.h":
    int PyString_CheckExact(object)
    char * PyString_AS_STRING(object)
    Py_ssize_t PyString_GET_SIZE(object)
    object PyString_FromStringAndSize(char *, Py_ssize_t)


cdef void *safe_malloc(size_t count) except NULL:
    cdef void *result
    result = malloc(count)
    if result == NULL:
        raise MemoryError('Failed to allocate %d bytes of memory' % (count,))
    return result


cdef void *safe_realloc(void * old, size_t count) except NULL:
    cdef void *result
    result = realloc(old, count)
    if result == NULL:
        raise MemoryError('Failed to reallocate to %d bytes of memory'
                          % (count,))
    return result


cdef int safe_free(void **val) except -1:
    assert val != NULL
    if val[0] != NULL:
        free(val[0])
        val[0] = NULL

def make_delta_index(source):
    return DeltaIndex(source)


cdef class DeltaIndex:

    # We need Pyrex 0.9.8+ to understand a 'list' definition, and this object
    # isn't performance critical
    # cdef readonly list _sources
    cdef readonly object _sources
    cdef source_info *_source_infos
    cdef delta_index *_index
    cdef readonly unsigned int _max_num_sources
    cdef public unsigned long _source_offset

    def __init__(self, source=None):
        self._sources = []
        self._index = NULL
        self._max_num_sources = 65000
        self._source_infos = <source_info *>safe_malloc(sizeof(source_info)
                                                        * self._max_num_sources)
        self._source_offset = 0

        if source is not None:
            self.add_source(source, 0)

    def __repr__(self):
        return '%s(%d, %d)' % (self.__class__.__name__,
            len(self._sources), self._source_offset)

    def __dealloc__(self):
        if self._index != NULL:
            free_delta_index(self._index)
            self._index = NULL
        safe_free(<void **>&self._source_infos)

    def add_delta_source(self, delta, unadded_bytes):
        """Add a new delta to the source texts.

        :param delta: The text of the delta, this must be a byte string.
        :param unadded_bytes: Number of bytes that were added to the source
            that were not indexed.
        """
        cdef char *c_delta
        cdef Py_ssize_t c_delta_size
        cdef delta_index *index
        cdef unsigned int source_location
        cdef source_info *src
        cdef unsigned int num_indexes

        if not PyString_CheckExact(delta):
            raise TypeError('delta is not a str')

        source_location = len(self._sources)
        if source_location >= self._max_num_sources:
            self._expand_sources()
        self._sources.append(delta)
        c_delta = PyString_AS_STRING(delta)
        c_delta_size = PyString_GET_SIZE(delta)
        src = self._source_infos + source_location
        src.buf = c_delta
        src.size = c_delta_size
        src.agg_offset = self._source_offset + unadded_bytes
        index = create_delta_index_from_delta(src, self._index)
        self._source_offset = src.agg_offset + src.size
        if index != NULL:
            free_delta_index(self._index)
            self._index = index

    def add_source(self, source, unadded_bytes):
        """Add a new bit of source text to the delta indexes.

        :param source: The text in question, this must be a byte string
        :param unadded_bytes: Assume there are this many bytes that didn't get
            added between this source and the end of the previous source.
        """
        cdef char *c_source
        cdef Py_ssize_t c_source_size
        cdef delta_index *index
        cdef unsigned int source_location
        cdef source_info *src
        cdef unsigned int num_indexes

        if not PyString_CheckExact(source):
            raise TypeError('source is not a str')

        source_location = len(self._sources)
        if source_location >= self._max_num_sources:
            self._expand_sources()
        self._sources.append(source)
        c_source = PyString_AS_STRING(source)
        c_source_size = PyString_GET_SIZE(source)
        src = self._source_infos + source_location
        src.buf = c_source
        src.size = c_source_size

        src.agg_offset = self._source_offset + unadded_bytes
        index = create_delta_index(src, self._index)
        self._source_offset = src.agg_offset + src.size
        if index != NULL:
            free_delta_index(self._index)
            self._index = index

    cdef _expand_sources(self):
        raise RuntimeError('if we move self._source_infos, then we need to'
                           ' change all of the index pointers as well.')
        self._max_num_sources = self._max_num_sources * 2
        self._source_infos = <source_info *>safe_realloc(self._source_infos,
                                                sizeof(source_info)
                                                * self._max_num_sources)

    def make_delta(self, target_bytes, max_delta_size=0):
        """Create a delta from the current source to the target bytes."""
        cdef char *target
        cdef Py_ssize_t target_size
        cdef void * delta
        cdef unsigned long delta_size

        if self._index == NULL:
            return None

        if not PyString_CheckExact(target_bytes):
            raise TypeError('target is not a str')

        target = PyString_AS_STRING(target_bytes)
        target_size = PyString_GET_SIZE(target_bytes)

        # TODO: inline some of create_delta so we at least don't have to double
        #       malloc, and can instead use PyString_FromStringAndSize, to
        #       allocate the bytes into the final string
        delta = create_delta(self._index,
                             target, target_size,
                             &delta_size, max_delta_size)
        result = None
        if delta:
            result = PyString_FromStringAndSize(<char *>delta, delta_size)
            free(delta)
        return result


def make_delta(source_bytes, target_bytes):
    """Create a delta, this is a wrapper around DeltaIndex.make_delta."""
    di = DeltaIndex(source_bytes)
    return di.make_delta(target_bytes)


def apply_delta(source_bytes, delta_bytes):
    """Apply a delta generated by make_delta to source_bytes."""
    cdef char *source
    cdef Py_ssize_t source_size
    cdef char *delta
    cdef Py_ssize_t delta_size

    if not PyString_CheckExact(source_bytes):
        raise TypeError('source is not a str')
    if not PyString_CheckExact(delta_bytes):
        raise TypeError('delta is not a str')
    source = PyString_AS_STRING(source_bytes)
    source_size = PyString_GET_SIZE(source_bytes)
    delta = PyString_AS_STRING(delta_bytes)
    delta_size = PyString_GET_SIZE(delta_bytes)
    # Code taken from patch-delta.c, only brought here to give better error
    # handling, and to avoid double allocating memory
    if (delta_size < DELTA_SIZE_MIN):
        # XXX: Invalid delta block
        raise RuntimeError('delta_size %d smaller than min delta size %d'
                           % (delta_size, DELTA_SIZE_MIN))

    return _apply_delta(source, source_size, delta, delta_size)


cdef unsigned char *_decode_copy_instruction(unsigned char *bytes,
    unsigned char cmd, unsigned int *offset, unsigned int *length):
    """Decode a copy instruction from the next few bytes.

    A copy instruction is a variable number of bytes, so we will parse the
    bytes we care about, and return the new position, as well as the offset and
    length referred to in the bytes.

    :param bytes: Pointer to the start of bytes after cmd
    :param cmd: The command code
    :return: Pointer to the bytes just after the last decode byte
    """
    cdef unsigned int off, size, count
    off = 0
    size = 0
    count = 0
    if (cmd & 0x01):
        off = bytes[count]
        count = count + 1
    if (cmd & 0x02):
        off = off | (bytes[count] << 8)
        count = count + 1
    if (cmd & 0x04):
        off = off | (bytes[count] << 16)
        count = count + 1
    if (cmd & 0x08):
        off = off | (bytes[count] << 24)
        count = count + 1
    if (cmd & 0x10):
        size = bytes[count]
        count = count + 1
    if (cmd & 0x20):
        size = size | (bytes[count] << 8)
        count = count + 1
    if (cmd & 0x40):
        size = size | (bytes[count] << 16)
        count = count + 1
    if (size == 0):
        size = 0x10000
    offset[0] = off
    length[0] = size
    return bytes + count


cdef object _apply_delta(char *source, Py_ssize_t source_size,
                         char *delta, Py_ssize_t delta_size):
    """common functionality between apply_delta and apply_delta_to_source."""
    cdef unsigned char *data, *top
    cdef unsigned char *dst_buf, *out, cmd
    cdef Py_ssize_t size
    cdef unsigned int cp_off, cp_size

    data = <unsigned char *>delta
    top = data + delta_size

    # now the result size
    size = get_delta_hdr_size(&data, top)
    result = PyString_FromStringAndSize(NULL, size)
    dst_buf = <unsigned char*>PyString_AS_STRING(result)

    out = dst_buf
    while (data < top):
        cmd = data[0]
        data = data + 1
        if (cmd & 0x80):
            # Copy instruction
            data = _decode_copy_instruction(data, cmd, &cp_off, &cp_size)
            if (cp_off + cp_size < cp_size or
                cp_off + cp_size > source_size or
                cp_size > size):
                raise RuntimeError('Something wrong with:'
                    ' cp_off = %s, cp_size = %s'
                    ' source_size = %s, size = %s'
                    % (cp_off, cp_size, source_size, size))
            memcpy(out, source + cp_off, cp_size)
            out = out + cp_size
            size = size - cp_size
        else:
            # Insert instruction
            if cmd == 0:
                # cmd == 0 is reserved for future encoding
                # extensions. In the mean time we must fail when
                # encountering them (might be data corruption).
                raise RuntimeError('Got delta opcode: 0, not supported')
            if (cmd > size):
                raise RuntimeError('Insert instruction longer than remaining'
                    ' bytes: %d > %d' % (cmd, size))
            memcpy(out, data, cmd)
            out = out + cmd
            data = data + cmd
            size = size - cmd

    # sanity check
    if (data != top or size != 0):
        raise RuntimeError('Did not extract the number of bytes we expected'
            ' we were left with %d bytes in "size", and top - data = %d'
            % (size, <int>(top - data)))
        return None

    # *dst_size = out - dst_buf;
    if (out - dst_buf) != PyString_GET_SIZE(result):
        raise RuntimeError('Number of bytes extracted did not match the'
            ' size encoded in the delta header.')
    return result


def apply_delta_to_source(source, delta_start, delta_end):
    """Extract a delta from source bytes, and apply it."""
    cdef char *c_source
    cdef Py_ssize_t c_source_size
    cdef char *c_delta
    cdef Py_ssize_t c_delta_size
    cdef Py_ssize_t c_delta_start, c_delta_end

    if not PyString_CheckExact(source):
        raise TypeError('source is not a str')
    c_source_size = PyString_GET_SIZE(source)
    c_delta_start = delta_start
    c_delta_end = delta_end
    if c_delta_start >= c_source_size:
        raise ValueError('delta starts after source')
    if c_delta_end > c_source_size:
        raise ValueError('delta ends after source')
    if c_delta_start >= c_delta_end:
        raise ValueError('delta starts after it ends')

    c_delta_size = c_delta_end - c_delta_start
    c_source = PyString_AS_STRING(source)
    c_delta = c_source + c_delta_start
    # We don't use source_size, because we know the delta should not refer to
    # any bytes after it starts
    return _apply_delta(c_source, c_delta_start, c_delta, c_delta_size)


def encode_base128_int(val):
    """Convert an integer into a 7-bit lsb encoding."""
    cdef unsigned int c_val
    cdef Py_ssize_t count
    cdef unsigned int num_bytes
    cdef unsigned char c_bytes[8] # max size for 32-bit int is 5 bytes

    c_val = val
    count = 0
    while c_val >= 0x80 and count < 8:
        c_bytes[count] = <unsigned char>((c_val | 0x80) & 0xFF)
        c_val = c_val >> 7
        count = count + 1
    if count >= 8 or c_val >= 0x80:
        raise ValueError('encode_base128_int overflowed the buffer')
    c_bytes[count] = <unsigned char>(c_val & 0xFF)
    count = count + 1
    return PyString_FromStringAndSize(<char *>c_bytes, count)


def decode_base128_int(bytes):
    """Decode an integer from a 7-bit lsb encoding."""
    cdef int offset
    cdef int val
    cdef unsigned int uval
    cdef int shift
    cdef Py_ssize_t num_low_bytes
    cdef unsigned char *c_bytes

    offset = 0
    val = 0
    shift = 0
    if not PyString_CheckExact(bytes):
        raise TypeError('bytes is not a string')
    c_bytes = <unsigned char*>PyString_AS_STRING(bytes)
    # We take off 1, because we have to be able to decode the non-expanded byte
    num_low_bytes = PyString_GET_SIZE(bytes) - 1
    while (c_bytes[offset] & 0x80) and offset < num_low_bytes:
        val |= (c_bytes[offset] & 0x7F) << shift
        shift = shift + 7
        offset = offset + 1
    if c_bytes[offset] & 0x80:
        raise ValueError('Data not properly formatted, we ran out of'
                         ' bytes before 0x80 stopped being set.')
    val |= c_bytes[offset] << shift
    offset = offset + 1
    if val < 0:
        uval = <unsigned int> val
        return uval, offset
    return val, offset


