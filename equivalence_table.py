# Copyright (C) 2008 Canonical Limited.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as published
# by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA
#

"""Functions for dealing with a persistent equivalency table."""


SENTINEL = -1


class EquivalenceTable(object):
    """This class tracks equivalencies between lists of hashable objects.

    :ivar lines: The 'static' lines that will be preserved between runs.
    :ival _matching_lines: A dict of {line:[matching offsets]}
    """

    def __init__(self, lines):
        self.lines = lines
        self._right_lines = None
        # For each line in 'left' give the offset to the other lines which
        # match it.
        self._generate_matching_lines()

    def _generate_matching_lines(self):
        matches = {}
        for idx, line in enumerate(self.lines):
            matches.setdefault(line, []).append(idx)
        self._matching_lines = matches

    def _update_matching_lines(self, new_lines, index):
        matches = self._matching_lines
        start_idx = len(self.lines)
        for idx, do_index in enumerate(index):
            if not do_index:
                continue
            matches.setdefault(new_lines[idx], []).append(start_idx + idx)

    def get_matches(self, line):
        """Return the lines which match the line in right."""
        try:
            return self._matching_lines[line]
        except KeyError:
            return None

    def _get_matching_lines(self):
        """Return a dictionary showing matching lines."""
        matching = {}
        for line in self.lines:
            matching[line] = self.get_matches(line)
        return matching

    def get_idx_matches(self, right_idx):
        """Return the left lines matching the right line at the given offset."""
        line = self._right_lines[right_idx]
        try:
            return self._matching_lines[line]
        except KeyError:
            return None

    def extend_lines(self, lines, index):
        """Add more lines to the left-lines list.

        :param lines: A list of lines to add
        :param index: A True/False for each node to define if it should be
            indexed.
        """
        self._update_matching_lines(lines, index)
        self.lines.extend(lines)

    def set_right_lines(self, lines):
        """Set the lines we will be matching against."""
        self._right_lines = lines
