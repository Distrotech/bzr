# Copyright (C) 2006-2010 Canonical Ltd
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

"""Tests for the Branch facility that are not interface  tests.

For interface tests see tests/per_branch/*.py.

For concrete class tests see this file, and for meta-branch tests
also see this file.
"""

from cStringIO import StringIO

from bzrlib import (
    branch as _mod_branch,
    bzrdir,
    config,
    errors,
    symbol_versioning,
    tests,
    trace,
    transport,
    urlutils,
    )


class TestDefaultFormat(tests.TestCase):

    def test_default_format(self):
        # update this if you change the default branch format
        self.assertIsInstance(_mod_branch.BranchFormat.get_default_format(),
                _mod_branch.BzrBranchFormat7)

    def test_default_format_is_same_as_bzrdir_default(self):
        # XXX: it might be nice if there was only one place the default was
        # set, but at the moment that's not true -- mbp 20070814 --
        # https://bugs.launchpad.net/bzr/+bug/132376
        self.assertEqual(
            _mod_branch.BranchFormat.get_default_format(),
            bzrdir.BzrDirFormat.get_default_format().get_branch_format())

    def test_get_set_default_format(self):
        # set the format and then set it back again
        old_format = _mod_branch.BranchFormat.get_default_format()
        _mod_branch.BranchFormat.set_default_format(SampleBranchFormat())
        try:
            # the default branch format is used by the meta dir format
            # which is not the default bzrdir format at this point
            dir = bzrdir.BzrDirMetaFormat1().initialize('memory:///')
            result = dir.create_branch()
            self.assertEqual(result, 'A branch')
        finally:
            _mod_branch.BranchFormat.set_default_format(old_format)
        self.assertEqual(old_format,
                         _mod_branch.BranchFormat.get_default_format())


class TestBranchFormat5(tests.TestCaseWithTransport):
    """Tests specific to branch format 5"""

    def test_branch_format_5_uses_lockdir(self):
        url = self.get_url()
        bdir = bzrdir.BzrDirMetaFormat1().initialize(url)
        bdir.create_repository()
        branch = bdir.create_branch()
        t = self.get_transport()
        self.log("branch instance is %r" % branch)
        self.assert_(isinstance(branch, _mod_branch.BzrBranch5))
        self.assertIsDirectory('.', t)
        self.assertIsDirectory('.bzr/branch', t)
        self.assertIsDirectory('.bzr/branch/lock', t)
        branch.lock_write()
        self.addCleanup(branch.unlock)
        self.assertIsDirectory('.bzr/branch/lock/held', t)

    def test_set_push_location(self):
        conf = config.LocationConfig.from_string('# comment\n', '.', save=True)

        branch = self.make_branch('.', format='knit')
        branch.set_push_location('foo')
        local_path = urlutils.local_path_from_url(branch.base[:-1])
        self.assertFileEqual("# comment\n"
                             "[%s]\n"
                             "push_location = foo\n"
                             "push_location:policy = norecurse\n" % local_path,
                             config.locations_config_filename())

    # TODO RBC 20051029 test getting a push location from a branch in a
    # recursive section - that is, it appends the branch name.


class SampleBranchFormat(_mod_branch.BranchFormat):
    """A sample format

    this format is initializable, unsupported to aid in testing the
    open and open_downlevel routines.
    """

    def get_format_string(self):
        """See BzrBranchFormat.get_format_string()."""
        return "Sample branch format."

    def initialize(self, a_bzrdir, name=None):
        """Format 4 branches cannot be created."""
        t = a_bzrdir.get_branch_transport(self, name=name)
        t.put_bytes('format', self.get_format_string())
        return 'A branch'

    def is_supported(self):
        return False

    def open(self, transport, name=None, _found=False, ignore_fallbacks=False):
        return "opened branch."


# Demonstrating how lazy loading is often implemented:
# A constant string is created.
SampleSupportedBranchFormatString = "Sample supported branch format."

# And the format class can then reference the constant to avoid skew.
class SampleSupportedBranchFormat(_mod_branch.BranchFormat):
    """A sample supported format."""

    def get_format_string(self):
        """See BzrBranchFormat.get_format_string()."""
        return SampleSupportedBranchFormatString

    def initialize(self, a_bzrdir, name=None):
        t = a_bzrdir.get_branch_transport(self, name=name)
        t.put_bytes('format', self.get_format_string())
        return 'A branch'

    def open(self, transport, name=None, _found=False, ignore_fallbacks=False):
        return "opened supported branch."


