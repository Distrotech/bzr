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

"""Helper functions for DirState.

This is the python implementation for DirState functions.
"""

from bzrlib.dirstate import DirState


cdef extern from *:
    ctypedef int size_t

cdef extern from "Python.h":
    # GetItem returns a borrowed reference
    void *PyDict_GetItem(object p, object key)
    int PyDict_SetItem(object p, object key, object val) except -1

    int PyList_Append(object lst, object item) except -1
    void *PyList_GetItem_object_void "PyList_GET_ITEM" (object lst, int index)
    object PyList_GET_ITEM (object lst, int index)
    int PyList_CheckExact(object)

    int PyTuple_CheckExact(object)
    void *PyTuple_GetItem_void_void "PyTuple_GET_ITEM" (void* tpl, int index)
    object PyTuple_New(int)
    int PyTuple_SetItem(object tpl, int offset, object val)
    void PyTuple_SET_ITEM(object tpl, int offset, object val)
    object PyTuple_Pack(int n, ...)

    char *PyString_AsString(object p)
    char *PyString_AS_STRING_void "PyString_AS_STRING" (void *p)
    int PyString_Size(object p)
    int PyString_GET_SIZE_void "PyString_GET_SIZE" (void *p)
    int PyString_CheckExact(object p)

    void Py_INCREF(object)
    void Py_DECREF(object)


cdef extern from "stdlib.h":
    unsigned long int strtoul(char *nptr, char **endptr, int base)

cdef extern from "string.h":
    char *strchr(char *s1, char c)


cdef object _split_from_path(object cache, object path):
    """get the dirblock tuple for a given path.

    :param cache: A Dictionary mapping string paths to tuples
    :param path: The path we care about.
    :return: A borrowed reference to a tuple stored in cache.
        You do not need to Py_DECREF() when you are done, unless you plan on
        using it for a while.
    """
    cdef void* value_ptr
    cdef object value

    value_ptr = PyDict_GetItem(cache, path)
    if value_ptr == NULL:
        value = path.split('/')
        cache[path] = value
    else:
        value = <object>value_ptr

    return value


cdef int _cmp_dirblock_strings(char *path1, int size1, char *path2, int size2):
    cdef char *cur1
    cdef char *cur2
    cdef char *end1
    cdef char *end2
    cdef int *cur_int1
    cdef int *cur_int2
    cdef int *end_int1
    cdef int *end_int2

    cur_int1 = <int*>path1
    cur_int2 = <int*>path2
    end_int1 = <int*>(path1 + size1 - (size1%4))
    end_int2 = <int*>(path2 + size2 - (size2%4))
    end1 = path1+size1
    end2 = path2+size2

    # Use 32-bit comparisons for the matching portion of the string.
    # Almost all CPU's are faster at loading and comparing 32-bit integers,
    # than they are at 8-bit integers.
    while cur_int1 < end_int1 and cur_int2 < end_int2:
        if cur_int1[0] != cur_int2[0]:
            break
        cur_int1 = cur_int1 + 1
        cur_int2 = cur_int2 + 1

    cur1 = <char*>cur_int1
    cur2 = <char*>cur_int2

    while cur1 < end1 and cur2 < end2:
        if cur1[0] == cur2[0]:
            # This character matches, just go to the next one
            cur1 = cur1 + 1
            cur2 = cur2 + 1
            continue
        # The current characters do not match
        if cur1[0] == c'/':
            return -1 # Reached the end of path1 segment first
        elif cur2[0] == c'/':
            return 1 # Reached the end of path2 segment first
        elif cur1[0] < cur2[0]:
            return -1
        else:
            return 1

    # We reached the end of at least one of the strings
    if cur1 < end1:
        return 1 # Not at the end of cur1, must be at the end of cur2
    if cur2 < end2:
        return -1 # At the end of cur1, but not at cur2
    # We reached the end of both strings
    return 0


def cmp_dirblock_strings(path1, path2):
    """Compare to python strings in dirblock fashion."""
    return _cmp_dirblock_strings(PyString_AsString(path1),
                                 PyString_Size(path1),
                                 PyString_AsString(path2),
                                 PyString_Size(path2))


def c_bisect_dirblock(dirblocks, dirname, lo=0, hi=None, cache=None):
    """Return the index where to insert dirname into the dirblocks.

    The return value idx is such that all directories blocks in dirblock[:idx]
    have names < dirname, and all blocks in dirblock[idx:] have names >=
    dirname.

    Optional args lo (default 0) and hi (default len(dirblocks)) bound the
    slice of a to be searched.
    """
    cdef int _lo
    cdef int _hi
    cdef int _mid
    cdef char *dirname_str
    cdef int dirname_size
    cdef char *cur_str
    cdef int cur_size
    cdef void *cur

    if hi is None:
        _hi = len(dirblocks)
    else:
        _hi = hi

    if not PyList_CheckExact(dirblocks):
        raise TypeError('you must pass a python list for dirblocks')
    _lo = lo
    if not PyString_CheckExact(dirname):
        raise TypeError('you must pass a string for dirname')
    dirname_str = PyString_AsString(dirname)
    dirname_size = PyString_Size(dirname)

    while _lo < _hi:
        _mid = (_lo+_hi)/2
        # Grab the dirname for the current dirblock
        # cur = dirblocks[_mid][0]
        cur = PyTuple_GetItem_void_void(
                PyList_GetItem_object_void(dirblocks, _mid), 0)
        cur_str = PyString_AS_STRING_void(cur)
        cur_size = PyString_GET_SIZE_void(cur)
        if _cmp_dirblock_strings(cur_str, cur_size,
                                 dirname_str, dirname_size) < 0:
            _lo = _mid+1
        else:
            _hi = _mid
    return _lo


