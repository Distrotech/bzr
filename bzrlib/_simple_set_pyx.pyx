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

"""Definition of a class that is similar to Set with some small changes."""

cdef extern from "Python.h":
    ctypedef unsigned long size_t
    ctypedef long (*hashfunc)(PyObject*)
    ctypedef PyObject *(*richcmpfunc)(PyObject *, PyObject *, int)
    ctypedef int (*visitproc)(PyObject *, void *)
    ctypedef int (*traverseproc)(PyObject *, visitproc, void *)
    int Py_EQ
    PyObject *Py_True
    PyObject *Py_NotImplemented
    void Py_INCREF(PyObject *)
    void Py_DECREF(PyObject *)
    ctypedef struct PyTypeObject:
        hashfunc tp_hash
        richcmpfunc tp_richcompare
        traverseproc tp_traverse

    PyTypeObject *Py_TYPE(PyObject *)
        
    void *PyMem_Malloc(size_t nbytes)
    void PyMem_Free(void *)
    void memset(void *, int, size_t)


cdef object _dummy_obj
cdef PyObject *_dummy
_dummy_obj = object()
_dummy = <PyObject *>_dummy_obj


cdef int _is_equal(PyObject *this, long this_hash, PyObject *other):
    cdef long other_hash
    cdef PyObject *res

    if this == other:
        return 1
    other_hash = Py_TYPE(other).tp_hash(other)
    if other_hash != this_hash:
        return 0
    res = Py_TYPE(this).tp_richcompare(this, other, Py_EQ)
    if res == Py_True:
        Py_DECREF(res)
        return 1
    if res == Py_NotImplemented:
        Py_DECREF(res)
        res = Py_TYPE(other).tp_richcompare(other, this, Py_EQ)
    if res == Py_True:
        Py_DECREF(res)
        return 1
    Py_DECREF(res)
    return 0


