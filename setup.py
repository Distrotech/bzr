#! /usr/bin/env python

"""Installation script for bzr.
Run it with
 './setup.py install', or
 './setup.py --help' for more options
"""

import os
import os.path
import sys
import copy
import glob

if sys.version_info < (2, 6):
    sys.stderr.write("[ERROR] Not a supported Python version. Need 2.6+\n")
    sys.exit(1)

# NOTE: The directory containing setup.py, whether run by 'python setup.py' or
# './setup.py' or the equivalent with another path, should always be at the
# start of the path, so this should find the right one...
import bzrlib

def get_long_description():
    dirname = os.path.dirname(__file__)
    readme = os.path.join(dirname, 'README')
    f = open(readme, 'rb')
    try:
        return f.read()
    finally:
        f.close()


##
# META INFORMATION FOR SETUP
# see http://docs.python.org/dist/meta-data.html
META_INFO = {
    'name':         'bzr',
    'version':      bzrlib.__version__,
    'author':       'Canonical Ltd',
    'author_email': 'bazaar@lists.canonical.com',
    'url':          'http://bazaar.canonical.com/',
    'description':  'Friendly distributed version control system',
    'license':      'GNU GPL v2',
    'download_url': 'https://launchpad.net/bzr/+download',
    'long_description': get_long_description(),
    'classifiers': [
        'Development Status :: 6 - Mature',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'License :: OSI Approved :: GNU General Public License (GPL)',
        'Operating System :: Microsoft :: Windows',
        'Operating System :: OS Independent',
        'Operating System :: POSIX',
        'Programming Language :: Python',
        'Programming Language :: C',
        'Topic :: Software Development :: Version Control',
        ],
    }

# The list of packages is automatically generated later. Add other things
# that are part of BZRLIB here.
BZRLIB = {}

PKG_DATA = {# install files from selftest suite
            'package_data': {'bzrlib': ['doc/api/*.txt',
                                        'tests/test_patches_data/*',
                                        'help_topics/en/*.txt',
                                        'tests/ssl_certs/ca.crt',
                                        'tests/ssl_certs/server_without_pass.key',
                                        'tests/ssl_certs/server_with_pass.key',
                                        'tests/ssl_certs/server.crt',
                                       ]},
           }
I18N_FILES = []
for filepath in glob.glob("bzrlib/locale/*/LC_MESSAGES/*.mo"):
    langfile = filepath[len("bzrlib/locale/"):]
    targetpath = os.path.dirname(os.path.join("share/locale", langfile))
    I18N_FILES.append((targetpath, [filepath]))

def get_bzrlib_packages():
    """Recurse through the bzrlib directory, and extract the package names"""

    packages = []
    base_path = os.path.dirname(os.path.abspath(bzrlib.__file__))
    for root, dirs, files in os.walk(base_path):
        if '__init__.py' in files:
            assert root.startswith(base_path)
            # Get just the path below bzrlib
            package_path = root[len(base_path):]
            # Remove leading and trailing slashes
            package_path = package_path.strip('\\/')
            if not package_path:
                package_name = 'bzrlib'
            else:
                package_name = ('bzrlib.' +
                            package_path.replace('/', '.').replace('\\', '.'))
            packages.append(package_name)
    return sorted(packages)


BZRLIB['packages'] = get_bzrlib_packages()


from distutils import log
from distutils.core import setup
from distutils.command.install_scripts import install_scripts
from distutils.command.install_data import install_data
from distutils.command.build import build

###############################
# Overridden distutils actions
###############################

class my_install_scripts(install_scripts):
    """ Customized install_scripts distutils action.
    Create bzr.bat for win32.
    """
    def run(self):
        install_scripts.run(self)   # standard action

        if sys.platform == "win32":
            try:
                scripts_dir = os.path.join(sys.prefix, 'Scripts')
                script_path = self._quoted_path(os.path.join(scripts_dir,
                                                             "bzr"))
                python_exe = self._quoted_path(sys.executable)
                args = self._win_batch_args()
                batch_str = "@%s %s %s" % (python_exe, script_path, args)
                batch_path = os.path.join(self.install_dir, "bzr.bat")
                f = file(batch_path, "w")
                f.write(batch_str)
                f.close()
                print("Created: %s" % batch_path)
            except Exception:
                e = sys.exc_info()[1]
                print("ERROR: Unable to create %s: %s" % (batch_path, e))

    def _quoted_path(self, path):
        if ' ' in path:
            return '"' + path + '"'
        else:
            return path

    def _win_batch_args(self):
        from bzrlib.win32utils import winver
        if winver == 'Windows NT':
            return '%*'
        else:
            return '%1 %2 %3 %4 %5 %6 %7 %8 %9'
