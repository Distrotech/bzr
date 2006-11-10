# Copyright (C) 2006 Canonical Ltd
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

import tempfile

from bzrlib import bzrdir, errors, revision
from bzrlib.bundle.serializer import write_bundle
from bzrlib.transport.smart import protocol


class SmartServerRequest(object):
    """Base class for request handlers.

    (Command pattern.)
    """

    def __init__(self, backing_transport):
        self._backing_transport = backing_transport

    def do(self):
        raise NotImplementedError(self.do)

    def do_body(self, body_bytes):
        raise NotImplementedError(self.do_body)


class SmartServerRequestHandler(object):
    """Protocol logic for smart server.
    
    This doesn't handle serialization at all, it just processes requests and
    creates responses.
    """

    # IMPORTANT FOR IMPLEMENTORS: It is important that SmartServerRequestHandler
    # not contain encoding or decoding logic to allow the wire protocol to vary
    # from the object protocol: we will want to tweak the wire protocol separate
    # from the object model, and ideally we will be able to do that without
    # having a SmartServerRequestHandler subclass for each wire protocol, rather
    # just a Protocol subclass.

    # TODO: Better way of representing the body for commands that take it,
    # and allow it to be streamed into the server.

    def __init__(self, backing_transport):
        self._backing_transport = backing_transport
        self._body_bytes = ''
        self.response = None
        self.finished_reading = False
        self._command = None

    def accept_body(self, bytes):
        """Accept body data."""

        # TODO: This should be overriden for each command that desired body data
        # to handle the right format of that data, i.e. plain bytes, a bundle,
        # etc.  The deserialisation into that format should be done in the
        # Protocol object.

        # default fallback is to accumulate bytes.
        self._body_bytes += bytes
        
    def end_of_body(self):
        """No more body data will be received."""
        self._run_handler_code(self._command.do_body, (self._body_bytes,), {})
        # cannot read after this.
        self.finished_reading = True

    def dispatch_command(self, cmd, args):
        """Deprecated compatibility method.""" # XXX XXX
        command = version_one_commands.get(cmd)
        if command is None:
            raise errors.SmartProtocolError("bad request %r" % (cmd,))
        self._command = command(self._backing_transport)
        self._run_handler_code(self._command.do, args, {})

    def _run_handler_code(self, callable, args, kwargs):
        """Run some handler specific code 'callable'.

        If a result is returned, it is considered to be the commands response,
        and finished_reading is set true, and its assigned to self.response.

        Any exceptions caught are translated and a response object created
        from them.
        """
        result = self._call_converting_errors(callable, args, kwargs)
        if result is not None:
            self.response = result
            self.finished_reading = True

    def _call_converting_errors(self, callable, args, kwargs):
        """Call callable converting errors to Response objects."""
        # XXX: most of this error conversion is VFS-related, and thus ought to
        # be in SmartServerVFSRequestHandler somewhere.
        try:
            return callable(*args, **kwargs)
        except errors.NoSuchFile, e:
            return protocol.SmartServerResponse(('NoSuchFile', e.path))
        except errors.FileExists, e:
            return protocol.SmartServerResponse(('FileExists', e.path))
        except errors.DirectoryNotEmpty, e:
            return protocol.SmartServerResponse(('DirectoryNotEmpty', e.path))
        except errors.ShortReadvError, e:
            return protocol.SmartServerResponse(('ShortReadvError',
                e.path, str(e.offset), str(e.length), str(e.actual)))
        except UnicodeError, e:
            # If it is a DecodeError, than most likely we are starting
            # with a plain string
            str_or_unicode = e.object
            if isinstance(str_or_unicode, unicode):
                # XXX: UTF-8 might have \x01 (our seperator byte) in it.  We
                # should escape it somehow.
                val = 'u:' + str_or_unicode.encode('utf-8')
            else:
                val = 's:' + str_or_unicode.encode('base64')
            # This handles UnicodeEncodeError or UnicodeDecodeError
            return protocol.SmartServerResponse((e.__class__.__name__,
                    e.encoding, val, str(e.start), str(e.end), e.reason))
        except errors.TransportNotPossible, e:
            if e.msg == "readonly transport":
                return protocol.SmartServerResponse(('ReadOnlyError', ))
            else:
                raise


class HelloRequest(SmartServerRequest):
    """Answer a version request with my version."""

    method = 'hello'

    def do(self):
        return protocol.SmartServerResponse(('ok', '1'))


class GetBundleRequest(SmartServerRequest):

    method = 'get_bundle'

    def do(self, path, revision_id):
        # open transport relative to our base
        t = self._backing_transport.clone(path)
        control, extra_path = bzrdir.BzrDir.open_containing_from_transport(t)
        repo = control.open_repository()
        tmpf = tempfile.TemporaryFile()
        base_revision = revision.NULL_REVISION
        write_bundle(repo, revision_id, base_revision, tmpf)
        tmpf.seek(0)
        return protocol.SmartServerResponse((), tmpf.read())


# This is extended by bzrlib/transport/smart/vfs.py
version_one_commands = {
    HelloRequest.method: HelloRequest,
    GetBundleRequest.method: GetBundleRequest,
}


