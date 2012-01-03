# Copyright (C) 2011 Canonical Ltd
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

"""Safe branch opening."""

from __future__ import absolute_import

import threading

from bzrlib import (
    errors,
    urlutils,
    )
from bzrlib.branch import Branch
from bzrlib.controldir import (
    ControlDir,
    )


class BadUrl(errors.BzrError):

    _fmt = "Tried to access a branch from bad URL %(url)s."


class BranchReferenceForbidden(errors.BzrError):

    _fmt = ("Trying to mirror a branch reference and the branch type "
            "does not allow references.")


class BranchLoopError(errors.BzrError):
    """Encountered a branch cycle.

    A URL may point to a branch reference or it may point to a stacked branch.
    In either case, it's possible for there to be a cycle in these references,
    and this exception is raised when we detect such a cycle.
    """

    _fmt = "Encountered a branch cycle"""


class BranchOpenPolicy(object):
    """Policy on how to open branches.

    In particular, a policy determines which branches are safe to open by
    checking their URLs and deciding whether or not to follow branch
    references.
    """

    def should_follow_references(self):
        """Whether we traverse references when mirroring.

        Subclasses must override this method.

        If we encounter a branch reference and this returns false, an error is
        raised.

        :returns: A boolean to indicate whether to follow a branch reference.
        """
        raise NotImplementedError(self.should_follow_references)

    def transform_fallback_location(self, branch, url):
        """Validate, maybe modify, 'url' to be used as a stacked-on location.

        :param branch:  The branch that is being opened.
        :param url: The URL that the branch provides for its stacked-on
            location.
        :return: (new_url, check) where 'new_url' is the URL of the branch to
            actually open and 'check' is true if 'new_url' needs to be
            validated by check_and_follow_branch_reference.
        """
        raise NotImplementedError(self.transform_fallback_location)

    def check_one_url(self, url):
        """Check the safety of the source URL.

        Subclasses must override this method.

        :param url: The source URL to check.
        :raise BadUrl: subclasses are expected to raise this or a subclass
            when it finds a URL it deems to be unsafe.
        """
        raise NotImplementedError(self.check_one_url)


class _BlacklistPolicy(BranchOpenPolicy):
    """Branch policy that forbids certain URLs.

    This doesn't cope with various alternative spellings of URLs,
    with e.g. url encoding. It's mostly useful for tests.
    """

    def __init__(self, should_follow_references, unsafe_urls=None):
        if unsafe_urls is None:
            unsafe_urls = set()
        self._unsafe_urls = unsafe_urls
        self._should_follow_references = should_follow_references

    def should_follow_references(self):
        return self._should_follow_references

    def check_one_url(self, url):
        if url in self._unsafe_urls:
            raise BadUrl(url)

    def transform_fallback_location(self, branch, url):
        """See `BranchOpenPolicy.transform_fallback_location`.

        This class is not used for testing our smarter stacking features so we
        just do the simplest thing: return the URL that would be used anyway
        and don't check it.
        """
        return urlutils.join(branch.base, url), False


class AcceptAnythingPolicy(_BlacklistPolicy):
    """Accept anything, to make testing easier."""

    def __init__(self):
        super(AcceptAnythingPolicy, self).__init__(True, set())


class WhitelistPolicy(BranchOpenPolicy):
    """Branch policy that only allows certain URLs."""

    def __init__(self, should_follow_references, allowed_urls=None,
                 check=False):
        if allowed_urls is None:
            allowed_urls = []
        self.allowed_urls = set(url.rstrip('/') for url in allowed_urls)
        self.check = check

    def should_follow_references(self):
        return self._should_follow_references

    def check_one_url(self, url):
        if url.rstrip('/') not in self.allowed_urls:
            raise BadUrl(url)

    def transform_fallback_location(self, branch, url):
        """See `BranchOpenPolicy.transform_fallback_location`.

        Here we return the URL that would be used anyway and optionally check
        it.
        """
        return urlutils.join(branch.base, url), self.check


class SingleSchemePolicy(BranchOpenPolicy):
    """Branch open policy that rejects URLs not on the given scheme."""

    def __init__(self, allowed_scheme):
        self.allowed_scheme = allowed_scheme

    def should_follow_references(self):
        return True

    def transform_fallback_location(self, branch, url):
        return urlutils.join(branch.base, url), True

    def check_one_url(self, url):
        """Check that `url` is safe to open."""
        if urlutils.URL.from_string(str(url)).scheme != self.allowed_scheme:
            raise BadUrl(url)