class TestBzrBranchFormat(tests.TestCaseWithTransport):
    """Tests for the BzrBranchFormat facility."""

    def test_find_format(self):
        # is the right format object found for a branch?
        # create a branch with a few known format objects.
        # this is not quite the same as
        self.build_tree(["foo/", "bar/"])
        def check_format(format, url):
            dir = format._matchingbzrdir.initialize(url)
            dir.create_repository()
            format.initialize(dir)
            found_format = _mod_branch.BranchFormat.find_format(dir)
            self.failUnless(isinstance(found_format, format.__class__))
        check_format(_mod_branch.BzrBranchFormat5(), "bar")

    def test_find_format_factory(self):
        dir = bzrdir.BzrDirMetaFormat1().initialize(self.get_url())
        SampleSupportedBranchFormat().initialize(dir)
        factory = _mod_branch.MetaDirBranchFormatFactory(
            SampleSupportedBranchFormatString,
            "bzrlib.tests.test_branch", "SampleSupportedBranchFormat")
        _mod_branch.BranchFormat.register_format(factory)
        self.addCleanup(_mod_branch.BranchFormat.unregister_format, factory)
        b = _mod_branch.Branch.open(self.get_url())
        self.assertEqual(b, "opened supported branch.")

    def test_find_format_not_branch(self):
        dir = bzrdir.BzrDirMetaFormat1().initialize(self.get_url())
        self.assertRaises(errors.NotBranchError,
                          _mod_branch.BranchFormat.find_format,
                          dir)

    def test_find_format_unknown_format(self):
        dir = bzrdir.BzrDirMetaFormat1().initialize(self.get_url())
        SampleBranchFormat().initialize(dir)
        self.assertRaises(errors.UnknownFormatError,
                          _mod_branch.BranchFormat.find_format,
                          dir)

    def test_register_unregister_format(self):
        format = SampleBranchFormat()
        # make a control dir
        dir = bzrdir.BzrDirMetaFormat1().initialize(self.get_url())
        # make a branch
        format.initialize(dir)
        # register a format for it.
        _mod_branch.BranchFormat.register_format(format)
        # which branch.Open will refuse (not supported)
        self.assertRaises(errors.UnsupportedFormatError,
                          _mod_branch.Branch.open, self.get_url())
        self.make_branch_and_tree('foo')
        # but open_downlevel will work
        self.assertEqual(
            format.open(dir),
            bzrdir.BzrDir.open(self.get_url()).open_branch(unsupported=True))
        # unregister the format
        _mod_branch.BranchFormat.unregister_format(format)
        self.make_branch_and_tree('bar')


#Used by TestMetaDirBranchFormatFactory 
FakeLazyFormat = None


class TestMetaDirBranchFormatFactory(tests.TestCase):

    def test_get_format_string_does_not_load(self):
        """Formats have a static format string."""
        factory = _mod_branch.MetaDirBranchFormatFactory("yo", None, None)
        self.assertEqual("yo", factory.get_format_string())

    def test_call_loads(self):
        # __call__ is used by the network_format_registry interface to get a
        # Format.
        global FakeLazyFormat
        del FakeLazyFormat
        factory = _mod_branch.MetaDirBranchFormatFactory(None,
            "bzrlib.tests.test_branch", "FakeLazyFormat")
        self.assertRaises(AttributeError, factory)

    def test_call_returns_call_of_referenced_object(self):
        global FakeLazyFormat
        FakeLazyFormat = lambda:'called'
        factory = _mod_branch.MetaDirBranchFormatFactory(None,
            "bzrlib.tests.test_branch", "FakeLazyFormat")
        self.assertEqual('called', factory())


