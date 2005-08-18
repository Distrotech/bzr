# Bazaar-NG -- distributed version control

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

import os, types, re, time, errno, sys
from stat import S_ISREG, S_ISDIR, S_ISLNK, ST_MODE, ST_SIZE

from bzrlib.errors import BzrError
from bzrlib.trace import mutter
import bzrlib

def make_readonly(filename):
    """Make a filename read-only."""
    # TODO: probably needs to be fixed for windows
    mod = os.stat(filename).st_mode
    mod = mod & 0777555
    os.chmod(filename, mod)


def make_writable(filename):
    mod = os.stat(filename).st_mode
    mod = mod | 0200
    os.chmod(filename, mod)


_QUOTE_RE = None


def quotefn(f):
    """Return a quoted filename filename

    This previously used backslash quoting, but that works poorly on
    Windows."""
    # TODO: I'm not really sure this is the best format either.x
    global _QUOTE_RE
    if _QUOTE_RE == None:
        _QUOTE_RE = re.compile(r'([^a-zA-Z0-9.,:/_~-])')
        
    if _QUOTE_RE.search(f):
        return '"' + f + '"'
    else:
        return f


def file_kind(f):
    mode = os.lstat(f)[ST_MODE]
    if S_ISREG(mode):
        return 'file'
    elif S_ISDIR(mode):
        return 'directory'
    elif S_ISLNK(mode):
        return 'symlink'
    else:
        raise BzrError("can't handle file kind with mode %o of %r" % (mode, f))


def kind_marker(kind):
    if kind == 'file':
        return ''
    elif kind == 'directory':
        return '/'
    elif kind == 'symlink':
        return '@'
    else:
        raise BzrError('invalid file kind %r' % kind)



def backup_file(fn):
    """Copy a file to a backup.

    Backups are named in GNU-style, with a ~ suffix.

    If the file is already a backup, it's not copied.
    """
    import os
    if fn[-1] == '~':
        return
    bfn = fn + '~'

    inf = file(fn, 'rb')
    try:
        content = inf.read()
    finally:
        inf.close()
    
    outf = file(bfn, 'wb')
    try:
        outf.write(content)
    finally:
        outf.close()

def rename(path_from, path_to):
    """Basically the same as os.rename() just special for win32"""
    if sys.platform == 'win32':
        try:
            os.remove(path_to)
        except OSError, e:
            if e.errno != e.ENOENT:
                raise
    os.rename(path_from, path_to)





def isdir(f):
    """True if f is an accessible directory."""
    try:
        return S_ISDIR(os.lstat(f)[ST_MODE])
    except OSError:
        return False



def isfile(f):
    """True if f is a regular file."""
    try:
        return S_ISREG(os.lstat(f)[ST_MODE])
    except OSError:
        return False


def is_inside(dir, fname):
    """True if fname is inside dir.
    
    The parameters should typically be passed to os.path.normpath first, so
    that . and .. and repeated slashes are eliminated, and the separators
    are canonical for the platform.
    
    The empty string as a dir name is taken as top-of-tree and matches 
    everything.
    
    >>> is_inside('src', 'src/foo.c')
    True
    >>> is_inside('src', 'srccontrol')
    False
    >>> is_inside('src', 'src/a/a/a/foo.c')
    True
    >>> is_inside('foo.c', 'foo.c')
    True
    >>> is_inside('foo.c', '')
    False
    >>> is_inside('', 'foo.c')
    True
    """
    # XXX: Most callers of this can actually do something smarter by 
    # looking at the inventory
    if dir == fname:
        return True
    
    if dir == '':
        return True
    
    if dir[-1] != os.sep:
        dir += os.sep
    
    return fname.startswith(dir)


def is_inside_any(dir_list, fname):
    """True if fname is inside any of given dirs."""
    for dirname in dir_list:
        if is_inside(dirname, fname):
            return True
    else:
        return False


def pumpfile(fromfile, tofile):
    """Copy contents of one file to another."""
    tofile.write(fromfile.read())


def uuid():
    """Return a new UUID"""
    try:
        return file('/proc/sys/kernel/random/uuid').readline().rstrip('\n')
    except IOError:
        return chomp(os.popen('uuidgen').readline())


def sha_file(f):
    import sha
    if hasattr(f, 'tell'):
        assert f.tell() == 0
    s = sha.new()
    BUFSIZE = 128<<10
    while True:
        b = f.read(BUFSIZE)
        if not b:
            break
        s.update(b)
    return s.hexdigest()


def sha_string(f):
    import sha
    s = sha.new()
    s.update(f)
    return s.hexdigest()



