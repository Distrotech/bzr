# groupcompress, a bzr plugin providing new compression logic.
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


"""Tests for the groupcompress bzr plugin."""


def load_tests(standard_tests, module, loader):
    test_modules = [
        'errors',
        'groupcompress',
        '_groupcompress_pyx',
        ]
    standard_tests.addTests(loader.loadTestsFromModuleNames(
        ['bzrlib.plugins.groupcompress.tests.test_' + name for
         name in test_modules]))
    return standard_tests