class TestBranch67(object):
    """Common tests for both branch 6 and 7 which are mostly the same."""

    def get_format_name(self):
        raise NotImplementedError(self.get_format_name)

    def get_format_name_subtree(self):
        raise NotImplementedError(self.get_format_name)

    def get_class(self):
        raise NotImplementedError(self.get_class)

    def test_creation(self):
        format = bzrdir.BzrDirMetaFormat1()
        format.set_branch_format(_mod_branch.BzrBranchFormat6())
        branch = self.make_branch('a', format=format)
        self.assertIsInstance(branch, self.get_class())
        branch = self.make_branch('b', format=self.get_format_name())
        self.assertIsInstance(branch, self.get_class())
        branch = _mod_branch.Branch.open('a')
        self.assertIsInstance(branch, self.get_class())

    def test_layout(self):
        branch = self.make_branch('a', format=self.get_format_name())
        self.failUnlessExists('a/.bzr/branch/last-revision')
        self.failIfExists('a/.bzr/branch/revision-history')
        self.failIfExists('a/.bzr/branch/references')

    def test_config(self):
        """Ensure that all configuration data is stored in the branch"""
        branch = self.make_branch('a', format=self.get_format_name())
        branch.set_parent('http://bazaar-vcs.org')
        self.failIfExists('a/.bzr/branch/parent')
        self.assertEqual('http://bazaar-vcs.org', branch.get_parent())
        branch.set_push_location('sftp://bazaar-vcs.org')
        config = branch.get_config()._get_branch_data_config()
        self.assertEqual('sftp://bazaar-vcs.org',
                         config.get_user_option('push_location'))
        branch.set_bound_location('ftp://bazaar-vcs.org')
        self.failIfExists('a/.bzr/branch/bound')
        self.assertEqual('ftp://bazaar-vcs.org', branch.get_bound_location())

    def test_set_revision_history(self):
        builder = self.make_branch_builder('.', format=self.get_format_name())
        builder.build_snapshot('foo', None,
            [('add', ('', None, 'directory', None))],
            message='foo')
        builder.build_snapshot('bar', None, [], message='bar')
        branch = builder.get_branch()
        branch.lock_write()
        self.addCleanup(branch.unlock)
        branch.set_revision_history(['foo', 'bar'])
        branch.set_revision_history(['foo'])
        self.assertRaises(errors.NotLefthandHistory,
                          branch.set_revision_history, ['bar'])

    def do_checkout_test(self, lightweight=False):
        tree = self.make_branch_and_tree('source',
            format=self.get_format_name_subtree())
        subtree = self.make_branch_and_tree('source/subtree',
            format=self.get_format_name_subtree())
        subsubtree = self.make_branch_and_tree('source/subtree/subsubtree',
            format=self.get_format_name_subtree())
        self.build_tree(['source/subtree/file',
                         'source/subtree/subsubtree/file'])
        subsubtree.add('file')
        subtree.add('file')
        subtree.add_reference(subsubtree)
        tree.add_reference(subtree)
        tree.commit('a revision')
        subtree.commit('a subtree file')
        subsubtree.commit('a subsubtree file')
        tree.branch.create_checkout('target', lightweight=lightweight)
        self.failUnlessExists('target')
        self.failUnlessExists('target/subtree')
        self.failUnlessExists('target/subtree/file')
        self.failUnlessExists('target/subtree/subsubtree/file')
        subbranch = _mod_branch.Branch.open('target/subtree/subsubtree')
        if lightweight:
            self.assertEndsWith(subbranch.base, 'source/subtree/subsubtree/')
        else:
            self.assertEndsWith(subbranch.base, 'target/subtree/subsubtree/')

    def test_checkout_with_references(self):
        self.do_checkout_test()

    def test_light_checkout_with_references(self):
        self.do_checkout_test(lightweight=True)

    def test_set_push(self):
        branch = self.make_branch('source', format=self.get_format_name())
        branch.get_config().set_user_option('push_location', 'old',
            store=config.STORE_LOCATION)
        warnings = []
        def warning(*args):
            warnings.append(args[0] % args[1:])
        _warning = trace.warning
        trace.warning = warning
        try:
            branch.set_push_location('new')
        finally:
            trace.warning = _warning
        self.assertEqual(warnings[0], 'Value "new" is masked by "old" from '
                         'locations.conf')


class TestBranch6(TestBranch67, tests.TestCaseWithTransport):

    def get_class(self):
        return _mod_branch.BzrBranch6

    def get_format_name(self):
        return "dirstate-tags"

    def get_format_name_subtree(self):
        return "dirstate-with-subtree"

    def test_set_stacked_on_url_errors(self):
        branch = self.make_branch('a', format=self.get_format_name())
        self.assertRaises(errors.UnstackableBranchFormat,
            branch.set_stacked_on_url, None)

    def test_default_stacked_location(self):
        branch = self.make_branch('a', format=self.get_format_name())
        self.assertRaises(errors.UnstackableBranchFormat, branch.get_stacked_on_url)


