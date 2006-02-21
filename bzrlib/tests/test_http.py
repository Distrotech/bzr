# (C) 2005 Canonical

import bzrlib
from bzrlib.tests import TestCase
from bzrlib.tests.HTTPTestUtil import TestCaseWithWebserver
from bzrlib.transport.http import HttpTransport, extract_auth

class FakeManager (object):
    def __init__(self):
        self.credentials = []
        
    def add_password(self, realm, host, username, password):
        self.credentials.append([realm, host, username, password])


class TestHttpUrls(TestCase):
    def test_url_parsing(self):
        f = FakeManager()
        url = extract_auth('http://example.com', f)
        self.assertEquals('http://example.com', url)
        self.assertEquals(0, len(f.credentials))
        url = extract_auth('http://user:pass@www.bazaar-ng.org/bzr/bzr.dev', f)
        self.assertEquals('http://www.bazaar-ng.org/bzr/bzr.dev', url)
        self.assertEquals(1, len(f.credentials))
        self.assertEquals([None, 'www.bazaar-ng.org', 'user', 'pass'], f.credentials[0])
        
    def test_abs_url(self):
        """Construction of absolute http URLs"""
        t = HttpTransport('http://bazaar-ng.org/bzr/bzr.dev/')
        eq = self.assertEqualDiff
        eq(t.abspath('.'),
           'http://bazaar-ng.org/bzr/bzr.dev')
        eq(t.abspath('foo/bar'), 
           'http://bazaar-ng.org/bzr/bzr.dev/foo/bar')
        eq(t.abspath('.bzr'),
           'http://bazaar-ng.org/bzr/bzr.dev/.bzr')
        eq(t.abspath('.bzr/1//2/./3'),
           'http://bazaar-ng.org/bzr/bzr.dev/.bzr/1/2/3')

    def test_invalid_http_urls(self):
        """Trap invalid construction of urls"""
        t = HttpTransport('http://bazaar-ng.org/bzr/bzr.dev/')
        self.assertRaises(ValueError,
            t.abspath,
            '.bzr/')
        self.assertRaises(ValueError,
            t.abspath,
            '/.bzr')

    def test_http_root_urls(self):
        """Construction of URLs from server root"""
        t = HttpTransport('http://bzr.ozlabs.org/')
        eq = self.assertEqualDiff
        eq(t.abspath('.bzr/tree-version'),
           'http://bzr.ozlabs.org/.bzr/tree-version')


class TestHttpConnections(TestCaseWithWebserver):

    def setUp(self):
        super(TestHttpConnections, self).setUp()
        self.build_tree(['xxx', 'foo/', 'foo/bar'], line_endings='binary')

    def test_http_has(self):
        t = HttpTransport(self.server.get_url())
        self.assertEqual(t.has('foo/bar'), True)
        self.assertEqual(len(self.server.logs), 1)
        self.assertTrue(self.server.logs[0].endswith(
            '"HEAD /foo/bar HTTP/1.1" 200 - "-" "bzr/%s"'
            % bzrlib.__version__))

        self.assertEqual(t.has('not-found'), False)
        self.assertTrue(self.server.logs[-1].endswith(
            '"HEAD /not-found HTTP/1.1" 404 - "-" "bzr/%s"'
            % bzrlib.__version__))

    def test_http_get(self):
        t = HttpTransport(self.server.get_url())
        fp = t.get('foo/bar')
        self.assertEqualDiff(
            fp.read(),
            'contents of foo/bar\n')
        self.assertEqual(len(self.server.logs), 1)
        self.assertTrue(self.server.logs[0].endswith(
            '"GET /foo/bar HTTP/1.1" 200 - "-" "bzr/%s"' % bzrlib.__version__))
