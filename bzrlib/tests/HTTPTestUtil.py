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

from cStringIO import StringIO
import errno
from SimpleHTTPServer import SimpleHTTPRequestHandler
import re
import socket
import urllib2
import urlparse

from bzrlib.tests import TestCaseWithTransport
from bzrlib.tests.HttpServer import (
    HttpServer,
    TestingHTTPRequestHandler,
    )
from bzrlib.transport import (
    get_transport,
    )
from bzrlib.smart import protocol


class WallRequestHandler(TestingHTTPRequestHandler):
    """Whatever request comes in, close the connection"""

    def handle_one_request(self):
        """Handle a single HTTP request, by abruptly closing the connection"""
        self.close_connection = 1


class BadStatusRequestHandler(TestingHTTPRequestHandler):
    """Whatever request comes in, returns a bad status"""

    def parse_request(self):
        """Fakes handling a single HTTP request, returns a bad status"""
        ignored = TestingHTTPRequestHandler.parse_request(self)
        try:
            self.send_response(0, "Bad status")
            self.end_headers()
        except socket.error, e:
            # We don't want to pollute the test results with
            # spurious server errors while test succeed. In our
            # case, it may occur that the test has already read
            # the 'Bad Status' and closed the socket while we are
            # still trying to send some headers... So the test is
            # ok, but if we raise the exception, the output is
            # dirty. So we don't raise, but we close the
            # connection, just to be safe :)
            spurious = [errno.EPIPE,
                        errno.ECONNRESET,
                        errno.ECONNABORTED,
                        ]
            if (len(e.args) > 0) and (e.args[0] in spurious):
                self.close_connection = 1
                pass
            else:
                raise
        return False


class InvalidStatusRequestHandler(TestingHTTPRequestHandler):
    """Whatever request comes in, returns am invalid status"""

    def parse_request(self):
        """Fakes handling a single HTTP request, returns a bad status"""
        ignored = TestingHTTPRequestHandler.parse_request(self)
        self.wfile.write("Invalid status line\r\n")
        return False


class BadProtocolRequestHandler(TestingHTTPRequestHandler):
    """Whatever request comes in, returns a bad protocol version"""

    def parse_request(self):
        """Fakes handling a single HTTP request, returns a bad status"""
        ignored = TestingHTTPRequestHandler.parse_request(self)
        # Returns an invalid protocol version, but curl just
        # ignores it and those cannot be tested.
        self.wfile.write("%s %d %s\r\n" % ('HTTP/0.0',
                                           404,
                                           'Look at my protocol version'))
        return False


class ForbiddenRequestHandler(TestingHTTPRequestHandler):
    """Whatever request comes in, returns a 403 code"""

    def parse_request(self):
        """Handle a single HTTP request, by replying we cannot handle it"""
        ignored = TestingHTTPRequestHandler.parse_request(self)
        self.send_error(403)
        return False


class HTTPServerWithSmarts(HttpServer):
    """HTTPServerWithSmarts extends the HttpServer with POST methods that will
    trigger a smart server to execute with a transport rooted at the rootdir of
    the HTTP server.
    """

    def __init__(self):
        HttpServer.__init__(self, SmartRequestHandler)


class SmartRequestHandler(TestingHTTPRequestHandler):
    """Extend TestingHTTPRequestHandler to support smart client POSTs."""

    def do_POST(self):
        """Hand the request off to a smart server instance."""
        self.send_response(200)
        self.send_header("Content-type", "application/octet-stream")
        transport = get_transport(self.server.test_case_server._home_dir)
        # TODO: We might like to support streaming responses.  1.0 allows no
        # Content-length in this case, so for integrity we should perform our
        # own chunking within the stream.
        # 1.1 allows chunked responses, and in this case we could chunk using
        # the HTTP chunking as this will allow HTTP persistence safely, even if
        # we have to stop early due to error, but we would also have to use the
        # HTTP trailer facility which may not be widely available.
        out_buffer = StringIO()
        smart_protocol_request = protocol.SmartServerRequestProtocolOne(
                transport, out_buffer.write)
        # if this fails, we should return 400 bad request, but failure is
        # failure for now - RBC 20060919
        data_length = int(self.headers['Content-Length'])
        # Perhaps there should be a SmartServerHTTPMedium that takes care of
        # feeding the bytes in the http request to the smart_protocol_request,
        # but for now it's simpler to just feed the bytes directly.
        smart_protocol_request.accept_bytes(self.rfile.read(data_length))
        assert smart_protocol_request.next_read_size() == 0, (
            "not finished reading, but all data sent to protocol.")
        self.send_header("Content-Length", str(len(out_buffer.getvalue())))
        self.end_headers()
        self.wfile.write(out_buffer.getvalue())