class TestBranch7(TestBranch67, tests.TestCaseWithTransport):

    def get_class(self):
        return _mod_branch.BzrBranch7

    def get_format_name(self):
        return "1.9"

    def get_format_name_subtree(self):
        return "development-subtree"

    def test_set_stacked_on_url_unstackable_repo(self):
        repo = self.make_repository('a', format='dirstate-tags')
        control = repo.bzrdir
        branch = _mod_branch.BzrBranchFormat7().initialize(control)
        target = self.make_branch('b')
        self.assertRaises(errors.UnstackableRepositoryFormat,
            branch.set_stacked_on_url, target.base)

    def test_clone_stacked_on_unstackable_repo(self):
        repo = self.make_repository('a', format='dirstate-tags')
        control = repo.bzrdir
        branch = _mod_branch.BzrBranchFormat7().initialize(control)
        # Calling clone should not raise UnstackableRepositoryFormat.
        cloned_bzrdir = control.clone('cloned')

    def _test_default_stacked_location(self):
        branch = self.make_branch('a', format=self.get_format_name())
        self.assertRaises(errors.NotStacked, branch.get_stacked_on_url)

    def test_stack_and_unstack(self):
        branch = self.make_branch('a', format=self.get_format_name())
        target = self.make_branch_and_tree('b', format=self.get_format_name())
        branch.set_stacked_on_url(target.branch.base)
        self.assertEqual(target.branch.base, branch.get_stacked_on_url())
        revid = target.commit('foo')
        self.assertTrue(branch.repository.has_revision(revid))
        branch.set_stacked_on_url(None)
        self.assertRaises(errors.NotStacked, branch.get_stacked_on_url)
        self.assertFalse(branch.repository.has_revision(revid))

    def test_open_opens_stacked_reference(self):
        branch = self.make_branch('a', format=self.get_format_name())
        target = self.make_branch_and_tree('b', format=self.get_format_name())
        branch.set_stacked_on_url(target.branch.base)
        branch = branch.bzrdir.open_branch()
        revid = target.commit('foo')
        self.assertTrue(branch.repository.has_revision(revid))


class BzrBranch8(tests.TestCaseWithTransport):

    def make_branch(self, location, format=None):
        if format is None:
            format = bzrdir.format_registry.make_bzrdir('1.9')
            format.set_branch_format(_mod_branch.BzrBranchFormat8())
        return tests.TestCaseWithTransport.make_branch(
            self, location, format=format)

    def create_branch_with_reference(self):
        branch = self.make_branch('branch')
        branch._set_all_reference_info({'file-id': ('path', 'location')})
        return branch

    @staticmethod
    def instrument_branch(branch, gets):
        old_get = branch._transport.get
        def get(*args, **kwargs):
            gets.append((args, kwargs))
            return old_get(*args, **kwargs)
        branch._transport.get = get

    def test_reference_info_caching_read_locked(self):
        gets = []
        branch = self.create_branch_with_reference()
        branch.lock_read()
        self.addCleanup(branch.unlock)
        self.instrument_branch(branch, gets)
        branch.get_reference_info('file-id')
        branch.get_reference_info('file-id')
        self.assertEqual(1, len(gets))

    def test_reference_info_caching_read_unlocked(self):
        gets = []
        branch = self.create_branch_with_reference()
        self.instrument_branch(branch, gets)
        branch.get_reference_info('file-id')
        branch.get_reference_info('file-id')
        self.assertEqual(2, len(gets))

    def test_reference_info_caching_write_locked(self):
        gets = []
        branch = self.make_branch('branch')
        branch.lock_write()
        self.instrument_branch(branch, gets)
        self.addCleanup(branch.unlock)
        branch._set_all_reference_info({'file-id': ('path2', 'location2')})
        path, location = branch.get_reference_info('file-id')
        self.assertEqual(0, len(gets))
        self.assertEqual('path2', path)
        self.assertEqual('location2', location)

    def test_reference_info_caches_cleared(self):
        branch = self.make_branch('branch')
        branch.lock_write()
        branch.set_reference_info('file-id', 'path2', 'location2')
        branch.unlock()
        doppelganger = _mod_branch.Branch.open('branch')
        doppelganger.set_reference_info('file-id', 'path3', 'location3')
        self.assertEqual(('path3', 'location3'),
                         branch.get_reference_info('file-id'))

