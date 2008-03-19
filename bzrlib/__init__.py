# Copyright (C) 2005, 2006, 2007, 2008 Canonical Ltd
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

"""bzr library"""

import time

# Keep track of when bzrlib was first imported, so that we can give rough
# timestamps relative to program start in the log file kept by bzrlib.trace.
_start_time = time.time()

from bzrlib.osutils import get_user_encoding


IGNORE_FILENAME = ".bzrignore"


# XXX: Compatibility. This should probably be deprecated
user_encoding = get_user_encoding()


__copyright__ = "Copyright 2005, 2006, 2007, 2008 Canonical Ltd."

# same format as sys.version_info: "A tuple containing the five components of
# the version number: major, minor, micro, releaselevel, and serial. All
# values except releaselevel are integers; the release level is 'alpha',
# 'beta', 'candidate', or 'final'. The version_info value corresponding to the
# Python version 2.0 is (2, 0, 0, 'final', 0)."  Additionally we use a
# releaselevel of 'dev' for unreleased under-development code.

version_info = (1, 3, 0, 'dev', 0)

# API compatibility version: bzrlib is currently API compatible with 0.18.
api_minimum_version = (0, 18, 0)

if version_info[3] == 'final':
    version_string = '%d.%d.%d' % version_info[:3]
else:
    version_string = '%d.%d.%d.%s.%d' % version_info
__version__ = version_string

# allow bzrlib plugins to be imported.
import bzrlib.plugin
bzrlib.plugin.set_plugins_path()


def test_suite():
    import tests
    return tests.test_suite()