class SingleRangeRequestHandler(TestingHTTPRequestHandler):
    """Always reply to range request as if they were single.

    Don't be explicit about it, just to annoy the clients.
    """

    def get_multiple_ranges(self, file, file_size, ranges):
        """Answer as if it was a single range request and ignores the rest"""
        (start, end) = ranges[0]
        return self.get_single_range(file, file_size, start, end)


class NoRangeRequestHandler(TestingHTTPRequestHandler):
    """Ignore range requests without notice"""

    # Just bypass the range handling done by TestingHTTPRequestHandler
    do_GET = SimpleHTTPRequestHandler.do_GET


class TestCaseWithWebserver(TestCaseWithTransport):
    """A support class that provides readonly urls that are http://.

    This is done by forcing the readonly server to be an http
    one. This will currently fail if the primary transport is not
    backed by regular disk files.
    """
    def setUp(self):
        super(TestCaseWithWebserver, self).setUp()
        self.transport_readonly_server = HttpServer


class TestCaseWithTwoWebservers(TestCaseWithWebserver):
    """A support class providing readonly urls on two servers that are http://.

    We set up two webservers to allows various tests involving
    proxies or redirections from one server to the other.
    """
    def setUp(self):
        super(TestCaseWithTwoWebservers, self).setUp()
        self.transport_secondary_server = HttpServer
        self.__secondary_server = None

    def create_transport_secondary_server(self):
        """Create a transport server from class defined at init.

        This is mostly a hook for daughter classes.
        """
        return self.transport_secondary_server()

    def get_secondary_server(self):
        """Get the server instance for the secondary transport."""
        if self.__secondary_server is None:
            self.__secondary_server = self.create_transport_secondary_server()
            self.__secondary_server.setUp()
            self.addCleanup(self.__secondary_server.tearDown)
        return self.__secondary_server


class ProxyServer(HttpServer):
    """A proxy test server for http transports."""

    proxy_requests = True


class RedirectRequestHandler(TestingHTTPRequestHandler):
    """Redirect all request to the specified server"""

    def parse_request(self):
        """Redirect a single HTTP request to another host"""
        valid = TestingHTTPRequestHandler.parse_request(self)
        if valid:
            tcs = self.server.test_case_server
            code, target = tcs.is_redirected(self.path)
            if code is not None and target is not None:
                # Redirect as instructed
                self.send_response(code)
                self.send_header('Location', target)
                self.end_headers()
                return False # The job is done
            else:
                # We leave the parent class serve the request
                pass
        return valid


class HTTPServerRedirecting(HttpServer):
    """An HttpServer redirecting to another server """

    def __init__(self, request_handler=RedirectRequestHandler):
        HttpServer.__init__(self, request_handler)
        # redirections is a list of tuples (source, target, code)
        # - source is a regexp for the paths requested
        # - target is a replacement for re.sub describing where
        #   the request will be redirected
        # - code is the http error code associated to the
        #   redirection (301 permanent, 302 temporarry, etc
        self.redirections = []

    def redirect_to(self, host, port):
        """Redirect all requests to a specific host:port"""
        self.redirections = [('(.*)',
                              r'http://%s:%s\1' % (host, port) ,
                              301)]

    def is_redirected(self, path):
        """Is the path redirected by this server.

        :param path: the requested relative path

        :returns: a tuple (code, target) if a matching
             redirection is found, (None, None) otherwise.
        """
        code = None
        target = None
        for (rsource, rtarget, rcode) in self.redirections:
            target, match = re.subn(rsource, rtarget, path)
            if match:
                code = rcode
                break # The first match wins
            else:
                target = None
        return code, target


class TestCaseWithRedirectedWebserver(TestCaseWithTwoWebservers):
   """A support class providing redirections from one server to another.

   We set up two webservers to allows various tests involving
   redirections.
   The 'old' server is redirected to the 'new' server.
   """

   def create_transport_secondary_server(self):
       """Create the secondary server redirecting to the primary server"""
       new = self.get_readonly_server()
       redirecting = HTTPServerRedirecting()
       redirecting.redirect_to(new.host, new.port)
       return redirecting

   def setUp(self):
       super(TestCaseWithRedirectedWebserver, self).setUp()
       # The redirections will point to the new server
       self.new_server = self.get_readonly_server()
       # The requests to the old server will be redirected
       self.old_server = self.get_secondary_server()


