# Copyright (C) 2009 Canonical Ltd
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

"""Interface thunk for a StaticTuple implementation."""

from bzrlib import debug

try:
    from bzrlib._static_tuple_c import StaticTuple
except ImportError, e:
    from bzrlib import osutils
    osutils.failed_to_load_extension(e)
    from bzrlib._static_tuple_py import StaticTuple


def expect_static_tuple(obj):
    """Check if the passed object is a StaticTuple.

    Cast it if necessary, but if the 'static_tuple' debug flag is set, raise an
    error instead.

    As apis are improved, we will probably eventually stop calling this as it
    adds overhead we shouldn't need.
    """
    if 'static_tuple' not in debug.debug_flags:
        return StaticTuple.from_sequence(obj)
    if type(obj) is not StaticTuple:
        raise TypeError('We expected a StaticTuple not a %s' % (type(obj),))
    return obj