def fingerprint_file(f):
    import sha
    s = sha.new()
    b = f.read()
    s.update(b)
    size = len(b)
    return {'size': size,
            'sha1': s.hexdigest()}


def config_dir():
    """Return per-user configuration directory.

    By default this is ~/.bzr.conf/
    
    TODO: Global option --config-dir to override this.
    """
    return os.path.expanduser("~/.bzr.conf")


def _auto_user_id():
    """Calculate automatic user identification.

    Returns (realname, email).

    Only used when none is set in the environment or the id file.

    This previously used the FQDN as the default domain, but that can
    be very slow on machines where DNS is broken.  So now we simply
    use the hostname.
    """
    import socket

    # XXX: Any good way to get real user name on win32?

    try:
        import pwd
        uid = os.getuid()
        w = pwd.getpwuid(uid)
        gecos = w.pw_gecos.decode(bzrlib.user_encoding)
        username = w.pw_name.decode(bzrlib.user_encoding)
        comma = gecos.find(',')
        if comma == -1:
            realname = gecos
        else:
            realname = gecos[:comma]
        if not realname:
            realname = username

    except ImportError:
        import getpass
        realname = username = getpass.getuser().decode(bzrlib.user_encoding)

    return realname, (username + '@' + socket.gethostname())


def _get_user_id(branch):
    """Return the full user id from a file or environment variable.

    e.g. "John Hacker <jhacker@foo.org>"

    branch
        A branch to use for a per-branch configuration, or None.

    The following are searched in order:

    1. $BZREMAIL
    2. .bzr/email for this branch.
    3. ~/.bzr.conf/email
    4. $EMAIL
    """
    v = os.environ.get('BZREMAIL')
    if v:
        return v.decode(bzrlib.user_encoding)

    if branch:
        try:
            return (branch.controlfile("email", "r") 
                    .read()
                    .decode(bzrlib.user_encoding)
                    .rstrip("\r\n"))
        except IOError, e:
            if e.errno != errno.ENOENT:
                raise
        except BzrError, e:
            pass
    
    try:
        return (open(os.path.join(config_dir(), "email"))
                .read()
                .decode(bzrlib.user_encoding)
                .rstrip("\r\n"))
    except IOError, e:
        if e.errno != errno.ENOENT:
            raise e

    v = os.environ.get('EMAIL')
    if v:
        return v.decode(bzrlib.user_encoding)
    else:    
        return None


def username(branch):
    """Return email-style username.

    Something similar to 'Martin Pool <mbp@sourcefrog.net>'

    TODO: Check it's reasonably well-formed.
    """
    v = _get_user_id(branch)
    if v:
        return v
    
    name, email = _auto_user_id()
    if name:
        return '%s <%s>' % (name, email)
    else:
        return email


def user_email(branch):
    """Return just the email component of a username."""
    e = _get_user_id(branch)
    if e:
        m = re.search(r'[\w+.-]+@[\w+.-]+', e)
        if not m:
            raise BzrError("%r doesn't seem to contain a reasonable email address" % e)
        return m.group(0)

    return _auto_user_id()[1]
    


def compare_files(a, b):
    """Returns true if equal in contents"""
    BUFSIZE = 4096
    while True:
        ai = a.read(BUFSIZE)
        bi = b.read(BUFSIZE)
        if ai != bi:
            return False
        if ai == '':
            return True



def local_time_offset(t=None):
    """Return offset of local zone from GMT, either at present or at time t."""
    # python2.3 localtime() can't take None
    if t == None:
        t = time.time()
        
    if time.localtime(t).tm_isdst and time.daylight:
        return -time.altzone
    else:
        return -time.timezone

    
def format_date(t, offset=0, timezone='original'):
    ## TODO: Perhaps a global option to use either universal or local time?
    ## Or perhaps just let people set $TZ?
    assert isinstance(t, float)
    
    if timezone == 'utc':
        tt = time.gmtime(t)
        offset = 0
    elif timezone == 'original':
        if offset == None:
            offset = 0
        tt = time.gmtime(t + offset)
    elif timezone == 'local':
        tt = time.localtime(t)
        offset = local_time_offset(t)
    else:
        raise BzrError("unsupported timezone format %r" % timezone,
                       ['options are "utc", "original", "local"'])

    return (time.strftime("%a %Y-%m-%d %H:%M:%S", tt)
            + ' %+03d%02d' % (offset / 3600, (offset / 60) % 60))


def compact_date(when):
    return time.strftime('%Y%m%d%H%M%S', time.gmtime(when))
    


