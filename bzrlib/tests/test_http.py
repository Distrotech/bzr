# Copyright (C) 2005, 2006 Canonical Ltd
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

# FIXME: This test should be repeated for each available http client
# implementation; at the moment we have urllib and pycurl.

# TODO: Should be renamed to bzrlib.transport.http.tests?
# TODO: What about renaming to bzrlib.tests.transport.http ?

import os
import select
import socket
import threading

import bzrlib
from bzrlib import errors
from bzrlib import osutils
from bzrlib.tests import (
    TestCase,
    TestSkipped,
    )
from bzrlib.tests.HttpServer import (
    HttpServer,
    HttpServer_PyCurl,
    HttpServer_urllib,
    )
from bzrlib.tests.HTTPTestUtil import (
    BadProtocolRequestHandler,
    BadStatusRequestHandler,
    FakeProxyRequestHandler,
    ForbiddenRequestHandler,
    InvalidStatusRequestHandler,
    NoRangeRequestHandler,
    SingleRangeRequestHandler,
    TestCaseWithTwoWebservers,
    TestCaseWithWebserver,
    WallRequestHandler,
    )
from bzrlib.transport import (
    get_transport,
    Transport,
    )
from bzrlib.transport.http import (
    extract_auth,
    HttpTransportBase,
    )
from bzrlib.transport.http._urllib import HttpTransport_urllib


class FakeManager(object):

    def __init__(self):
        self.credentials = []

    def add_password(self, realm, host, username, password):
        self.credentials.append([realm, host, username, password])


class RecordingServer(object):
    """A fake HTTP server.
    
    It records the bytes sent to it, and replies with a 200.
    """

    def __init__(self, expect_body_tail=None):
        """Constructor.

        :type expect_body_tail: str
        :param expect_body_tail: a reply won't be sent until this string is
            received.
        """
        self._expect_body_tail = expect_body_tail
        self.host = None
        self.port = None
        self.received_bytes = ''

    def setUp(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(('127.0.0.1', 0))
        self.host, self.port = self._sock.getsockname()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._accept_read_and_reply)
        self._thread.setDaemon(True)
        self._thread.start()
        self._ready.wait(5)

    def _accept_read_and_reply(self):
        self._sock.listen(1)
        self._ready.set()
        self._sock.settimeout(5)
        try:
            conn, address = self._sock.accept()
            # On win32, the accepted connection will be non-blocking to start
            # with because we're using settimeout.
            conn.setblocking(True)
            while not self.received_bytes.endswith(self._expect_body_tail):
                self.received_bytes += conn.recv(4096)
            conn.sendall('HTTP/1.1 200 OK\r\n')
        except socket.timeout:
            # Make sure the client isn't stuck waiting for us to e.g. accept.
            self._sock.close()
        except socket.error:
            # The client may have already closed the socket.
            pass

    def tearDown(self):
        try:
            self._sock.close()
        except socket.error:
            # We might have already closed it.  We don't care.
            pass
        self.host = None
        self.port = None


