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

"""Tests for ssh transport"""

# all of this deals with byte strings so this is safe
from cStringIO import StringIO

import bzrlib
import bzrlib.tests as tests
import bzrlib.transport.ssh as ssh
from bzrlib.transport import local, memory

class TestSSHTransport(tests.TestCase):
    
    def test_loopback_ssh_connection_exists(self):
        ssh.LoopbackSSHConnection()

    def test_ssh_query_version(self):
        """Feed a canned query version to a server"""
        to_server = StringIO('hello\0011\n')
        from_server = StringIO()
        server = ssh.Server(to_server, from_server, local.LocalTransport('file:///'))
        server._serve_one_request()
        self.assertEqual('bzr server\0011\n',
                         from_server.getvalue())

    def test_canned_get_response(self):
        transport = memory.MemoryTransport('memory:///')
        transport.put('hello', StringIO('contents\nof\nfile\n'))
        to_server = StringIO('get\001./hello\n')
        from_server = StringIO()
        server = ssh.Server(to_server, from_server, transport)
        server._serve_one_request()
        self.assertEqual('ok\n'
                         '17\n'
                         'contents\nof\nfile\n'
                         'done\n',
                         from_server.getvalue())

    def test_open_loopback_server(self):
        conn = ssh.LoopbackSSHConnection()
        version = conn.query_version()
        self.assertEqual(1, version)

    def test_server_shutdown_on_client_disconnect(self):
        conn = ssh.LoopbackSSHConnection()
        conn.disconnect()
        conn._server_thread.join()
        self.assertFalse(conn._server_thread.isAlive())

    def test_multiple_requests(self):
        conn = ssh.LoopbackSSHConnection()
        version = conn.query_version()
        self.assertEqual(1, version)
        version = conn.query_version()
        self.assertEqual(1, version)

    def test_ssh_transport_has(self):
        """Checking for file existence over ssh."""
        conn = ssh.LoopbackSSHConnection()
        conn.backing_transport.put("foo", StringIO("contents of foo\n"))
        self.assertTrue(conn.has("foo"))
        self.assertFalse(conn.has("non-foo"))

    def test_ssh_transport_get(self):
        """Read back a file over ssh."""
        conn = ssh.LoopbackSSHConnection()
        conn.backing_transport.put("foo", StringIO("contents\nof\nfoo\n"))
        fp = conn.get("foo")
        self.assertEqual('contents\nof\nfoo\n', fp.read())
        
    def test_get_error(self):
        """Error reported from server getting nonexistent file."""
        return
        conn = ssh.LoopbackSSHConnection()
        fp = conn.get("non-foo")
        # self.assertEqual('contents of foo\n', fp.read())

    # TODO: Try sending multiple requests; they should all get answers.

    # TODO: If the server raises an error within its processing that should be
    # caught and propagated back to the client.
