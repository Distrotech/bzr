# Copyright (C) 2004, 2005 by Canonical Ltd

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

import errno
import os

from warnings import warn
from bzrlib.osutils import rename

class AtomicFile(object):
    """A file that does an atomic-rename to move into place.

    This also causes hardlinks to break when it's written out.

    Open this as for a regular file, then use commit() to move into
    place or abort() to cancel.

    An encoding can be specified; otherwise the default is ascii.
    """

    def __init__(self, filename, mode='wb', encoding=None, new_mode=None):
        if mode != 'wb' and mode != 'wt':
            raise ValueError("invalid AtomicFile mode %r" % mode)

        import socket
        self.tmpfilename = '%s.%d.%s.tmp' % (filename, os.getpid(),
                                             socket.gethostname())
        self.realfilename = filename
        
        if encoding:
            import codecs
            self.f = codecs.open(self.tmpfilename, mode, encoding)
        else:
            self.f = open(self.tmpfilename, mode)

        self.write = self.f.write
        self.closed = False
        self._new_mode = new_mode


    def __repr__(self):
        return '%s(%r)' % (self.__class__.__name__,
                           self.realfilename)
    

    def commit(self):
        """Close the file and move to final name."""

        if self.closed:
            raise Exception('%r is already closed' % self)

        self.closed = True
        self.f.close()
        self.f = None
        
        try:
            if self._new_mode is None:
                self._new_mode = os.lstat(self.realfilename).st_mode
        except OSError, e:
            if e.errno != errno.ENOENT:
                raise
        else:
            os.chmod(self.tmpfilename, self._new_mode)

        rename(self.tmpfilename, self.realfilename)


    def abort(self):
        """Discard temporary file without committing changes."""

        if self.closed:
            raise Exception('%r is already closed' % self)

        self.closed = True
        self.f.close()
        self.f = None
        os.remove(self.tmpfilename)


    def close(self):
        """Discard the file unless already committed."""
        if not self.closed:
            self.abort()


    def __del__(self):
        if hasattr(self, 'closed') and not self.closed:
            warn("%r leaked" % self)
        