cdef public api class SimpleSet [object SimpleSetObject, type SimpleSet_Type]:
    """This class can be used to track canonical forms for objects.

    It is similar in function to the interned dictionary that is used by
    strings. However:

      1) It assumes that hash(obj) is cheap, so does not need to inline a copy
         of it
      2) It only stores one reference to the object, rather than 2 (key vs
         key:value)

    As such, it uses 1/3rd the amount of memory to store a pointer to the
    interned object.
    """
    # Attributes are defined in the .pxd file
    DEF DEFAULT_SIZE=1024
    DEF PERTURB_SHIFT=5

    def __init__(self):
        cdef Py_ssize_t size, n_bytes

        size = DEFAULT_SIZE
        self._mask = size - 1
        self._used = 0
        self._fill = 0
        n_bytes = sizeof(PyObject*) * size;
        self._table = <PyObject **>PyMem_Malloc(n_bytes)
        if self._table == NULL:
            raise MemoryError()
        memset(self._table, 0, n_bytes)

    def __dealloc__(self):
        if self._table != NULL:
            PyMem_Free(self._table)
            self._table = NULL

    property used:
        def __get__(self):
            return self._used

    property fill:
        def __get__(self):
            return self._fill

    property mask:
        def __get__(self):
            return self._mask

    def _memory_size(self):
        """Return the number of bytes of memory consumed by this class."""
        return sizeof(self) + (sizeof(PyObject*)*(self._mask + 1))

    def __len__(self):
        return self._used

    def _test_lookup(self, key):
        cdef PyObject **slot

        slot = _lookup(self, key)
        if slot[0] == NULL:
            res = '<null>'
        elif slot[0] == _dummy:
            res = '<dummy>'
        else:
            res = <object>slot[0]
        return <int>(slot - self._table), res

    def __contains__(self, key):
        """Is key present in this SimpleSet."""
        cdef PyObject **slot

        slot = _lookup(self, key)
        if slot[0] == NULL or slot[0] == _dummy:
            return False
        return True

    cdef PyObject *_get(self, object key) except? NULL:
        """Return the object (or nothing) define at the given location."""
        cdef PyObject **slot

        slot = _lookup(self, key)
        if slot[0] == NULL or slot[0] == _dummy:
            return NULL
        return slot[0]

    def __getitem__(self, key):
        """Return a stored item that is equivalent to key."""
        cdef PyObject *py_val

        py_val = self._get(key)
        if py_val == NULL:
            raise KeyError("Key %s is not present" % key)
        val = <object>(py_val)
        return val

    cdef int _insert_clean(self, PyObject *key) except -1:
        """Insert a key into self.table.

        This is only meant to be used during times like '_resize',
        as it makes a lot of assuptions about keys not already being present,
        and there being no dummy entries.
        """
        cdef size_t i, perturb, mask
        cdef long the_hash
        cdef PyObject **table, **entry

        mask = self._mask
        table = self._table

        the_hash = Py_TYPE(key).tp_hash(key)
        i = the_hash & mask
        entry = &table[i]
        perturb = the_hash
        # Because we know that we made all items unique before, we can just
        # iterate as long as the target location is not empty, we don't have to
        # do any comparison, etc.
        while entry[0] != NULL:
            i = (i << 2) + i + perturb + 1
            entry = &table[i & mask]
            perturb >>= PERTURB_SHIFT
        entry[0] = key
        self._fill += 1
        self._used += 1

    def _py_resize(self, min_used):
        """Do not use this directly, it is only exposed for testing."""
        return self._resize(min_used)

    cdef Py_ssize_t _resize(self, Py_ssize_t min_used) except -1:
        """Resize the internal table.

        The final table will be big enough to hold at least min_used entries.
        We will copy the data from the existing table over, leaving out dummy
        entries.

        :return: The new size of the internal table
        """
        cdef Py_ssize_t new_size, n_bytes, remaining
        cdef PyObject **new_table, **old_table, **entry

        new_size = DEFAULT_SIZE
        while new_size <= min_used and new_size > 0:
            new_size = new_size << 1
        # We rolled over our signed size field
        if new_size <= 0:
            raise MemoryError()
        # Even if min_used == self._mask + 1, and we aren't changing the actual
        # size, we will still run the algorithm so that dummy entries are
        # removed
        # TODO: Test this
        # if new_size < self._used:
        #     raise RuntimeError('cannot shrink SimpleSet to something'
        #                        ' smaller than the number of used slots.')
        n_bytes = sizeof(PyObject*) * new_size;
        new_table = <PyObject **>PyMem_Malloc(n_bytes)
        if new_table == NULL:
            raise MemoryError()

        old_table = self._table
        self._table = new_table
        memset(self._table, 0, n_bytes)
        self._mask = new_size - 1
        self._used = 0
        remaining = self._fill
        self._fill = 0

        # Moving everything to the other table is refcount neutral, so we don't
        # worry about it.
        entry = old_table
        while remaining > 0:
            if entry[0] == NULL: # unused slot
                pass 
            elif entry[0] == _dummy: # dummy slot
                remaining -= 1
            else: # active slot
                remaining -= 1
                self._insert_clean(entry[0])
            entry += 1
        PyMem_Free(old_table)
        return new_size

    def add(self, key):
        """Similar to set.add(), start tracking this key.
        
        There is one small difference, which is that we return the object that
        is stored at the given location. (which is closer to the
        dict.setdefault() functionality.)
        """
        return self._add(key)

    cdef object _add(self, key):
        cdef PyObject **slot, *py_key
        cdef int added

        added = 0
        # We need at least one empty slot
        assert self._used < self._mask
        slot = _lookup(self, key)
        py_key = <PyObject *>key
        if (slot[0] == NULL):
            Py_INCREF(py_key)
            self._fill += 1
            self._used += 1
            slot[0] = py_key
            added = 1
        elif (slot[0] == _dummy):
            Py_INCREF(py_key)
            self._used += 1
            slot[0] = py_key
            added = 1
        # No else: clause. If _lookup returns a pointer to
        # a live object, then we already have a value at this location.
        retval = <object>(slot[0])
        # PySet and PyDict use a 2-3rds full algorithm, we'll follow suit
        if added and (self._fill * 3) >= ((self._mask + 1) * 2):
            # However, we always work for a load factor of 2:1
            self._resize(self._used * 2)
        # Even if we resized and ended up moving retval into a different slot,
        # it is still the value that is held at the slot equivalent to 'key',
        # so we can still return it
        return retval

    def discard(self, key):
        """Remove key from the dict, whether it exists or not.

        :return: 0 if the item did not exist, 1 if it did
        """
        return self._discard(key)

    cdef int _discard(self, key) except -1:
        cdef PyObject **slot, *py_key

        slot = _lookup(self, key)
        if slot[0] == NULL or slot[0] == _dummy:
            return 0
        self._used -= 1
        Py_DECREF(slot[0])
        slot[0] = _dummy
        # PySet uses the heuristic: If more than 1/5 are dummies, then resize
        #                           them away
        #   if ((so->_fill - so->_used) * 5 < so->mask)
        # However, we are planning on using this as an interning structure, in
        # which we will be putting a lot of objects. And we expect that large
        # groups of them are going to have the same lifetime.
        # Dummy entries hurt a little bit because they cause the lookup to keep
        # searching, but resizing is also rather expensive
        # For now, we'll just use their algorithm, but we may want to revisit
        # it
        if ((self._fill - self._used) * 5 > self._mask):
            self._resize(self._used * 2)
        return 1

    def __delitem__(self, key):
        """Remove the given item from the dict.

        Raise a KeyError if the key was not present.
        """
        cdef int exists
        exists = self._discard(key)
        if not exists:
            raise KeyError('Key %s not present' % (key,))

    def __iter__(self):
        return _SimpleSet_iterator(self)


