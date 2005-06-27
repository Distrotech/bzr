#! /usr/bin/python

# Copyright (C) 2005 Canonical Ltd

# GNU GPL v2

# Author: Martin Pool <mbp@canonical.com>


"""knit - a weave-like structure"""


class Knit(object):
    """knit - versioned text file storage.
    
    A Knit manages versions of line-based text files, keeping track of the
    originating version for each line.

    Texts can be identified in either of two ways:

    * a nonnegative index number.

    * a version-id string.

    Typically the index number will be valid only inside this knit and
    the version-id is used to reference it in the larger world.

    _l
        List of edit instructions.

        Each line is stored as a tuple of (index-id, text).  The line
        is present in the version equal to index-id.

    _v
        List of versions, indexed by index number.

        For each version we store the tuple (included_versions), which
        lists the previous versions also considered active.
    """
    def __init__(self):
        self._l = []
        self._v = []

        
    def add(self, text):
        """Add a single text on top of the weave.

        Returns the index number of the newly added version."""
        if not isinstance(text, list):
            raise ValueError("text should be a list, not %s" % type(text))

        idx = len(self._v)

        # all of the previous texts are turned off; just append lines at the bottom
        for line in text:
            self._l.append((idx, line))

        included = ()
        vers_info = (included,)
        self._v.append(vers_info)
        return idx

    
    def annotate(self, index):
        return list(self.annotate_iter(index))


    def annotate_iter(self, index):
        """Yield list of (index-id, line) pairs for the specified version.

        The index indicates when the line originated in the weave."""
        vers_info = self._v[index]

        included = set(vers_info[0])
        included.add(index)

        for origin, line in self._l:
            if origin in included:
                yield origin, line


    def getiter(self, index):
        """Yield lines for the specified version."""
        for origin, line in self.annotate_iter(index):
            yield line


    def get(self, index):
        return list(self.getiter(index))


    def dump(self, to_file):
        from pprint import pprint
        print >>to_file, "knit lines:"
        pprint(self._l, to_file)


    def check(self):
        for vers_info in self._v:
            included = set()
            for vi in vers_info[0]:
                if vi < 0 or vi >= index:
                    raise ValueError("invalid included_version %d for index %d"
                                     % (vi, index))
                if vi in included:
                    raise ValueError("repeated included_version %d for index %d"
                                     % (vi, index))
                included.add(vi)
            



def update_knit(knit, new_vers, new_lines):
    """Return a new knit whose text matches new_lines.

    First of all the knit is diffed against the new lines, considering
    only the text of the lines from the knit.  This identifies lines
    unchanged from the knit, plus insertions and deletions.

    The deletions are marked as deleted.  The insertions are added
    with their new values.

    
    """
    if not isinstance(new_vers, int):
        raise TypeError('new version-id must be an int: %r' % new_vers)
    
    from difflib import SequenceMatcher
    knit_lines = knit2text(knit)
    m = SequenceMatcher(None, knit_lines, new_lines)

    for block in m.get_matching_blocks():
        print "a[%d] and b[%d] match for %d elements" % block
    
    new_knit = []
    for tag, i1, i2, j1, j2 in m.get_opcodes():
        print ("%7s a[%d:%d] (%s) b[%d:%d] (%s)" %
               (tag, i1, i2, knit_lines[i1:i2], j1, j2, new_lines[j1:j2]))

        if tag == 'equal':
            new_knit.extend(knit[i1:i2])
        elif tag == 'delete':
            for i in range(i1, i2):
                kl = knit[i]
                new_knit.append((kl[0], kl[1], False))

    return new_knit
        

