# Copyright (C) 2010, 2011 Canonical Ltd
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

"""ControlDir is the basic control directory class.

The ControlDir class is the base for the control directory used
by all bzr and foreign formats. For the ".bzr" implementation,
see bzrlib.bzrdir.BzrDir.

"""

from __future__ import absolute_import

from bzrlib.lazy_import import lazy_import
lazy_import(globals(), """
import textwrap

from bzrlib import (
    errors,
    hooks,
    revision as _mod_revision,
    transport as _mod_transport,
    trace,
    ui,
    urlutils,
    )
from bzrlib.transport import local
from bzrlib.push import (
    PushResult,
    )

from bzrlib.i18n import gettext
""")

from bzrlib import registry


class ControlComponent(object):
    """Abstract base class for control directory components.

    This provides interfaces that are common across controldirs,
    repositories, branches, and workingtree control directories.

    They all expose two urls and transports: the *user* URL is the
    one that stops above the control directory (eg .bzr) and that
    should normally be used in messages, and the *control* URL is
    under that in eg .bzr/checkout and is used to read the control
    files.

    This can be used as a mixin and is intended to fit with
    foreign formats.
    """

    @property
    def control_transport(self):
        raise NotImplementedError

    @property
    def control_url(self):
        return self.control_transport.base

    @property
    def user_transport(self):
        raise NotImplementedError

    @property
    def user_url(self):
        return self.user_transport.base