cdef class _SimpleSet_iterator:
    """Iterator over the SimpleSet structure."""

    cdef Py_ssize_t pos
    cdef SimpleSet set
    cdef Py_ssize_t _used # track if things have been mutated while iterating
    cdef Py_ssize_t len # number of entries left

    def __init__(self, obj):
        self.set = obj
        self.pos = 0
        self._used = self.set._used
        self.len = self.set._used

    def __iter__(self):
        return self

    def __next__(self):
        cdef Py_ssize_t mask, i
        cdef PyObject **table

        if self.set is None:
            raise StopIteration
        if self.set._used != self._used:
            # Force this exception to continue to be raised
            self._used = -1
            raise RuntimeError("Set size changed during iteration")
        i = self.pos
        mask = self.set._mask
        table = self.set._table
        assert i >= 0
        while i <= mask and (table[i] == NULL or table[i] == _dummy):
            i += 1
        self.pos = i + 1
        if i > mask:
            # we walked to the end
            self.set = None
            raise StopIteration
        # We must have found one
        key = <object>(table[i])
        self.len -= 1
        return key

    def __length_hint__(self):
        if self.set is not None and self._used == self.set._used:
            return self.len
        return 0
    


cdef api SimpleSet SimpleSet_New():
    """Create a new SimpleSet object."""
    return SimpleSet()


cdef SimpleSet _check_self(object self):
    """Check that the parameter is not None.

    Pyrex/Cython will do type checking, but only to ensure that an object is
    either the right type or None. You can say "object foo not None" for pure
    python functions, but not for C functions.
    So this is just a helper for all the apis that need to do the check.
    """
    cdef SimpleSet true_self
    if self is None:
        raise TypeError('self must not be None')
    true_self = self
    return true_self


cdef PyObject **_lookup(SimpleSet self, object key) except NULL:
    """Find the slot where 'key' would fit.

    This is the same as a dicts 'lookup' function.

    :param key: An object we are looking up
    :param hash: The hash for key
    :return: The location in self.table where key should be put
        should never be NULL, but may reference a NULL (PyObject*)
    """
    # This is the heart of most functions, which is why it is pulled out as an
    # cdef inline function.
    cdef size_t i, perturb
    cdef Py_ssize_t mask
    cdef long key_hash
    cdef long this_hash
    cdef PyObject **table, **cur, **free_slot, *py_key

    key_hash = hash(key)
    mask = self._mask
    table = self._table
    i = key_hash & mask
    cur = &table[i]
    py_key = <PyObject *>key
    if cur[0] == NULL:
        # Found a blank spot, or found the exact key
        return cur
    if cur[0] == py_key:
        return cur
    if cur[0] == _dummy:
        free_slot = cur
    else:
        if _is_equal(py_key, key_hash, cur[0]):
            # Both py_key and cur[0] belong in this slot, return it
            return cur
        free_slot = NULL
    # size_t is unsigned, hash is signed...
    perturb = key_hash
    while True:
        i = (i << 2) + i + perturb + 1
        cur = &table[i & mask]
        if cur[0] == NULL: # Found an empty spot
            if free_slot: # Did we find a _dummy earlier?
                return free_slot
            else:
                return cur
        if (cur[0] == py_key # exact match
            or _is_equal(py_key, key_hash, cur[0])): # Equivalent match
            return cur
        if (cur[0] == _dummy and free_slot == NULL):
            free_slot = cur
        perturb >>= PERTURB_SHIFT
    raise AssertionError('should never get here')