cdef object _List_GetItem_Incref(object lst, int offset):
    """Get an item, and increment a reference to it.

    The caller must have checked that the object really is a list.
    """
    cdef object cur
    cur = PyList_GET_ITEM(lst, offset)
    Py_INCREF(cur)
    return cur


cdef object _fields_to_entry_0_parents(object fields):
    cdef object path_name_file_id_key
    cdef char *size_str
    cdef unsigned long int size
    cdef char* executable_str
    cdef object is_executable
    if not PyList_CheckExact(fields):
        raise TypeError('fields must be a list')
    path_name_file_id_key = (_List_GetItem_Incref(fields, 0),
                             _List_GetItem_Incref(fields, 1),
                             _List_GetItem_Incref(fields, 2),
                            )

    size_str = PyString_AS_STRING_void(
                PyList_GetItem_object_void(fields, 5))
    size = strtoul(size_str, NULL, 10)
    executable_str = PyString_AS_STRING_void(
                        PyList_GetItem_object_void(fields, 6))
    if executable_str[0] == c'y':
        is_executable = True
    else:
        is_executable = False
    return (path_name_file_id_key, [
        ( # Current tree
            _List_GetItem_Incref(fields, 3),# minikind
            _List_GetItem_Incref(fields, 4),# fingerprint
            size,                           # size
            is_executable,                  # executable
            _List_GetItem_Incref(fields, 7),# packed_stat or revision_id
        )])


def _c_read_dirblocks(state):
    """Read in the dirblocks for the given DirState object.

    This is tightly bound to the DirState internal representation. It should be
    thought of as a member function, which is only separated out so that we can
    re-write it in pyrex.

    :param state: A DirState object.
    :return: None
    """
    cdef int cur
    cdef int pos
    cdef int entry_size
    cdef int field_count
    cdef int num_present_parents

    state._state_file.seek(state._end_of_header)
    text = state._state_file.read()
    # TODO: check the crc checksums. crc_measured = zlib.crc32(text)

    fields = text.split('\0')
    # Remove the last blank entry
    trailing = fields.pop()
    assert trailing == ''
    # consider turning fields into a tuple.

    # skip the first field which is the trailing null from the header.
    cur = 1
    # Each line now has an extra '\n' field which is not used
    # so we just skip over it
    # entry size:
    #  3 fields for the key
    #  + number of fields per tree_data (5) * tree count
    #  + newline
    num_present_parents = state._num_present_parents()
    tree_count = 1 + num_present_parents
    entry_size = state._fields_per_entry()
    expected_field_count = entry_size * state._num_entries
    field_count = len(fields)
    # this checks our adjustment, and also catches file too short.
    assert field_count - cur == expected_field_count, \
        'field count incorrect %s != %s, entry_size=%s, '\
        'num_entries=%s fields=%r' % (
            field_count - cur, expected_field_count, entry_size,
            state._num_entries, fields)

    if num_present_parents == 0:
        # Move the iterator to the current position
        state._dirblocks = [('', []), ('', [])]
        current_block = state._dirblocks[0][1]
        current_dirname = ''
        append_entry = current_block.append
        pos = cur
        while pos < field_count:
            entry = _fields_to_entry_0_parents(fields[pos:pos+entry_size])
            pos = pos + entry_size
            dirname = entry[0][0]
            if dirname != current_dirname:
                # new block - different dirname
                current_block = []
                current_dirname = dirname
                state._dirblocks.append((current_dirname, current_block))
                append_entry = current_block.append
            append_entry(entry)
        state._split_root_dirblock_into_contents()
    elif num_present_parents == 1:
        # Bind external functions to local names
        _int = int
        # We access all fields in order, so we can just iterate over
        # them. Grab an straight iterator over the fields. (We use an
        # iterator because we don't want to do a lot of additions, nor
        # do we want to do a lot of slicing)
        next = iter(fields).next
        # Move the iterator to the current position
        for x in xrange(cur):
            next()
        # The two blocks here are deliberate: the root block and the
        # contents-of-root block.
        state._dirblocks = [('', []), ('', [])]
        current_block = state._dirblocks[0][1]
        current_dirname = ''
        append_entry = current_block.append
        for count in xrange(state._num_entries):
            dirname = next()
            name = next()
            file_id = next()
            if dirname != current_dirname:
                # new block - different dirname
                current_block = []
                current_dirname = dirname
                state._dirblocks.append((current_dirname, current_block))
                append_entry = current_block.append
            # we know current_dirname == dirname, so re-use it to avoid
            # creating new strings
            entry = ((current_dirname, name, file_id),
                     [(# Current Tree
                         next(),                # minikind
                         next(),                # fingerprint
                         _int(next()),          # size
                         next() == 'y',         # executable
                         next(),                # packed_stat or revision_id
                     ),
                     ( # Parent 1
                         next(),                # minikind
                         next(),                # fingerprint
                         _int(next()),          # size
                         next() == 'y',         # executable
                         next(),                # packed_stat or revision_id
                     ),
                     ])
            trailing = next()
            assert trailing == '\n'
            # append the entry to the current block
            append_entry(entry)
        state._split_root_dirblock_into_contents()
    else:
        fields_to_entry = state._get_fields_to_entry()
        entries = []
        entries_append = entries.append
        pos = cur
        while pos < field_count:
            entries_append(fields_to_entry(fields[pos:pos+entry_size]))
            pos = pos + entry_size
        state._entries_to_current_state(entries)
    # To convert from format 2  => format 3
    # state._dirblocks = sorted(state._dirblocks,
    #                          key=lambda blk:blk[0].split('/'))
    # To convert from format 3 => format 2
    # state._dirblocks = sorted(state._dirblocks)
    state._dirblock_state = DirState.IN_MEMORY_UNMODIFIED