class ControlDir(ControlComponent):
    """A control directory.

    While this represents a generic control directory, there are a few
    features that are present in this interface that are currently only
    supported by one of its implementations, BzrDir.

    These features (bound branches, stacked branches) are currently only
    supported by Bazaar, but could be supported by other version control
    systems as well. Implementations are required to raise the appropriate
    exceptions when an operation is requested that is not supported.

    This also makes life easier for API users who can rely on the
    implementation always allowing a particular feature to be requested but
    raising an exception when it is not supported, rather than requiring the
    API users to check for magic attributes to see what features are supported.
    """

    def can_convert_format(self):
        """Return true if this controldir is one whose format we can convert
        from."""
        return True

    def list_branches(self):
        """Return a sequence of all branches local to this control directory.

        """
        return self.get_branches().values()

    def get_branches(self):
        """Get all branches in this control directory, as a dictionary.
        
        :return: Dictionary mapping branch names to instances.
        """
        try:
           return { "": self.open_branch() }
        except (errors.NotBranchError, errors.NoRepositoryPresent):
           return {}

    def is_control_filename(self, filename):
        """True if filename is the name of a path which is reserved for
        controldirs.

        :param filename: A filename within the root transport of this
            controldir.

        This is true IF and ONLY IF the filename is part of the namespace reserved
        for bzr control dirs. Currently this is the '.bzr' directory in the root
        of the root_transport. it is expected that plugins will need to extend
        this in the future - for instance to make bzr talk with svn working
        trees.
        """
        raise NotImplementedError(self.is_control_filename)

    def needs_format_conversion(self, format=None):
        """Return true if this controldir needs convert_format run on it.

        For instance, if the repository format is out of date but the
        branch and working tree are not, this should return True.

        :param format: Optional parameter indicating a specific desired
                       format we plan to arrive at.
        """
        raise NotImplementedError(self.needs_format_conversion)

    def create_repository(self, shared=False):
        """Create a new repository in this control directory.

        :param shared: If a shared repository should be created
        :return: The newly created repository
        """
        raise NotImplementedError(self.create_repository)

    def destroy_repository(self):
        """Destroy the repository in this ControlDir."""
        raise NotImplementedError(self.destroy_repository)

    def create_branch(self, name=None, repository=None,
                      append_revisions_only=None):
        """Create a branch in this ControlDir.

        :param name: Name of the colocated branch to create, None for
            the default branch.
        :param append_revisions_only: Whether this branch should only allow
            appending new revisions to its history.

        The controldirs format will control what branch format is created.
        For more control see BranchFormatXX.create(a_controldir).
        """
        raise NotImplementedError(self.create_branch)

    def destroy_branch(self, name=None):
        """Destroy a branch in this ControlDir.

        :param name: Name of the branch to destroy, None for the default 
            branch.
        """
        raise NotImplementedError(self.destroy_branch)

    def create_workingtree(self, revision_id=None, from_branch=None,
        accelerator_tree=None, hardlink=False):
        """Create a working tree at this ControlDir.

        :param revision_id: create it as of this revision id.
        :param from_branch: override controldir branch 
            (for lightweight checkouts)
        :param accelerator_tree: A tree which can be used for retrieving file
            contents more quickly than the revision tree, i.e. a workingtree.
            The revision tree will be used for cases where accelerator_tree's
            content is different.
        """
        raise NotImplementedError(self.create_workingtree)

    def destroy_workingtree(self):
        """Destroy the working tree at this ControlDir.

        Formats that do not support this may raise UnsupportedOperation.
        """
        raise NotImplementedError(self.destroy_workingtree)

    def destroy_workingtree_metadata(self):
        """Destroy the control files for the working tree at this ControlDir.

        The contents of working tree files are not affected.
        Formats that do not support this may raise UnsupportedOperation.
        """
        raise NotImplementedError(self.destroy_workingtree_metadata)

    def find_branch_format(self, name=None):
        """Find the branch 'format' for this controldir.

        This might be a synthetic object for e.g. RemoteBranch and SVN.
        """
        raise NotImplementedError(self.find_branch_format)

    def get_branch_reference(self, name=None):
        """Return the referenced URL for the branch in this controldir.

        :param name: Optional colocated branch name
        :raises NotBranchError: If there is no Branch.
        :raises NoColocatedBranchSupport: If a branch name was specified
            but colocated branches are not supported.
        :return: The URL the branch in this controldir references if it is a
            reference branch, or None for regular branches.
        """
        if name is not None:
            raise errors.NoColocatedBranchSupport(self)
        return None

    def open_branch(self, name=None, unsupported=False,
                    ignore_fallbacks=False, possible_transports=None):
        """Open the branch object at this ControlDir if one is present.

        :param unsupported: if True, then no longer supported branch formats can
            still be opened.
        :param ignore_fallbacks: Whether to open fallback repositories
        :param possible_transports: Transports to use for opening e.g.
            fallback repositories.
        """
        raise NotImplementedError(self.open_branch)

    def open_repository(self, _unsupported=False):
        """Open the repository object at this ControlDir if one is present.

        This will not follow the Branch object pointer - it's strictly a direct
        open facility. Most client code should use open_branch().repository to
        get at a repository.

        :param _unsupported: a private parameter, not part of the api.
        """
        raise NotImplementedError(self.open_repository)

    def find_repository(self):
        """Find the repository that should be used.

        This does not require a branch as we use it to find the repo for
        new branches as well as to hook existing branches up to their
        repository.
        """
        raise NotImplementedError(self.find_repository)

    def open_workingtree(self, unsupported=False,
                         recommend_upgrade=True, from_branch=None):
        """Open the workingtree object at this ControlDir if one is present.

        :param recommend_upgrade: Optional keyword parameter, when True (the
            default), emit through the ui module a recommendation that the user
            upgrade the working tree when the workingtree being opened is old
            (but still fully supported).
        :param from_branch: override controldir branch (for lightweight
            checkouts)
        """
        raise NotImplementedError(self.open_workingtree)

    def has_branch(self, name=None):
        """Tell if this controldir contains a branch.

        Note: if you're going to open the branch, you should just go ahead
        and try, and not ask permission first.  (This method just opens the
        branch and discards it, and that's somewhat expensive.)
        """
        try:
            self.open_branch(name, ignore_fallbacks=True)
            return True
        except errors.NotBranchError:
            return False

    def _get_selected_branch(self):
        """Return the name of the branch selected by the user.

        :return: Name of the branch selected by the user, or None.
        """
        branch = self.root_transport.get_segment_parameters().get("branch")
        if branch is None:
            branch = ""
        return urlutils.unescape(branch)

    def has_workingtree(self):
        """Tell if this controldir contains a working tree.

        This will still raise an exception if the controldir has a workingtree
        that is remote & inaccessible.

        Note: if you're going to open the working tree, you should just go ahead
        and try, and not ask permission first.  (This method just opens the
        workingtree and discards it, and that's somewhat expensive.)
        """
        try:
            self.open_workingtree(recommend_upgrade=False)
            return True
        except errors.NoWorkingTree:
            return False

    def cloning_metadir(self, require_stacking=False):
        """Produce a metadir suitable for cloning or sprouting with.

        These operations may produce workingtrees (yes, even though they're
        "cloning" something that doesn't have a tree), so a viable workingtree
        format must be selected.

        :require_stacking: If True, non-stackable formats will be upgraded
            to similar stackable formats.
        :returns: a ControlDirFormat with all component formats either set
            appropriately or set to None if that component should not be
            created.
        """
        raise NotImplementedError(self.cloning_metadir)

    def checkout_metadir(self):
        """Produce a metadir suitable for checkouts of this controldir.

        :returns: A ControlDirFormat with all component formats
            either set appropriately or set to None if that component
            should not be created.
        """
        return self.cloning_metadir()

    def sprout(self, url, revision_id=None, force_new_repo=False,
               recurse='down', possible_transports=None,
               accelerator_tree=None, hardlink=False, stacked=False,
               source_branch=None, create_tree_if_local=True):
        """Create a copy of this controldir prepared for use as a new line of
        development.

        If url's last component does not exist, it will be created.

        Attributes related to the identity of the source branch like
        branch nickname will be cleaned, a working tree is created
        whether one existed before or not; and a local branch is always
        created.

        :param revision_id: if revision_id is not None, then the clone
            operation may tune itself to download less data.
        :param accelerator_tree: A tree which can be used for retrieving file
            contents more quickly than the revision tree, i.e. a workingtree.
            The revision tree will be used for cases where accelerator_tree's
            content is different.
        :param hardlink: If true, hard-link files from accelerator_tree,
            where possible.
        :param stacked: If true, create a stacked branch referring to the
            location of this control directory.
        :param create_tree_if_local: If true, a working-tree will be created
            when working locally.
        """
        raise NotImplementedError(self.sprout)

    def push_branch(self, source, revision_id=None, overwrite=False, 
        remember=False, create_prefix=False):
        """Push the source branch into this ControlDir."""
        br_to = None
        # If we can open a branch, use its direct repository, otherwise see
        # if there is a repository without a branch.
        try:
            br_to = self.open_branch()
        except errors.NotBranchError:
            # Didn't find a branch, can we find a repository?
            repository_to = self.find_repository()
        else:
            # Found a branch, so we must have found a repository
            repository_to = br_to.repository

        push_result = PushResult()
        push_result.source_branch = source
        if br_to is None:
            # We have a repository but no branch, copy the revisions, and then
            # create a branch.
            if revision_id is None:
                # No revision supplied by the user, default to the branch
                # revision
                revision_id = source.last_revision()
            repository_to.fetch(source.repository, revision_id=revision_id)
            br_to = source.clone(self, revision_id=revision_id)
            if source.get_push_location() is None or remember:
                source.set_push_location(br_to.base)
            push_result.stacked_on = None
            push_result.branch_push_result = None
            push_result.old_revno = None
            push_result.old_revid = _mod_revision.NULL_REVISION
            push_result.target_branch = br_to
            push_result.master_branch = None
            push_result.workingtree_updated = False
        else:
            # We have successfully opened the branch, remember if necessary:
            if source.get_push_location() is None or remember:
                source.set_push_location(br_to.base)
            try:
                tree_to = self.open_workingtree()
            except errors.NotLocalUrl:
                push_result.branch_push_result = source.push(br_to, 
                    overwrite, stop_revision=revision_id)
                push_result.workingtree_updated = False
            except errors.NoWorkingTree:
                push_result.branch_push_result = source.push(br_to,
                    overwrite, stop_revision=revision_id)
                push_result.workingtree_updated = None # Not applicable
            else:
                tree_to.lock_write()
                try:
                    push_result.branch_push_result = source.push(
                        tree_to.branch, overwrite, stop_revision=revision_id)
                    tree_to.update()
                finally:
                    tree_to.unlock()
                push_result.workingtree_updated = True
            push_result.old_revno = push_result.branch_push_result.old_revno
            push_result.old_revid = push_result.branch_push_result.old_revid
            push_result.target_branch = \
                push_result.branch_push_result.target_branch
        return push_result

    def _get_tree_branch(self, name=None):
        """Return the branch and tree, if any, for this controldir.

        :param name: Name of colocated branch to open.

        Return None for tree if not present or inaccessible.
        Raise NotBranchError if no branch is present.
        :return: (tree, branch)
        """
        try:
            tree = self.open_workingtree()
        except (errors.NoWorkingTree, errors.NotLocalUrl):
            tree = None
            branch = self.open_branch(name=name)
        else:
            if name is not None:
                branch = self.open_branch(name=name)
            else:
                branch = tree.branch
        return tree, branch

    def get_config(self):
        """Get configuration for this ControlDir."""
        raise NotImplementedError(self.get_config)

    def check_conversion_target(self, target_format):
        """Check that a controldir as a whole can be converted to a new format."""
        raise NotImplementedError(self.check_conversion_target)

    def clone(self, url, revision_id=None, force_new_repo=False,
              preserve_stacking=False):
        """Clone this controldir and its contents to url verbatim.

        :param url: The url create the clone at.  If url's last component does
            not exist, it will be created.
        :param revision_id: The tip revision-id to use for any branch or
            working tree.  If not None, then the clone operation may tune
            itself to download less data.
        :param force_new_repo: Do not use a shared repository for the target
                               even if one is available.
        :param preserve_stacking: When cloning a stacked branch, stack the
            new branch on top of the other branch's stacked-on branch.
        """
        return self.clone_on_transport(_mod_transport.get_transport(url),
                                       revision_id=revision_id,
                                       force_new_repo=force_new_repo,
                                       preserve_stacking=preserve_stacking)

    def clone_on_transport(self, transport, revision_id=None,
        force_new_repo=False, preserve_stacking=False, stacked_on=None,
        create_prefix=False, use_existing_dir=True, no_tree=False):
        """Clone this controldir and its contents to transport verbatim.

        :param transport: The transport for the location to produce the clone
            at.  If the target directory does not exist, it will be created.
        :param revision_id: The tip revision-id to use for any branch or
            working tree.  If not None, then the clone operation may tune
            itself to download less data.
        :param force_new_repo: Do not use a shared repository for the target,
                               even if one is available.
        :param preserve_stacking: When cloning a stacked branch, stack the
            new branch on top of the other branch's stacked-on branch.
        :param create_prefix: Create any missing directories leading up to
            to_transport.
        :param use_existing_dir: Use an existing directory if one exists.
        :param no_tree: If set to true prevents creation of a working tree.
        """
        raise NotImplementedError(self.clone_on_transport)

    @classmethod
    def find_bzrdirs(klass, transport, evaluate=None, list_current=None):
        """Find control dirs recursively from current location.

        This is intended primarily as a building block for more sophisticated
        functionality, like finding trees under a directory, or finding
        branches that use a given repository.

        :param evaluate: An optional callable that yields recurse, value,
            where recurse controls whether this controldir is recursed into
            and value is the value to yield.  By default, all bzrdirs
            are recursed into, and the return value is the controldir.
        :param list_current: if supplied, use this function to list the current
            directory, instead of Transport.list_dir
        :return: a generator of found bzrdirs, or whatever evaluate returns.
        """
        if list_current is None:
            def list_current(transport):
                return transport.list_dir('')
        if evaluate is None:
            def evaluate(controldir):
                return True, controldir

        pending = [transport]
        while len(pending) > 0:
            current_transport = pending.pop()
            recurse = True
            try:
                controldir = klass.open_from_transport(current_transport)
            except (errors.NotBranchError, errors.PermissionDenied):
                pass
            else:
                recurse, value = evaluate(controldir)
                yield value
            try:
                subdirs = list_current(current_transport)
            except (errors.NoSuchFile, errors.PermissionDenied):
                continue
            if recurse:
                for subdir in sorted(subdirs, reverse=True):
                    pending.append(current_transport.clone(subdir))

    @classmethod
    def find_branches(klass, transport):
        """Find all branches under a transport.

        This will find all branches below the transport, including branches
        inside other branches.  Where possible, it will use
        Repository.find_branches.

        To list all the branches that use a particular Repository, see
        Repository.find_branches
        """
        def evaluate(controldir):
            try:
                repository = controldir.open_repository()
            except errors.NoRepositoryPresent:
                pass
            else:
                return False, ([], repository)
            return True, (controldir.list_branches(), None)
        ret = []
        for branches, repo in klass.find_bzrdirs(
                transport, evaluate=evaluate):
            if repo is not None:
                ret.extend(repo.find_branches())
            if branches is not None:
                ret.extend(branches)
        return ret

    @classmethod
    def create_branch_and_repo(klass, base, force_new_repo=False, format=None):
        """Create a new ControlDir, Branch and Repository at the url 'base'.

        This will use the current default ControlDirFormat unless one is
        specified, and use whatever
        repository format that that uses via controldir.create_branch and
        create_repository. If a shared repository is available that is used
        preferentially.

        The created Branch object is returned.

        :param base: The URL to create the branch at.
        :param force_new_repo: If True a new repository is always created.
        :param format: If supplied, the format of branch to create.  If not
            supplied, the default is used.
        """
        controldir = klass.create(base, format)
        controldir._find_or_create_repository(force_new_repo)
        return controldir.create_branch()

    @classmethod
    def create_branch_convenience(klass, base, force_new_repo=False,
                                  force_new_tree=None, format=None,
                                  possible_transports=None):
        """Create a new ControlDir, Branch and Repository at the url 'base'.

        This is a convenience function - it will use an existing repository
        if possible, can be told explicitly whether to create a working tree or
        not.

        This will use the current default ControlDirFormat unless one is
        specified, and use whatever
        repository format that that uses via ControlDir.create_branch and
        create_repository. If a shared repository is available that is used
        preferentially. Whatever repository is used, its tree creation policy
        is followed.

        The created Branch object is returned.
        If a working tree cannot be made due to base not being a file:// url,
        no error is raised unless force_new_tree is True, in which case no
        data is created on disk and NotLocalUrl is raised.

        :param base: The URL to create the branch at.
        :param force_new_repo: If True a new repository is always created.
        :param force_new_tree: If True or False force creation of a tree or
                               prevent such creation respectively.
        :param format: Override for the controldir format to create.
        :param possible_transports: An optional reusable transports list.
        """
        if force_new_tree:
            # check for non local urls
            t = _mod_transport.get_transport(base, possible_transports)
            if not isinstance(t, local.LocalTransport):
                raise errors.NotLocalUrl(base)
        controldir = klass.create(base, format, possible_transports)
        repo = controldir._find_or_create_repository(force_new_repo)
        result = controldir.create_branch()
        if force_new_tree or (repo.make_working_trees() and
                              force_new_tree is None):
            try:
                controldir.create_workingtree()
            except errors.NotLocalUrl:
                pass
        return result

    @classmethod
    def create_standalone_workingtree(klass, base, format=None):
        """Create a new ControlDir, WorkingTree, Branch and Repository at 'base'.

        'base' must be a local path or a file:// url.

        This will use the current default ControlDirFormat unless one is
        specified, and use whatever
        repository format that that uses for bzrdirformat.create_workingtree,
        create_branch and create_repository.

        :param format: Override for the controldir format to create.
        :return: The WorkingTree object.
        """
        t = _mod_transport.get_transport(base)
        if not isinstance(t, local.LocalTransport):
            raise errors.NotLocalUrl(base)
        controldir = klass.create_branch_and_repo(base,
                                               force_new_repo=True,
                                               format=format).bzrdir
        return controldir.create_workingtree()

    @classmethod
    def open_unsupported(klass, base):
        """Open a branch which is not supported."""
        return klass.open(base, _unsupported=True)

    @classmethod
    def open(klass, base, possible_transports=None, probers=None,
             _unsupported=False):
        """Open an existing controldir, rooted at 'base' (url).

        :param _unsupported: a private parameter to the ControlDir class.
        """
        t = _mod_transport.get_transport(base, possible_transports)
        return klass.open_from_transport(t, probers=probers,
                _unsupported=_unsupported)

    @classmethod
    def open_from_transport(klass, transport, _unsupported=False,
                            probers=None):
        """Open a controldir within a particular directory.

        :param transport: Transport containing the controldir.
        :param _unsupported: private.
        """
        for hook in klass.hooks['pre_open']:
            hook(transport)
        # Keep initial base since 'transport' may be modified while following
        # the redirections.
        base = transport.base
        def find_format(transport):
            return transport, ControlDirFormat.find_format(transport,
                probers=probers)

        def redirected(transport, e, redirection_notice):
            redirected_transport = transport._redirected_to(e.source, e.target)
            if redirected_transport is None:
                raise errors.NotBranchError(base)
            trace.note(gettext('{0} is{1} redirected to {2}').format(
                 transport.base, e.permanently, redirected_transport.base))
            return redirected_transport

        try:
            transport, format = _mod_transport.do_catching_redirections(
                find_format, transport, redirected)
        except errors.TooManyRedirections:
            raise errors.NotBranchError(base)

        format.check_support_status(_unsupported)
        return format.open(transport, _found=True)

    @classmethod
    def open_containing(klass, url, possible_transports=None):
        """Open an existing branch which contains url.

        :param url: url to search from.

        See open_containing_from_transport for more detail.
        """
        transport = _mod_transport.get_transport(url, possible_transports)
        return klass.open_containing_from_transport(transport)

    @classmethod
    def open_containing_from_transport(klass, a_transport):
        """Open an existing branch which contains a_transport.base.

        This probes for a branch at a_transport, and searches upwards from there.

        Basically we keep looking up until we find the control directory or
        run into the root.  If there isn't one, raises NotBranchError.
        If there is one and it is either an unrecognised format or an unsupported
        format, UnknownFormatError or UnsupportedFormatError are raised.
        If there is one, it is returned, along with the unused portion of url.

        :return: The ControlDir that contains the path, and a Unicode path
                for the rest of the URL.
        """
        # this gets the normalised url back. I.e. '.' -> the full path.
        url = a_transport.base
        while True:
            try:
                result = klass.open_from_transport(a_transport)
                return result, urlutils.unescape(a_transport.relpath(url))
            except errors.NotBranchError, e:
                pass
            except errors.PermissionDenied:
                pass
            try:
                new_t = a_transport.clone('..')
            except errors.InvalidURLJoin:
                # reached the root, whatever that may be
                raise errors.NotBranchError(path=url)
            if new_t.base == a_transport.base:
                # reached the root, whatever that may be
                raise errors.NotBranchError(path=url)
            a_transport = new_t

    @classmethod
    def open_tree_or_branch(klass, location):
        """Return the branch and working tree at a location.

        If there is no tree at the location, tree will be None.
        If there is no branch at the location, an exception will be
        raised
        :return: (tree, branch)
        """
        controldir = klass.open(location)
        return controldir._get_tree_branch()

    @classmethod
    def open_containing_tree_or_branch(klass, location):
        """Return the branch and working tree contained by a location.

        Returns (tree, branch, relpath).
        If there is no tree at containing the location, tree will be None.
        If there is no branch containing the location, an exception will be
        raised
        relpath is the portion of the path that is contained by the branch.
        """
        controldir, relpath = klass.open_containing(location)
        tree, branch = controldir._get_tree_branch()
        return tree, branch, relpath

    @classmethod
    def open_containing_tree_branch_or_repository(klass, location):
        """Return the working tree, branch and repo contained by a location.

        Returns (tree, branch, repository, relpath).
        If there is no tree containing the location, tree will be None.
        If there is no branch containing the location, branch will be None.
        If there is no repository containing the location, repository will be
        None.
        relpath is the portion of the path that is contained by the innermost
        ControlDir.

        If no tree, branch or repository is found, a NotBranchError is raised.
        """
        controldir, relpath = klass.open_containing(location)
        try:
            tree, branch = controldir._get_tree_branch()
        except errors.NotBranchError:
            try:
                repo = controldir.find_repository()
                return None, None, repo, relpath
            except (errors.NoRepositoryPresent):
                raise errors.NotBranchError(location)
        return tree, branch, branch.repository, relpath

    @classmethod
    def create(klass, base, format=None, possible_transports=None):
        """Create a new ControlDir at the url 'base'.

        :param format: If supplied, the format of branch to create.  If not
            supplied, the default is used.
        :param possible_transports: If supplied, a list of transports that
            can be reused to share a remote connection.
        """
        if klass is not ControlDir:
            raise AssertionError("ControlDir.create always creates the"
                "default format, not one of %r" % klass)
        t = _mod_transport.get_transport(base, possible_transports)
        t.ensure_base()
        if format is None:
            format = ControlDirFormat.get_default_format()
        return format.initialize_on_transport(t)


