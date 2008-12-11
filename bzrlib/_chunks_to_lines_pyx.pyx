# Copyright (C) 2008 Canonical Ltd
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
#

"""Pyrex extensions for converting chunks to lines."""

#python2.4 support
cdef extern from "python-compat.h":
    pass

cdef extern from "stdlib.h":
    ctypedef unsigned size_t

cdef extern from "Python.h":
    ctypedef int Py_ssize_t # Required for older pyrex versions
    ctypedef struct PyObject:
        pass
    int PyList_Append(object lst, object item) except -1

    char *PyString_AsString(object p) except NULL
    int PyString_AsStringAndSize(object s, char **buf, Py_ssize_t *len) except -1

cdef extern from "string.h":
    void *memchr(void *s, int c, size_t n)


def chunks_to_lines(chunks):
    cdef char *c_str
    cdef char *newline
    cdef char *c_last
    cdef Py_ssize_t the_len
    cdef Py_ssize_t chunks_len
    cdef Py_ssize_t cur

    # Check to see if the chunks are already lines
    chunks_len = len(chunks)
    if chunks_len == 0:
        return chunks
    cur = 0
    for chunk in chunks:
        cur += 1
        PyString_AsStringAndSize(chunk, &c_str, &the_len)
        if the_len == 0:
            break
        c_last = c_str + the_len - 1
        newline = <char *>memchr(c_str, c'\n', the_len)
        if newline != c_last and not (newline == NULL and cur == chunks_len):
            break
    else:
        return chunks

    from bzrlib import osutils
    return osutils.split_lines(''.join(chunks))