class AuthRequestHandler(TestingHTTPRequestHandler):
    """Requires an authentication to process requests.

    This is intended to be used with a server that always and
    only use one authentication scheme (implemented by daughter
    classes).
    """

    # The following attributes should be defined in the server
    # - auth_header_sent: the header name sent to require auth
    # - auth_header_recv: the header received containing auth
    # - auth_error_code: the error code to indicate auth required

    def do_GET(self):
        if self.authorized():
            return TestingHTTPRequestHandler.do_GET(self)
        else:
            # Note that we must update test_case_server *before*
            # sending the error or the client may try to read it
            # before we have sent the whole error back.
            tcs = self.server.test_case_server
            tcs.auth_required_errors += 1
            self.send_response(tcs.auth_error_code)
            self.send_header_auth_reqed()
            self.end_headers()
            return

        TestingHTTPRequestHandler.do_GET(self)


class BasicAuthRequestHandler(AuthRequestHandler):
    """Implements the basic authentication of a request"""

    def authorized(self):
        tcs = self.server.test_case_server
        if tcs.auth_scheme != 'basic':
            return False

        auth_header = self.headers.get(tcs.auth_header_recv)
        if auth_header and auth_header.lower().startswith('basic '):
            raw_auth = auth_header[len('Basic '):]
            user, password = raw_auth.decode('base64').split(':')
            return tcs.authorized(user, password)

        return False

    def send_header_auth_reqed(self):
        self.send_header(self.server.test_case_server.auth_header_sent,
                         'Basic realm="Thou should not pass"')


class DigestAuthRequestHandler(AuthRequestHandler):
    """Implements the digest authentication of a request"""

    def authorized(self):
        tcs = self.server.test_case_server
        if tcs.auth_scheme != 'digest':
            return False

        auth_header = self.headers.get(tcs.auth_header_recv)
        if auth_header and auth_header.lower().startswith('digest '):
            raw_auth = auth_header[len('Basic '):]
            user, password = raw_auth.decode('base64').split(':')
            return tcs.authorized(user, password)

        return False

    def send_header_auth_reqed(self):
        self.send_header(self.server.test_case_server.auth_header_sent,
                         'Basic realm="Thou should not pass"')

class AuthServer(HttpServer):
    """Extends HttpServer with a dictionary of passwords.

    This is used as a base class for various schemes which should
    all use or redefined the associated AuthRequestHandler.

    Note that no users are defined by default, so add_user should
    be called before issuing the first request.

    """
    # The following attributes should be set dy daughter classes
    # and are used by AuthRequestHandler.
    auth_header_sent = None
    auth_header_recv = None
    auth_error_code = None

    def __init__(self, request_handler, auth_scheme):
        HttpServer.__init__(self, request_handler)
        self.auth_scheme = auth_scheme
        self.password_of = {}
        self.auth_required_errors = 0

    def add_user(self, user, password):
        """Declare a user with an associated password.

        password can be empty, use an empty string ('') in that
        case, not None.
        """
        self.password_of[user] = password

    def authorized(self, user, password):
        expected_password = self.password_of.get(user, None)
        return expected_password is not None and password == expected_password


class HTTPAuthServer(AuthServer):
    """An HTTP server requiring authentication"""

    auth_header_sent = 'WWW-Authenticate'
    auth_header_recv = 'Authorization'
    auth_error_code = 401


class ProxyAuthServer(AuthServer):
    """A proxy server requiring authentication"""

    proxy_requests = True
    auth_header_sent = 'Proxy-Authenticate'
    auth_header_recv = 'Proxy-Authorization'
    auth_error_code = 407


class HTTPBasicAuthServer(HTTPAuthServer):
    """An HTTP server requiring basic authentication"""

    def __init__(self):
        HTTPAuthServer.__init__(self, BasicAuthRequestHandler, 'basic')


class HTTPDigestAuthServer(HTTPAuthServer):
    """An HTTP server requiring digest authentication"""

    def __init__(self):
        HTTPAuthServer.__init__(self, DigestAuthRequestHandler, 'digest')


class ProxyBasicAuthServer(ProxyAuthServer):
    """An proxy server requiring basic authentication"""

    def __init__(self):
        ProxyAuthServer.__init__(self, BasicAuthRequestHandler, 'basic')


class ProxyDigestAuthServer(ProxyAuthServer):
    """An proxy server requiring basic authentication"""

    def __init__(self):
        ProxyAuthServer.__init__(self, DigestAuthRequestHandler, 'digest')