class ControlDirHooks(hooks.Hooks):
    """Hooks for ControlDir operations."""

    def __init__(self):
        """Create the default hooks."""
        hooks.Hooks.__init__(self, "bzrlib.controldir", "ControlDir.hooks")
        self.add_hook('pre_open',
            "Invoked before attempting to open a ControlDir with the transport "
            "that the open will use.", (1, 14))
        self.add_hook('post_repo_init',
            "Invoked after a repository has been initialized. "
            "post_repo_init is called with a "
            "bzrlib.controldir.RepoInitHookParams.",
            (2, 2))

# install the default hooks
ControlDir.hooks = ControlDirHooks()


class ControlComponentFormat(object):
    """A component that can live inside of a control directory."""

    upgrade_recommended = False

    def get_format_description(self):
        """Return the short description for this format."""
        raise NotImplementedError(self.get_format_description)

    def is_supported(self):
        """Is this format supported?

        Supported formats must be initializable and openable.
        Unsupported formats may not support initialization or committing or
        some other features depending on the reason for not being supported.
        """
        return True

    def check_support_status(self, allow_unsupported, recommend_upgrade=True,
        basedir=None):
        """Give an error or warning on old formats.

        :param allow_unsupported: If true, allow opening
            formats that are strongly deprecated, and which may
            have limited functionality.

        :param recommend_upgrade: If true (default), warn
            the user through the ui object that they may wish
            to upgrade the object.
        """
        if not allow_unsupported and not self.is_supported():
            # see open_downlevel to open legacy branches.
            raise errors.UnsupportedFormatError(format=self)
        if recommend_upgrade and self.upgrade_recommended:
            ui.ui_factory.recommend_upgrade(
                self.get_format_description(), basedir)

    @classmethod
    def get_format_string(cls):
        raise NotImplementedError(cls.get_format_string)


