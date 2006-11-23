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

"""Messages and logging for bazaar-ng.

Messages are supplied by callers as a string-formatting template, plus values
to be inserted into it.  The actual %-formatting is deferred to the log
library so that it doesn't need to be done for messages that won't be emitted.

Messages are classified by severity levels: critical, error, warning, info,
and debug.

They can be sent to two places: to stderr, and to ~/.bzr.log.  For purposes
such as running the test suite, they can also be redirected away from both of
those two places to another location.

~/.bzr.log gets all messages, and full tracebacks for uncaught exceptions.
This trace file is always in UTF-8, regardless of the user's default encoding,
so that we can always rely on writing any message.

Output to stderr depends on the mode chosen by the user.  By default, messages
of info and above are sent out, which results in progress messages such as the
list of files processed by add and commit.  In quiet mode, only warnings and
above are shown.  In debug mode, stderr gets debug messages too.

Errors that terminate an operation are generally passed back as exceptions;
others may be just emitted as messages.

Exceptions are reported in a brief form to stderr so as not to look scary.
BzrErrors are required to be able to format themselves into a properly
explanatory message.  This is not true for builtin exceptions such as
KeyError, which typically just str to "0".  They're printed in a different
form.
"""

# FIXME: Unfortunately it turns out that python's logging module
# is quite expensive, even when the message is not printed by any handlers.
# We should perhaps change back to just simply doing it here.

import os
import re
import sys

from bzrlib.lazy_import import lazy_import
lazy_import(globals(), """
import errno
import logging
from cStringIO import StringIO
""")

import bzrlib
from bzrlib.symbol_versioning import (deprecated_function,
        zero_nine,
        )

lazy_import(globals(), """
from bzrlib import debug
""")

_file_handler = None
_stderr_handler = None
_stderr_quiet = False
_trace_file = None
_trace_depth = 0
_bzr_log_file = None
_use_apport = True


# configure convenient aliases for output routines

_bzr_logger = logging.getLogger('bzr')

def note(*args, **kwargs):
    # FIXME note always emits utf-8, regardless of the terminal encoding
    import bzrlib.ui
    bzrlib.ui.ui_factory.clear_term()
    _bzr_logger.info(*args, **kwargs)

def warning(*args, **kwargs):
    import bzrlib.ui
    bzrlib.ui.ui_factory.clear_term()
    _bzr_logger.warning(*args, **kwargs)

info = note
log_error = _bzr_logger.error
error =     _bzr_logger.error


def mutter(fmt, *args):
    if _trace_file is None:
        return
    if (getattr(_trace_file, 'closed', None) is not None) and _trace_file.closed:
        return

    if isinstance(fmt, unicode):
        fmt = fmt.encode('utf8')

    if len(args) > 0:
        # It seems that if we do ascii % (unicode, ascii) we can
        # get a unicode cannot encode ascii error, so make sure that "fmt"
        # is a unicode string
        real_args = []
        for arg in args:
            if isinstance(arg, unicode):
                arg = arg.encode('utf8')
            real_args.append(arg)
        out = fmt % tuple(real_args)
    else:
        out = fmt
    out += '\n'
    _trace_file.write(out)
    # TODO: jam 20051227 Consider flushing the trace file to help debugging
    #_trace_file.flush()


def _rollover_trace_maybe(trace_fname):
    import stat
    try:
        size = os.stat(trace_fname)[stat.ST_SIZE]
        if size <= 4 << 20:
            return
        old_fname = trace_fname + '.old'
        from osutils import rename
        rename(trace_fname, old_fname)
    except OSError:
        return


