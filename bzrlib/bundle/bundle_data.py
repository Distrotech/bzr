# Copyright (C) 2006 Canonical Ltd
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

"""Read in a bundle stream, and process it into a BundleReader object."""

import base64
from cStringIO import StringIO
import os
import pprint

from bzrlib import (
    osutils,
    timestamp,
    )
import bzrlib.errors
from bzrlib.bundle import apply_bundle
from bzrlib.errors import (TestamentMismatch, BzrError, 
                           MalformedHeader, MalformedPatches, NotABundle)
from bzrlib.inventory import (Inventory, InventoryEntry,
                              InventoryDirectory, InventoryFile,
                              InventoryLink)
from bzrlib.osutils import sha_file, sha_string, pathjoin
from bzrlib.revision import Revision, NULL_REVISION
from bzrlib.testament import StrictTestament
from bzrlib.trace import mutter, warning
import bzrlib.transport
from bzrlib.tree import Tree
import bzrlib.urlutils
from bzrlib.xml5 import serializer_v5


class RevisionInfo(object):
    """Gets filled out for each revision object that is read.
    """
    def __init__(self, revision_id):
        self.revision_id = revision_id
        self.sha1 = None
        self.committer = None
        self.date = None
        self.timestamp = None
        self.timezone = None
        self.inventory_sha1 = None

        self.parent_ids = None
        self.base_id = None
        self.message = None
        self.properties = None
        self.tree_actions = None

    def __str__(self):
        return pprint.pformat(self.__dict__)

    def as_revision(self):
        rev = Revision(revision_id=self.revision_id,
            committer=self.committer,
            timestamp=float(self.timestamp),
            timezone=int(self.timezone),
            inventory_sha1=self.inventory_sha1,
            message='\n'.join(self.message))

        if self.parent_ids:
            rev.parent_ids.extend(self.parent_ids)

        if self.properties:
            for property in self.properties:
                key_end = property.find(': ')
                if key_end == -1:
                    if not property.endswith(':'):
                        raise ValueError(property)
                    key = str(property[:-1])
                    value = ''
                else:
                    key = str(property[:key_end])
                    value = property[key_end+2:]
                rev.properties[key] = value

        return rev

    @staticmethod
    def from_revision(revision):
        revision_info = RevisionInfo(revision.revision_id)
        date = timestamp.format_highres_date(revision.timestamp,
                                             revision.timezone)
        revision_info.date = date
        revision_info.timezone = revision.timezone
        revision_info.timestamp = revision.timestamp
        revision_info.message = revision.message.split('\n')
        revision_info.properties = [': '.join(p) for p in
                                    revision.properties.iteritems()]
        return revision_info


