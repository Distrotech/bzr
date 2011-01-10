# Copyright (C) 2006-2011 Canonical Ltd
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

"""Test read_bundle works properly across various transports."""

import cStringIO
import os

import bzrlib.bundle
from bzrlib.bundle.serializer import write_bundle
import bzrlib.bzrdir
import bzrlib.errors as errors
from bzrlib.symbol_versioning import deprecated_in
from bzrlib import tests
from bzrlib.tests.test_transport import TestTransportImplementation
from bzrlib.tests.per_transport import transport_test_permutations
import bzrlib.transport
from bzrlib.transport.memory import MemoryTransport
import bzrlib.urlutils
from bzrlib.tests.scenarios import load_tests_apply_scenarios


load_tests = load_tests_apply_scenarios


def create_bundle_file(test_case):
    test_case.build_tree(['tree/', 'tree/a', 'tree/subdir/'])

    format = bzrlib.bzrdir.BzrDirFormat.get_default_format()

    bzrdir = format.initialize('tree')
    repo = bzrdir.create_repository()
    branch = repo.bzrdir.create_branch()
    wt = branch.bzrdir.create_workingtree()

    wt.add(['a', 'subdir/'])
    wt.commit('new project', rev_id='commit-1')

    out = cStringIO.StringIO()
    rev_ids = write_bundle(wt.branch.repository,
                           wt.get_parent_ids()[0], 'null:', out)
    out.seek(0)
    return out, wt


class TestReadMergeableBundleFromURL(TestTransportImplementation):
    """Test that read_bundle works properly across multiple transports"""

    scenarios = transport_test_permutations()

    def setUp(self):
        super(TestReadMergeableBundleFromURL, self).setUp()
        self.bundle_name = 'test_bundle'
        # read_mergeable_from_url will invoke get_transport which may *not*
        # respect self._transport (i.e. returns a transport that is different
        # from the one we want to test, so we must inject a correct transport
        # into possible_transports first).
        self.possible_transports = [self.get_transport(self.bundle_name)]
        self.overrideEnv('BZR_NO_SMART_VFS', None)
        wt = self.create_test_bundle()

    def read_mergeable_from_url(self, url):
        return bzrlib.bundle.read_mergeable_from_url(
            url, possible_transports=self.possible_transports)

    def get_url(self, relpath=''):
        return bzrlib.urlutils.join(self._server.get_url(), relpath)

    def create_test_bundle(self):
        out, wt = create_bundle_file(self)
        if self.get_transport().is_readonly():
            self.build_tree_contents([(self.bundle_name, out.getvalue())])
        else:
            self.get_transport().put_file(self.bundle_name, out)
            self.log('Put to: %s', self.get_url(self.bundle_name))
        return wt

    def test_read_mergeable_from_url(self):
        info = self.read_mergeable_from_url(
            unicode(self.get_url(self.bundle_name)))
        revision = info.real_revisions[-1]
        self.assertEqual('commit-1', revision.revision_id)

    def test_read_fail(self):
        # Trying to read from a directory, or non-bundle file
        # should fail with NotABundle
        self.assertRaises(errors.NotABundle,
                          self.read_mergeable_from_url, self.get_url('tree'))
        self.assertRaises(errors.NotABundle,
                          self.read_mergeable_from_url, self.get_url('tree/a'))

    def test_read_mergeable_respects_possible_transports(self):
        if not isinstance(self.get_transport(self.bundle_name),
                          bzrlib.transport.ConnectedTransport):
            # There is no point testing transport reuse for not connected
            # transports (the test will fail even).
            raise tests.TestSkipped(
                'Need a ConnectedTransport to test transport reuse')
        url = unicode(self.get_url(self.bundle_name))
        info = self.read_mergeable_from_url(url)
        self.assertEqual(1, len(self.possible_transports))
