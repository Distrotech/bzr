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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA


"""InterTree implementation tests for bzr.

These test the conformance of all the InterTree variations to the expected API.
Specific tests for individual variations are in other places such as:
 - tests/test_workingtree.py
"""

import bzrlib
import bzrlib.errors as errors
from bzrlib.transport import get_transport
from bzrlib.transform import TransformPreview
from bzrlib.tests import (
    default_transport,
    multiply_tests,
    )
from bzrlib.tests.tree_implementations import (
    return_parameter,
    revision_tree_from_workingtree,
    TestCaseWithTree,
    )
from bzrlib.tree import InterTree
from bzrlib.workingtree import (
    WorkingTreeFormat3,
    )


def return_provided_trees(test_case, source, target):
    """Return the source and target tree unaltered."""
    return source, target


class TestCaseWithTwoTrees(TestCaseWithTree):

    def make_to_branch_and_tree(self, relpath):
        """Make a to_workingtree_format branch and tree."""
        made_control = self.make_bzrdir(relpath,
            format=self.workingtree_format_to._matchingbzrdir)
        made_control.create_repository()
        made_control.create_branch()
        return self.workingtree_format_to.initialize(made_control)


def make_scenarios(transport_server, transport_readonly_server, formats):
    """Transform the input formats to a list of scenarios.

    :param formats: A list of tuples:.
        (intertree_class,
         workingtree_format,
         workingtree_format_to,
         mutable_trees_to_test_trees)
    """
    result = []
    for (label, intertree_class,
        workingtree_format,
        workingtree_format_to,
        mutable_trees_to_test_trees) in formats:
        scenario = (label, {
            "transport_server": transport_server,
            "transport_readonly_server": transport_readonly_server,
            "bzrdir_format":workingtree_format._matchingbzrdir,
            "workingtree_format":workingtree_format,
            "intertree_class":intertree_class,
            "workingtree_format_to":workingtree_format_to,
            # mutable_trees_to_test_trees takes two trees and converts them to,
            # whatever relationship the optimiser under test requires.,
            "mutable_trees_to_test_trees":mutable_trees_to_test_trees,
            # workingtree_to_test_tree is set to disable changing individual,
            # trees: instead the mutable_trees_to_test_trees helper is used.,
            "_workingtree_to_test_tree": return_parameter,
            })
        result.append(scenario)
    return result


def mutable_trees_to_preview_trees(test_case, source, target):
    preview = TransformPreview(target)
    test_case.addCleanup(preview.finalize)
    return source, preview.get_preview_tree()


def load_tests(standard_tests, module, loader):
    default_tree_format = WorkingTreeFormat3()
    submod_tests = loader.loadTestsFromModuleNames([
        'bzrlib.tests.intertree_implementations.test_compare',
        ])
    test_intertree_permutations = [
        # test InterTree with two default-format working trees.
        (InterTree.__name__, InterTree, default_tree_format, default_tree_format,
         return_provided_trees)]
    for optimiser in InterTree._optimisers:
        if optimiser is bzrlib.workingtree_4.InterDirStateTree:
            # Its a little ugly to be conditional here, but less so than having
            # the optimiser listed twice.
            # Add once, compiled version
            test_intertree_permutations.append(
                (optimiser.__name__ + "(C)",
                 optimiser,
                 optimiser._matching_from_tree_format,
                 optimiser._matching_to_tree_format,
                 optimiser.make_source_parent_tree_compiled_dirstate))
            # python version
            test_intertree_permutations.append(
                (optimiser.__name__ + "(PY)",
                 optimiser,
                 optimiser._matching_from_tree_format,
                 optimiser._matching_to_tree_format,
                 optimiser.make_source_parent_tree_python_dirstate))
        else:
            test_intertree_permutations.append(
                (optimiser.__name__,
                 optimiser,
                 optimiser._matching_from_tree_format,
                 optimiser._matching_to_tree_format,
                 optimiser._test_mutable_trees_to_test_trees))
    # PreviewTree does not have an InterTree optimiser class.
    test_intertree_permutations.append(
        (InterTree.__name__ + "(PreviewTree)",
         InterTree,
         default_tree_format,
         default_tree_format,
         mutable_trees_to_preview_trees))
    scenarios = make_scenarios(
        default_transport,
        # None here will cause a readonly decorator to be created
        # by the TestCaseWithTransport.get_readonly_transport method.
        None,
        test_intertree_permutations)
    # add the tests for the sub modules to the standard tests.
    return multiply_tests(submod_tests, scenarios, standard_tests)
