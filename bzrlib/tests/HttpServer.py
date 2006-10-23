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

import BaseHTTPServer
import errno
import os
from SimpleHTTPServer import SimpleHTTPRequestHandler
import socket
import random
import re
import sys
import threading
import time

from bzrlib.transport import Server


class WebserverNotAvailable(Exception):
    pass


class BadWebserverPath(ValueError):
    def __str__(self):
        return 'path %s is not in %s' % self.args


class TestingHTTPRequestHandler(SimpleHTTPRequestHandler):

    def log_message(self, format, *args):
        self.server.test_case.log('webserver - %s - - [%s] %s "%s" "%s"',
                                  self.address_string(),
                                  self.log_date_time_string(),
                                  format % args,
                                  self.headers.get('referer', '-'),
                                  self.headers.get('user-agent', '-'))

    def handle_one_request(self):
        """Handle a single HTTP request.

        You normally don't need to override this method; see the class
        __doc__ string for information on how to handle specific HTTP
        commands such as GET and POST.

        """
        for i in xrange(1,11): # Don't try more than 10 times
            try:
                self.raw_requestline = self.rfile.readline()
            except socket.error, e:
                if e.args[0] in (errno.EAGAIN, errno.EWOULDBLOCK):
                    # omitted for now because some tests look at the log of
                    # the server and expect to see no errors.  see recent
                    # email thread. -- mbp 20051021. 
                    ## self.log_message('EAGAIN (%d) while reading from raw_requestline' % i)
                    time.sleep(0.01)
                    continue
                raise
            else:
                break
        if not self.raw_requestline:
            self.close_connection = 1
            return
        if not self.parse_request(): # An error code has been sent, just exit
            return
        mname = 'do_' + self.command
        if getattr(self, mname, None) is None:
            self.send_error(501, "Unsupported method (%r)" % self.command)
            return
        method = getattr(self, mname)
        method()

    _range_regexp = re.compile(r'^(?P<start>\d+)-(?P<end>\d+)$')
    _tail_regexp = re.compile(r'^-(?P<tail>\d+)$')

    def parse_ranges(self, ranges_header):
        """Parse the range header value and returns ranges and tail"""
        tail = 0
        ranges = []
        assert ranges_header.startswith('bytes=')
        ranges_header = ranges_header[len('bytes='):]
        for range_str in ranges_header.split(','):
            range_match = self._range_regexp.match(range_str)
            if range_match is not None:
                ranges.append((int(range_match.group('start')),
                               int(range_match.group('end'))))
            else:
                tail_match = self._tail_regexp.match(range_str)
                if tail_match is not None:
                    tail = int(tail_match.group('tail'))
        return tail, ranges

    def send_range_content(self, file, start, length):
        file.seek(start)
        self.wfile.write(file.read(length))

    def get_single_range(self, file, file_size, start, end):
        self.send_response(206)
        length = end - start + 1
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header("Content-Length", "%d" % length)

        self.send_header("Content-Type", 'application/octet-stream')
        self.send_header("Content-Range", "bytes %d-%d/%d" % (start,
                                                              end,
                                                              file_size))
        self.end_headers()
        self.send_range_content(file, start, length)

    def get_multiple_ranges(self, file, file_size, ranges):
        self.send_response(206)
        self.send_header('Accept-Ranges', 'bytes')
        boundary = "%d" % random.randint(0,0x7FFFFFFF)
        self.send_header("Content-Type",
                         "multipart/byteranges; boundary=%s" % boundary)
        self.end_headers()
        for (start, end) in ranges:
            self.wfile.write("--%s\r\n" % boundary)
            self.send_header("Content-type", 'application/octet-stream')
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start,
                                                                  end,
                                                                  file_size))
            self.end_headers()
            self.send_range_content(file, start, end - start + 1)
            self.wfile.write("--%s\r\n" % boundary)
            pass

    def do_GET(self):
        """Serve a GET request.

        Handles the Range header.
        """

        path = self.translate_path(self.path)
        ranges_header_value = self.headers.get('Range')
        if ranges_header_value is None or os.path.isdir(path):
            # Let the mother class handle most cases
            return SimpleHTTPRequestHandler.do_GET(self)

        try:
            # Always read in binary mode. Opening files in text
            # mode may cause newline translations, making the
            # actual size of the content transmitted *less* than
            # the content-length!
            file = open(path, 'rb')
        except IOError:
            self.send_error(404, "File not found")
            return None

        file_size = os.fstat(file.fileno())[6]
        tail, ranges = self.parse_ranges(ranges_header_value)
        # Normalize tail into ranges
        if tail != 0:
            ranges.append((file_size - tail, file_size))

        ranges_valid = True
        if len(ranges) == 0:
            ranges_valid = False
        else:
            for (start, end) in ranges:
                if start >= file_size or end >= file_size:
                    ranges_valid = False
                    break
        if not ranges_valid:
            # RFC2616 14-16 says that invalid Range headers
            # should be ignored and in that case, the whole file
            # should be returned as if no Range header was
            # present
            file.close() # Will be reopened by the following call
            return SimpleHTTPRequestHandler.do_GET(self)

        if len(ranges) == 1:
            (start, end) = ranges[0]
            self.get_single_range(file, file_size, start, end)
        else:
            self.get_multiple_ranges(file, file_size, ranges)
        file.close()

    if sys.platform == 'win32':
        # On win32 you cannot access non-ascii filenames without
        # decoding them into unicode first.
        # However, under Linux, you can access bytestream paths
        # without any problems. If this function was always active
        # it would probably break tests when LANG=C was set
        def translate_path(self, path):
            """Translate a /-separated PATH to the local filename syntax.

            For bzr, all url paths are considered to be utf8 paths.
            On Linux, you can access these paths directly over the bytestream
            request, but on win32, you must decode them, and access them
            as Unicode files.
            """
            # abandon query parameters
            path = urlparse.urlparse(path)[2]
            path = posixpath.normpath(urllib.unquote(path))
            path = path.decode('utf-8')
            words = path.split('/')
            words = filter(None, words)
            path = os.getcwdu()
            for word in words:
                drive, word = os.path.splitdrive(word)
                head, word = os.path.split(word)
                if word in (os.curdir, os.pardir): continue
                path = os.path.join(path, word)
            return path


