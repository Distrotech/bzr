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


"""Topological sorting routines."""


import bzrlib.errors as errors


__all__ = ["topo_sort", "TopoSorter", "merge_sort", "MergeSorter"]


def topo_sort(graph):
    """Topological sort a graph.

    graph -- sequence of pairs of node->parents_list.

    The result is a list of node names, such that all parents come before
    their children.

    node identifiers can be any hashable object, and are typically strings.
    """
    return TopoSorter(graph).sorted()


class TopoSorter(object):

    def __init__(self, graph):
        """Topological sorting of a graph.
    
        :param graph: sequence of pairs of node_name->parent_names_list.
                      i.e. [('C', ['B']), ('B', ['A']), ('A', [])]
                      For this input the output from the sort or
                      iter_topo_order routines will be:
                      'A', 'B', 'C'
        
        node identifiers can be any hashable object, and are typically strings.

        If you have a graph like [('a', ['b']), ('a', ['c'])] this will only use
        one of the two values for 'a'.

        The graph is sorted lazily: until you iterate or sort the input is
        not processed other than to create an internal representation.

        iteration or sorting may raise GraphCycleError if a cycle is present 
        in the graph.
        """
        # a dict of the graph.
        self._graph = dict(graph)
        ### if debugging:
        # self._original_graph = dict(graph)
        
        # this is a stack storing the depth first search into the graph.
        self._node_name_stack = []
        # at each level of 'recursion' we have to check each parent. This
        # stack stores the parents we have not yet checked for the node at the 
        # matching depth in _node_name_stack
        self._pending_parents_stack = []
        # this is a set of the completed nodes for fast checking whether a
        # parent in a node we are processing on the stack has already been
        # emitted and thus can be skipped.
        self._completed_node_names = set()

    def sorted(self):
        """Sort the graph and return as a list.
        
        After calling this the sorter is empty and you must create a new one.
        """
        return list(self.iter_topo_order())

###        Useful if fiddling with this code.
###        # cross check
###        sorted_names = list(self.iter_topo_order())
###        for index in range(len(sorted_names)):
###            rev = sorted_names[index]
###            for left_index in range(index):
###                if rev in self.original_graph[sorted_names[left_index]]:
###                    print "revision in parent list of earlier revision"
###                    import pdb;pdb.set_trace()

    def iter_topo_order(self):
        """Yield the nodes of the graph in a topological order.
        
        After finishing iteration the sorter is empty and you cannot continue
        iteration.
        """
        while self._graph:
            # now pick a random node in the source graph, and transfer it to the
            # top of the depth first search stack.
            node_name, parents = self._graph.popitem()
            self._push_node(node_name, parents)
            while self._node_name_stack:
                # loop until this call completes.
                parents_to_visit = self._pending_parents_stack[-1]
                # if all parents are done, the revision is done
                if not parents_to_visit:
                    # append the revision to the topo sorted list
                    # all the nodes parents have been added to the output, now
                    # we can add it to the output.
                    yield self._pop_node()
                else:
                    while self._pending_parents_stack[-1]:
                        # recurse depth first into a single parent 
                        next_node_name = self._pending_parents_stack[-1].pop()
                        if next_node_name in self._completed_node_names:
                            # this parent was completed by a child on the
                            # call stack. skip it.
                            continue
                        # otherwise transfer it from the source graph into the
                        # top of the current depth first search stack.
                        try:
                            parents = self._graph.pop(next_node_name)
                        except KeyError:
                            # if the next node is not in the source graph it has
                            # already been popped from it and placed into the
                            # current search stack (but not completed or we would
                            # have hit the continue 4 lines up.
                            # this indicates a cycle.
                            raise errors.GraphCycleError(self._node_name_stack)
                        self._push_node(next_node_name, parents)
                        # and do not continue processing parents until this 'call' 
                        # has recursed.
                        break

    def _push_node(self, node_name, parents):
        """Add node_name to the pending node stack.
        
        Names in this stack will get emitted into the output as they are popped
        off the stack.
        """
        self._node_name_stack.append(node_name)
        self._pending_parents_stack.append(list(parents))

    def _pop_node(self):
        """Pop the top node off the stack 

        The node is appended to the sorted output.
        """
        # we are returning from the flattened call frame:
        # pop off the local variables
        node_name = self._node_name_stack.pop()
        self._pending_parents_stack.pop()

        self._completed_node_names.add(node_name)
        return node_name