class ControlComponentFormatRegistry(registry.FormatRegistry):
    """A registry for control components (branch, workingtree, repository)."""

    def __init__(self, other_registry=None):
        super(ControlComponentFormatRegistry, self).__init__(other_registry)
        self._extra_formats = []

    def register(self, format):
        """Register a new format."""
        super(ControlComponentFormatRegistry, self).register(
            format.get_format_string(), format)

    def remove(self, format):
        """Remove a registered format."""
        super(ControlComponentFormatRegistry, self).remove(
            format.get_format_string())

    def register_extra(self, format):
        """Register a format that can not be used in a metadir.

        This is mainly useful to allow custom repository formats, such as older
        Bazaar formats and foreign formats, to be tested.
        """
        self._extra_formats.append(registry._ObjectGetter(format))

    def remove_extra(self, format):
        """Remove an extra format.
        """
        self._extra_formats.remove(registry._ObjectGetter(format))

    def register_extra_lazy(self, module_name, member_name):
        """Register a format lazily.
        """
        self._extra_formats.append(
            registry._LazyObjectGetter(module_name, member_name))

    def _get_extra(self):
        """Return all "extra" formats, not usable in meta directories."""
        result = []
        for getter in self._extra_formats:
            f = getter.get_obj()
            if callable(f):
                f = f()
            result.append(f)
        return result

    def _get_all(self):
        """Return all formats, even those not usable in metadirs.
        """
        result = []
        for name in self.keys():
            fmt = self.get(name)
            if callable(fmt):
                fmt = fmt()
            result.append(fmt)
        return result + self._get_extra()

    def _get_all_modules(self):
        """Return a set of the modules providing objects."""
        modules = set()
        for name in self.keys():
            modules.add(self._get_module(name))
        for getter in self._extra_formats:
            modules.add(getter.get_module())
        return modules