#/class my_install_scripts


class bzr_build(build):
    """Customized build distutils action.
    Generate bzr.1.
    """

    sub_commands = build.sub_commands + [
            ('build_mo', lambda _: True),
            ]

    def run(self):
        build.run(self)

        from tools import generate_docs
        generate_docs.main(argv=["bzr", "man"])


########################
## Setup
########################

from bzrlib.bzr_distutils import build_mo

command_classes = {'install_scripts': my_install_scripts,
                   'build': bzr_build,
                   'build_mo': build_mo,
                   }
from distutils import log
from distutils.errors import CCompilerError, DistutilsPlatformError
from distutils.extension import Extension
ext_modules = []
try:
    try:
        from Cython.Distutils import build_ext
        from Cython.Compiler.Version import version as pyrex_version
    except ImportError:
        print("No Cython, trying Pyrex...")
        from Pyrex.Distutils import build_ext
        from Pyrex.Compiler.Version import version as pyrex_version
except ImportError:
    have_pyrex = False
    # try to build the extension from the prior generated source.
    print("")
    print("The python package 'Pyrex' is not available."
          " If the .c files are available,")
    print("they will be built,"
          " but modifying the .pyx files will not rebuild them.")
    print("")
    from distutils.command.build_ext import build_ext
else:
    have_pyrex = True
    pyrex_version_info = tuple(map(int, pyrex_version.rstrip("+").split('.')))


class build_ext_if_possible(build_ext):

    user_options = build_ext.user_options + [
        ('allow-python-fallback', None,
         "When an extension cannot be built, allow falling"
         " back to the pure-python implementation.")
        ]

    def initialize_options(self):
        build_ext.initialize_options(self)
        self.allow_python_fallback = False

    def run(self):
        try:
            build_ext.run(self)
        except DistutilsPlatformError:
            e = sys.exc_info()[1]
            if not self.allow_python_fallback:
                log.warn('\n  Cannot build extensions.\n'
                         '  Use "build_ext --allow-python-fallback" to use'
                         ' slower python implementations instead.\n')
                raise
            log.warn(str(e))
            log.warn('\n  Extensions cannot be built.\n'
                     '  Using the slower Python implementations instead.\n')

    def build_extension(self, ext):
        try:
            build_ext.build_extension(self, ext)
        except CCompilerError:
            if not self.allow_python_fallback:
                log.warn('\n  Cannot build extension "%s".\n'
                         '  Use "build_ext --allow-python-fallback" to use'
                         ' slower python implementations instead.\n'
                         % (ext.name,))
                raise
            log.warn('\n  Building of "%s" extension failed.\n'
                     '  Using the slower Python implementation instead.'
                     % (ext.name,))


# Override the build_ext if we have Pyrex available
command_classes['build_ext'] = build_ext_if_possible
unavailable_files = []


def add_pyrex_extension(module_name, libraries=None, extra_source=[]):
    """Add a pyrex module to build.

    This will use Pyrex to auto-generate the .c file if it is available.
    Otherwise it will fall back on the .c file. If the .c file is not
    available, it will warn, and not add anything.

    You can pass any extra options to Extension through kwargs. One example is
    'libraries = []'.

    :param module_name: The python path to the module. This will be used to
        determine the .pyx and .c files to use.
    """
    path = module_name.replace('.', '/')
    pyrex_name = path + '.pyx'
    c_name = path + '.c'
    define_macros = []
    if sys.platform == 'win32':
        # pyrex uses the macro WIN32 to detect the platform, even though it
        # should be using something like _WIN32 or MS_WINDOWS, oh well, we can
        # give it the right value.
        define_macros.append(('WIN32', None))
    if have_pyrex:
        source = [pyrex_name]
    else:
        if not os.path.isfile(c_name):
            unavailable_files.append(c_name)
            return
        else:
            source = [c_name]
    source.extend(extra_source)
    ext_modules.append(Extension(module_name, source,
        define_macros=define_macros, libraries=libraries))