def merge_sort(graph, branch_tip, mainline_revisions=None, generate_revno=False):
    """Topological sort a graph which groups merges.

    :param graph: sequence of pairs of node->parents_list.
    :param branch_tip: the tip of the branch to graph. Revisions not 
                       reachable from branch_tip are not included in the
                       output.
    :param mainline_revisions: If not None this forces a mainline to be
                               used rather than synthesised from the graph.
                               This must be a valid path through some part
                               of the graph. If the mainline does not cover all
                               the revisions, output stops at the start of the
                               old revision listed in the mainline revisions
                               list.
                               The order for this parameter is oldest-first.
    :param generate_revno: Optional parameter controlling the generation of
        revision number sequences in the output. See the output description of
        the MergeSorter docstring for details.
    :result: See the MergeSorter docstring for details.
    node identifiers can be any hashable object, and are typically strings.
    """
    return MergeSorter(graph, branch_tip, mainline_revisions,
        generate_revno).sorted()


class MergeSorter(object):

    def __init__(self, graph, branch_tip, mainline_revisions=None,
        generate_revno=False):
        """Merge-aware topological sorting of a graph.
    
        :param graph: sequence of pairs of node_name->parent_names_list.
                      i.e. [('C', ['B']), ('B', ['A']), ('A', [])]
                      For this input the output from the sort or
                      iter_topo_order routines will be:
                      'A', 'B', 'C'
        :param branch_tip: the tip of the branch to graph. Revisions not 
                       reachable from branch_tip are not included in the
                       output.
        :param mainline_revisions: If not None this forces a mainline to be
                               used rather than synthesised from the graph.
                               This must be a valid path through some part
                               of the graph. If the mainline does not cover all
                               the revisions, output stops at the start of the
                               old revision listed in the mainline revisions
                               list.
                               The order for this parameter is oldest-first.
        :param generate_revno: Optional parameter controlling the generation of
            revision number sequences in the output. See the output description
            for more details.

        The result is a list sorted so that all parents come before
        their children. Each element of the list is a tuple containing:
        (sequence_number, node_name, merge_depth, end_of_merge)
         * sequence_number: The sequence of this row in the output. Useful for 
           GUIs.
         * node_name: The node name: opaque text to the merge routine.
         * merge_depth: How many levels of merging deep this node has been
           found.
         * revno_sequence: When requested this field provides a sequence of
             revision numbers for all revisions. The format is:
             REVNO[[.BRANCHREVNO.REVNO] ...]. BRANCHREVNO is the number of the
             branch that the revno is on. From left to right the REVNO numbers
             are the sequence numbers within that branch of the revision.
             For instance, the graph {A:[], B:['A'], C:['A', 'B']} will get
             the following revno_sequences assigned: A:(1,), B:(1,1,1), C:(2,).
             This should be read as 'A is the first commit in the trunk',
             'B is the first commit on the first branch made from A', 'C is the
             second commit in the trunk'.
         * end_of_merge: When True the next node is part of a different merge.

        
        node identifiers can be any hashable object, and are typically strings.

        If you have a graph like [('a', ['b']), ('a', ['c'])] this will only use
        one of the two values for 'a'.

        The graph is sorted lazily: until you iterate or sort the input is
        not processed other than to create an internal representation.

        iteration or sorting may raise GraphCycleError if a cycle is present 
        in the graph.

        Background information on the design:
        -------------------------------------
        definition: the end of any cluster or 'merge' occurs when:
            1 - the next revision has a lower merge depth than we do.
              i.e.
              A 0
              B  1
              C   2
              D  1
              E 0
              C, D are the ends of clusters, E might be but we need more data.
            2 - or the next revision at our merge depth is not our left most
              ancestor.
              This is required to handle multiple-merges in one commit.
              i.e.
              A 0    [F, B, E]
              B  1   [D, C]
              C   2  [D]
              D  1   [F]
              E  1   [F]
              F 0
              C is the end of a cluster due to rule 1.
              D is not the end of a cluster from rule 1, but is from rule 2: E 
                is not its left most ancestor
              E is the end of a cluster due to rule 1
              F might be but we need more data.
              
        we show connecting lines to a parent when:
         - The parent is the start of a merge within this cluster.
           That is, the merge was not done to the mainline before this cluster 
           was merged to the mainline.
           This can be detected thus:
            * The parent has a higher merge depth and is the next revision in 
              the list.
          
          The next revision in the list constraint is needed for this case:
          A 0   [D, B]   
          B  1  [C, F]   # we do not want to show a line to F which is depth 2 
                           but not a merge
          C  1  [H]      # note that this is a long line to show back to the 
                           ancestor - see the end of merge rules.
          D 0   [G, E]
          E  1  [G, F]
          F   2 [G]
          G  1  [H]
          H 0
         - Part of this merges 'branch':
          The parent has the same merge depth and is our left most parent and we
           are not the end of the cluster.
          A 0   [C, B] lines: [B, C]
          B  1  [E, C] lines: [C]
          C 0   [D]    lines: [D]
          D 0   [F, E] lines: [E, F]
          E  1  [F]    lines: [F]
          F 0
         - The end of this merge/cluster:
          we can ONLY have multiple parents at the end of a cluster if this
          branch was previously merged into the 'mainline'.
          - if we have one and only one parent, show it
            Note that this may be to a greater merge depth - for instance if
            this branch continued from a deeply nested branch to add something
            to it.
          - if we have more than one parent - show the second oldest (older ==
            further down the list) parent with
            an equal or lower merge depth
             XXXX revisit when awake. ddaa asks about the relevance of each one
             - maybe more than one parent is relevant
        """
        self._generate_revno = generate_revno
        # a dict of the graph.
        self._graph = dict(graph)
        # if there is an explicit mainline, alter the graph to match. This is
        # easier than checking at every merge whether we are on the mainline and
        # if so which path to take.
        if mainline_revisions is None:
            self._mainline_revisions = []
            self._stop_revision = None
        else:
            self._mainline_revisions = list(mainline_revisions)
            self._stop_revision = self._mainline_revisions[0]
        # skip the first revision, its what we reach and its parents are 
        # therefore irrelevant
        for index, revision in enumerate(self._mainline_revisions[1:]):
            # NB: index 0 means self._mainline_revisions[1]
            # if the mainline matches the graph, nothing to do.
            parent = self._mainline_revisions[index]
            if parent is None:
                # end of mainline_revisions history
                continue
            if self._graph[revision][0] == parent:
                continue
            # remove it from its prior spot
            self._graph[revision].remove(parent)
            # insert it into the start of the mainline
            self._graph[revision].insert(0, parent)
        # we need to do a check late in the process to detect end-of-merges
        # which requires the parents to be accessible: its easier for now
        # to just keep the original graph around.
        self._original_graph = dict(self._graph.items())
        # we need to know the revision numbers of revisions to determine
        # the revision numbers of their descendants
        # this is a graph from node to [revno_tuple, sequence_number]
        # where sequence is the number of branches made from the node,
        # and revno_tuple is the tuple that was assigned to the node.
        # we dont know revnos to start with, so we start it seeded with
        # [None, 0]
        self._revnos = dict((revision, [None, 0]) for revision in self._graph)
        # the global implicit root node has revno 0, but we need to know
        # the sequence number for it too:
        self._root_sequence = 0
        
        # this is a stack storing the depth first search into the graph.
        self._node_name_stack = []
        # at each level of recursion we need the merge depth this node is at:
        self._node_merge_depth_stack = []
        # at each level of 'recursion' we have to check each parent. This
        # stack stores the parents we have not yet checked for the node at the 
        # matching depth in _node_name_stack
        self._pending_parents_stack = []
        # When we first look at a node we assign it a seqence number from its
        # leftmost parent.
        self._assigned_sequence_stack = []
        # this is a set of the nodes who have been completely analysed for fast
        # membership checking
        self._completed_node_names = set()
        # this is the scheduling of nodes list.
        # Nodes are scheduled
        # from the bottom left of the tree: in the tree
        # A 0  [D, B]
        # B  1 [C]
        # C  1 [D]
        # D 0  [F, E]
        # E  1 [F]
        # F 0
        # the scheduling order is: F, E, D, C, B, A 
        # that is - 'left subtree, right subtree, node'
        # which would mean that when we schedule A we can emit the entire tree.
        self._scheduled_nodes = []
        # This records for each node when we have processed its left most 
        # unmerged subtree. After this subtree is scheduled, all other subtrees
        # have their merge depth increased by one from this nodes merge depth.
        # it contains tuples - name, merge_depth
        self._left_subtree_pushed_stack = []

        # seed the search with the tip of the branch
        if branch_tip is not None:
            parents = self._graph.pop(branch_tip)
            self._push_node(branch_tip, 0, parents)

    def sorted(self):
        """Sort the graph and return as a list.
        
        After calling this the sorter is empty and you must create a new one.
        """
        return list(self.iter_topo_order())

    def iter_topo_order(self):
        """Yield the nodes of the graph in a topological order.
        
        After finishing iteration the sorter is empty and you cannot continue
        iteration.
        """
        while self._node_name_stack:
            # loop until this call completes.
            parents_to_visit = self._pending_parents_stack[-1]
            # if all parents are done, the revision is done
            if not parents_to_visit:
                # append the revision to the topo sorted scheduled list:
                # all the nodes parents have been scheduled added, now
                # we can add it to the output.
                self._pop_node()
            else:
                while self._pending_parents_stack[-1]:
                    if not self._left_subtree_pushed_stack[-1]:
                        # recurse depth first into the primary parent
                        next_node_name = self._pending_parents_stack[-1].pop(0)
                    else:
                        # place any merges in right-to-left order for scheduling
                        # which gives us left-to-right order after we reverse
                        # the scheduled queue. XXX: This has the effect of 
                        # allocating common-new revisions to the right-most
                        # subtree rather than the left most, which will 
                        # display nicely (you get smaller trees at the top
                        # of the combined merge).
                        next_node_name = self._pending_parents_stack[-1].pop()
                    if next_node_name in self._completed_node_names:
                        # this parent was completed by a child on the
                        # call stack. skip it.
                        continue
                    # otherwise transfer it from the source graph into the
                    # top of the current depth first search stack.
                    try:
                        parents = self._graph.pop(next_node_name)
                    except KeyError:
                        # if the next node is not in the source graph it has
                        # already been popped from it and placed into the
                        # current search stack (but not completed or we would
                        # have hit the continue 4 lines up.
                        # this indicates a cycle.
                        raise errors.GraphCycleError(self._node_name_stack)
                    next_merge_depth = 0
                    if self._left_subtree_pushed_stack[-1]:
                        # a new child branch from name_stack[-1]
                        next_merge_depth = 1
                    else:
                        next_merge_depth = 0
                        self._left_subtree_pushed_stack[-1] = True
                    next_merge_depth = (
                        self._node_merge_depth_stack[-1] + next_merge_depth)
                    self._push_node(
                        next_node_name,
                        next_merge_depth,
                        parents)
                    # and do not continue processing parents until this 'call' 
                    # has recursed.
                    break
        # We have scheduled the graph. Now deliver the ordered output:
        sequence_number = 0
        while self._scheduled_nodes:
            node_name, merge_depth, revno = self._scheduled_nodes.pop()
            if node_name == self._stop_revision:
                return
            if not len(self._scheduled_nodes):
                # last revision is the end of a merge
                end_of_merge = True
            elif self._scheduled_nodes[-1][1] < merge_depth:
                # the next node is to our left
                end_of_merge = True
            elif (self._scheduled_nodes[-1][1] == merge_depth and
                  (self._scheduled_nodes[-1][0] not in
                   self._original_graph[node_name])):
                # the next node was part of a multiple-merge.
                end_of_merge = True
            else:
                end_of_merge = False
            if self._generate_revno:
                yield (sequence_number, node_name, merge_depth, revno, end_of_merge)
            else:
                yield (sequence_number, node_name, merge_depth, end_of_merge)
            sequence_number += 1

    def _push_node(self, node_name, merge_depth, parents):
        """Add node_name to the pending node stack.
        
        Names in this stack will get emitted into the output as they are popped
        off the stack.
        """
        self._node_name_stack.append(node_name)
        self._node_merge_depth_stack.append(merge_depth)
        self._left_subtree_pushed_stack.append(False)
        self._pending_parents_stack.append(list(parents))
        # as we push it, assign it a sequence number against its parent:
        parents = self._original_graph[node_name]
        if parents:
            # node has parents, assign from the left most parent.
            parent_revno = self._revnos[parents[0]]
            sequence = parent_revno[1]
            parent_revno[1] += 1
        else:
            # no parents, use the root sequence
            sequence = self._root_sequence
            self._root_sequence +=1
        self._assigned_sequence_stack.append(sequence)

    def _pop_node(self):
        """Pop the top node off the stack 

        The node is appended to the sorted output.
        """
        # we are returning from the flattened call frame:
        # pop off the local variables
        node_name = self._node_name_stack.pop()
        merge_depth = self._node_merge_depth_stack.pop()
        sequence = self._assigned_sequence_stack.pop()
        # remove this node from the pending lists:
        self._left_subtree_pushed_stack.pop()
        self._pending_parents_stack.pop()

        parents = self._original_graph[node_name]
        if parents:
            # node has parents, assign from the left most parent.
            parent_revno = self._revnos[parents[0]]
            if sequence:
                # not the first child, make a new branch
                revno = parent_revno[0] + (sequence, 1)
            else:
                # increment the sequence number within the branch
                revno = parent_revno[0][:-1] + (parent_revno[0][-1] + 1,)
        else:
            # no parents, use the root sequence
            if sequence:
                # make a parallel import revision number
                revno = (0, sequence, 1)
            else:
                revno = (1,)

        # store the revno for this node for future reference
        self._revnos[node_name][0] = revno
        self._completed_node_names.add(node_name)
        self._scheduled_nodes.append((node_name, merge_depth, self._revnos[node_name][0]))
        return node_name