cdef api PyObject **_SimpleSet_Lookup(object self, object key) except NULL:
    """Find the slot where 'key' would fit.

    This is the same as a dicts 'lookup' function. This is a private
    api because mutating what you get without maintaing the other invariants
    is a 'bad thing'.

    :param key: An object we are looking up
    :param hash: The hash for key
    :return: The location in self._table where key should be put
        should never be NULL, but may reference a NULL (PyObject*)
    """
    return _lookup(_check_self(self), key)


cdef api object SimpleSet_Add(object self, object key):
    """Add a key to the SimpleSet (set).

    :param self: The SimpleSet to add the key to.
    :param key: The key to be added. If the key is already present,
        self will not be modified
    :return: The current key stored at the location defined by 'key'.
        This may be the same object, or it may be an equivalent object.
        (consider dict.setdefault(key, key))
    """
    return _check_self(self)._add(key)
    

cdef api int SimpleSet_Contains(object self, object key) except -1:
    """Is key present in self?"""
    return (key in _check_self(self))


cdef api int SimpleSet_Discard(object self, object key) except -1:
    """Remove the object referenced at location 'key'.

    :param self: The SimpleSet being modified
    :param key: The key we are checking on
    :return: 1 if there was an object present, 0 if there was not, and -1 on
        error.
    """
    return _check_self(self)._discard(key)


cdef api PyObject *SimpleSet_Get(SimpleSet self, object key) except? NULL:
    """Get a pointer to the object present at location 'key'.

    This returns an object which is equal to key which was previously added to
    self. This returns a borrowed reference, as it may also return NULL if no
    value is present at that location.

    :param key: The value we are looking for
    :return: The object present at that location
    """
    return _check_self(self)._get(key)


cdef api Py_ssize_t SimpleSet_Size(object self) except -1:
    """Get the number of active entries in 'self'"""
    return _check_self(self)._used


# TODO: this should probably have direct tests, since it isn't used by __iter__
cdef api int SimpleSet_Next(object self, Py_ssize_t *pos, PyObject **key):
    """Walk over items in a SimpleSet.

    :param pos: should be initialized to 0 by the caller, and will be updated
        by this function
    :param key: Will return a borrowed reference to key
    :return: 0 if nothing left, 1 if we are returning a new value
    """
    cdef Py_ssize_t i, mask
    cdef SimpleSet true_self
    cdef PyObject **table
    true_self = _check_self(self)
    i = pos[0]
    if (i < 0):
        return 0
    mask = true_self._mask
    table= true_self._table
    while (i <= mask and (table[i] == NULL or table[i] == _dummy)):
        i += 1
    pos[0] = i + 1
    if (i > mask):
        return 0 # All done
    if (key != NULL):
        key[0] = table[i]
    return 1


cdef int SimpleSet_traverse(SimpleSet self, visitproc visit, void *arg):
    """This is an implementation of 'tp_traverse' that hits the whole table.

    Cython/Pyrex don't seem to let you define a tp_traverse, and they only
    define one for you if you have an 'object' attribute. Since they don't
    support C arrays of objects, we access the PyObject * directly.
    """
    cdef Py_ssize_t pos
    cdef PyObject *next_key
    cdef int ret

    pos = 0
    while SimpleSet_Next(self, &pos, &next_key):
        ret = visit(next_key, arg)
        if ret:
            return ret

    return 0;

# It is a little bit ugly to do this, but it works, and means that Meliae can
# dump the total memory consumed by all child objects.
(<PyTypeObject *>SimpleSet).tp_traverse = <traverseproc>SimpleSet_traverse
