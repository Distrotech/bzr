# Copyright (C) 2005, 2006, 2007, 2008, 2009 Canonical Ltd
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


"""Locking using OS file locks or file existence.

Note: This method of locking is generally deprecated in favour of LockDir, but
is used to lock local WorkingTrees, and by some old formats.  It's accessed
through Transport.lock_read(), etc.

This module causes two methods, lock() and unlock() to be defined in
any way that works on the current platform.

It is not specified whether these locks are reentrant (i.e. can be
taken repeatedly by a single process) or whether they exclude
different threads in a single process.  That reentrancy is provided by
LockableFiles.

This defines two classes: ReadLock and WriteLock, which can be
implemented in different ways on different platforms.  Both have an
unlock() method.
"""

import errno
import os
import sys

from bzrlib import (
    errors,
    osutils,
    trace,
    )
from bzrlib.hooks import HookPoint, Hooks


class LockHooks(Hooks):

    def __init__(self):
        Hooks.__init__(self)
        self.create_hook(HookPoint('lock_acquired',
            "Called with a bzrlib.lock.LockResult when a physical lock is "
            "acquired.", (1, 8), None))
        self.create_hook(HookPoint('lock_released',
            "Called with a bzrlib.lock.LockResult when a physical lock is "
            "released.", (1, 8), None))
        self.create_hook(HookPoint('lock_broken',
            "Called with a bzrlib.lock.LockResult when a physical lock is "
            "broken.", (1, 15), None))


class Lock(object):
    """Base class for locks.

    :cvar hooks: Hook dictionary for operations on locks.
    """

    hooks = LockHooks()


class LockResult(object):
    """Result of an operation on a lock; passed to a hook"""

    def __init__(self, lock_url, details=None):
        """Create a lock result for lock with optional details about the lock."""
        self.lock_url = lock_url
        self.details = details

    def __eq__(self, other):
        return self.lock_url == other.lock_url and self.details == other.details

    def __repr__(self):
        return '%s(%s%s)' % (self.__class__.__name__,
                             self.lock_url, self.details)


try:
    import fcntl
    have_fcntl = True
except ImportError:
    have_fcntl = False

have_pywin32 = False
have_ctypes_win32 = False
if sys.platform == 'win32':
    import msvcrt
    try:
        import win32file, pywintypes, winerror
        have_pywin32 = True
    except ImportError:
        pass

    try:
        import ctypes
        have_ctypes_win32 = True
    except ImportError:
        pass


class _OSLock(object):

    def __init__(self):
        self.f = None
        self.filename = None

    def _open(self, filename, filemode):
        self.filename = osutils.realpath(filename)
        try:
            self.f = open(self.filename, filemode)
            return self.f
        except IOError, e:
            if e.errno in (errno.EACCES, errno.EPERM):
                raise errors.LockFailed(self.filename, str(e))
            if e.errno != errno.ENOENT:
                raise

            # maybe this is an old branch (before may 2005)
            trace.mutter("trying to create missing lock %r", self.filename)

            self.f = open(self.filename, 'wb+')
            return self.f

    def _clear_f(self):
        """Clear the self.f attribute cleanly."""
        if self.f:
            self.f.close()
            self.f = None

    def __del__(self):
        if self.f:
            from warnings import warn
            warn("lock on %r not released" % self.f)
            self.unlock()

    def unlock(self):
        raise NotImplementedError()


_lock_classes = []