class TestHttpUrls(TestCase):

    # FIXME: Some of these tests should be done for both
    # implementations

    def test_url_parsing(self):
        f = FakeManager()
        url = extract_auth('http://example.com', f)
        self.assertEquals('http://example.com', url)
        self.assertEquals(0, len(f.credentials))
        url = extract_auth('http://user:pass@www.bazaar-vcs.org/bzr/bzr.dev', f)
        self.assertEquals('http://www.bazaar-vcs.org/bzr/bzr.dev', url)
        self.assertEquals(1, len(f.credentials))
        self.assertEquals([None, 'www.bazaar-vcs.org', 'user', 'pass'],
                          f.credentials[0])

    def test_abs_url(self):
        """Construction of absolute http URLs"""
        t = HttpTransport_urllib('http://bazaar-vcs.org/bzr/bzr.dev/')
        eq = self.assertEqualDiff
        eq(t.abspath('.'),
           'http://bazaar-vcs.org/bzr/bzr.dev')
        eq(t.abspath('foo/bar'),
           'http://bazaar-vcs.org/bzr/bzr.dev/foo/bar')
        eq(t.abspath('.bzr'),
           'http://bazaar-vcs.org/bzr/bzr.dev/.bzr')
        eq(t.abspath('.bzr/1//2/./3'),
           'http://bazaar-vcs.org/bzr/bzr.dev/.bzr/1/2/3')

    def test_invalid_http_urls(self):
        """Trap invalid construction of urls"""
        t = HttpTransport_urllib('http://bazaar-vcs.org/bzr/bzr.dev/')
        self.assertRaises(ValueError,
            t.abspath,
            '.bzr/')
        t = HttpTransport_urllib('http://http://bazaar-vcs.org/bzr/bzr.dev/')
        self.assertRaises(errors.InvalidURL, t.has, 'foo/bar')

    def test_http_root_urls(self):
        """Construction of URLs from server root"""
        t = HttpTransport_urllib('http://bzr.ozlabs.org/')
        eq = self.assertEqualDiff
        eq(t.abspath('.bzr/tree-version'),
           'http://bzr.ozlabs.org/.bzr/tree-version')

    def test_http_impl_urls(self):
        """There are servers which ask for particular clients to connect"""
        server = HttpServer_PyCurl()
        try:
            server.setUp()
            url = server.get_url()
            self.assertTrue(url.startswith('http+pycurl://'))
        finally:
            server.tearDown()


class TestHttpConnections(object):
    """Test the http connections.

    This MUST be used by daughter classes that also inherit from
    TestCaseWithWebserver.

    We can't inherit directly from TestCaseWithWebserver or the
    test framework will try to create an instance which cannot
    run, its implementation being incomplete.
    """

    def setUp(self):
        TestCaseWithWebserver.setUp(self)
        self.build_tree(['xxx', 'foo/', 'foo/bar'], line_endings='binary',
                        transport=self.get_transport())

    def test_http_has(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())
        self.assertEqual(t.has('foo/bar'), True)
        self.assertEqual(len(server.logs), 1)
        self.assertContainsRe(server.logs[0],
            r'"HEAD /foo/bar HTTP/1.." (200|302) - "-" "bzr/')

    def test_http_has_not_found(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())
        self.assertEqual(t.has('not-found'), False)
        self.assertContainsRe(server.logs[1],
            r'"HEAD /not-found HTTP/1.." 404 - "-" "bzr/')

    def test_http_get(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())
        fp = t.get('foo/bar')
        self.assertEqualDiff(
            fp.read(),
            'contents of foo/bar\n')
        self.assertEqual(len(server.logs), 1)
        self.assertTrue(server.logs[0].find(
            '"GET /foo/bar HTTP/1.1" 200 - "-" "bzr/%s'
            % bzrlib.__version__) > -1)

    def test_get_smart_medium(self):
        # For HTTP, get_smart_medium should return the transport object.
        server = self.get_readonly_server()
        http_transport = self._transport(server.get_url())
        medium = http_transport.get_smart_medium()
        self.assertIs(medium, http_transport)

    def test_has_on_bogus_host(self):
        # Get a free address and don't 'accept' on it, so that we
        # can be sure there is no http handler there, but set a
        # reasonable timeout to not slow down tests too much.
        default_timeout = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(2)
            s = socket.socket()
            s.bind(('localhost', 0))
            t = self._transport('http://%s:%s/' % s.getsockname())
            self.assertRaises(errors.ConnectionError, t.has, 'foo/bar')
        finally:
            socket.setdefaulttimeout(default_timeout)


class TestWithTransport_pycurl(object):
    """Test case to inherit from if pycurl is present"""

    def _get_pycurl_maybe(self):
        try:
            from bzrlib.transport.http._pycurl import PyCurlTransport
            return PyCurlTransport
        except errors.DependencyNotPresent:
            raise TestSkipped('pycurl not present')

    _transport = property(_get_pycurl_maybe)


class TestHttpConnections_urllib(TestHttpConnections, TestCaseWithWebserver):
    """Test http connections with urllib"""

    _transport = HttpTransport_urllib