add_pyrex_extension('bzrlib._annotator_pyx')
add_pyrex_extension('bzrlib._bencode_pyx')
add_pyrex_extension('bzrlib._chunks_to_lines_pyx')
add_pyrex_extension('bzrlib._groupcompress_pyx',
                    extra_source=['bzrlib/diff-delta.c'])
add_pyrex_extension('bzrlib._knit_load_data_pyx')
add_pyrex_extension('bzrlib._known_graph_pyx')
add_pyrex_extension('bzrlib._rio_pyx')
if sys.platform == 'win32':
    add_pyrex_extension('bzrlib._dirstate_helpers_pyx',
                        libraries=['Ws2_32'])
    add_pyrex_extension('bzrlib._walkdirs_win32')
else:
    if have_pyrex and pyrex_version_info[:3] == (0,9,4):
        # Pyrex 0.9.4.1 fails to compile this extension correctly
        # The code it generates re-uses a "local" pointer and
        # calls "PY_DECREF" after having set it to NULL. (It mixes PY_XDECREF
        # which is NULL safe with PY_DECREF which is not.)
        # <https://bugs.launchpad.net/bzr/+bug/449372>
        # <https://bugs.launchpad.net/bzr/+bug/276868>
        print('Cannot build extension "bzrlib._dirstate_helpers_pyx" using')
        print('your version of pyrex "%s". Please upgrade your pyrex'
              % (pyrex_version,))
        print('install. For now, the non-compiled (python) version will')
        print('be used instead.')
    else:
        add_pyrex_extension('bzrlib._dirstate_helpers_pyx')
    add_pyrex_extension('bzrlib._readdir_pyx')
add_pyrex_extension('bzrlib._chk_map_pyx')
ext_modules.append(Extension('bzrlib._patiencediff_c',
                             ['bzrlib/_patiencediff_c.c']))
if have_pyrex and pyrex_version_info < (0, 9, 6, 3):
    print("")
    print('Your Pyrex/Cython version %s is too old to build the simple_set' % (
        pyrex_version))
    print('and static_tuple extensions.')
    print('Please upgrade to at least Pyrex 0.9.6.3')
    print("")
    # TODO: Should this be a fatal error?
else:
    # We only need 0.9.6.3 to build _simple_set_pyx, but static_tuple depends
    # on simple_set
    add_pyrex_extension('bzrlib._simple_set_pyx')
    ext_modules.append(Extension('bzrlib._static_tuple_c',
                                 ['bzrlib/_static_tuple_c.c']))
add_pyrex_extension('bzrlib._btree_serializer_pyx')


if unavailable_files:
    print('C extension(s) not found:')
    print('   %s' % ('\n  '.join(unavailable_files),))
    print('The python versions will be used instead.')
    print("")