def filesize(f):
    """Return size of given open file."""
    return os.fstat(f.fileno())[ST_SIZE]


if hasattr(os, 'urandom'): # python 2.4 and later
    rand_bytes = os.urandom
elif sys.platform == 'linux2':
    rand_bytes = file('/dev/urandom', 'rb').read
else:
    # not well seeded, but better than nothing
    def rand_bytes(n):
        import random
        s = ''
        while n:
            s += chr(random.randint(0, 255))
            n -= 1
        return s


## TODO: We could later have path objects that remember their list
## decomposition (might be too tricksy though.)

def splitpath(p):
    """Turn string into list of parts.

    >>> splitpath('a')
    ['a']
    >>> splitpath('a/b')
    ['a', 'b']
    >>> splitpath('a/./b')
    ['a', 'b']
    >>> splitpath('a/.b')
    ['a', '.b']
    >>> splitpath('a/../b')
    Traceback (most recent call last):
    ...
    BzrError: sorry, '..' not allowed in path
    """
    assert isinstance(p, types.StringTypes)

    # split on either delimiter because people might use either on
    # Windows
    ps = re.split(r'[\\/]', p)

    rps = []
    for f in ps:
        if f == '..':
            raise BzrError("sorry, %r not allowed in path" % f)
        elif (f == '.') or (f == ''):
            pass
        else:
            rps.append(f)
    return rps

def joinpath(p):
    assert isinstance(p, list)
    for f in p:
        if (f == '..') or (f == None) or (f == ''):
            raise BzrError("sorry, %r not allowed in path" % f)
    return os.path.join(*p)


def appendpath(p1, p2):
    if p1 == '':
        return p2
    else:
        return os.path.join(p1, p2)
    

def extern_command(cmd, ignore_errors = False):
    mutter('external command: %s' % `cmd`)
    if os.system(cmd):
        if not ignore_errors:
            raise BzrError('command failed')


def _read_config_value(name):
    """Read a config value from the file ~/.bzr.conf/<name>
    Return None if the file does not exist"""
    try:
        f = file(os.path.join(config_dir(), name), "r")
        return f.read().decode(bzrlib.user_encoding).rstrip("\r\n")
    except IOError, e:
        if e.errno == errno.ENOENT:
            return None
        raise


def _get_editor():
    """Return a sequence of possible editor binaries for the current platform"""
    e = _read_config_value("editor")
    if e is not None:
        yield e
        
    if os.name == "windows":
        yield "notepad.exe"
    elif os.name == "posix":
        try:
            yield os.environ["EDITOR"]
        except KeyError:
            yield "/usr/bin/vi"


def _run_editor(filename):
    """Try to execute an editor to edit the commit message. Returns True on success,
    False on failure"""
    for e in _get_editor():
        x = os.spawnvp(os.P_WAIT, e, (e, filename))
        if x == 0:
            return True
        elif x == 127:
            continue
        else:
            break
    raise BzrError("Could not start any editor. Please specify $EDITOR or use ~/.bzr.conf/editor")
    return False
                          

def get_text_message(infotext, ignoreline = "default"):
    import tempfile
    
    if ignoreline == "default":
        ignoreline = "-- This line and the following will be ignored --"
        
    try:
        tmp_fileno, msgfilename = tempfile.mkstemp()
        msgfile = os.close(tmp_fileno)
        if infotext is not None and infotext != "":
            hasinfo = True
            msgfile = file(msgfilename, "w")
            msgfile.write("\n\n%s\n\n%s" % (ignoreline, infotext))
            msgfile.close()
        else:
            hasinfo = False

        if not _run_editor(msgfilename):
            return None
        
        started = False
        msg = []
        lastline, nlines = 0, 0
        for line in file(msgfilename, "r"):
            stripped_line = line.strip()
            # strip empty line before the log message starts
            if not started:
                if stripped_line != "":
                    started = True
                else:
                    continue
            # check for the ignore line only if there
            # is additional information at the end
            if hasinfo and stripped_line == ignoreline:
                break
            nlines += 1
            # keep track of the last line that had some content
            if stripped_line != "":
                lastline = nlines
            msg.append(line)
            
        if len(msg) == 0:
            return None
        # delete empty lines at the end
        del msg[lastline:]
        # add a newline at the end, if needed
        if not msg[-1].endswith("\n"):
            return "%s%s" % ("".join(msg), "\n")
        else:
            return "".join(msg)
    finally:
        # delete the msg file in any case
        try: os.unlink(msgfilename)
        except IOError: pass