class TestBranchReference(tests.TestCaseWithTransport):
    """Tests for the branch reference facility."""

    def test_create_open_reference(self):
        bzrdirformat = bzrdir.BzrDirMetaFormat1()
        t = transport.get_transport(self.get_url('.'))
        t.mkdir('repo')
        dir = bzrdirformat.initialize(self.get_url('repo'))
        dir.create_repository()
        target_branch = dir.create_branch()
        t.mkdir('branch')
        branch_dir = bzrdirformat.initialize(self.get_url('branch'))
        made_branch = _mod_branch.BranchReferenceFormat().initialize(
            branch_dir, target_branch=target_branch)
        self.assertEqual(made_branch.base, target_branch.base)
        opened_branch = branch_dir.open_branch()
        self.assertEqual(opened_branch.base, target_branch.base)

    def test_get_reference(self):
        """For a BranchReference, get_reference should reutrn the location."""
        branch = self.make_branch('target')
        checkout = branch.create_checkout('checkout', lightweight=True)
        reference_url = branch.bzrdir.root_transport.abspath('') + '/'
        # if the api for create_checkout changes to return different checkout types
        # then this file read will fail.
        self.assertFileEqual(reference_url, 'checkout/.bzr/branch/location')
        self.assertEqual(reference_url,
            _mod_branch.BranchReferenceFormat().get_reference(checkout.bzrdir))


class TestHooks(tests.TestCaseWithTransport):

    def test_constructor(self):
        """Check that creating a BranchHooks instance has the right defaults."""
        hooks = _mod_branch.BranchHooks()
        self.assertTrue("set_rh" in hooks, "set_rh not in %s" % hooks)
        self.assertTrue("post_push" in hooks, "post_push not in %s" % hooks)
        self.assertTrue("post_commit" in hooks, "post_commit not in %s" % hooks)
        self.assertTrue("pre_commit" in hooks, "pre_commit not in %s" % hooks)
        self.assertTrue("post_pull" in hooks, "post_pull not in %s" % hooks)
        self.assertTrue("post_uncommit" in hooks,
                        "post_uncommit not in %s" % hooks)
        self.assertTrue("post_change_branch_tip" in hooks,
                        "post_change_branch_tip not in %s" % hooks)
        self.assertTrue("post_branch_init" in hooks,
                        "post_branch_init not in %s" % hooks)
        self.assertTrue("post_switch" in hooks,
                        "post_switch not in %s" % hooks)

    def test_installed_hooks_are_BranchHooks(self):
        """The installed hooks object should be a BranchHooks."""
        # the installed hooks are saved in self._preserved_hooks.
        self.assertIsInstance(self._preserved_hooks[_mod_branch.Branch][1],
                              _mod_branch.BranchHooks)

    def test_post_branch_init_hook(self):
        calls = []
        _mod_branch.Branch.hooks.install_named_hook('post_branch_init',
            calls.append, None)
        self.assertLength(0, calls)
        branch = self.make_branch('a')
        self.assertLength(1, calls)
        params = calls[0]
        self.assertIsInstance(params, _mod_branch.BranchInitHookParams)
        self.assertTrue(hasattr(params, 'bzrdir'))
        self.assertTrue(hasattr(params, 'branch'))

    def test_post_branch_init_hook_repr(self):
        param_reprs = []
        _mod_branch.Branch.hooks.install_named_hook('post_branch_init',
            lambda params: param_reprs.append(repr(params)), None)
        branch = self.make_branch('a')
        self.assertLength(1, param_reprs)
        param_repr = param_reprs[0]
        self.assertStartsWith(param_repr, '<BranchInitHookParams of ')

    def test_post_switch_hook(self):
        from bzrlib import switch
        calls = []
        _mod_branch.Branch.hooks.install_named_hook('post_switch',
            calls.append, None)
        tree = self.make_branch_and_tree('branch-1')
        self.build_tree(['branch-1/file-1'])
        tree.add('file-1')
        tree.commit('rev1')
        to_branch = tree.bzrdir.sprout('branch-2').open_branch()
        self.build_tree(['branch-1/file-2'])
        tree.add('file-2')
        tree.remove('file-1')
        tree.commit('rev2')
        checkout = tree.branch.create_checkout('checkout')
        self.assertLength(0, calls)
        switch.switch(checkout.bzrdir, to_branch)
        self.assertLength(1, calls)
        params = calls[0]
        self.assertIsInstance(params, _mod_branch.SwitchHookParams)
        self.assertTrue(hasattr(params, 'to_branch'))
        self.assertTrue(hasattr(params, 'revision_id'))