def get_tbzr_py2exe_info(includes, excludes, packages, console_targets,
                         gui_targets, data_files):
    packages.append('tbzrcommands')

    # ModuleFinder can't handle runtime changes to __path__, but
    # win32com uses them.  Hook this in so win32com.shell is found.
    import modulefinder
    import win32com
    import cPickle as pickle
    for p in win32com.__path__[1:]:
        modulefinder.AddPackagePath("win32com", p)
    for extra in ["win32com.shell"]:
        __import__(extra)
        m = sys.modules[extra]
        for p in m.__path__[1:]:
            modulefinder.AddPackagePath(extra, p)

    # TBZR points to the TBZR directory
    tbzr_root = os.environ["TBZR"]

    # Ensure tbzrlib itself is on sys.path
    sys.path.append(tbzr_root)

    packages.append("tbzrlib")

    # collect up our icons.
    cwd = os.getcwd()
    ico_root = os.path.join(tbzr_root, 'tbzrlib', 'resources')
    icos = [] # list of (path_root, relative_ico_path)
    # First always bzr's icon and its in the root of the bzr tree.
    icos.append(('', 'bzr.ico'))
    for root, dirs, files in os.walk(ico_root):
        icos.extend([(ico_root, os.path.join(root, f)[len(ico_root)+1:])
                     for f in files if f.endswith('.ico')])
    # allocate an icon ID for each file and the full path to the ico
    icon_resources = [(rid, os.path.join(ico_dir, ico_name))
                      for rid, (ico_dir, ico_name) in enumerate(icos)]
    # create a string resource with the mapping.  Might as well save the
    # runtime some effort and write a pickle.
    # Runtime expects unicode objects with forward-slash seps.
    fse = sys.getfilesystemencoding()
    map_items = [(f.replace('\\', '/').decode(fse), rid)
                 for rid, (_, f) in enumerate(icos)]
    ico_map = dict(map_items)
    # Create a new resource type of 'ICON_MAP', and use ID=1
    other_resources = [ ("ICON_MAP", 1, pickle.dumps(ico_map))]

    excludes.extend("""pywin pywin.dialogs pywin.dialogs.list
                       win32ui crawler.Crawler""".split())

    # tbzrcache executables - a "console" version for debugging and a
    # GUI version that is generally used.
    tbzrcache = dict(
        script = os.path.join(tbzr_root, "scripts", "tbzrcache.py"),
        icon_resources = icon_resources,
        other_resources = other_resources,
    )
    console_targets.append(tbzrcache)

    # Make a windows version which is the same except for the base name.
    tbzrcachew = tbzrcache.copy()
    tbzrcachew["dest_base"]="tbzrcachew"
    gui_targets.append(tbzrcachew)

    # ditto for the tbzrcommand tool
    tbzrcommand = dict(
        script = os.path.join(tbzr_root, "scripts", "tbzrcommand.py"),
        icon_resources = icon_resources,
        other_resources = other_resources,
    )
    console_targets.append(tbzrcommand)
    tbzrcommandw = tbzrcommand.copy()
    tbzrcommandw["dest_base"]="tbzrcommandw"
    gui_targets.append(tbzrcommandw)
    
    # A utility to see python output from both C++ and Python based shell
    # extensions
    tracer = dict(script=os.path.join(tbzr_root, "scripts", "tbzrtrace.py"))
    console_targets.append(tracer)

    # The C++ implemented shell extensions.
    dist_dir = os.path.join(tbzr_root, "shellext", "build")
    data_files.append(('', [os.path.join(dist_dir, 'tbzrshellext_x86.dll')]))
    data_files.append(('', [os.path.join(dist_dir, 'tbzrshellext_x64.dll')]))


def get_qbzr_py2exe_info(includes, excludes, packages, data_files):
    # PyQt4 itself still escapes the plugin detection code for some reason...
    includes.append('PyQt4.QtCore')
    includes.append('PyQt4.QtGui')
    includes.append('PyQt4.QtTest')
    includes.append('sip') # extension module required for Qt.
    packages.append('pygments') # colorizer for qbzr
    packages.append('docutils') # html formatting
    includes.append('win32event')  # for qsubprocess stuff
    # the qt binaries might not be on PATH...
    # They seem to install to a place like C:\Python25\PyQt4\*
    # Which is not the same as C:\Python25\Lib\site-packages\PyQt4
    pyqt_dir = os.path.join(sys.prefix, "PyQt4")
    pyqt_bin_dir = os.path.join(pyqt_dir, "bin")
    if os.path.isdir(pyqt_bin_dir):
        path = os.environ.get("PATH", "")
        if pyqt_bin_dir.lower() not in [p.lower() for p in path.split(os.pathsep)]:
            os.environ["PATH"] = path + os.pathsep + pyqt_bin_dir
    # also add all imageformat plugins to distribution
    # We will look in 2 places, dirname(PyQt4.__file__) and pyqt_dir
    base_dirs_to_check = []
    if os.path.isdir(pyqt_dir):
        base_dirs_to_check.append(pyqt_dir)
    try:
        import PyQt4
    except ImportError:
        pass
    else:
        pyqt4_base_dir = os.path.dirname(PyQt4.__file__)
        if pyqt4_base_dir != pyqt_dir:
            base_dirs_to_check.append(pyqt4_base_dir)
    if not base_dirs_to_check:
        log.warn("Can't find PyQt4 installation -> not including imageformat"
                 " plugins")
    else:
        files = []
        for base_dir in base_dirs_to_check:
            plug_dir = os.path.join(base_dir, 'plugins', 'imageformats')
            if os.path.isdir(plug_dir):
                for fname in os.listdir(plug_dir):
                    # Include plugin dlls, but not debugging dlls
                    fullpath = os.path.join(plug_dir, fname)
                    if fname.endswith('.dll') and not fname.endswith('d4.dll'):
                        files.append(fullpath)
        if files:
            data_files.append(('imageformats', files))
        else:
            log.warn('PyQt4 was found, but we could not find any imageformat'
                     ' plugins. Are you sure your configuration is correct?')


