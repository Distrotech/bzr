# Copyright (C) 2005 by Canonical Ltd
#
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

"""bzr postinstall helper for win32 installation
Written by Alexander Belchenko
"""

import os
import sys


##
# CONSTANTS

VERSION = "1.3.20060513"

USAGE = """Bzr postinstall helper for win32 installation
Usage: %s [options]

OPTIONS:
    -h, --help                  - help message
    -v, --version               - version info

    -n, --dry-run               - print actions rather than execute them
    -q, --silent                - no messages for user

    --start-bzr                 - update start_bzr.bat
    --add-path                  - add bzr directory to environment PATH
    --delete-path               - delete bzr directory to environment PATH
    --add-shell-menu            - add shell context menu to start bzr session
    --delete-shell-menu         - delete context menu from shell
    --check-mfc71               - check if MFC71.DLL present in system
""" % os.path.basename(sys.argv[0])

##
# INTERNAL VARIABLES

(OK, ERROR) = range(2)
VERSION_FORMAT = "%-50s%s"


def main():
    import getopt
    import re
    import _winreg

    import locale
    user_encoding = locale.getpreferredencoding() or 'ascii'

    import ctypes

    hkey_str = {_winreg.HKEY_LOCAL_MACHINE: 'HKEY_LOCAL_MACHINE',
                _winreg.HKEY_CURRENT_USER: 'HKEY_CURRENT_USER',
                _winreg.HKEY_CLASSES_ROOT: 'HKEY_CLASSES_ROOT',
               }

    dry_run = False
    silent = False
    start_bzr = False
    add_path = False
    delete_path = False
    add_shell_menu = False
    delete_shell_menu = False
    check_mfc71 = False

    try:
        opts, args = getopt.getopt(sys.argv[1:], "hvnq",
                                   ["help", "version",
                                    "dry-run",
                                    "silent",
                                    "start-bzr",
                                    "add-path",
                                    "delete-path",
                                    "add-shell-menu",
                                    "delete-shell-menu",
                                    "check-mfc71",
                                   ])

        for o, a in opts:
            if o in ("-h", "--help"):
                print USAGE
                return OK
            elif o in ("-v", "--version"):
                print VERSION_FORMAT % (USAGE.splitlines()[0], VERSION)
                return OK

            elif o in ('-n', "--dry-run"):
                dry_run = True
            elif o in ('-q', '--silent'):
                silent = True

            elif o == "--start-bzr":
                start_bzr = True
            elif o == "--add-path":
                add_path = True
            elif o == "--delete-path":
                delete_path = True
            elif o == "--add-shell-menu":
                add_shell_menu = True
            elif o == "--delete-shell-menu":
                delete_shell_menu = True
            elif o == "--check-mfc71":
                check_mfc71 = True

    except getopt.GetoptError, msg:
        print str(msg)
        print USAGE
        return ERROR

    # message box from Win32API
    MessageBoxA = ctypes.windll.user32.MessageBoxA
    MB_OK = 0
    MB_ICONERROR = 16
    MB_ICONEXCLAMATION = 48

    bzr_dir = os.path.dirname(sys.argv[0])

    if start_bzr:
        fname = os.path.join(bzr_dir, "start_bzr.bat")
        if os.path.isfile(fname):
            f = file(fname, "r")
            content = f.readlines()
            f.close()
        else:
            content = ["bzr.exe help\n"]

        for ix in xrange(len(content)):
            s = content[ix]
            if re.match(r'.*(?<!\\)bzr\.exe([ "].*)?$',
                        s.rstrip('\r\n'),
                        re.IGNORECASE):
                content[ix] = s.replace('bzr.exe',
                                        '"%s"' % os.path.join(bzr_dir,
                                                              'bzr.exe'))

        if dry_run:
            print "*** Write file: start_bzr.bat"
            print "*** File content:"
            print ''.join(content)
        else:
            f = file(fname, 'w')
            f.write(''.join(content))
            f.close()

    if add_path or delete_path:
        # find appropriate registry key:
        # 1. HKLM\System\CurrentControlSet\Control\SessionManager\Environment
        # 2. HKCU\Environment
        keys = ((_winreg.HKEY_LOCAL_MACHINE, (r'System\CurrentControlSet\Control'
                                              r'\Session Manager\Environment')),
                (_winreg.HKEY_CURRENT_USER, r'Environment'),
               )

        hkey = None
        for key, subkey in keys:
            try:
                hkey = _winreg.OpenKey(key, subkey, 0, _winreg.KEY_ALL_ACCESS)
                try:
                    path_u, type_ = _winreg.QueryValueEx(hkey, 'Path')
                except WindowsError:
                    if key != _winreg.HKEY_CURRENT_USER:
                        _winreg.CloseKey(hkey)
                        hkey = None
                        continue
                    else:
                        path_u = u''
                        type_ = _winreg.REG_SZ
            except EnvironmentError:
                continue
            break

        if hkey is None:
            print "Cannot find appropriate registry key for PATH"
        else:
            path_list = [i for i in path_u.split(os.pathsep) if i != '']
            f_change = False
            for ix, item in enumerate(path_list[:]):
                if item == bzr_dir:
                    if delete_path:
                        del path_list[ix]
                        f_change = True
                    elif add_path:
                        print "*** Bzr already in PATH"
                    break
            else:
                if add_path and not delete_path:
                    path_list.append(bzr_dir.decode(user_encoding))
                    f_change = True

            if f_change:
                path_u = os.pathsep.join(path_list)
                if dry_run:
                    print "*** Registry key %s\\%s" % (hkey_str[key], subkey)
                    print "*** Modify PATH variable. New value:"
                    print path_u
                else:
                    _winreg.SetValueEx(hkey, 'Path', 0, type_, path_u)
                    _winreg.FlushKey(hkey)

        if not hkey is None:
            _winreg.CloseKey(hkey)

    if add_shell_menu and not delete_shell_menu:
        hkey = None
        try:
            hkey = _winreg.CreateKey(_winreg.HKEY_CLASSES_ROOT,
                                     r'Folder\shell\bzr')
        except EnvironmentError:
            if not silent:
                MessageBoxA(None,
                            'Unable to create registry key for context menu',
                            'EnvironmentError',
                            MB_OK | MB_ICONERROR)

        if not hkey is None:
            _winreg.SetValue(hkey, '', _winreg.REG_SZ, 'Bzr Here')
            hkey2 = _winreg.CreateKey(hkey, 'command')
            _winreg.SetValue(hkey2, '', _winreg.REG_SZ,
                             'cmd /K "%s"' % os.path.join(bzr_dir,
                                                          'start_bzr.bat'))
            _winreg.CloseKey(hkey2)
            _winreg.CloseKey(hkey)

    if delete_shell_menu:
        try:
            _winreg.DeleteKey(_winreg.HKEY_CLASSES_ROOT,
                              r'Folder\shell\bzr\command')
        except EnvironmentError:
            pass

        try:
            _winreg.DeleteKey(_winreg.HKEY_CLASSES_ROOT,
                              r'Folder\shell\bzr')
        except EnvironmentError:
            pass

    if check_mfc71:
        try:
            ctypes.windll.LoadLibrary('mfc71.dll')
        except WindowsError:
            MessageBoxA(None,
                        ("Library MFC71.DLL is not found on your system.\n"
                         "This library needed for SFTP transport.\n"
                         "If you need to work via SFTP you should download\n"
                         "this library manually and put it to directory\n"
                         "where Bzr installed.\n"
                         "For detailed instructions see:\n"
                         "http://bazaar-vcs.org/BzrOnPureWindows"
                        ),
                        "Warning",
                        MB_OK | MB_ICONEXCLAMATION)

    return OK


if __name__ == "__main__":
    sys.exit(main())