class TestHttpConnections_pycurl(TestWithTransport_pycurl,
                                 TestHttpConnections,
                                 TestCaseWithWebserver):
    """Test http connections with pycurl"""


class TestHttpTransportRegistration(TestCase):
    """Test registrations of various http implementations"""

    def test_http_registered(self):
        # urlllib should always be present
        t = get_transport('http+urllib://bzr.google.com/')
        self.assertIsInstance(t, Transport)
        self.assertIsInstance(t, HttpTransport_urllib)


class TestOffsets(TestCase):
    """Test offsets_to_ranges method"""

    def test_offsets_to_ranges_simple(self):
        to_range = HttpTransportBase.offsets_to_ranges
        ranges = to_range([(10, 1)])
        self.assertEqual([[10, 10]], ranges)

        ranges = to_range([(0, 1), (1, 1)])
        self.assertEqual([[0, 1]], ranges)

        ranges = to_range([(1, 1), (0, 1)])
        self.assertEqual([[0, 1]], ranges)

    def test_offset_to_ranges_overlapped(self):
        to_range = HttpTransportBase.offsets_to_ranges

        ranges = to_range([(10, 1), (20, 2), (22, 5)])
        self.assertEqual([[10, 10], [20, 26]], ranges)

        ranges = to_range([(10, 1), (11, 2), (22, 5)])
        self.assertEqual([[10, 12], [22, 26]], ranges)


class TestPost(object):

    def _test_post_body_is_received(self, scheme):
        server = RecordingServer(expect_body_tail='end-of-body')
        server.setUp()
        self.addCleanup(server.tearDown)
        url = '%s://%s:%s/' % (scheme, server.host, server.port)
        try:
            http_transport = get_transport(url)
        except errors.UnsupportedProtocol:
            raise TestSkipped('%s not available' % scheme)
        code, response = http_transport._post('abc def end-of-body')
        self.assertTrue(
            server.received_bytes.startswith('POST /.bzr/smart HTTP/1.'))
        self.assertTrue('content-length: 19\r' in server.received_bytes.lower())
        # The transport should not be assuming that the server can accept
        # chunked encoding the first time it connects, because HTTP/1.1, so we
        # check for the literal string.
        self.assertTrue(
            server.received_bytes.endswith('\r\n\r\nabc def end-of-body'))


class TestPost_urllib(TestCase, TestPost):
    """TestPost for urllib implementation"""

    _transport = HttpTransport_urllib

    def test_post_body_is_received_urllib(self):
        self._test_post_body_is_received('http+urllib')


class TestPost_pycurl(TestWithTransport_pycurl, TestCase, TestPost):
    """TestPost for pycurl implementation"""

    def test_post_body_is_received_pycurl(self):
        self._test_post_body_is_received('http+pycurl')


class TestRangeHeader(TestCase):
    """Test range_header method"""

    def check_header(self, value, ranges=[], tail=0):
        range_header = HttpTransportBase.range_header
        self.assertEqual(value, range_header(ranges, tail))

    def test_range_header_single(self):
        self.check_header('0-9', ranges=[[0,9]])
        self.check_header('100-109', ranges=[[100,109]])

    def test_range_header_tail(self):
        self.check_header('-10', tail=10)
        self.check_header('-50', tail=50)

    def test_range_header_multi(self):
        self.check_header('0-9,100-200,300-5000',
                          ranges=[(0,9), (100, 200), (300,5000)])

    def test_range_header_mixed(self):
        self.check_header('0-9,300-5000,-50',
                          ranges=[(0,9), (300,5000)],
                          tail=50)


class TestWallServer(object):
    """Tests exceptions during the connection phase"""

    def create_transport_readonly_server(self):
        return HttpServer(WallRequestHandler)

    def test_http_has(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())
        # Unfortunately httplib (see HTTPResponse._read_status
        # for details) make no distinction between a closed
        # socket and badly formatted status line, so we can't
        # just test for ConnectionError, we have to test
        # InvalidHttpResponse too.
        self.assertRaises((errors.ConnectionError, errors.InvalidHttpResponse),
                          t.has, 'foo/bar')

    def test_http_get(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())
        self.assertRaises((errors.ConnectionError, errors.InvalidHttpResponse),
                          t.get, 'foo/bar')


