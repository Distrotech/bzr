# Copyright (C) 2004, 2005 by Martin Pool
# Copyright (C) 2005 by Canonical Ltd


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

from sets import Set
import time

import bzrlib
from osutils import format_date

def show_info(b):
    # TODO: Maybe show space used by working tree, versioned files,
    # unknown files, text store.
    
    print 'branch format:', b.controlfile('branch-format', 'r').readline().rstrip('\n')

    def plural(n, base='', pl=None):
        if n == 1:
            return base
        elif pl == None:
            return pl
        else:
            return 's'

    count_version_dirs = 0

    count_status = {'A': 0, 'D': 0, 'M': 0, 'R': 0, '?': 0, 'I': 0, '.': 0}
    for st_tup in bzrlib.diff_trees(b.basis_tree(), b.working_tree()):
        fs = st_tup[0]
        count_status[fs] += 1
        if fs not in ['I', '?'] and st_tup[4] == 'directory':
            count_version_dirs += 1

    print
    print 'in the working tree:'
    for name, fs in (('unchanged', '.'),
                     ('modified', 'M'), ('added', 'A'), ('removed', 'D'),
                     ('renamed', 'R'), ('unknown', '?'), ('ignored', 'I'),
                     ):
        print '  %8d %s' % (count_status[fs], name)
    print '  %8d versioned subdirector%s' % (count_version_dirs,
                                             plural(count_version_dirs, 'y', 'ies'))

    print
    print 'branch history:'
    history = b.revision_history()
    revno = len(history)
    print '  %8d revision%s' % (revno, plural(revno))
    committers = Set()
    for rev in history:
        committers.add(b.get_revision(rev).committer)
    print '  %8d committer%s' % (len(committers), plural(len(committers)))
    if revno > 0:
        firstrev = b.get_revision(history[0])
        age = int((time.time() - firstrev.timestamp) / 3600 / 24)
        print '  %8d day%s old' % (age, plural(age))
        print '   first revision: %s' % format_date(firstrev.timestamp,
                                                    firstrev.timezone)

        lastrev = b.get_revision(history[-1])
        print '  latest revision: %s' % format_date(lastrev.timestamp,
                                                    lastrev.timezone)

    print
    print 'text store:'
    c, t = b.text_store.total_size()
    print '  %8d file texts' % c
    print '  %8d kB' % (t/1024)

    print
    print 'revision store:'
    c, t = b.revision_store.total_size()
    print '  %8d revisions' % c
    print '  %8d kB' % (t/1024)


    print
    print 'inventory store:'
    c, t = b.inventory_store.total_size()
    print '  %8d inventories' % c
    print '  %8d kB' % (t/1024)