class Converter(object):
    """Converts a disk format object from one format to another."""

    def convert(self, to_convert, pb):
        """Perform the conversion of to_convert, giving feedback via pb.

        :param to_convert: The disk object to convert.
        :param pb: a progress bar to use for progress information.
        """

    def step(self, message):
        """Update the pb by a step."""
        self.count +=1
        self.pb.update(message, self.count, self.total)


class ControlDirFormat(object):
    """An encapsulation of the initialization and open routines for a format.

    Formats provide three things:
     * An initialization routine,
     * a format string,
     * an open routine.

    Formats are placed in a dict by their format string for reference
    during controldir opening. These should be subclasses of ControlDirFormat
    for consistency.

    Once a format is deprecated, just deprecate the initialize and open
    methods on the format class. Do not deprecate the object, as the
    object will be created every system load.

    :cvar colocated_branches: Whether this formats supports colocated branches.
    :cvar supports_workingtrees: This control directory can co-exist with a
        working tree.
    """

    _default_format = None
    """The default format used for new control directories."""

    _server_probers = []
    """The registered server format probers, e.g. RemoteBzrProber.

    This is a list of Prober-derived classes.
    """

    _probers = []
    """The registered format probers, e.g. BzrProber.

    This is a list of Prober-derived classes.
    """

    colocated_branches = False
    """Whether co-located branches are supported for this control dir format.
    """

    supports_workingtrees = True
    """Whether working trees can exist in control directories of this format.
    """

    fixed_components = False
    """Whether components can not change format independent of the control dir.
    """

    upgrade_recommended = False
    """Whether an upgrade from this format is recommended."""

    def get_format_description(self):
        """Return the short description for this format."""
        raise NotImplementedError(self.get_format_description)

    def get_converter(self, format=None):
        """Return the converter to use to convert controldirs needing converts.

        This returns a bzrlib.controldir.Converter object.

        This should return the best upgrader to step this format towards the
        current default format. In the case of plugins we can/should provide
        some means for them to extend the range of returnable converters.

        :param format: Optional format to override the default format of the
                       library.
        """
        raise NotImplementedError(self.get_converter)

    def is_supported(self):
        """Is this format supported?

        Supported formats must be openable.
        Unsupported formats may not support initialization or committing or
        some other features depending on the reason for not being supported.
        """
        return True

    def is_initializable(self):
        """Whether new control directories of this format can be initialized.
        """
        return self.is_supported()

    def check_support_status(self, allow_unsupported, recommend_upgrade=True,
        basedir=None):
        """Give an error or warning on old formats.

        :param allow_unsupported: If true, allow opening
            formats that are strongly deprecated, and which may
            have limited functionality.

        :param recommend_upgrade: If true (default), warn
            the user through the ui object that they may wish
            to upgrade the object.
        """
        if not allow_unsupported and not self.is_supported():
            # see open_downlevel to open legacy branches.
            raise errors.UnsupportedFormatError(format=self)
        if recommend_upgrade and self.upgrade_recommended:
            ui.ui_factory.recommend_upgrade(
                self.get_format_description(), basedir)

    def same_model(self, target_format):
        return (self.repository_format.rich_root_data ==
            target_format.rich_root_data)

    @classmethod
    def register_format(klass, format):
        """Register a format that does not use '.bzr' for its control dir.

        """
        raise errors.BzrError("ControlDirFormat.register_format() has been "
            "removed in Bazaar 2.4. Please upgrade your plugins.")

    @classmethod
    def register_prober(klass, prober):
        """Register a prober that can look for a control dir.

        """
        klass._probers.append(prober)

    @classmethod
    def unregister_prober(klass, prober):
        """Unregister a prober.

        """
        klass._probers.remove(prober)

    @classmethod
    def register_server_prober(klass, prober):
        """Register a control format prober for client-server environments.

        These probers will be used before ones registered with
        register_prober.  This gives implementations that decide to the
        chance to grab it before anything looks at the contents of the format
        file.
        """
        klass._server_probers.append(prober)

    def __str__(self):
        # Trim the newline
        return self.get_format_description().rstrip()

    @classmethod
    def all_probers(klass):
        return klass._server_probers + klass._probers

    @classmethod
    def known_formats(klass):
        """Return all the known formats.
        """
        result = set()
        for prober_kls in klass.all_probers():
            result.update(prober_kls.known_formats())
        return result

    @classmethod
    def find_format(klass, transport, probers=None):
        """Return the format present at transport."""
        if probers is None:
            probers = klass.all_probers()
        for prober_kls in probers:
            prober = prober_kls()
            try:
                return prober.probe_transport(transport)
            except errors.NotBranchError:
                # this format does not find a control dir here.
                pass
        raise errors.NotBranchError(path=transport.base)

    def initialize(self, url, possible_transports=None):
        """Create a control dir at this url and return an opened copy.

        While not deprecated, this method is very specific and its use will
        lead to many round trips to setup a working environment. See
        initialize_on_transport_ex for a [nearly] all-in-one method.

        Subclasses should typically override initialize_on_transport
        instead of this method.
        """
        return self.initialize_on_transport(
            _mod_transport.get_transport(url, possible_transports))

    def initialize_on_transport(self, transport):
        """Initialize a new controldir in the base directory of a Transport."""
        raise NotImplementedError(self.initialize_on_transport)

    def initialize_on_transport_ex(self, transport, use_existing_dir=False,
        create_prefix=False, force_new_repo=False, stacked_on=None,
        stack_on_pwd=None, repo_format_name=None, make_working_trees=None,
        shared_repo=False, vfs_only=False):
        """Create this format on transport.

        The directory to initialize will be created.

        :param force_new_repo: Do not use a shared repository for the target,
                               even if one is available.
        :param create_prefix: Create any missing directories leading up to
            to_transport.
        :param use_existing_dir: Use an existing directory if one exists.
        :param stacked_on: A url to stack any created branch on, None to follow
            any target stacking policy.
        :param stack_on_pwd: If stack_on is relative, the location it is
            relative to.
        :param repo_format_name: If non-None, a repository will be
            made-or-found. Should none be found, or if force_new_repo is True
            the repo_format_name is used to select the format of repository to
            create.
        :param make_working_trees: Control the setting of make_working_trees
            for a new shared repository when one is made. None to use whatever
            default the format has.
        :param shared_repo: Control whether made repositories are shared or
            not.
        :param vfs_only: If True do not attempt to use a smart server
        :return: repo, controldir, require_stacking, repository_policy. repo is
            None if none was created or found, controldir is always valid.
            require_stacking is the result of examining the stacked_on
            parameter and any stacking policy found for the target.
        """
        raise NotImplementedError(self.initialize_on_transport_ex)

    def network_name(self):
        """A simple byte string uniquely identifying this format for RPC calls.

        Bzr control formats use this disk format string to identify the format
        over the wire. Its possible that other control formats have more
        complex detection requirements, so we permit them to use any unique and
        immutable string they desire.
        """
        raise NotImplementedError(self.network_name)

    def open(self, transport, _found=False):
        """Return an instance of this format for the dir transport points at.
        """
        raise NotImplementedError(self.open)

    @classmethod
    def _set_default_format(klass, format):
        """Set default format (for testing behavior of defaults only)"""
        klass._default_format = format

    @classmethod
    def get_default_format(klass):
        """Return the current default format."""
        return klass._default_format

    def supports_transport(self, transport):
        """Check if this format can be opened over a particular transport.
        """
        raise NotImplementedError(self.supports_transport)