class TestWallServer_urllib(TestWallServer, TestCaseWithWebserver):
    """Tests "wall" server for urllib implementation"""

    _transport = HttpTransport_urllib


class TestWallServer_pycurl(TestWithTransport_pycurl,
                            TestWallServer,
                            TestCaseWithWebserver):
    """Tests "wall" server for pycurl implementation"""


class TestBadStatusServer(object):
    """Tests bad status from server."""

    def create_transport_readonly_server(self):
        return HttpServer(BadStatusRequestHandler)

    def test_http_has(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())
        self.assertRaises(errors.InvalidHttpResponse, t.has, 'foo/bar')

    def test_http_get(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())
        self.assertRaises(errors.InvalidHttpResponse, t.get, 'foo/bar')


class TestBadStatusServer_urllib(TestBadStatusServer, TestCaseWithWebserver):
    """Tests bad status server for urllib implementation"""

    _transport = HttpTransport_urllib


class TestBadStatusServer_pycurl(TestWithTransport_pycurl,
                                 TestBadStatusServer,
                                 TestCaseWithWebserver):
    """Tests bad status server for pycurl implementation"""


class TestInvalidStatusServer(TestBadStatusServer):
    """Tests invalid status from server.

    Both implementations raises the same error as for a bad status.
    """

    def create_transport_readonly_server(self):
        return HttpServer(InvalidStatusRequestHandler)


class TestInvalidStatusServer_urllib(TestInvalidStatusServer,
                                     TestCaseWithWebserver):
    """Tests invalid status server for urllib implementation"""

    _transport = HttpTransport_urllib


class TestInvalidStatusServer_pycurl(TestWithTransport_pycurl,
                                     TestInvalidStatusServer,
                                     TestCaseWithWebserver):
    """Tests invalid status server for pycurl implementation"""


class TestBadProtocolServer(object):
    """Tests bad protocol from server."""

    def create_transport_readonly_server(self):
        return HttpServer(BadProtocolRequestHandler)

    def test_http_has(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())
        self.assertRaises(errors.InvalidHttpResponse, t.has, 'foo/bar')

    def test_http_get(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())
        self.assertRaises(errors.InvalidHttpResponse, t.get, 'foo/bar')


class TestBadProtocolServer_urllib(TestBadProtocolServer,
                                   TestCaseWithWebserver):
    """Tests bad protocol server for urllib implementation"""

    _transport = HttpTransport_urllib

# curl don't check the protocol version
#class TestBadProtocolServer_pycurl(TestWithTransport_pycurl,
#                                   TestBadProtocolServer,
#                                   TestCaseWithWebserver):
#    """Tests bad protocol server for pycurl implementation"""


class TestForbiddenServer(object):
    """Tests forbidden server"""

    def create_transport_readonly_server(self):
        return HttpServer(ForbiddenRequestHandler)

    def test_http_has(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())
        self.assertRaises(errors.TransportError, t.has, 'foo/bar')

    def test_http_get(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())
        self.assertRaises(errors.TransportError, t.get, 'foo/bar')


class TestForbiddenServer_urllib(TestForbiddenServer, TestCaseWithWebserver):
    """Tests forbidden server for urllib implementation"""

    _transport = HttpTransport_urllib


class TestForbiddenServer_pycurl(TestWithTransport_pycurl,
                                 TestForbiddenServer,
                                 TestCaseWithWebserver):
    """Tests forbidden server for pycurl implementation"""


class TestRecordingServer(TestCase):

    def test_create(self):
        server = RecordingServer(expect_body_tail=None)
        self.assertEqual('', server.received_bytes)
        self.assertEqual(None, server.host)
        self.assertEqual(None, server.port)

    def test_setUp_and_tearDown(self):
        server = RecordingServer(expect_body_tail=None)
        server.setUp()
        try:
            self.assertNotEqual(None, server.host)
            self.assertNotEqual(None, server.port)
        finally:
            server.tearDown()
        self.assertEqual(None, server.host)
        self.assertEqual(None, server.port)

    def test_send_receive_bytes(self):
        server = RecordingServer(expect_body_tail='c')
        server.setUp()
        self.addCleanup(server.tearDown)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((server.host, server.port))
        sock.sendall('abc')
        self.assertEqual('HTTP/1.1 200 OK\r\n',
                         osutils.recv_all(sock, 4096))
        self.assertEqual('abc', server.received_bytes)