if have_fcntl:

    class _fcntl_FileLock(_OSLock):

        def _unlock(self):
            fcntl.lockf(self.f, fcntl.LOCK_UN)
            self._clear_f()


    class _fcntl_WriteLock(_fcntl_FileLock):

        _open_locks = set()

        def __init__(self, filename):
            super(_fcntl_WriteLock, self).__init__()
            # Check we can grab a lock before we actually open the file.
            self.filename = osutils.realpath(filename)
            if (self.filename in _fcntl_WriteLock._open_locks
                or self.filename in _fcntl_ReadLock._open_locks):
                self._clear_f()
                raise errors.LockContention(self.filename)

            self._open(self.filename, 'rb+')
            # reserve a slot for this lock - even if the lockf call fails,
            # at thisi point unlock() will be called, because self.f is set.
            # TODO: make this fully threadsafe, if we decide we care.
            _fcntl_WriteLock._open_locks.add(self.filename)
            try:
                # LOCK_NB will cause IOError to be raised if we can't grab a
                # lock right away.
                fcntl.lockf(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except IOError, e:
                if e.errno in (errno.EAGAIN, errno.EACCES):
                    # We couldn't grab the lock
                    self.unlock()
                # we should be more precise about whats a locking
                # error and whats a random-other error
                raise errors.LockContention(self.filename, e)

        def unlock(self):
            _fcntl_WriteLock._open_locks.remove(self.filename)
            self._unlock()


    class _fcntl_ReadLock(_fcntl_FileLock):

        _open_locks = {}

        def __init__(self, filename):
            super(_fcntl_ReadLock, self).__init__()
            self.filename = osutils.realpath(filename)
            if self.filename in _fcntl_WriteLock._open_locks:
                raise errors.LockContention(self.filename)
            _fcntl_ReadLock._open_locks.setdefault(self.filename, 0)
            _fcntl_ReadLock._open_locks[self.filename] += 1
            self._open(filename, 'rb')
            try:
                # LOCK_NB will cause IOError to be raised if we can't grab a
                # lock right away.
                fcntl.lockf(self.f, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except IOError, e:
                # we should be more precise about whats a locking
                # error and whats a random-other error
                raise errors.LockContention(self.filename, e)

        def unlock(self):
            count = _fcntl_ReadLock._open_locks[self.filename]
            if count == 1:
                del _fcntl_ReadLock._open_locks[self.filename]
            else:
                _fcntl_ReadLock._open_locks[self.filename] = count - 1
            self._unlock()

        def temporary_write_lock(self):
            """Try to grab a write lock on the file.

            On platforms that support it, this will upgrade to a write lock
            without unlocking the file.
            Otherwise, this will release the read lock, and try to acquire a
            write lock.

            :return: A token which can be used to switch back to a read lock.
            """
            if self.filename in _fcntl_WriteLock._open_locks:
                raise AssertionError('file already locked: %r'
                    % (self.filename,))
            try:
                wlock = _fcntl_TemporaryWriteLock(self)
            except errors.LockError:
                # We didn't unlock, so we can just return 'self'
                return False, self
            return True, wlock


    class _fcntl_TemporaryWriteLock(_OSLock):
        """A token used when grabbing a temporary_write_lock.

        Call restore_read_lock() when you are done with the write lock.
        """

        def __init__(self, read_lock):
            super(_fcntl_TemporaryWriteLock, self).__init__()
            self._read_lock = read_lock
            self.filename = read_lock.filename

            count = _fcntl_ReadLock._open_locks[self.filename]
            if count > 1:
                # Something else also has a read-lock, so we cannot grab a
                # write lock.
                raise errors.LockContention(self.filename)

            if self.filename in _fcntl_WriteLock._open_locks:
                raise AssertionError('file already locked: %r'
                    % (self.filename,))

            # See if we can open the file for writing. Another process might
            # have a read lock. We don't use self._open() because we don't want
            # to create the file if it exists. That would have already been
            # done by _fcntl_ReadLock
            try:
                new_f = open(self.filename, 'rb+')
            except IOError, e:
                if e.errno in (errno.EACCES, errno.EPERM):
                    raise errors.LockFailed(self.filename, str(e))
                raise
            try:
                # LOCK_NB will cause IOError to be raised if we can't grab a
                # lock right away.
                fcntl.lockf(new_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except IOError, e:
                # TODO: Raise a more specific error based on the type of error
                raise errors.LockContention(self.filename, e)
            _fcntl_WriteLock._open_locks.add(self.filename)

            self.f = new_f

        def restore_read_lock(self):
            """Restore the original ReadLock."""
            # For fcntl, since we never released the read lock, just release the
            # write lock, and return the original lock.
            fcntl.lockf(self.f, fcntl.LOCK_UN)
            self._clear_f()
            _fcntl_WriteLock._open_locks.remove(self.filename)
            # Avoid reference cycles
            read_lock = self._read_lock
            self._read_lock = None
            return read_lock


    _lock_classes.append(('fcntl', _fcntl_WriteLock, _fcntl_ReadLock))


if have_pywin32 and sys.platform == 'win32':
    if os.path.supports_unicode_filenames:
        # for Windows NT/2K/XP/etc
        win32file_CreateFile = win32file.CreateFileW
    else:
        # for Windows 98
        win32file_CreateFile = win32file.CreateFile

    class _w32c_FileLock(_OSLock):

        def _open(self, filename, access, share, cflags, pymode):
            self.filename = osutils.realpath(filename)
            try:
                self._handle = win32file_CreateFile(filename, access, share,
                    None, win32file.OPEN_ALWAYS,
                    win32file.FILE_ATTRIBUTE_NORMAL, None)
            except pywintypes.error, e:
                if e.args[0] == winerror.ERROR_ACCESS_DENIED:
                    raise errors.LockFailed(filename, e)
                if e.args[0] == winerror.ERROR_SHARING_VIOLATION:
                    raise errors.LockContention(filename, e)
                raise
            fd = win32file._open_osfhandle(self._handle, cflags)
            self.f = os.fdopen(fd, pymode)
            return self.f

        def unlock(self):
            self._clear_f()
            self._handle = None


    class _w32c_ReadLock(_w32c_FileLock):
        def __init__(self, filename):
            super(_w32c_ReadLock, self).__init__()
            self._open(filename, win32file.GENERIC_READ,
                win32file.FILE_SHARE_READ, os.O_RDONLY, "rb")

        def temporary_write_lock(self):
            """Try to grab a write lock on the file.

            On platforms that support it, this will upgrade to a write lock
            without unlocking the file.
            Otherwise, this will release the read lock, and try to acquire a
            write lock.

            :return: A token which can be used to switch back to a read lock.
            """
            # I can't find a way to upgrade a read lock to a write lock without
            # unlocking first. So here, we do just that.
            self.unlock()
            try:
                wlock = _w32c_WriteLock(self.filename)
            except errors.LockError:
                return False, _w32c_ReadLock(self.filename)
            return True, wlock


    class _w32c_WriteLock(_w32c_FileLock):
        def __init__(self, filename):
            super(_w32c_WriteLock, self).__init__()
            self._open(filename,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE, 0,
                os.O_RDWR, "rb+")

        def restore_read_lock(self):
            """Restore the original ReadLock."""
            # For win32 we had to completely let go of the original lock, so we
            # just unlock and create a new read lock.
            self.unlock()
            return _w32c_ReadLock(self.filename)


    _lock_classes.append(('pywin32', _w32c_WriteLock, _w32c_ReadLock))


if have_ctypes_win32:
    from ctypes.wintypes import DWORD, LPCSTR, LPCWSTR
    LPSECURITY_ATTRIBUTES = ctypes.c_void_p # used as NULL no need to declare
    HANDLE = ctypes.c_int # rather than unsigned as in ctypes.wintypes
    if os.path.supports_unicode_filenames:
        _function_name = "CreateFileW"
        LPTSTR = LPCWSTR
    else:
        _function_name = "CreateFileA"
        class LPTSTR(LPCSTR):
            def __new__(cls, obj):
                return LPCSTR.__new__(cls, obj.encode("mbcs"))

    # CreateFile <http://msdn.microsoft.com/en-us/library/aa363858.aspx>
    _CreateFile = ctypes.WINFUNCTYPE(
            HANDLE,                # return value
            LPTSTR,                # lpFileName
            DWORD,                 # dwDesiredAccess
            DWORD,                 # dwShareMode
            LPSECURITY_ATTRIBUTES, # lpSecurityAttributes
            DWORD,                 # dwCreationDisposition
            DWORD,                 # dwFlagsAndAttributes
            HANDLE                 # hTemplateFile
        )((_function_name, ctypes.windll.kernel32))

    INVALID_HANDLE_VALUE = -1

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 1
    OPEN_ALWAYS = 4
    FILE_ATTRIBUTE_NORMAL = 128

    ERROR_ACCESS_DENIED = 5
    ERROR_SHARING_VIOLATION = 32

    class _ctypes_FileLock(_OSLock):

        def _open(self, filename, access, share, cflags, pymode):
            self.filename = osutils.realpath(filename)
            handle = _CreateFile(filename, access, share, None, OPEN_ALWAYS,
                FILE_ATTRIBUTE_NORMAL, 0)
            if handle in (INVALID_HANDLE_VALUE, 0):
                e = ctypes.WinError()
                if e.args[0] == ERROR_ACCESS_DENIED:
                    raise errors.LockFailed(filename, e)
                if e.args[0] == ERROR_SHARING_VIOLATION:
                    raise errors.LockContention(filename, e)
                raise e
            fd = msvcrt.open_osfhandle(handle, cflags)
            self.f = os.fdopen(fd, pymode)
            return self.f

        def unlock(self):
            self._clear_f()


    class _ctypes_ReadLock(_ctypes_FileLock):
        def __init__(self, filename):
            super(_ctypes_ReadLock, self).__init__()
            self._open(filename, GENERIC_READ, FILE_SHARE_READ, os.O_RDONLY,
                "rb")

        def temporary_write_lock(self):
            """Try to grab a write lock on the file.

            On platforms that support it, this will upgrade to a write lock
            without unlocking the file.
            Otherwise, this will release the read lock, and try to acquire a
            write lock.

            :return: A token which can be used to switch back to a read lock.
            """
            # I can't find a way to upgrade a read lock to a write lock without
            # unlocking first. So here, we do just that.
            self.unlock()
            try:
                wlock = _ctypes_WriteLock(self.filename)
            except errors.LockError:
                return False, _ctypes_ReadLock(self.filename)
            return True, wlock

    class _ctypes_WriteLock(_ctypes_FileLock):
        def __init__(self, filename):
            super(_ctypes_WriteLock, self).__init__()
            self._open(filename, GENERIC_READ | GENERIC_WRITE, 0, os.O_RDWR,
                "rb+")

        def restore_read_lock(self):
            """Restore the original ReadLock."""
            # For win32 we had to completely let go of the original lock, so we
            # just unlock and create a new read lock.
            self.unlock()
            return _ctypes_ReadLock(self.filename)


    _lock_classes.append(('ctypes', _ctypes_WriteLock, _ctypes_ReadLock))


if len(_lock_classes) == 0:
    raise NotImplementedError(
        "We must have one of fcntl, pywin32, or ctypes available"
        " to support OS locking."
        )


# We default to using the first available lock class.
_lock_type, WriteLock, ReadLock = _lock_classes[0]