def get_svn_py2exe_info(includes, excludes, packages):
    packages.append('subvertpy')
    packages.append('sqlite3')


def get_git_py2exe_info(includes, excludes, packages):
    packages.append('dulwich')


def get_fastimport_py2exe_info(includes, excludes, packages):
    # This is the python-fastimport package, not to be confused with the
    # bzr-fastimport plugin.
    packages.append('fastimport')


if 'bdist_wininst' in sys.argv:
    def find_docs():
        docs = []
        for root, dirs, files in os.walk('doc'):
            r = []
            for f in files:
                if (os.path.splitext(f)[1] in ('.html','.css','.png','.pdf')
                    or f == 'quick-start-summary.svg'):
                    r.append(os.path.join(root, f))
            if r:
                relative = root[4:]
                if relative:
                    target = os.path.join('Doc\\Bazaar', relative)
                else:
                    target = 'Doc\\Bazaar'
                docs.append((target, r))
        return docs

    # python's distutils-based win32 installer
    ARGS = {'scripts': ['bzr', 'tools/win32/bzr-win32-bdist-postinstall.py'],
            'ext_modules': ext_modules,
            # help pages
            'data_files': find_docs(),
            # for building pyrex extensions
            'cmdclass': command_classes,
           }

    ARGS.update(META_INFO)
    ARGS.update(BZRLIB)
    PKG_DATA['package_data']['bzrlib'].append('locale/*/LC_MESSAGES/*.mo')
    ARGS.update(PKG_DATA)

    setup(**ARGS)