class TestRangeRequestServer(object):
    """Tests readv requests against server.

    This MUST be used by daughter classes that also inherit from
    TestCaseWithWebserver.

    We can't inherit directly from TestCaseWithWebserver or the
    test framework will try to create an instance which cannot
    run, its implementation being incomplete.
    """

    def setUp(self):
        TestCaseWithWebserver.setUp(self)
        self.build_tree_contents([('a', '0123456789')],)

    def test_readv(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())
        l = list(t.readv('a', ((0, 1), (1, 1), (3, 2), (9, 1))))
        self.assertEqual(l[0], (0, '0'))
        self.assertEqual(l[1], (1, '1'))
        self.assertEqual(l[2], (3, '34'))
        self.assertEqual(l[3], (9, '9'))

    def test_readv_out_of_order(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())
        l = list(t.readv('a', ((1, 1), (9, 1), (0, 1), (3, 2))))
        self.assertEqual(l[0], (1, '1'))
        self.assertEqual(l[1], (9, '9'))
        self.assertEqual(l[2], (0, '0'))
        self.assertEqual(l[3], (3, '34'))

    def test_readv_invalid_ranges(self):
        server = self.get_readonly_server()
        t = self._transport(server.get_url())

        # This is intentionally reading off the end of the file
        # since we are sure that it cannot get there
        self.assertListRaises((errors.InvalidRange, errors.ShortReadvError,),
                              t.readv, 'a', [(1,1), (8,10)])

        # This is trying to seek past the end of the file, it should
        # also raise a special error
        self.assertListRaises((errors.InvalidRange, errors.ShortReadvError,),
                              t.readv, 'a', [(12,2)])


class TestSingleRangeRequestServer(TestRangeRequestServer):
    """Test readv against a server which accept only single range requests"""

    def create_transport_readonly_server(self):
        return HttpServer(SingleRangeRequestHandler)


class TestSingleRangeRequestServer_urllib(TestSingleRangeRequestServer,
                                          TestCaseWithWebserver):
    """Tests single range requests accepting server for urllib implementation"""

    _transport = HttpTransport_urllib


class TestSingleRangeRequestServer_pycurl(TestWithTransport_pycurl,
                                          TestSingleRangeRequestServer,
                                          TestCaseWithWebserver):
    """Tests single range requests accepting server for pycurl implementation"""


class TestNoRangeRequestServer(TestRangeRequestServer):
    """Test readv against a server which do not accept range requests"""

    def create_transport_readonly_server(self):
        return HttpServer(NoRangeRequestHandler)


class TestNoRangeRequestServer_urllib(TestNoRangeRequestServer,
                                      TestCaseWithWebserver):
    """Tests range requests refusing server for urllib implementation"""

    _transport = HttpTransport_urllib


class TestNoRangeRequestServer_pycurl(TestWithTransport_pycurl,
                               TestNoRangeRequestServer,
                               TestCaseWithWebserver):
    """Tests range requests refusing server for pycurl implementation"""