def open_tracefile(tracefilename='~/.bzr.log'):
    # Messages are always written to here, so that we have some
    # information if something goes wrong.  In a future version this
    # file will be removed on successful completion.
    global _file_handler, _bzr_log_file
    import codecs

    trace_fname = os.path.join(os.path.expanduser(tracefilename))
    _rollover_trace_maybe(trace_fname)
    try:
        LINE_BUFFERED = 1
        #tf = codecs.open(trace_fname, 'at', 'utf8', buffering=LINE_BUFFERED)
        tf = open(trace_fname, 'at', LINE_BUFFERED)
        _bzr_log_file = tf
        if tf.tell() == 0:
            tf.write("\nthis is a debug log for diagnosing/reporting problems in bzr\n")
            tf.write("you can delete or truncate this file, or include sections in\n")
            tf.write("bug reports to bazaar-ng@lists.canonical.com\n\n")
        _file_handler = logging.StreamHandler(tf)
        fmt = r'[%(process)5d] %(asctime)s.%(msecs)03d %(levelname)s: %(message)s'
        datefmt = r'%a %H:%M:%S'
        _file_handler.setFormatter(logging.Formatter(fmt, datefmt))
        _file_handler.setLevel(logging.DEBUG)
        logging.getLogger('').addHandler(_file_handler)
    except IOError, e:
        warning("failed to open trace file: %s" % (e))


@deprecated_function(zero_nine)
def log_exception(msg=None):
    """Log the last exception to stderr and the trace file.

    The exception string representation is used as the error
    summary, unless msg is given.
    """
    if msg:
        error(msg)
    log_exception_quietly()


def log_exception_quietly():
    """Log the last exception to the trace file only.

    Used for exceptions that occur internally and that may be 
    interesting to developers but not to users.  For example, 
    errors loading plugins.
    """
    import traceback
    mutter(traceback.format_exc())


def enable_default_logging():
    """Configure default logging to stderr and .bzr.log"""
    # FIXME: if this is run twice, things get confused
    global _stderr_handler, _file_handler, _trace_file, _bzr_log_file
    _stderr_handler = logging.StreamHandler()
    logging.getLogger('').addHandler(_stderr_handler)
    _stderr_handler.setLevel(logging.INFO)
    if not _file_handler:
        open_tracefile()
    _trace_file = _bzr_log_file
    if _file_handler:
        _file_handler.setLevel(logging.DEBUG)
    _bzr_logger.setLevel(logging.DEBUG)


def be_quiet(quiet=True):
    global _stderr_handler, _stderr_quiet
    
    _stderr_quiet = quiet
    if quiet:
        _stderr_handler.setLevel(logging.WARNING)
    else:
        _stderr_handler.setLevel(logging.INFO)


def is_quiet():
    global _stderr_quiet
    return _stderr_quiet


def disable_default_logging():
    """Turn off default log handlers.

    This is intended to be used by the test framework, which doesn't
    want leakage from the code-under-test into the main logs.
    """

    l = logging.getLogger('')
    l.removeHandler(_stderr_handler)
    if _file_handler:
        l.removeHandler(_file_handler)
    _trace_file = None


def enable_test_log(to_file):
    """Redirect logging to a temporary file for a test
    
    returns an opaque reference that should be passed to disable_test_log
    after the test completes.
    """
    disable_default_logging()
    global _trace_file
    global _trace_depth
    hdlr = logging.StreamHandler(to_file)
    hdlr.setLevel(logging.DEBUG)
    hdlr.setFormatter(logging.Formatter('%(levelname)8s  %(message)s'))
    _bzr_logger.addHandler(hdlr)
    _bzr_logger.setLevel(logging.DEBUG)
    result = hdlr, _trace_file, _trace_depth
    _trace_file = to_file
    _trace_depth += 1
    return result


def disable_test_log((test_log_hdlr, old_trace_file, old_trace_depth)):
    _bzr_logger.removeHandler(test_log_hdlr)
    test_log_hdlr.close()
    global _trace_file
    global _trace_depth
    _trace_file = old_trace_file
    _trace_depth = old_trace_depth
    if not _trace_depth:
        enable_default_logging()