class TestingHTTPServer(BaseHTTPServer.HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, test_case):
        BaseHTTPServer.HTTPServer.__init__(self, server_address,
                                                RequestHandlerClass)
        self.test_case = test_case


class HttpServer(Server):
    """A test server for http transports.

    Subclasses can provide a specific request handler.
    """

    # used to form the url that connects to this server
    _url_protocol = 'http'

    # Subclasses can provide a specific request handler
    def __init__(self, request_handler=TestingHTTPRequestHandler):
        Server.__init__(self)
        self.request_handler = request_handler

    def _get_httpd(self):
        return TestingHTTPServer(('localhost', 0),
                                  self.request_handler,
                                  self)

    def _http_start(self):
        httpd = None
        httpd = self._get_httpd()
        host, port = httpd.socket.getsockname()
        self._http_base_url = '%s://localhost:%s/' % (self._url_protocol, port)
        self._http_starting.release()
        httpd.socket.settimeout(0.1)

        while self._http_running:
            try:
                httpd.handle_request()
            except socket.timeout:
                pass

    def _get_remote_url(self, path):
        path_parts = path.split(os.path.sep)
        if os.path.isabs(path):
            if path_parts[:len(self._local_path_parts)] != \
                   self._local_path_parts:
                raise BadWebserverPath(path, self.test_dir)
            remote_path = '/'.join(path_parts[len(self._local_path_parts):])
        else:
            remote_path = '/'.join(path_parts)

        self._http_starting.acquire()
        self._http_starting.release()
        return self._http_base_url + remote_path

    def log(self, format, *args):
        """Capture Server log output."""
        self.logs.append(format % args)

    def setUp(self):
        """See bzrlib.transport.Server.setUp."""
        self._home_dir = os.getcwdu()
        self._local_path_parts = self._home_dir.split(os.path.sep)
        self._http_starting = threading.Lock()
        self._http_starting.acquire()
        self._http_running = True
        self._http_base_url = None
        self._http_thread = threading.Thread(target=self._http_start)
        self._http_thread.setDaemon(True)
        self._http_thread.start()
        self._http_proxy = os.environ.get("http_proxy")
        if self._http_proxy is not None:
            del os.environ["http_proxy"]
        self.logs = []

    def tearDown(self):
        """See bzrlib.transport.Server.tearDown."""
        self._http_running = False
        self._http_thread.join()
        if self._http_proxy is not None:
            import os
            os.environ["http_proxy"] = self._http_proxy

    def get_url(self):
        """See bzrlib.transport.Server.get_url."""
        return self._get_remote_url(self._home_dir)

    def get_bogus_url(self):
        """See bzrlib.transport.Server.get_bogus_url."""
        # this is chosen to try to prevent trouble with proxies, weird dns,
        # etc
        return 'http://127.0.0.1:1/'


class HttpServer_urllib(HttpServer):
    """Subclass of HttpServer that gives http+urllib urls.

    This is for use in testing: connections to this server will always go
    through urllib where possible.
    """

    # urls returned by this server should require the urllib client impl
    _url_protocol = 'http+urllib'


class HttpServer_PyCurl(HttpServer):
    """Subclass of HttpServer that gives http+pycurl urls.

    This is for use in testing: connections to this server will always go
    through pycurl where possible.
    """

    # We don't care about checking the pycurl availability as
    # this server will be required only when pycurl is present

    # urls returned by this server should require the pycurl client impl
    _url_protocol = 'http+pycurl'
