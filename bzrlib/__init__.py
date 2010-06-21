# Copyright (C) 2005-2010 Canonical Ltd
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

"""All of bzr.

Developer documentation is available at
http://doc.bazaar.canonical.com/bzr.dev/developers/

The project website is at http://bazaar.canonical.com/

Some particularly interesting things in bzrlib are:

 * bzrlib.initialize -- setup the library for use
 * bzrlib.plugin.load_plugins -- load all installed plugins
 * bzrlib.branch.Branch.open -- open a branch
 * bzrlib.workingtree.WorkingTree.open -- open a working tree

We hope you enjoy this library.
"""

import time

# Keep track of when bzrlib was first imported, so that we can give rough
# timestamps relative to program start in the log file kept by bzrlib.trace.
_start_time = time.time()

import sys
if getattr(sys, '_bzr_lazy_regex', False):
    # The 'bzr' executable sets _bzr_lazy_regex.  We install the lazy regex
    # hack as soon as possible so that as much of the standard library can
    # benefit, including the 'string' module.
    del sys._bzr_lazy_regex
    import bzrlib.lazy_regex
    bzrlib.lazy_regex.install_lazy_compile()


IGNORE_FILENAME = ".bzrignore"


__copyright__ = "Copyright 2005-2010 Canonical Ltd."

# same format as sys.version_info: "A tuple containing the five components of
# the version number: major, minor, micro, releaselevel, and serial. All
# values except releaselevel are integers; the release level is 'alpha',
# 'beta', 'candidate', or 'final'. The version_info value corresponding to the
# Python version 2.0 is (2, 0, 0, 'final', 0)."  Additionally we use a
# releaselevel of 'dev' for unreleased under-development code.

version_info = (2, 2, 0, 'beta', 3)

# API compatibility version
api_minimum_version = (2, 2, 0)


def _format_version_tuple(version_info):
    """Turn a version number 2, 3 or 5-tuple into a short string.

    This format matches <http://docs.python.org/dist/meta-data.html>
    and the typical presentation used in Python output.

    This also checks that the version is reasonable: the sub-release must be
    zero for final releases.

    >>> print _format_version_tuple((1, 0, 0, 'final', 0))
    1.0.0
    >>> print _format_version_tuple((1, 2, 0, 'dev', 0))
    1.2.0dev
    >>> print bzrlib._format_version_tuple((1, 2, 0, 'dev', 1))
    1.2.0dev1
    >>> print _format_version_tuple((1, 1, 1, 'candidate', 2))
    1.1.1rc2
    >>> print bzrlib._format_version_tuple((2, 1, 0, 'beta', 1))
    2.1b1
    >>> print _format_version_tuple((1, 4, 0))
    1.4.0
    >>> print _format_version_tuple((1, 4))
    1.4
    >>> print bzrlib._format_version_tuple((2, 1, 0, 'final', 1))
    Traceback (most recent call last):
    ...
    ValueError: version_info (2, 1, 0, 'final', 1) not valid
    >>> print _format_version_tuple((1, 4, 0, 'wibble', 0))
    Traceback (most recent call last):
    ...
    ValueError: version_info (1, 4, 0, 'wibble', 0) not valid
    """
    if len(version_info) == 2:
        main_version = '%d.%d' % version_info[:2]
    else:
        main_version = '%d.%d.%d' % version_info[:3]
    if len(version_info) <= 3:
        return main_version

    release_type = version_info[3]
    sub = version_info[4]

    # check they're consistent
    if release_type == 'final' and sub == 0:
        sub_string = ''
    elif release_type == 'dev' and sub == 0:
        sub_string = 'dev'
    elif release_type == 'dev':
        sub_string = 'dev' + str(sub)
    elif release_type in ('alpha', 'beta'):
        if version_info[2] == 0:
            main_version = '%d.%d' % version_info[:2]
        sub_string = release_type[0] + str(sub)
    elif release_type == 'candidate':
        sub_string = 'rc' + str(sub)
    else:
        raise ValueError("version_info %r not valid" % (version_info,))

    return main_version + sub_string


__version__ = _format_version_tuple(version_info)
version_string = __version__