elif 'py2exe' in sys.argv:
    # py2exe setup
    import py2exe

    # pick real bzr version
    import bzrlib

    version_number = []
    for i in bzrlib.version_info[:4]:
        try:
            i = int(i)
        except ValueError:
            i = 0
        version_number.append(str(i))
    version_str = '.'.join(version_number)

    # An override to install_data used only by py2exe builds, which arranges
    # to byte-compile any .py files in data_files (eg, our plugins)
    # Necessary as we can't rely on the user having the relevant permissions
    # to the "Program Files" directory to generate them on the fly.
    class install_data_with_bytecompile(install_data):
        def run(self):
            from distutils.util import byte_compile

            install_data.run(self)

            py2exe = self.distribution.get_command_obj('py2exe', False)
            # GZ 2010-04-19: Setup has py2exe.optimize as 2, but give plugins
            #                time before living with docstring stripping
            optimize = 1
            compile_names = [f for f in self.outfiles if f.endswith('.py')]
            # Round mtime to nearest even second so that installing on a FAT
            # filesystem bytecode internal and script timestamps will match
            for f in compile_names:
                mtime = os.stat(f).st_mtime
                remainder = mtime % 2
                if remainder:
                    mtime -= remainder
                    os.utime(f, (mtime, mtime))
            byte_compile(compile_names,
                         optimize=optimize,
                         force=self.force, prefix=self.install_dir,
                         dry_run=self.dry_run)
            self.outfiles.extend([f + 'o' for f in compile_names])
    # end of class install_data_with_bytecompile

    target = py2exe.build_exe.Target(script = "bzr",
                                     dest_base = "bzr",
                                     icon_resources = [(0,'bzr.ico')],
                                     name = META_INFO['name'],
                                     version = version_str,
                                     description = META_INFO['description'],
                                     author = META_INFO['author'],
                                     copyright = "(c) Canonical Ltd, 2005-2010",
                                     company_name = "Canonical Ltd.",
                                     comments = META_INFO['description'],
                                    )
    gui_target = copy.copy(target)
    gui_target.dest_base = "bzrw"

    packages = BZRLIB['packages']
    packages.remove('bzrlib')
    packages = [i for i in packages if not i.startswith('bzrlib.plugins')]
    includes = []
    for i in glob.glob('bzrlib\\*.py'):
        module = i[:-3].replace('\\', '.')
        if module.endswith('__init__'):
            module = module[:-len('__init__')]
        includes.append(module)

    additional_packages = set()
    if sys.version.startswith('2.4'):
        # adding elementtree package
        additional_packages.add('elementtree')
    elif sys.version.startswith('2.6') or sys.version.startswith('2.5'):
        additional_packages.add('xml.etree')
    else:
        import warnings
        warnings.warn('Unknown Python version.\n'
                      'Please check setup.py script for compatibility.')

    # Although we currently can't enforce it, we consider it an error for
    # py2exe to report any files are "missing".  Such modules we know aren't
    # used should be listed here.
    excludes = """Tkinter psyco ElementPath r_hmac
                  ImaginaryModule cElementTree elementtree.ElementTree
                  Crypto.PublicKey._fastmath
                  medusa medusa.filesys medusa.ftp_server
                  tools
                  resource validate""".split()
    dll_excludes = []

    # email package from std python library use lazy import,
    # so we need to explicitly add all package
    additional_packages.add('email')
    # And it uses funky mappings to conver to 'Oldname' to 'newname'.  As
    # a result, packages like 'email.Parser' show as missing.  Tell py2exe
    # to exclude them.
    import email
    for oldname in getattr(email, '_LOWERNAMES', []):
        excludes.append("email." + oldname)
    for oldname in getattr(email, '_MIMENAMES', []):
        excludes.append("email.MIME" + oldname)

    # text files for help topis
    text_topics = glob.glob('bzrlib/help_topics/en/*.txt')
    topics_files = [('lib/help_topics/en', text_topics)]

    # built-in plugins
    plugins_files = []
    # XXX - should we consider having the concept of an 'official' build,
    # which hard-codes the list of plugins, gets more upset if modules are
    # missing, etc?
    plugins = None # will be a set after plugin sniffing...
    for root, dirs, files in os.walk('bzrlib/plugins'):
        if root == 'bzrlib/plugins':
            plugins = set(dirs)
            # We ship plugins as normal files on the file-system - however,
            # the build process can cause *some* of these plugin files to end
            # up in library.zip. Thus, we saw (eg) "plugins/svn/test" in
            # library.zip, and then saw import errors related to that as the
            # rest of the svn plugin wasn't. So we tell py2exe to leave the
            # plugins out of the .zip file
            excludes.extend(["bzrlib.plugins." + d for d in dirs])
        x = []
        for i in files:
            # Throw away files we don't want packaged. Note that plugins may
            # have data files with all sorts of extensions so we need to
            # be conservative here about what we ditch.
            ext = os.path.splitext(i)[1]
            if ext.endswith('~') or ext in [".pyc", ".swp"]:
                continue
            if i == '__init__.py' and root == 'bzrlib/plugins':
                continue
            x.append(os.path.join(root, i))
        if x:
            target_dir = root[len('bzrlib/'):]  # install to 'plugins/...'
            plugins_files.append((target_dir, x))
    # find modules for built-in plugins
    import tools.package_mf
    mf = tools.package_mf.CustomModuleFinder()
    mf.run_package('bzrlib/plugins')
    packs, mods = mf.get_result()
    additional_packages.update(packs)
    includes.extend(mods)

    console_targets = [target,
                       'tools/win32/bzr_postinstall.py',
                       ]
    gui_targets = [gui_target]
    data_files = topics_files + plugins_files + I18N_FILES

    if 'qbzr' in plugins:
        get_qbzr_py2exe_info(includes, excludes, packages, data_files)

    if 'svn' in plugins:
        get_svn_py2exe_info(includes, excludes, packages)

    if 'git' in plugins:
        get_git_py2exe_info(includes, excludes, packages)

    if 'fastimport' in plugins:
        get_fastimport_py2exe_info(includes, excludes, packages)

    if "TBZR" in os.environ:
        # TORTOISE_OVERLAYS_MSI_WIN32 must be set to the location of the
        # TortoiseOverlays MSI installer file. It is in the TSVN svn repo and
        # can be downloaded from (username=guest, blank password):
        # http://tortoisesvn.tigris.org/svn/tortoisesvn/TortoiseOverlays
        # look for: version-1.0.4/bin/TortoiseOverlays-1.0.4.11886-win32.msi
        # Ditto for TORTOISE_OVERLAYS_MSI_X64, pointing at *-x64.msi.
        for needed in ('TORTOISE_OVERLAYS_MSI_WIN32',
                       'TORTOISE_OVERLAYS_MSI_X64'):
            url = ('http://guest:@tortoisesvn.tigris.org/svn/tortoisesvn'
                   '/TortoiseOverlays')
            if not os.path.isfile(os.environ.get(needed, '<nofile>')):
                raise RuntimeError(
                    "\nPlease set %s to the location of the relevant"
                    "\nTortoiseOverlays .msi installer file."
                    " The installers can be found at"
                    "\n  %s"
                    "\ncheck in the version-X.Y.Z/bin/ subdir" % (needed, url))
        get_tbzr_py2exe_info(includes, excludes, packages, console_targets,
                             gui_targets, data_files)
    else:
        # print this warning to stderr as output is redirected, so it is seen
        # at build time.  Also to stdout so it appears in the log
        for f in (sys.stderr, sys.stdout):
            f.write("Skipping TBZR binaries - "
                "please set TBZR to a directory to enable\n")

    # MSWSOCK.dll is a system-specific library, which py2exe accidentally pulls
    # in on Vista.
    dll_excludes.extend(["MSWSOCK.dll",
                         "MSVCP60.dll",
                         "MSVCP90.dll",
                         "powrprof.dll",
                         "SHFOLDER.dll"])
    options_list = {"py2exe": {"packages": packages + list(additional_packages),
                               "includes": includes,
                               "excludes": excludes,
                               "dll_excludes": dll_excludes,
                               "dist_dir": "win32_bzr.exe",
                               "optimize": 2,
                               "custom_boot_script":
                                        "tools/win32/py2exe_boot_common.py",
                              },
                   }

    # We want the libaray.zip to have optimize = 2, but the exe to have
    # optimize = 1, so that .py files that get compilied at run time
    # (e.g. user installed plugins) dont have their doc strings removed.
    class py2exe_no_oo_exe(py2exe.build_exe.py2exe):
        def build_executable(self, *args, **kwargs):
            self.optimize = 1
            py2exe.build_exe.py2exe.build_executable(self, *args, **kwargs)
            self.optimize = 2

    if __name__ == '__main__':
        command_classes['install_data'] = install_data_with_bytecompile
        command_classes['py2exe'] = py2exe_no_oo_exe
        setup(options=options_list,
              console=console_targets,
              windows=gui_targets,
              zipfile='lib/library.zip',
              data_files=data_files,
              cmdclass=command_classes,
              )

else:
    # ad-hoc for easy_install
    DATA_FILES = []
    if not 'bdist_egg' in sys.argv:
        # generate and install bzr.1 only with plain install, not the
        # easy_install one
        DATA_FILES = [('share/man/man1', ['bzr.1'])]

    DATA_FILES = DATA_FILES + I18N_FILES
    # std setup
    ARGS = {'scripts': ['bzr'],
            'data_files': DATA_FILES,
            'cmdclass': command_classes,
            'ext_modules': ext_modules,
           }

    ARGS.update(META_INFO)
    ARGS.update(BZRLIB)
    ARGS.update(PKG_DATA)

    if __name__ == '__main__':
        setup(**ARGS)
