# Copyright (C) 2010 Canonical Ltd.
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
#
# Author: Mattias Eriksson

"""Implementation of Transport over gio.

Written by Mattias Eriksson <snaggen@acc.umu.se> based on the ftp transport.

It provides the gio+XXX:// protocols where XXX is any of the protocols
supported by gio.
"""

from cStringIO import StringIO
import getpass
import os
import random
import socket
import stat

import time
import gio
import gtk
import sys
import getpass
import urlparse

from bzrlib import (
    config,
    errors,
    osutils,
    urlutils,
    )
from bzrlib.trace import mutter, warning
from bzrlib.transport import (
    FileStream,
    ConnectedTransport,
    _file_streams,
    Server,
    )


class GioFileStream(FileStream):
    """A file stream object returned by open_write_stream.

    This version uses GIO to perform writes.
    """

    def __init__(self, transport, relpath):
        FileStream.__init__(self, transport, relpath)
        self.gio_file = transport._get_GIO(relpath)
        self.stream = self.gio_file.create()

    def _close(self):
        self.stream.close()

    def write(self, bytes):
        try:
            #Using pump_string_file seems to make things crash
            osutils.pumpfile(StringIO(bytes), self.stream)
        except gio.Error, e:
            #self.transport._translate_gio_error(e,self.relpath)
            raise errors.BzrError(str(e))


class GioStatResult(object):

    def __init__(self, f):
        info = f.query_info('standard::size,standard::type')
        self.st_size = info.get_size()
        #mutter("stat size is %d bytes" % self.st_size)
        type = info.get_file_type()
        if (type == gio.FILE_TYPE_REGULAR):
            self.st_mode = stat.S_IFREG
        elif type == gio.FILE_TYPE_DIRECTORY:
            self.st_mode = stat.S_IFDIR