def report_exception(exc_info, err_file):
    exc_type, exc_object, exc_tb = exc_info
    # Log the full traceback to ~/.bzr.log
    log_exception_quietly()
    if (isinstance(exc_object, IOError)
        and getattr(exc_object, 'errno', None) == errno.EPIPE):
        print >>err_file, "bzr: broken pipe"
    elif isinstance(exc_object, KeyboardInterrupt):
        print >>err_file, "bzr: interrupted"
    elif not getattr(exc_object, 'internal_error', True):
        report_user_error(exc_info, err_file)
    elif isinstance(exc_object, (OSError, IOError)):
        # Might be nice to catch all of these and show them as something more
        # specific, but there are too many cases at the moment.
        report_user_error(exc_info, err_file)
    else:
        return report_bug(exc_info, err_file)


# TODO: Should these be specially encoding the output?
def report_user_error(exc_info, err_file):
    """Report to err_file an error that's not an internal error.

    These don't get a traceback unless -Derror was given.
    """
    if 'error' in debug.debug_flags:
        report_bug(exc_info, err_file)
        return
    print >>err_file, "bzr: ERROR:", str(exc_info[1])


def report_bug(exc_info, err_file):
    """Report an exception that probably indicates a bug in bzr"""
    # local import because its only needed here, and this is not a loop.
    import tempfile
    # local import because the other functions do it too.
    import traceback
    # local import due to circular dependency
    import bzrlib.plugin
    global _use_apport
    try:
        # detect apport presence.
        import apport_utils
        import problem_report
    except ImportError:
        # not present, dont use it.
        _use_apport = False
    if not _use_apport:
        # policy disabled, or not present, use the old ui.
        return _old_report_bug(exc_info, err_file)

    exc_type, exc_object, exc_tb = exc_info
    err_file.write(
        "bzr: ERROR: %s.%s: %s\n" % (
        exc_type.__module__, exc_type.__name__, exc_object)
        )
    report = problem_report.ProblemReport()
    report_file, report_filename = tempfile.mkstemp(
        suffix='.txt', prefix='bzr-crash-', dir='/tmp')
    python_report_file = os.fdopen(report_file, 'w')
    try:
        report['CommandLine'] = ' '.join(sys.argv)
        # assume we are packaged as bzr.
        apport_utils.report_add_package_info(report, 'bzr')
        report['BzrPlugins'] = ' '.join(bzrlib.plugin.all_plugins())
        tb_file = StringIO()
        traceback.print_exception(exc_type, exc_object, exc_tb, file=tb_file)
        report['Traceback'] = tb_file.getvalue()
        apport_utils.report_add_os_info(report)
        report.write(python_report_file)
        # give the user a pretty output.

        err_file.write(
            'This is an unexpected error within bzr and we would appreciate a bug report.\n'
            '\n'
            'bzr has written a crash report file that will assist our debugging of this\n'
            'in the file %s\n'
            '\n'
            'This is a plain text file, whose contents you can check if you have privacy\n'
            'concerns. We gather the package data about bzr, your command line, plugins\n'
            'And the backtrace from within bzr. If you had a password in the URL you\n'
            'provided to bzr, you should edit that file to remove the password.\n'
            '\n'
            '** To file a bug for this please visit our bugtracker at the URL \n'
            '"https://launchpad.net/products/bzr/+filebug" and report a bug describing\n'
            'what you were attempting and attach the bzr-crash file mentioned above.\n'
            'Alternatively you can email bazaar-ng@lists.canonical.com with the same\n'
            'description and attach the bzr-crash file to the email.\n' %
                report_filename
            )
    finally:
        python_report_file.close()
    return report, report_filename

def _old_report_bug(exc_info, err_file):
    """Write a synopsis of an exception that is probably a bug to err_file."""
    import traceback
    exc_type, exc_object, exc_tb = exc_info
    print >>err_file, "bzr: ERROR: %s.%s: %s" % (
        exc_type.__module__, exc_type.__name__, exc_object)
    print >>err_file
    traceback.print_exception(exc_type, exc_object, exc_tb, file=err_file)
    print >>err_file
    print >>err_file, 'bzr %s on python %s (%s)' % \
                       (bzrlib.__version__,
                        '.'.join(map(str, sys.version_info)),
                        sys.platform)
    print >>err_file, 'arguments: %r' % sys.argv
    print >>err_file
    print >>err_file, "** please send this report to bazaar-ng@lists.ubuntu.com"