class BundleInfo(object):
    """This contains the meta information. Stuff that allows you to
    recreate the revision or inventory XML.
    """
    def __init__(self, bundle_format=None):
        self.bundle_format = None
        self.committer = None
        self.date = None
        self.message = None

        # A list of RevisionInfo objects
        self.revisions = []

        # The next entries are created during complete_info() and
        # other post-read functions.

        # A list of real Revision objects
        self.real_revisions = []

        self.timestamp = None
        self.timezone = None

        # Have we checked the repository yet?
        self._validated_revisions_against_repo = False

    def __str__(self):
        return pprint.pformat(self.__dict__)

    def complete_info(self):
        """This makes sure that all information is properly
        split up, based on the assumptions that can be made
        when information is missing.
        """
        from bzrlib.timestamp import unpack_highres_date
        # Put in all of the guessable information.
        if not self.timestamp and self.date:
            self.timestamp, self.timezone = unpack_highres_date(self.date)

        self.real_revisions = []
        for rev in self.revisions:
            if rev.timestamp is None:
                if rev.date is not None:
                    rev.timestamp, rev.timezone = \
                            unpack_highres_date(rev.date)
                else:
                    rev.timestamp = self.timestamp
                    rev.timezone = self.timezone
            if rev.message is None and self.message:
                rev.message = self.message
            if rev.committer is None and self.committer:
                rev.committer = self.committer
            self.real_revisions.append(rev.as_revision())

    def get_base(self, revision):
        revision_info = self.get_revision_info(revision.revision_id)
        if revision_info.base_id is not None:
            return revision_info.base_id
        if len(revision.parent_ids) == 0:
            # There is no base listed, and
            # the lowest revision doesn't have a parent
            # so this is probably against the empty tree
            # and thus base truly is NULL_REVISION
            return NULL_REVISION
        else:
            return revision.parent_ids[-1]

    def _get_target(self):
        """Return the target revision."""
        if len(self.real_revisions) > 0:
            return self.real_revisions[0].revision_id
        elif len(self.revisions) > 0:
            return self.revisions[0].revision_id
        return None

    target = property(_get_target, doc='The target revision id')

    def get_revision(self, revision_id):
        for r in self.real_revisions:
            if r.revision_id == revision_id:
                return r
        raise KeyError(revision_id)

    def get_revision_info(self, revision_id):
        for r in self.revisions:
            if r.revision_id == revision_id:
                return r
        raise KeyError(revision_id)

    def revision_tree(self, repository, revision_id, base=None):
        revision = self.get_revision(revision_id)
        base = self.get_base(revision)
        if base == revision_id:
            raise AssertionError()
        if not self._validated_revisions_against_repo:
            self._validate_references_from_repository(repository)
        revision_info = self.get_revision_info(revision_id)
        inventory_revision_id = revision_id
        bundle_tree = BundleTree(repository.revision_tree(base), 
                                  inventory_revision_id)
        self._update_tree(bundle_tree, revision_id)

        inv = bundle_tree.inventory
        self._validate_inventory(inv, revision_id)
        self._validate_revision(inv, revision_id)

        return bundle_tree

    def _validate_references_from_repository(self, repository):
        """Now that we have a repository which should have some of the
        revisions we care about, go through and validate all of them
        that we can.
        """
        rev_to_sha = {}
        inv_to_sha = {}
        def add_sha(d, revision_id, sha1):
            if revision_id is None:
                if sha1 is not None:
                    raise BzrError('A Null revision should always'
                        'have a null sha1 hash')
                return
            if revision_id in d:
                # This really should have been validated as part
                # of _validate_revisions but lets do it again
                if sha1 != d[revision_id]:
                    raise BzrError('** Revision %r referenced with 2 different'
                            ' sha hashes %s != %s' % (revision_id,
                                sha1, d[revision_id]))
            else:
                d[revision_id] = sha1

        # All of the contained revisions were checked
        # in _validate_revisions
        checked = {}
        for rev_info in self.revisions:
            checked[rev_info.revision_id] = True
            add_sha(rev_to_sha, rev_info.revision_id, rev_info.sha1)
                
        for (rev, rev_info) in zip(self.real_revisions, self.revisions):
            add_sha(inv_to_sha, rev_info.revision_id, rev_info.inventory_sha1)

        count = 0
        missing = {}
        for revision_id, sha1 in rev_to_sha.iteritems():
            if repository.has_revision(revision_id):
                testament = StrictTestament.from_revision(repository, 
                                                          revision_id)
                local_sha1 = self._testament_sha1_from_revision(repository,
                                                                revision_id)
                if sha1 != local_sha1:
                    raise BzrError('sha1 mismatch. For revision id {%s}' 
                            'local: %s, bundle: %s' % (revision_id, local_sha1, sha1))
                else:
                    count += 1
            elif revision_id not in checked:
                missing[revision_id] = sha1

        if len(missing) > 0:
            # I don't know if this is an error yet
            warning('Not all revision hashes could be validated.'
                    ' Unable validate %d hashes' % len(missing))
        mutter('Verified %d sha hashes for the bundle.' % count)
        self._validated_revisions_against_repo = True

    def _validate_inventory(self, inv, revision_id):
        """At this point we should have generated the BundleTree,
        so build up an inventory, and make sure the hashes match.
        """
        # Now we should have a complete inventory entry.
        s = serializer_v5.write_inventory_to_string(inv)
        sha1 = sha_string(s)
        # Target revision is the last entry in the real_revisions list
        rev = self.get_revision(revision_id)
        if rev.revision_id != revision_id:
            raise AssertionError()
        if sha1 != rev.inventory_sha1:
            open(',,bogus-inv', 'wb').write(s)
            warning('Inventory sha hash mismatch for revision %s. %s'
                    ' != %s' % (revision_id, sha1, rev.inventory_sha1))

    def _validate_revision(self, inventory, revision_id):
        """Make sure all revision entries match their checksum."""

        # This is a mapping from each revision id to it's sha hash
        rev_to_sha1 = {}
        
        rev = self.get_revision(revision_id)
        rev_info = self.get_revision_info(revision_id)
        if not (rev.revision_id == rev_info.revision_id):
            raise AssertionError()
        if not (rev.revision_id == revision_id):
            raise AssertionError()
        sha1 = self._testament_sha1(rev, inventory)
        if sha1 != rev_info.sha1:
            raise TestamentMismatch(rev.revision_id, rev_info.sha1, sha1)
        if rev.revision_id in rev_to_sha1:
            raise BzrError('Revision {%s} given twice in the list'
                    % (rev.revision_id))
        rev_to_sha1[rev.revision_id] = sha1

    def _update_tree(self, bundle_tree, revision_id):
        """This fills out a BundleTree based on the information
        that was read in.

        :param bundle_tree: A BundleTree to update with the new information.
        """

        def get_rev_id(last_changed, path, kind):
            if last_changed is not None:
                # last_changed will be a Unicode string because of how it was
                # read. Convert it back to utf8.
                changed_revision_id = osutils.safe_revision_id(last_changed,
                                                               warn=False)
            else:
                changed_revision_id = revision_id
            bundle_tree.note_last_changed(path, changed_revision_id)
            return changed_revision_id

        def extra_info(info, new_path):
            last_changed = None
            encoding = None
            for info_item in info:
                try:
                    name, value = info_item.split(':', 1)
                except ValueError:
                    raise 'Value %r has no colon' % info_item
                if name == 'last-changed':
                    last_changed = value
                elif name == 'executable':
                    val = (value == 'yes')
                    bundle_tree.note_executable(new_path, val)
                elif name == 'target':
                    bundle_tree.note_target(new_path, value)
                elif name == 'encoding':
                    encoding = value
            return last_changed, encoding

        def do_patch(path, lines, encoding):
            if encoding == 'base64':
                patch = base64.decodestring(''.join(lines))
            elif encoding is None:
                patch =  ''.join(lines)
            else:
                raise ValueError(encoding)
            bundle_tree.note_patch(path, patch)

        def renamed(kind, extra, lines):
            info = extra.split(' // ')
            if len(info) < 2:
                raise BzrError('renamed action lines need both a from and to'
                        ': %r' % extra)
            old_path = info[0]
            if info[1].startswith('=> '):
                new_path = info[1][3:]
            else:
                new_path = info[1]

            bundle_tree.note_rename(old_path, new_path)
            last_modified, encoding = extra_info(info[2:], new_path)
            revision = get_rev_id(last_modified, new_path, kind)
            if lines:
                do_patch(new_path, lines, encoding)

        def removed(kind, extra, lines):
            info = extra.split(' // ')
            if len(info) > 1:
                # TODO: in the future we might allow file ids to be
                # given for removed entries
                raise BzrError('removed action lines should only have the path'
                        ': %r' % extra)
            path = info[0]
            bundle_tree.note_deletion(path)

        def added(kind, extra, lines):
            info = extra.split(' // ')
            if len(info) <= 1:
                raise BzrError('add action lines require the path and file id'
                        ': %r' % extra)
            elif len(info) > 5:
                raise BzrError('add action lines have fewer than 5 entries.'
                        ': %r' % extra)
            path = info[0]
            if not info[1].startswith('file-id:'):
                raise BzrError('The file-id should follow the path for an add'
                        ': %r' % extra)
            # This will be Unicode because of how the stream is read. Turn it
            # back into a utf8 file_id
            file_id = osutils.safe_file_id(info[1][8:], warn=False)

            bundle_tree.note_id(file_id, path, kind)
            # this will be overridden in extra_info if executable is specified.
            bundle_tree.note_executable(path, False)
            last_changed, encoding = extra_info(info[2:], path)
            revision = get_rev_id(last_changed, path, kind)
            if kind == 'directory':
                return
            do_patch(path, lines, encoding)

        def modified(kind, extra, lines):
            info = extra.split(' // ')
            if len(info) < 1:
                raise BzrError('modified action lines have at least'
                        'the path in them: %r' % extra)
            path = info[0]

            last_modified, encoding = extra_info(info[1:], path)
            revision = get_rev_id(last_modified, path, kind)
            if lines:
                do_patch(path, lines, encoding)
            
        valid_actions = {
            'renamed':renamed,
            'removed':removed,
            'added':added,
            'modified':modified
        }
        for action_line, lines in \
            self.get_revision_info(revision_id).tree_actions:
            first = action_line.find(' ')
            if first == -1:
                raise BzrError('Bogus action line'
                        ' (no opening space): %r' % action_line)
            second = action_line.find(' ', first+1)
            if second == -1:
                raise BzrError('Bogus action line'
                        ' (missing second space): %r' % action_line)
            action = action_line[:first]
            kind = action_line[first+1:second]
            if kind not in ('file', 'directory', 'symlink'):
                raise BzrError('Bogus action line'
                        ' (invalid object kind %r): %r' % (kind, action_line))
            extra = action_line[second+1:]

            if action not in valid_actions:
                raise BzrError('Bogus action line'
                        ' (unrecognized action): %r' % action_line)
            valid_actions[action](kind, extra, lines)

    def install_revisions(self, target_repo, stream_input=True):
        """Install revisions and return the target revision

        :param target_repo: The repository to install into
        :param stream_input: Ignored by this implementation.
        """
        apply_bundle.install_bundle(target_repo, self)
        return self.target

    def get_merge_request(self, target_repo):
        """Provide data for performing a merge

        Returns suggested base, suggested target, and patch verification status
        """
        return None, self.target, 'inapplicable'


