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

"""Test that WorkingTrees don't fail if they are in a readonly dir."""

import os
import sys
import time

from bzrlib import (
    errors,
    hashcache,
    tests,
    )
from bzrlib.tests.workingtree_implementations import TestCaseWithWorkingTree


class TestReadonly(TestCaseWithWorkingTree):

    def setUp(self):
        if not self.platform_supports_readonly_dirs():
            raise tests.TestSkipped('platform does not support readonly'
                                    ' directories.')
        super(TestReadonly, self).setUp()

    def platform_supports_readonly_dirs(self):
        if sys.platform in ('win32', 'cygwin'):
            # Setting a directory to readonly in windows or cygwin doesn't seem
            # to have any effect. You can still create files in subdirectories.
            # TODO: jam 20061219 We could cheat and set just the hashcache file
            #       to readonly, which would make it fail when we try to delete
            #       or rewrite it. But that is a lot of cheating...
            return False
        return True

    def _set_all_dirs(self, basedir, readonly=True):
        """Recursively set all directories beneath this one."""
        if readonly:
            mode = 0555
        else:
            mode = 0755

        for root, dirs, files in os.walk(basedir, topdown=False):
            for d in dirs:
                path = os.path.join(root, d)
                os.chmod(path, mode)

    def set_dirs_readonly(self, basedir):
        """Set all directories readonly, and have it cleanup on test exit."""
        self._set_all_dirs(basedir, readonly=True)

        def cleanup():
            self._set_all_dirs(basedir, readonly=False)

        self.addCleanup(cleanup)

    def create_basic_tree(self):
        tree = self.make_branch_and_tree('tree')
        self.build_tree(['tree/a', 'tree/b/', 'tree/b/c'])
        tree.add(['a', 'b', 'b/c'])
        tree.commit('creating an initial tree.')
        return tree

    def _custom_cutoff_time(self):
        """We need to fake the cutoff time."""
        return time.time() + 10

    def test_readonly_unclean(self):
        """Even if the tree is unclean, we should still handle readonly dirs."""
        # First create a tree
        tree = self.create_basic_tree()

        # XXX: *Ugly* *ugly* hack, we need the hashcache to think it is out of
        # date, but we don't want to actually wait 3 seconds doing nothing.

        the_hashcache = getattr(tree, '_hashcache')
        if (the_hashcache is not None
            and isinstance(the_hashcache, hashcache.HashCache)):
            the_hashcache._cutoff_time = self._custom_cutoff_time

        # Make it a little dirty
        self.build_tree_contents([('tree/a', 'new contents of a\n')])

        # Make it readonly, and do some operations and then unlock
        self.set_dirs_readonly('tree')

        tree.lock_read()
        try:
            # Make sure we check all the files
            for file_id in tree:
                size = tree.get_file_size(file_id)
                sha1 = tree.get_file_sha1(file_id)
        finally:
            tree.unlock()