class GioTransport(ConnectedTransport):
    """This is the transport agent for gio+XXX:// access."""

    def __init__(self, base, _from_transport=None):
        self.mounted = 0
        """Set the base path where files will be stored."""
        if not base.startswith('gio+'):
            raise ValueError(base)

        (scheme, user, password, host, port, path) = urlutils.parse_url(base[len('gio+'):])
        #Seems it is not possible to list supported backends for GIO
        #so a hardcoded list it is then.
        gio_backends = [ 'dav', 'ftp', 'obex', 'sftp', 'ssh', 'smb' ]
        if scheme not in gio_backends:
            raise errors.InvalidURL(base, extra="GIO support is only available for " + ', '.join(gio_backends))

        #Remove the username and password from the url we send to GIO
        netloc = host
        if port:
            netloc = "%s:%s" % (host, port)
        u = (scheme, netloc, path, '', '', '')
        self.url = urlparse.urlunparse(u)

        # And finally initialize super
        super(GioTransport, self).__init__(base,
            _from_transport=_from_transport)

    def _relpath_to_url(self, relpath):
        full_url = urlutils.join(self.url, relpath)
        return full_url

    def _get_GIO(self, relpath):
        """Return the ftplib.GIO instance for this object."""
        # Ensures that a connection is established
        connection = self._get_connection()
        if connection is None:
            # First connection ever
            connection, credentials = self._create_connection()
            self._set_connection(connection, credentials)
        fileurl = self._relpath_to_url(relpath)
        file = gio.File(fileurl)
        return file

    def _ask_password_cb(self, op, message, default_user, default_domain, flags):
        #really use bzrlib.auth get_password for this
        #or possibly better gnome-keyring?
        print message
        if flags & gio.ASK_PASSWORD_NEED_USERNAME:
            print "Username: "
            user = sys.stdin.readline()
            if user[-1] == '\n':
                user = user[:-1]
            op.set_username(user)
        if flags & gio.ASK_PASSWORD_NEED_DOMAIN:
            print "Domain: "
            domain = sys.stdin.readline()
            if domain[-1] == '\n':
                domain = domain[:-1]
            op.set_domain(domain)
        if flags & gio.ASK_PASSWORD_NEED_PASSWORD:
            print "Password: "
            isatty = getattr(sys.stdin, 'isatty', None)
            if isatty is not None and isatty():
                # getpass() ensure the password is not echoed and other
                # cross-platform niceties
                password = getpass.getpass('')
            else:
                # echo doesn't make sense without a terminal
                password = sys.stdin.readline()
                if not password:
                    password = None
                elif password[-1] == '\n':
                    password = password[:-1]
            op.set_password(password)
        op.reply(gio.MOUNT_OPERATION_HANDLED)

    def _mount_done_cb(self, obj, res):
        try:
            obj.mount_enclosing_volume_finish(res)
            self.mounted = 1
        except gio.Error, e:
            print "ERROR: ", e
            self.mounted = -1

    def _create_connection(self, credentials=None):
        if credentials is None:
            user, password = self._user, self._password
        else:
            user, password = credentials

        try:
            connection = gio.File(self.url)
            mount = None
            try:
                mount = connection.find_enclosing_mount()
                if mount != None:
                    self.mounted = 1
            except gio.Error, e:
                if (e.code == gio.ERROR_NOT_MOUNTED):
                    op = gio.MountOperation()
                    if user:
                        op.set_username(user)
                    if password:
                        op.set_password(password)
                    op.connect('ask-password', self._ask_password_cb)
                    m = connection.mount_enclosing_volume(op, self._mount_done_cb)
                    while self.mounted == 0:
                        if gtk.events_pending():
                            gtk.main_iteration()
                        time.sleep(0.1)
                else:
                    mounted = 1
        except gio.Error, e:
            raise errors.TransportError(msg="Error setting up connection:"
                                        " %s" % str(e), orig_error=e)
        return connection, (user, password)

    def _reconnect(self):
        """Create a new connection with the previously used credentials"""
        credentials = self._get_credentials()
        connection, credentials = self._create_connection(credentials)
        self._set_connection(connection, credentials)

    def _remote_path(self, relpath):
        relative = urlutils.unescape(relpath).encode('utf-8')
        remote_path = self._combine_paths(self._path, relative)
        return remote_path

    def has(self, relpath):
        """Does the target location exist?"""
        try:
            mutter('GIO has check: %s' % relpath)
            f = self._get_GIO(relpath)
            st = GioStatResult(f)
            if stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode):
                return True
            return False
        except gio.Error, e:
            if e.code == gio.ERROR_NOT_FOUND:
                return False
            else:
                self._translate_gio_error(e, relpath)

    def get(self, relpath, decode=False, retries=0):
        """Get the file at the given relative path.

        :param relpath: The relative path to the file
        :param retries: Number of retries after temporary failures so far
                        for this operation.

        We're meant to return a file-like object which bzr will
        then read from. For now we do this via the magic of StringIO
        """
        try:
            mutter("GIO get: %s" % relpath)
            f = self._get_GIO(relpath)
            fin = f.read()
            buf = fin.read()
            fin.close()
            ret = StringIO(buf)
            return ret
        except gio.Error, e:
            #Currently no retries code is implemented, don't
            #know if that is needed or is gio makes things more
            #reliable
            self._translate_gio_error(e, relpath)

    def put_file(self, relpath, fp, mode=None):
        """Copy the file-like object into the location.

        :param relpath: Location to put the contents, relative to base.
        :param fp:       File-like or string object.
        """
        mutter("GIO put_file %s" % relpath)
        tmppath = '%s.tmp.%.9f.%d.%d' % (relpath, time.time(),
                    os.getpid(), random.randint(0, 0x7FFFFFFF))
        f = None
        fout = None
        try:
            try:
                f = self._get_GIO(tmppath)
                fout = f.create()
                closed = False
                length = self._pump(fp, fout)
                fout.close()
                closed = True
                self.stat(tmppath)
                dest = self._get_GIO(relpath)
                f.move(dest, flags=gio.FILE_COPY_OVERWRITE)

                if mode is not None:
                    self._setmode(relpath, mode)
                #mutter("GIO put_file wrote %d bytes", length);
                return length
            except gio.Error, e:
                self._translate_gio_error(e, relpath)
        except Exception, e:
            import traceback
            mutter(traceback.format_exc())

            try:
                if not closed and fout is not None:
                    fout.close()
                if f is not None:
                    f.delete()
            except:
                # raise the saved except
                raise e
            # raise the original with its traceback if we can.
            raise

    def mkdir(self, relpath, mode=None):
        """Create a directory at the given path."""
        try:
            mutter("GIO mkdir: %s" % relpath)
            f = self._get_GIO(relpath)
            f.make_directory()
            self._setmode(relpath, mode)
        except gio.Error, e:
            self._translate_gio_error(e, relpath)

    def open_write_stream(self, relpath, mode=None):
        """See Transport.open_write_stream."""
        mutter("GIO open_write_stream %s" % relpath)
        if mode is not None:
            self._setmode(relpath, mode)
        result = GioFileStream(self, relpath)
        _file_streams[self.abspath(relpath)] = result
        return result

    def recommended_page_size(self):
        """See Transport.recommended_page_size().

        For FTP we suggest a large page size to reduce the overhead
        introduced by latency.
        """
        mutter("GIO recommended_page")
        return 64 * 1024

    def rmdir(self, relpath):
        """Delete the directory at rel_path"""
        try:
            mutter("GIO rmdir %s" % relpath)
            st = self.stat(relpath)
            if stat.S_ISDIR(st.st_mode):
                f = self._get_GIO(relpath)
                f.delete()
            else:
                raise errors.NotADirectory(relpath)
        except gio.Error, e:
            self._translate_gio_error(e, relpath)
        except errors.NotADirectory, e:
            #just pass it forward
            raise e
        except Exception, e:
            mutter('failed to rmdir %s: %s' % (relpath, e))
            raise errors.PathError(relpath)

    def append_file(self, relpath, file, mode=None):
        """Append the text in the file-like object into the final
        location.
        """
        #GIO append_to seems not to append but to truncate
        #Work around this.
        mutter("GIO append_file: %s" % relpath)
        tmppath = '%s.tmp.%.9f.%d.%d' % (relpath, time.time(),
                    os.getpid(), random.randint(0, 0x7FFFFFFF))
        try:
            result = 0
            fo = self._get_GIO(tmppath)
            fi = self._get_GIO(relpath)
            fout = fo.create()
            try:
                info = GioStatResult(fi)
                result = info.st_size
                fin = fi.read()
                length = self._pump(fin, fout)
                fin.close()
            except gio.Error, e:
                if e.code != gio.ERROR_NOT_FOUND:
                    self._translate_gio_error(e, relpath)
            length = self._pump(file, fout)
            fout.close()
            fo.move(fi, flags=gio.FILE_COPY_OVERWRITE)
            info = GioStatResult(fi)
            if info.st_size != result + length:
                raise errors.BzrError("Failed to append size after (%d) is not beforfe (%d) + written (%d) total (%d)" % (info.st_size, result, length, result + length))
            return result
        except gio.Error, e:
            self._translate_gio_error(e, relpath)

    def _setmode(self, relpath, mode):
        """Set permissions on a path.

        Only set permissions on Unix systems
        """
        mutter("GIO _setmode %s" % relpath)
        if mode:
            try:
                f = self._get_GIO(relpath)
                f.set_attribute_uint32(gio.FILE_ATTRIBUTE_UNIX_MODE, mode)
            except gio.Error, e:
                if e.code == gio.ERROR_NOT_SUPPORTED:
                    # Command probably not available on this server
                    mutter("GIO Could not set permissions to %s on %s. %s",
                        oct(mode), self._remote_path(relpath), str(e))
                else:
                    self._translate_gio_error(e, relpath)

    def rename(self, rel_from, rel_to):
        """Rename without special overwriting"""
        try:
            mutter("GIO move (rename): %s => %s", rel_from, rel_to)
            f = self._get_GIO(rel_from)
            t = self._get_GIO(rel_to)
            f.move(t)
        except gio.Error, e:
            self._translate_gio_error(e, relfrom)

    def move(self, rel_from, rel_to):
        """Move the item at rel_from to the location at rel_to"""
        try:
            mutter("GIO move: %s => %s", rel_from, rel_to)
            f = self._get_GIO(rel_from)
            t = self._get_GIO(rel_to)
            f.move(t, flags=gio.FILE_COPY_OVERWRITE)
        except gio.Error, e:
            self._translate_gio_error(e, relfrom)

    def delete(self, relpath):
        """Delete the item at relpath"""
        try:
            mutter("GIO delete: %s", relpath)
            f = self._get_GIO(relpath)
            f.delete()
        except gio.Error, e:
            self._translate_gio_error(e, relpath)

    def external_url(self):
        """See bzrlib.transport.Transport.external_url."""
        mutter("GIO external_url", self.base)
        # GIO external url
        return self.base

    def listable(self):
        """See Transport.listable."""
        mutter("GIO listable")
        return True

    def list_dir(self, relpath):
        """See Transport.list_dir."""
        mutter("GIO list_dir")
        try:
            entries = []
            f = self._get_GIO(relpath)
            children = f.enumerate_children(gio.FILE_ATTRIBUTE_STANDARD_NAME)
            for child in children:
                entries.append(urlutils.escape(child.get_name()))
            return entries
        except gio.Error, e:
            self._translate_gio_error(e, relpath)

    def iter_files_recursive(self):
        """See Transport.iter_files_recursive.

        This is cargo-culted from the SFTP transport"""
        mutter("GIO iter_files_recursive")
        queue = list(self.list_dir("."))
        while queue:
            relpath = queue.pop(0)
            st = self.stat(relpath)
            if stat.S_ISDIR(st.st_mode):
                for i, basename in enumerate(self.list_dir(relpath)):
                    queue.insert(i, relpath + "/" + basename)
            else:
                yield relpath

    def stat(self, relpath):
        """Return the stat information for a file."""
        try:
            mutter("GIO stat: %s", relpath)
            f = self._get_GIO(relpath)
            return GioStatResult(f)
        except gio.Error, e:
            self._translate_gio_error(e, relpath, extra='error w/ stat')

    def lock_read(self, relpath):
        """Lock the given file for shared (read) access.
        :return: A lock object, which should be passed to Transport.unlock()
        """
        mutter("GIO lock_read", relpath)

        class BogusLock(object):
            # The old RemoteBranch ignore lock for reading, so we will
            # continue that tradition and return a bogus lock object.

            def __init__(self, path):
                self.path = path

            def unlock(self):
                pass

        return BogusLock(relpath)

    def lock_write(self, relpath):
        """Lock the given file for exclusive (write) access.
        WARNING: many transports do not support this, so trying avoid using it

        :return: A lock object, which should be passed to Transport.unlock()
        """
        mutter("GIO lock_write", relpath)
        return self.lock_read(relpath)

    def _translate_gio_error(self, err, path, extra=None):
        mutter("GIO Error: %s %s" % (str(err), path))
        if extra is None:
            extra = str(err)
        if err.code == gio.ERROR_NOT_FOUND:
            raise errors.NoSuchFile(path, extra=extra)
        elif err.code == gio.ERROR_EXISTS:
            raise errors.FileExists(path, extra=extra)
        elif err.code == gio.ERROR_NOT_DIRECTORY:
            raise errors.NotADirectory(path, extra=extra)
        elif err.code == gio.ERROR_NOT_EMPTY:
            raise errors.DirectoryNotEmpty(path, extra=extra)
        elif err.code == gio.ERROR_BUSY:
            raise errors.ResourceBusy(path, extra=extra)
        elif err.code == gio.ERROR_PERMISSION_DENIED:
            raise errors.PermissionDenied(path, extra=extra)
        else:
            mutter('unable to understand error for path: %s: %s', path, err)
            raise err

#def get_test_permutations():
#    """Return the permutations to be used in testing."""
#    from bzrlib.tests import stub_sftp
#    return [(GioTransport, stub_sftp.SFTPAbsoluteServer),
#            (GioTransport, stub_sftp.SFTPHomeDirServer),
#            (GioTransport, stub_sftp.SFTPSiblingAbsoluteServer),
#            ]