class TestHttpProxyWhiteBox(TestCase):
    """Whitebox test proxy http authorization.

    These tests concern urllib implementation only.
    """

    def setUp(self):
        TestCase.setUp(self)
        self._old_env = {}

    def tearDown(self):
        self._restore_env()

    def _set_and_capture_env_var(self, name, new_value):
        """Set an environment variable, and reset it when finished."""
        self._old_env[name] = osutils.set_or_unset_env(name, new_value)

    def _install_env(self, env):
        for name, value in env.iteritems():
            self._set_and_capture_env_var(name, value)

    def _restore_env(self):
        for name, value in self._old_env.iteritems():
            osutils.set_or_unset_env(name, value)

    def _proxied_request(self):
        from bzrlib.transport.http._urllib2_wrappers import (
            ProxyHandler,
            Request,
            )

        handler = ProxyHandler()
        request = Request('GET','http://baz/buzzle')
        handler.set_proxy(request, 'http')
        return request

    def test_empty_user(self):
        self._install_env({'http_proxy': 'http://bar.com'})
        request = self._proxied_request()
        self.assertFalse(request.headers.has_key('Proxy-authorization'))

    def test_empty_pass(self):
        self._install_env({'http_proxy': 'http://joe@bar.com'})
        request = self._proxied_request()
        self.assertEqual('Basic ' + 'joe:'.encode('base64').strip(),
                         request.headers['Proxy-authorization'])
    def test_user_pass(self):
        self._install_env({'http_proxy': 'http://joe:foo@bar.com'})
        request = self._proxied_request()
        self.assertEqual('Basic ' + 'joe:foo'.encode('base64').strip(),
                         request.headers['Proxy-authorization'])

    def test_invalid_proxy(self):
        """A proxy env variable without scheme"""
        self._install_env({'http_proxy': 'host:1234'})
        self.assertRaises(errors.InvalidURL, self._proxied_request)


class TestProxyHttpServer(object):
    """Tests proxy server.

    This MUST be used by daughter classes that also inherit from
    TestCaseWithTwoWebservers.

    We can't inherit directly from TestCaseWithTwoWebservers or
    the test framework will try to create an instance which
    cannot run, its implementation being incomplete.

    Be aware that we do not setup a real proxy here. Instead, we
    check that the *connection* goes through the proxy by serving
    different content (the faked proxy server append '-proxied'
    to the file names).
    """

    # FIXME: We don't have an https server available, so we don't
    # test https connections.

    # FIXME: Once the test suite is better fitted to test
    # authorization schemes, test proxy authorizations too (see
    # bug #83954).

    def setUp(self):
        TestCaseWithTwoWebservers.setUp(self)
        self.build_tree_contents([('foo', 'contents of foo\n'),
                                  ('foo-proxied', 'proxied contents of foo\n')])
        # Let's setup some attributes for tests
        self.server = self.get_readonly_server()
        # FIXME: We should not rely on 'localhost' being the hostname
        self.proxy_address = 'localhost:%d' % self.server.port
        self.no_proxy_host = self.proxy_address
        # The secondary server is the proxy
        self.proxy = self.get_secondary_server()
        self.proxy_url = self.proxy.get_url()
        self._old_env = {}

    def create_transport_secondary_server(self):
        """Creates an http server that will serve files with
        '-proxied' appended to their names.
        """
        return HttpServer(FakeProxyRequestHandler)

    def _set_and_capture_env_var(self, name, new_value):
        """Set an environment variable, and reset it when finished."""
        self._old_env[name] = osutils.set_or_unset_env(name, new_value)

    def _install_env(self, env):
        for name, value in env.iteritems():
            self._set_and_capture_env_var(name, value)

    def _restore_env(self):
        for name, value in self._old_env.iteritems():
            osutils.set_or_unset_env(name, value)

    def proxied_in_env(self, env):
        self._install_env(env)
        url = self.server.get_url()
        t = self._transport(url)
        try:
            self.assertEqual(t.get('foo').read(), 'proxied contents of foo\n')
        finally:
            self._restore_env()

    def not_proxied_in_env(self, env):
        self._install_env(env)
        url = self.server.get_url()
        t = self._transport(url)
        try:
            self.assertEqual(t.get('foo').read(), 'contents of foo\n')
        finally:
            self._restore_env()

    def test_http_proxy(self):
        self.proxied_in_env({'http_proxy': self.proxy_url})

    def test_HTTP_PROXY(self):
        self.proxied_in_env({'HTTP_PROXY': self.proxy_url})

    def test_all_proxy(self):
        self.proxied_in_env({'all_proxy': self.proxy_url})

    def test_ALL_PROXY(self):
        self.proxied_in_env({'ALL_PROXY': self.proxy_url})

    def test_http_proxy_with_no_proxy(self):
        self.not_proxied_in_env({'http_proxy': self.proxy_url,
                                 'no_proxy': self.no_proxy_host})

    def test_HTTP_PROXY_with_NO_PROXY(self):
        self.not_proxied_in_env({'HTTP_PROXY': self.proxy_url,
                                 'NO_PROXY': self.no_proxy_host})

    def test_all_proxy_with_no_proxy(self):
        self.not_proxied_in_env({'all_proxy': self.proxy_url,
                                 'no_proxy': self.no_proxy_host})

    def test_ALL_PROXY_with_NO_PROXY(self):
        self.not_proxied_in_env({'ALL_PROXY': self.proxy_url,
                                 'NO_PROXY': self.no_proxy_host})

    def test_http_proxy_without_scheme(self):
        self.assertRaises(errors.InvalidURL,
                          self.proxied_in_env,
                          {'http_proxy': self.proxy_address})


