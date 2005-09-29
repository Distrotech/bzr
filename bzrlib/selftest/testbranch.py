# (C) 2005 Canonical Ltd

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

import os
from bzrlib.selftest import TestCaseInTempDir
from bzrlib.branch import Branch, copy_stores, copy_branch
from bzrlib.commit import commit
from bzrlib.errors import NoSuchRevision, UnlistableBranch

class TestBranch(TestCaseInTempDir):

    def test_append_revisions(self):
        """Test appending more than one revision"""
        br = Branch.initialize(".")
        br.append_revision("rev1")
        self.assertEquals(br.revision_history(), ["rev1",])
        br.append_revision("rev2", "rev3")
        self.assertEquals(br.revision_history(), ["rev1", "rev2", "rev3"])

    def test_copy_stores(self):
        """Copy the stores from one branch to another"""
        os.mkdir('a')
        br_a = Branch.initialize("a")
        file('a/b', 'wb').write('b')
        br_a.add('b')
        commit(br_a, "silly commit")

        os.mkdir('b')
        br_b = Branch.initialize("b")
        self.assertRaises(NoSuchRevision, br_b.get_revision, 
                          br_a.revision_history()[0])
        copy_stores(br_a, br_b)
        rev = br_b.get_revision(br_a.revision_history()[0])
        tree = br_b.revision_tree(br_a.revision_history()[0])
        for file_id in tree:
            if tree.inventory[file_id].kind == "file":
                tree.get_file(file_id).read()
        return br_a, br_b

    def test_copy_branch(self):
        """Copy the stores from one branch to another"""
        br_a, br_b = self.test_copy_stores()
        commit(br_b, "silly commit")
        os.mkdir('c')
        br_c = copy_branch(br_a, 'c', basis_branch=br_b)
        self.assertEqual(br_a.revision_history(), br_c.revision_history())
        assert br_b.last_patch() not in br_c.revision_history()
        br_c.get_revision(br_b.last_patch())
# TODO: rewrite this as a regular unittest, without relying on the displayed output        
#         >>> from bzrlib.commit import commit
#         >>> bzrlib.trace.silent = True
#         >>> br1 = ScratchBranch(files=['foo', 'bar'])
#         >>> br1.add('foo')
#         >>> br1.add('bar')
#         >>> commit(br1, "lala!", rev_id="REVISION-ID-1", verbose=False)
#         >>> br2 = ScratchBranch()
#         >>> br2.update_revisions(br1)
#         Added 2 texts.
#         Added 1 inventories.
#         Added 1 revisions.
#         >>> br2.revision_history()
#         [u'REVISION-ID-1']
#         >>> br2.update_revisions(br1)
#         Added 0 revisions.
#         >>> br1.text_store.total_size() == br2.text_store.total_size()
#         True