class BundleTree(Tree):
    def __init__(self, base_tree, revision_id):
        self.base_tree = base_tree
        self._renamed = {} # Mapping from old_path => new_path
        self._renamed_r = {} # new_path => old_path
        self._new_id = {} # new_path => new_id
        self._new_id_r = {} # new_id => new_path
        self._kinds = {} # new_id => kind
        self._last_changed = {} # new_id => revision_id
        self._executable = {} # new_id => executable value
        self.patches = {}
        self._targets = {} # new path => new symlink target
        self.deleted = []
        self.contents_by_id = True
        self.revision_id = revision_id
        self._inventory = None

    def __str__(self):
        return pprint.pformat(self.__dict__)

    def note_rename(self, old_path, new_path):
        """A file/directory has been renamed from old_path => new_path"""
        if new_path in self._renamed:
            raise AssertionError(new_path)
        if old_path in self._renamed_r:
            raise AssertionError(old_path)
        self._renamed[new_path] = old_path
        self._renamed_r[old_path] = new_path

    def note_id(self, new_id, new_path, kind='file'):
        """Files that don't exist in base need a new id."""
        self._new_id[new_path] = new_id
        self._new_id_r[new_id] = new_path
        self._kinds[new_id] = kind

    def note_last_changed(self, file_id, revision_id):
        if (file_id in self._last_changed
                and self._last_changed[file_id] != revision_id):
            raise BzrError('Mismatched last-changed revision for file_id {%s}'
                    ': %s != %s' % (file_id,
                                    self._last_changed[file_id],
                                    revision_id))
        self._last_changed[file_id] = revision_id

    def note_patch(self, new_path, patch):
        """There is a patch for a given filename."""
        self.patches[new_path] = patch

    def note_target(self, new_path, target):
        """The symlink at the new path has the given target"""
        self._targets[new_path] = target

    def note_deletion(self, old_path):
        """The file at old_path has been deleted."""
        self.deleted.append(old_path)

    def note_executable(self, new_path, executable):
        self._executable[new_path] = executable

    def old_path(self, new_path):
        """Get the old_path (path in the base_tree) for the file at new_path"""
        if new_path[:1] in ('\\', '/'):
            raise ValueError(new_path)
        old_path = self._renamed.get(new_path)
        if old_path is not None:
            return old_path
        dirname,basename = os.path.split(new_path)
        # dirname is not '' doesn't work, because
        # dirname may be a unicode entry, and is
        # requires the objects to be identical
        if dirname != '':
            old_dir = self.old_path(dirname)
            if old_dir is None:
                old_path = None
            else:
                old_path = pathjoin(old_dir, basename)
        else:
            old_path = new_path
        #If the new path wasn't in renamed, the old one shouldn't be in
        #renamed_r
        if old_path in self._renamed_r:
            return None
        return old_path 

    def new_path(self, old_path):
        """Get the new_path (path in the target_tree) for the file at old_path
        in the base tree.
        """
        if old_path[:1] in ('\\', '/'):
            raise ValueError(old_path)
        new_path = self._renamed_r.get(old_path)
        if new_path is not None:
            return new_path
        if new_path in self._renamed:
            return None
        dirname,basename = os.path.split(old_path)
        if dirname != '':
            new_dir = self.new_path(dirname)
            if new_dir is None:
                new_path = None
            else:
                new_path = pathjoin(new_dir, basename)
        else:
            new_path = old_path
        #If the old path wasn't in renamed, the new one shouldn't be in
        #renamed_r
        if new_path in self._renamed:
            return None
        return new_path 

    def path2id(self, path):
        """Return the id of the file present at path in the target tree."""
        file_id = self._new_id.get(path)
        if file_id is not None:
            return file_id
        old_path = self.old_path(path)
        if old_path is None:
            return None
        if old_path in self.deleted:
            return None
        if getattr(self.base_tree, 'path2id', None) is not None:
            return self.base_tree.path2id(old_path)
        else:
            return self.base_tree.inventory.path2id(old_path)

    def id2path(self, file_id):
        """Return the new path in the target tree of the file with id file_id"""
        path = self._new_id_r.get(file_id)
        if path is not None:
            return path
        old_path = self.base_tree.id2path(file_id)
        if old_path is None:
            return None
        if old_path in self.deleted:
            return None
        return self.new_path(old_path)

    def old_contents_id(self, file_id):
        """Return the id in the base_tree for the given file_id.
        Return None if the file did not exist in base.
        """
        if self.contents_by_id:
            if self.base_tree.has_id(file_id):
                return file_id
            else:
                return None
        new_path = self.id2path(file_id)
        return self.base_tree.path2id(new_path)
        
    def get_file(self, file_id):
        """Return a file-like object containing the new contents of the
        file given by file_id.

        TODO:   It might be nice if this actually generated an entry
                in the text-store, so that the file contents would
                then be cached.
        """
        base_id = self.old_contents_id(file_id)
        if (base_id is not None and
            base_id != self.base_tree.inventory.root.file_id):
            patch_original = self.base_tree.get_file(base_id)
        else:
            patch_original = None
        file_patch = self.patches.get(self.id2path(file_id))
        if file_patch is None:
            if (patch_original is None and 
                self.get_kind(file_id) == 'directory'):
                return StringIO()
            if patch_original is None:
                raise AssertionError("None: %s" % file_id)
            return patch_original

        if file_patch.startswith('\\'):
            raise ValueError(
                'Malformed patch for %s, %r' % (file_id, file_patch))
        return patched_file(file_patch, patch_original)

    def get_symlink_target(self, file_id):
        new_path = self.id2path(file_id)
        try:
            return self._targets[new_path]
        except KeyError:
            return self.base_tree.get_symlink_target(file_id)

    def get_kind(self, file_id):
        if file_id in self._kinds:
            return self._kinds[file_id]
        return self.base_tree.inventory[file_id].kind

    def is_executable(self, file_id):
        path = self.id2path(file_id)
        if path in self._executable:
            return self._executable[path]
        else:
            return self.base_tree.inventory[file_id].executable

    def get_last_changed(self, file_id):
        path = self.id2path(file_id)
        if path in self._last_changed:
            return self._last_changed[path]
        return self.base_tree.inventory[file_id].revision

    def get_size_and_sha1(self, file_id):
        """Return the size and sha1 hash of the given file id.
        If the file was not locally modified, this is extracted
        from the base_tree. Rather than re-reading the file.
        """
        new_path = self.id2path(file_id)
        if new_path is None:
            return None, None
        if new_path not in self.patches:
            # If the entry does not have a patch, then the
            # contents must be the same as in the base_tree
            ie = self.base_tree.inventory[file_id]
            if ie.text_size is None:
                return ie.text_size, ie.text_sha1
            return int(ie.text_size), ie.text_sha1
        fileobj = self.get_file(file_id)
        content = fileobj.read()
        return len(content), sha_string(content)

    def _get_inventory(self):
        """Build up the inventory entry for the BundleTree.

        This need to be called before ever accessing self.inventory
        """
        from os.path import dirname, basename
        base_inv = self.base_tree.inventory
        inv = Inventory(None, self.revision_id)

        def add_entry(file_id):
            path = self.id2path(file_id)
            if path is None:
                return
            if path == '':
                parent_id = None
            else:
                parent_path = dirname(path)
                parent_id = self.path2id(parent_path)

            kind = self.get_kind(file_id)
            revision_id = self.get_last_changed(file_id)

            name = basename(path)
            if kind == 'directory':
                ie = InventoryDirectory(file_id, name, parent_id)
            elif kind == 'file':
                ie = InventoryFile(file_id, name, parent_id)
                ie.executable = self.is_executable(file_id)
            elif kind == 'symlink':
                ie = InventoryLink(file_id, name, parent_id)
                ie.symlink_target = self.get_symlink_target(file_id)
            ie.revision = revision_id

            if kind in ('directory', 'symlink'):
                ie.text_size, ie.text_sha1 = None, None
            else:
                ie.text_size, ie.text_sha1 = self.get_size_and_sha1(file_id)
            if (ie.text_size is None) and (kind == 'file'):
                raise BzrError('Got a text_size of None for file_id %r' % file_id)
            inv.add(ie)

        sorted_entries = self.sorted_path_id()
        for path, file_id in sorted_entries:
            add_entry(file_id)

        return inv

    # Have to overload the inherited inventory property
    # because _get_inventory is only called in the parent.
    # Reading the docs, property() objects do not use
    # overloading, they use the function as it was defined
    # at that instant
    inventory = property(_get_inventory)

    def __iter__(self):
        for path, entry in self.inventory.iter_entries():
            yield entry.file_id

    def sorted_path_id(self):
        paths = []
        for result in self._new_id.iteritems():
            paths.append(result)
        for id in self.base_tree:
            path = self.id2path(id)
            if path is None:
                continue
            paths.append((path, id))
        paths.sort()
        return paths


def patched_file(file_patch, original):
    """Produce a file-like object with the patched version of a text"""
    from bzrlib.patches import iter_patched
    from bzrlib.iterablefile import IterableFile
    if file_patch == "":
        return IterableFile(())
    # string.splitlines(True) also splits on '\r', but the iter_patched code
    # only expects to iterate over '\n' style lines
    return IterableFile(iter_patched(original,
                StringIO(file_patch).readlines()))