class TestProxyHttpServer_urllib(TestProxyHttpServer,
                                 TestCaseWithTwoWebservers):
    """Tests proxy server for urllib implementation"""

    _transport = HttpTransport_urllib


class TestProxyHttpServer_pycurl(TestWithTransport_pycurl,
                                 TestProxyHttpServer,
                                 TestCaseWithTwoWebservers):
    """Tests proxy server for pycurl implementation"""

    def setUp(self):
        TestProxyHttpServer.setUp(self)
        # Oh my ! pycurl does not check for the port as part of
        # no_proxy :-( So we just test the host part
        self.no_proxy_host = 'localhost'

    def test_HTTP_PROXY(self):
        # pycurl do not check HTTP_PROXY for security reasons
        # (for use in a CGI context that we do not care
        # about. Should we ?)
        raise TestSkipped()

    def test_HTTP_PROXY_with_NO_PROXY(self):
        raise TestSkipped()

    def test_http_proxy_without_scheme(self):
        # pycurl *ignores* invalid proxy env variables. If that
        # ever change in the future, this test will fail
        # indicating that pycurl do not ignore anymore such
        # variables.
        self.not_proxied_in_env({'http_proxy': self.proxy_address})


class TestRanges(object):
    """Test the Range header in GET methods..

    This MUST be used by daughter classes that also inherit from
    TestCaseWithWebserver.

    We can't inherit directly from TestCaseWithWebserver or the
    test framework will try to create an instance which cannot
    run, its implementation being incomplete.
    """

    def setUp(self):
        TestCaseWithWebserver.setUp(self)
        self.build_tree_contents([('a', '0123456789')],)
        server = self.get_readonly_server()
        self.transport = self._transport(server.get_url())

    def _file_contents(self, relpath, ranges, tail_amount=0):
         code, data = self.transport._get(relpath, ranges)
         self.assertTrue(code in (200, 206),'_get returns: %d' % code)
         for start, end in ranges:
             data.seek(start)
             yield data.read(end - start + 1)

    def _file_tail(self, relpath, tail_amount):
         code, data = self.transport._get(relpath, [], tail_amount)
         self.assertTrue(code in (200, 206),'_get returns: %d' % code)
         data.seek(-tail_amount + 1, 2)
         return data.read(tail_amount)

    def test_range_header(self):
        # Valid ranges
        map(self.assertEqual,['0', '234'],
            list(self._file_contents('a', [(0,0), (2,4)])),)
        # Tail
        self.assertEqual('789', self._file_tail('a', 3))
        # Syntactically invalid range
        self.assertRaises(errors.InvalidRange,
                          self.transport._get, 'a', [(4, 3)])
        # Semantically invalid range
        self.assertRaises(errors.InvalidRange,
                          self.transport._get, 'a', [(42, 128)])


class TestRanges_urllib(TestRanges, TestCaseWithWebserver):
    """Test the Range header in GET methods for urllib implementation"""

    _transport = HttpTransport_urllib


class TestRanges_pycurl(TestWithTransport_pycurl,
                        TestRanges,
                        TestCaseWithWebserver):
    """Test the Range header in GET methods for pycurl implementation"""