# bzr has various bits of global state that are slowly being eliminated.
# This variable is intended to permit any new state-like things to be attached
# to a BzrLibraryState object rather than getting new global variables that
# need to be hunted down. Accessing the current BzrLibraryState through this
# variable is not encouraged: it is better to pass it around as part of the
# context of an operation than to look it up directly, but when that is too
# hard, it is better to use this variable than to make a branch new global
# variable.
# If using this variable my looking it up (because it can't be easily obtained)
# it is important to store the reference you get, rather than looking it up
# repeatedly; that way your code will behave properly in the bzrlib test suite
# and from programs that do use multiple library contexts.
global_state = None


class BzrLibraryState(object):
    """The state about how bzrlib has been configured.
    
    :ivar saved_state: The bzrlib.global_state at the time __enter__ was
        called.
    :ivar cleanups: An ObjectWithCleanups which can be used for cleanups that
        should occur when the use of bzrlib is completed. This is initialised
        in __enter__ and executed in __exit__.
    """

    def __init__(self, setup_ui=True, stdin=None, stdout=None, stderr=None):
        """Create library start for normal use of bzrlib.

        Most applications that embed bzrlib, including bzr itself, should just
        call bzrlib.initialize(), but it is possible to use the state class
        directly.

        More options may be added in future so callers should use named
        arguments.

        BzrLibraryState implements the Python 2.5 Context Manager protocol, and
        can be used with the with statement. Upon __enter__ the global
        variables in use by bzr are set, and they are cleared on __exit__.

        :param setup_ui: If true (default) use a terminal UI; otherwise 
            some other ui_factory must be assigned to `bzrlib.ui.ui_factory` by
            the caller.
        :param stdin, stdout, stderr: If provided, use these for terminal IO;
            otherwise use the files in `sys`.
        """
        self.setup_ui = setup_ui
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr

    def __enter__(self):
        # NB: This function tweaks so much global state it's hard to test it in
        # isolation within the same interpreter.  It's not reached on normal
        # in-process run_bzr calls.  If it's broken, we expect that
        # TestRunBzrSubprocess may fail.
        if version_info[3] == 'final':
            from bzrlib.symbol_versioning import suppress_deprecation_warnings
            suppress_deprecation_warnings(override=True)

        import bzrlib.cleanup
        import bzrlib.trace
        self.cleanups = bzrlib.cleanup.ObjectWithCleanups()
        bzrlib.trace.enable_default_logging()

        if self.setup_ui:
            import bzrlib.ui
            stdin = self.stdin or sys.stdin
            stdout = self.stdout or sys.stdout
            stderr = self.stderr or sys.stderr
            bzrlib.ui.ui_factory = bzrlib.ui.make_ui_for_terminal(
                stdin, stdout, stderr)
        global global_state
        self.saved_state = global_state
        global_state = self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanups.cleanup_now()
        import bzrlib.ui
        bzrlib.trace._flush_stdout_stderr()
        bzrlib.trace._flush_trace()
        import bzrlib.osutils
        bzrlib.osutils.report_extension_load_failures()
        bzrlib.ui.ui_factory.__exit__(None, None, None)
        bzrlib.ui.ui_factory = None
        global global_state
        global_state = self.saved_state
        return False # propogate exceptions.


def initialize(setup_ui=True, stdin=None, stdout=None, stderr=None):
    """Set up everything needed for normal use of bzrlib.

    Most applications that embed bzrlib, including bzr itself, should call
    this function to initialize various subsystems.  

    More options may be added in future so callers should use named arguments.

    :param setup_ui: If true (default) use a terminal UI; otherwise 
        some other ui_factory must be assigned to `bzrlib.ui.ui_factory` by
        the caller.
    :param stdin, stdout, stderr: If provided, use these for terminal IO;
        otherwise use the files in `sys`.
    :return: A context manager for the use of bzrlib. The __enter__ method of
        this context needs to be alled before it takes effect, and the __exit__
        should be called by the caller before exiting their process or
        otherwise stopping use of bzrlib. Advanced callers can use
        BzrLibraryState directly.
    """
    return BzrLibraryState(setup_ui=setup_ui, stdin=stdin,
        stdout=stdout, stderr=stderr)


def test_suite():
    import tests
    return tests.test_suite()
