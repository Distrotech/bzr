# Copyright (C) 2007 Canonical Ltd
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


from bzrlib.lazy_import import lazy_import
lazy_import(globals(), """
from bzrlib import registry
""")


def get_url(tag, branch, bug_id):
    return tracker_registry.get_tracker(tag, branch).get_bug_url(bug_id)


class TrackerRegistry(registry.Registry):
    """Registry of bug tracker types."""

    def get_tracker(self, tag, branch):
        for tracker_name, tracker_type in self.iteritems():
            tracker = tracker_type.get(tag, branch)
            if tracker is not None:
                return tracker

tracker_registry = TrackerRegistry()
"""Registry of bug trackers."""


class LaunchpadTracker(object):
    """The Launchpad bug tracker."""

    @classmethod
    def get(klass, tag, branch):
        """Return a Launchpad tracker if tag is 'lp'. Return None otherwise."""
        if tag != 'lp':
            return None
        return klass()

    def get_bug_url(self, bug_id):
        """Return a Launchpad URL for bug_id."""
        return 'https://launchpad.net/bugs/%s' % (bug_id,)

tracker_registry.register('launchpad', LaunchpadTracker)


class TracTracker(object):
    """A Trac instance."""

    @classmethod
    def get(klass, tag, branch):
        """Return a TracTracker for the given tag.

        Looks in the configuration of 'branch' for a 'trac_<tag>_url' setting,
        which should refer to the base URL of a project's Trac instance.
        e.g.
            trac_twisted_url = http://twistedmatrix.com
        """
        url = branch.get_config().get_user_option('trac_%s_url' % (tag,))
        if url is None:
            return None
        return klass(url)

    def __init__(self, url):
        self._url = url

    def get_bug_url(self, bug_id):
        """Return a URL for a bug on this Trac instance."""
        return '%s/ticket/%s' % (self._url, bug_id)

tracker_registry.register('trac', TracTracker)