class TestBranchOptions(tests.TestCaseWithTransport):

    def setUp(self):
        super(TestBranchOptions, self).setUp()
        self.branch = self.make_branch('.')
        self.config = self.branch.get_config()

    def check_append_revisions_only(self, expected_value, value=None):
        """Set append_revisions_only in config and check its interpretation."""
        if value is not None:
            self.config.set_user_option('append_revisions_only', value)
        self.assertEqual(expected_value,
                         self.branch._get_append_revisions_only())

    def test_valid_append_revisions_only(self):
        self.assertEquals(None,
                          self.config.get_user_option('append_revisions_only'))
        self.check_append_revisions_only(None)
        self.check_append_revisions_only(False, 'False')
        self.check_append_revisions_only(True, 'True')
        # The following values will cause compatibility problems on projects
        # using older bzr versions (<2.2) but are accepted
        self.check_append_revisions_only(False, 'false')
        self.check_append_revisions_only(True, 'true')

    def test_invalid_append_revisions_only(self):
        """Ensure warning is noted on invalid settings"""
        self.warnings = []
        def warning(*args):
            self.warnings.append(args[0] % args[1:])
        self.overrideAttr(trace, 'warning', warning)
        self.check_append_revisions_only(None, 'not-a-bool')
        self.assertLength(1, self.warnings)
        self.assertEqual(
            'Value "not-a-bool" is not a boolean for "append_revisions_only"',
            self.warnings[0])


class TestPullResult(tests.TestCase):

    def test_pull_result_to_int(self):
        # to support old code, the pull result can be used as an int
        r = _mod_branch.PullResult()
        r.old_revno = 10
        r.new_revno = 20
        # this usage of results is not recommended for new code (because it
        # doesn't describe very well what happened), but for api stability
        # it's still supported
        self.assertEqual(self.applyDeprecated(
            symbol_versioning.deprecated_in((2, 3, 0)),
            r.__int__),
            10)

    def test_report_changed(self):
        r = _mod_branch.PullResult()
        r.old_revid = "old-revid"
        r.old_revno = 10
        r.new_revid = "new-revid"
        r.new_revno = 20
        f = StringIO()
        r.report(f)
        self.assertEqual("Now on revision 20.\n", f.getvalue())

    def test_report_unchanged(self):
        r = _mod_branch.PullResult()
        r.old_revid = "same-revid"
        r.new_revid = "same-revid"
        f = StringIO()
        r.report(f)
        self.assertEqual("No revisions to pull.\n", f.getvalue())


class _StubLockable(object):
    """Helper for TestRunWithWriteLockedTarget."""

    def __init__(self, calls, unlock_exc=None):
        self.calls = calls
        self.unlock_exc = unlock_exc

    def lock_write(self):
        self.calls.append('lock_write')

    def unlock(self):
        self.calls.append('unlock')
        if self.unlock_exc is not None:
            raise self.unlock_exc


class _ErrorFromCallable(Exception):
    """Helper for TestRunWithWriteLockedTarget."""


class _ErrorFromUnlock(Exception):
    """Helper for TestRunWithWriteLockedTarget."""


class TestRunWithWriteLockedTarget(tests.TestCase):
    """Tests for _run_with_write_locked_target."""

    def setUp(self):
        tests.TestCase.setUp(self)
        self._calls = []

    def func_that_returns_ok(self):
        self._calls.append('func called')
        return 'ok'

    def func_that_raises(self):
        self._calls.append('func called')
        raise _ErrorFromCallable()

    def test_success_unlocks(self):
        lockable = _StubLockable(self._calls)
        result = _mod_branch._run_with_write_locked_target(
            lockable, self.func_that_returns_ok)
        self.assertEqual('ok', result)
        self.assertEqual(['lock_write', 'func called', 'unlock'], self._calls)

    def test_exception_unlocks_and_propagates(self):
        lockable = _StubLockable(self._calls)
        self.assertRaises(_ErrorFromCallable,
                          _mod_branch._run_with_write_locked_target,
                          lockable, self.func_that_raises)
        self.assertEqual(['lock_write', 'func called', 'unlock'], self._calls)

    def test_callable_succeeds_but_error_during_unlock(self):
        lockable = _StubLockable(self._calls, unlock_exc=_ErrorFromUnlock())
        self.assertRaises(_ErrorFromUnlock,
                          _mod_branch._run_with_write_locked_target,
                          lockable, self.func_that_returns_ok)
        self.assertEqual(['lock_write', 'func called', 'unlock'], self._calls)

    def test_error_during_unlock_does_not_mask_original_error(self):
        lockable = _StubLockable(self._calls, unlock_exc=_ErrorFromUnlock())
        self.assertRaises(_ErrorFromCallable,
                          _mod_branch._run_with_write_locked_target,
                          lockable, self.func_that_raises)
        self.assertEqual(['lock_write', 'func called', 'unlock'], self._calls)