class Prober(object):
    """Abstract class that can be used to detect a particular kind of
    control directory.

    At the moment this just contains a single method to probe a particular
    transport, but it may be extended in the future to e.g. avoid
    multiple levels of probing for Subversion repositories.

    See BzrProber and RemoteBzrProber in bzrlib.bzrdir for the
    probers that detect .bzr/ directories and Bazaar smart servers,
    respectively.

    Probers should be registered using the register_server_prober or
    register_prober methods on ControlDirFormat.
    """

    def probe_transport(self, transport):
        """Return the controldir style format present in a directory.

        :raise UnknownFormatError: If a control dir was found but is
            in an unknown format.
        :raise NotBranchError: If no control directory was found.
        :return: A ControlDirFormat instance.
        """
        raise NotImplementedError(self.probe_transport)

    @classmethod
    def known_formats(klass):
        """Return the control dir formats known by this prober.

        Multiple probers can return the same formats, so this should
        return a set.

        :return: A set of known formats.
        """
        raise NotImplementedError(klass.known_formats)


class ControlDirFormatInfo(object):

    def __init__(self, native, deprecated, hidden, experimental):
        self.deprecated = deprecated
        self.native = native
        self.hidden = hidden
        self.experimental = experimental


class ControlDirFormatRegistry(registry.Registry):
    """Registry of user-selectable ControlDir subformats.

    Differs from ControlDirFormat._formats in that it provides sub-formats,
    e.g. BzrDirMeta1 with weave repository.  Also, it's more user-oriented.
    """

    def __init__(self):
        """Create a ControlDirFormatRegistry."""
        self._aliases = set()
        self._registration_order = list()
        super(ControlDirFormatRegistry, self).__init__()

    def aliases(self):
        """Return a set of the format names which are aliases."""
        return frozenset(self._aliases)

    def register(self, key, factory, help, native=True, deprecated=False,
                 hidden=False, experimental=False, alias=False):
        """Register a ControlDirFormat factory.

        The factory must be a callable that takes one parameter: the key.
        It must produce an instance of the ControlDirFormat when called.

        This function mainly exists to prevent the info object from being
        supplied directly.
        """
        registry.Registry.register(self, key, factory, help,
            ControlDirFormatInfo(native, deprecated, hidden, experimental))
        if alias:
            self._aliases.add(key)
        self._registration_order.append(key)

    def register_lazy(self, key, module_name, member_name, help, native=True,
        deprecated=False, hidden=False, experimental=False, alias=False):
        registry.Registry.register_lazy(self, key, module_name, member_name,
            help, ControlDirFormatInfo(native, deprecated, hidden, experimental))
        if alias:
            self._aliases.add(key)
        self._registration_order.append(key)

    def set_default(self, key):
        """Set the 'default' key to be a clone of the supplied key.

        This method must be called once and only once.
        """
        registry.Registry.register(self, 'default', self.get(key),
            self.get_help(key), info=self.get_info(key))
        self._aliases.add('default')

    def set_default_repository(self, key):
        """Set the FormatRegistry default and Repository default.

        This is a transitional method while Repository.set_default_format
        is deprecated.
        """
        if 'default' in self:
            self.remove('default')
        self.set_default(key)
        format = self.get('default')()

    def make_bzrdir(self, key):
        return self.get(key)()

    def help_topic(self, topic):
        output = ""
        default_realkey = None
        default_help = self.get_help('default')
        help_pairs = []
        for key in self._registration_order:
            if key == 'default':
                continue
            help = self.get_help(key)
            if help == default_help:
                default_realkey = key
            else:
                help_pairs.append((key, help))

        def wrapped(key, help, info):
            if info.native:
                help = '(native) ' + help
            return ':%s:\n%s\n\n' % (key,
                textwrap.fill(help, initial_indent='    ',
                    subsequent_indent='    ',
                    break_long_words=False))
        if default_realkey is not None:
            output += wrapped(default_realkey, '(default) %s' % default_help,
                              self.get_info('default'))
        deprecated_pairs = []
        experimental_pairs = []
        for key, help in help_pairs:
            info = self.get_info(key)
            if info.hidden:
                continue
            elif info.deprecated:
                deprecated_pairs.append((key, help))
            elif info.experimental:
                experimental_pairs.append((key, help))
            else:
                output += wrapped(key, help, info)
        output += "\nSee :doc:`formats-help` for more about storage formats."
        other_output = ""
        if len(experimental_pairs) > 0:
            other_output += "Experimental formats are shown below.\n\n"
            for key, help in experimental_pairs:
                info = self.get_info(key)
                other_output += wrapped(key, help, info)
        else:
            other_output += \
                "No experimental formats are available.\n\n"
        if len(deprecated_pairs) > 0:
            other_output += "\nDeprecated formats are shown below.\n\n"
            for key, help in deprecated_pairs:
                info = self.get_info(key)
                other_output += wrapped(key, help, info)
        else:
            other_output += \
                "\nNo deprecated formats are available.\n\n"
        other_output += \
                "\nSee :doc:`formats-help` for more about storage formats."

        if topic == 'other-formats':
            return other_output
        else:
            return output


class RepoInitHookParams(object):
    """Object holding parameters passed to `*_repo_init` hooks.

    There are 4 fields that hooks may wish to access:

    :ivar repository: Repository created
    :ivar format: Repository format
    :ivar bzrdir: The controldir for the repository
    :ivar shared: The repository is shared
    """

    def __init__(self, repository, format, controldir, shared):
        """Create a group of RepoInitHook parameters.

        :param repository: Repository created
        :param format: Repository format
        :param controldir: The controldir for the repository
        :param shared: The repository is shared
        """
        self.repository = repository
        self.format = format
        self.bzrdir = controldir
        self.shared = shared

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __repr__(self):
        if self.repository:
            return "<%s for %s>" % (self.__class__.__name__,
                self.repository)
        else:
            return "<%s for %s>" % (self.__class__.__name__,
                self.bzrdir)


# Please register new formats after old formats so that formats
# appear in chronological order and format descriptions can build
# on previous ones.
format_registry = ControlDirFormatRegistry()

network_format_registry = registry.FormatRegistry()
"""Registry of formats indexed by their network name.

The network name for a ControlDirFormat is an identifier that can be used when
referring to formats with smart server operations. See
ControlDirFormat.network_name() for more detail.
"""