class SafeBranchOpener(object):
    """Safe branch opener.

    All locations that are opened (stacked-on branches, references) are
    checked against a policy object.

    The policy object is expected to have the following methods:
    * check_one_url 
    * should_follow_references
    * transform_fallback_location
    """

    _threading_data = threading.local()

    def __init__(self, policy, probers=None):
        """Create a new SafeBranchOpener.

        :param policy: The opener policy to use.
        :param probers: Optional list of probers to allow.
            Defaults to local and remote bzr probers.
        """
        self.policy = policy
        self._seen_urls = set()
        self.probers = probers

    @classmethod
    def install_hook(cls):
        """Install the ``transform_fallback_location`` hook.

        This is done at module import time, but transform_fallback_locationHook
        doesn't do anything unless the `_active_openers` threading.Local
        object has a 'opener' attribute in this thread.

        This is in a module-level function rather than performed at module
        level so that it can be called in setUp for testing `SafeBranchOpener`
        as bzrlib.tests.TestCase.setUp clears hooks.
        """
        Branch.hooks.install_named_hook(
            'transform_fallback_location',
            cls.transform_fallback_locationHook,
            'SafeBranchOpener.transform_fallback_locationHook')

    def check_and_follow_branch_reference(self, url):
        """Check URL (and possibly the referenced URL) for safety.

        This method checks that `url` passes the policy's `check_one_url`
        method, and if `url` refers to a branch reference, it checks whether
        references are allowed and whether the reference's URL passes muster
        also -- recursively, until a real branch is found.

        :param url: URL to check
        :raise BranchLoopError: If the branch references form a loop.
        :raise BranchReferenceForbidden: If this opener forbids branch
            references.
        """
        while True:
            if url in self._seen_urls:
                raise BranchLoopError()
            self._seen_urls.add(url)
            self.policy.check_one_url(url)
            next_url = self.follow_reference(url)
            if next_url is None:
                return url
            url = next_url
            if not self.policy.should_follow_references():
                raise BranchReferenceForbidden(url)

    @classmethod
    def transform_fallback_locationHook(cls, branch, url):
        """Installed as the 'transform_fallback_location' Branch hook.

        This method calls `transform_fallback_location` on the policy object and
        either returns the url it provides or passes it back to
        check_and_follow_branch_reference.
        """
        try:
            opener = getattr(cls._threading_data, "opener")
        except AttributeError:
            return url
        new_url, check = opener.policy.transform_fallback_location(branch, url)
        if check:
            return opener.check_and_follow_branch_reference(new_url)
        else:
            return new_url

    def run_with_transform_fallback_location_hook_installed(
            self, callable, *args, **kw):
        if (self.transform_fallback_locationHook not in
                Branch.hooks['transform_fallback_location']):
            raise AssertionError("hook not installed")
        self._threading_data.opener = self
        try:
            return callable(*args, **kw)
        finally:
            del self._threading_data.opener
            # We reset _seen_urls here to avoid multiple calls to open giving
            # spurious loop exceptions.
            self._seen_urls = set()

    def follow_reference(self, url):
        """Get the branch-reference value at the specified url.

        This exists as a separate method only to be overriden in unit tests.
        """
        bzrdir = ControlDir.open(url, probers=self.probers)
        return bzrdir.get_branch_reference()

    def open(self, url):
        """Open the Bazaar branch at url, first checking for safety.

        What safety means is defined by a subclasses `follow_reference` and
        `check_one_url` methods.
        """
        if type(url) != str:
            raise TypeError

        url = self.check_and_follow_branch_reference(url)

        def open_branch(url):
            dir = ControlDir.open(url, probers=self.probers)
            return dir.open_branch()
        return self.run_with_transform_fallback_location_hook_installed(
            open_branch, url)


def safe_open(allowed_scheme, url):
    """Open the branch at `url`, only accessing URLs on `allowed_scheme`.

    :raises BadUrl: An attempt was made to open a URL that was not on
        `allowed_scheme`.
    """
    return SafeBranchOpener(SingleSchemePolicy(allowed_scheme)).open(url)


SafeBranchOpener.install_hook()
