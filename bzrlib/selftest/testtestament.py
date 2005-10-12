# Copyright (C) 2005 by Canonical Ltd
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

"""Test testaments for gpg signing."""

import os
import sys

from bzrlib.selftest import TestCaseInTempDir
from bzrlib.branch import Branch
from bzrlib.testament import Testament
from bzrlib.trace import mutter

class TestamentTests(TestCaseInTempDir):
    def setUp(self):
        super(TestamentTests, self).setUp()
        b = self.b = Branch.initialize('.')
        b.commit(message='initial null commit',
                 committer='test@user',
                 timestamp=1129025423, # 'Tue Oct 11 20:10:23 2005'
                 timezone=0,
                 rev_id='test@user-1')

    def test_null_testament(self):
        """Testament for a revision with no contents."""
        t = Testament.from_revision(self.b, 'test@user-1')
        ass = self.assertTrue
        eq = self.assertEqual
        ass(isinstance(t, Testament))
        eq(t.revision_id, 'test@user-1')
        eq(t.committer, 'test@user')
        eq(t.timestamp, 1129025423)
        eq(t.timezone, 0)

    def test_testment_text_form(self):
        """Conversion of testament to canonical text form."""
        t = Testament.from_revision(self.b, 'test@user-1')
        text_form = t.to_text_form_1()
        self.log('testament text form:\n' + text_form)
        expect = """\
bazaar-ng testament version 1
revision-id: test@user-1
committer: test@user
timestamp: 1129025423.0
timezone: 0
entries: 0
message:
  initial null commit
"""
        self.assertEqual(text_form, expect)


