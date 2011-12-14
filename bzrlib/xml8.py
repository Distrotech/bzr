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

import cStringIO

from bzrlib import (
    cache_utf8,
    lazy_regex,
    revision as _mod_revision,
    trace,
    )
from bzrlib.xml_serializer import (
    Element,
    SubElement,
    XMLSerializer,
    encode_and_escape,
    escape_invalid_chars,
    get_utf8_or_ascii,
    serialize_inventory_flat,
    unpack_inventory_entry,
    unpack_inventory_flat,
    )
from bzrlib.revision import Revision
from bzrlib.errors import BzrError


_xml_unescape_map = {
    'apos':"'",
    'quot':'"',
    'amp':'&',
    'lt':'<',
    'gt':'>'
}


def _unescaper(match, _map=_xml_unescape_map):
    code = match.group(1)
    try:
        return _map[code]
    except KeyError:
        if not code.startswith('#'):
            raise
        return unichr(int(code[1:])).encode('utf8')


_unescape_re = lazy_regex.lazy_compile('\&([^;]*);')

def _unescape_xml(data):
    """Unescape predefined XML entities in a string of data."""
    return _unescape_re.sub(_unescaper, data)


class Serializer_v8(XMLSerializer):
    """This serialiser adds rich roots.

    Its revision format number matches its inventory number.
    """

    __slots__ = []

    root_id = None
    support_altered_by_hack = True
    # This format supports the altered-by hack that reads file ids directly out
    # of the versionedfile, without doing XML parsing.

    supported_kinds = set(['file', 'directory', 'symlink'])
    format_num = '8'
    revision_format_num = None

    # The search regex used by xml based repositories to determine what things
    # where changed in a single commit.
    _file_ids_altered_regex = lazy_regex.lazy_compile(
        r'file_id="(?P<file_id>[^"]+)"'
        r'.* revision="(?P<revision_id>[^"]+)"'
        )

    def _check_revisions(self, inv):
        """Extension point for subclasses to check during serialisation.

        :param inv: An inventory about to be serialised, to be checked.
        :raises: AssertionError if an error has occurred.
        """
        if inv.revision_id is None:
            raise AssertionError("inv.revision_id is None")
        if inv.root.revision is None:
            raise AssertionError("inv.root.revision is None")

    def _check_cache_size(self, inv_size, entry_cache):
        """Check that the entry_cache is large enough.

        We want the cache to be ~2x the size of an inventory. The reason is
        because we use a FIFO cache, and how Inventory records are likely to
        change. In general, you have a small number of records which change
        often, and a lot of records which do not change at all. So when the
        cache gets full, you actually flush out a lot of the records you are
        interested in, which means you need to recreate all of those records.
        An LRU Cache would be better, but the overhead negates the cache
        coherency benefit.

        One way to look at it, only the size of the cache > len(inv) is your
        'working' set. And in general, it shouldn't be a problem to hold 2
        inventories in memory anyway.

        :param inv_size: The number of entries in an inventory.
        """
        if entry_cache is None:
            return
        # 1.5 times might also be reasonable.
        recommended_min_cache_size = inv_size * 1.5
        if entry_cache.cache_size() < recommended_min_cache_size:
            recommended_cache_size = inv_size * 2
            trace.mutter('Resizing the inventory entry cache from %d to %d',
                         entry_cache.cache_size(), recommended_cache_size)
            entry_cache.resize(recommended_cache_size)

    def write_inventory_to_lines(self, inv):
        """Return a list of lines with the encoded inventory."""
        return self.write_inventory(inv, None)

    def write_inventory_to_string(self, inv, working=False):
        """Just call write_inventory with a StringIO and return the value.

        :param working: If True skip history data - text_sha1, text_size,
            reference_revision, symlink_target.
        """
        sio = cStringIO.StringIO()
        self.write_inventory(inv, sio, working)
        return sio.getvalue()

    def write_inventory(self, inv, f, working=False):
        """Write inventory to a file.

        :param inv: the inventory to write.
        :param f: the file to write. (May be None if the lines are the desired
            output).
        :param working: If True skip history data - text_sha1, text_size,
            reference_revision, symlink_target.
        :return: The inventory as a list of lines.
        """
        output = []
        append = output.append
        self._append_inventory_root(append, inv)
        serialize_inventory_flat(inv, append,
            self.root_id, self.supported_kinds, working)
        if f is not None:
            f.writelines(output)
        # Just to keep the cache from growing without bounds
        # but we may actually not want to do clear the cache
        #_clear_cache()
        return output

    def _append_inventory_root(self, append, inv):
        """Append the inventory root to output."""
        if inv.revision_id is not None:
            revid1 = ' revision_id="'
            revid2 = encode_and_escape(inv.revision_id)
        else:
            revid1 = ""
            revid2 = ""
        append('<inventory format="%s"%s%s>\n' % (
            self.format_num, revid1, revid2))
        append('<directory file_id="%s name="%s revision="%s />\n' % (
            encode_and_escape(inv.root.file_id),
            encode_and_escape(inv.root.name),
            encode_and_escape(inv.root.revision)))

    def _pack_revision(self, rev):
        """Revision object -> xml tree"""
        # For the XML format, we need to write them as Unicode rather than as
        # utf-8 strings. So that cElementTree can handle properly escaping
        # them.
        decode_utf8 = cache_utf8.decode
        revision_id = rev.revision_id
        if isinstance(revision_id, str):
            revision_id = decode_utf8(revision_id)
        format_num = self.format_num
        if self.revision_format_num is not None:
            format_num = self.revision_format_num
        root = Element('revision',
                       committer = rev.committer,
                       timestamp = '%.3f' % rev.timestamp,
                       revision_id = revision_id,
                       inventory_sha1 = rev.inventory_sha1,
                       format=format_num,
                       )
        if rev.timezone is not None:
            root.set('timezone', str(rev.timezone))
        root.text = '\n'
        msg = SubElement(root, 'message')
        msg.text = escape_invalid_chars(rev.message)[0]
        msg.tail = '\n'
        if rev.parent_ids:
            pelts = SubElement(root, 'parents')
            pelts.tail = pelts.text = '\n'
            for parent_id in rev.parent_ids:
                _mod_revision.check_not_reserved_id(parent_id)
                p = SubElement(pelts, 'revision_ref')
                p.tail = '\n'
                if isinstance(parent_id, str):
                    parent_id = decode_utf8(parent_id)
                p.set('revision_id', parent_id)
        if rev.properties:
            self._pack_revision_properties(rev, root)
        return root

    def _pack_revision_properties(self, rev, under_element):
        top_elt = SubElement(under_element, 'properties')
        for prop_name, prop_value in sorted(rev.properties.items()):
            prop_elt = SubElement(top_elt, 'property')
            prop_elt.set('name', prop_name)
            prop_elt.text = prop_value
            prop_elt.tail = '\n'
        top_elt.tail = '\n'

    def _unpack_entry(self, elt, entry_cache=None, return_from_cache=False):
        # This is here because it's overridden by xml7
        return unpack_inventory_entry(elt, entry_cache,
                return_from_cache)

    def _unpack_inventory(self, elt, revision_id=None, entry_cache=None,
                          return_from_cache=False):
        """Construct from XML Element"""
        inv = unpack_inventory_flat(elt, self.format_num, self._unpack_entry,
            entry_cache, return_from_cache)
        self._check_cache_size(len(inv), entry_cache)
        return inv

    def _unpack_revision(self, elt):
        """XML Element -> Revision object"""
        format = elt.get('format')
        format_num = self.format_num
        if self.revision_format_num is not None:
            format_num = self.revision_format_num
        if format is not None:
            if format != format_num:
                raise BzrError("invalid format version %r on revision"
                                % format)
        get_cached = get_utf8_or_ascii
        rev = Revision(committer = elt.get('committer'),
                       timestamp = float(elt.get('timestamp')),
                       revision_id = get_cached(elt.get('revision_id')),
                       inventory_sha1 = elt.get('inventory_sha1')
                       )
        parents = elt.find('parents') or []
        for p in parents:
            rev.parent_ids.append(get_cached(p.get('revision_id')))
        self._unpack_revision_properties(elt, rev)
        v = elt.get('timezone')
        if v is None:
            rev.timezone = 0
        else:
            rev.timezone = int(v)
        rev.message = elt.findtext('message') # text of <message>
        return rev

    def _unpack_revision_properties(self, elt, rev):
        """Unpack properties onto a revision."""
        props_elt = elt.find('properties')
        if not props_elt:
            return
        for prop_elt in props_elt:
            if prop_elt.tag != 'property':
                raise AssertionError(
                    "bad tag under properties list: %r" % prop_elt.tag)
            name = prop_elt.get('name')
            value = prop_elt.text
            # If a property had an empty value ('') cElementTree reads
            # that back as None, convert it back to '', so that all
            # properties have string values
            if value is None:
                value = ''
            if name in rev.properties:
                raise AssertionError("repeated property %r" % name)
            rev.properties[name] = value

    def _find_text_key_references(self, line_iterator):
        """Core routine for extracting references to texts from inventories.

        This performs the translation of xml lines to revision ids.

        :param line_iterator: An iterator of lines, origin_version_id
        :return: A dictionary mapping text keys ((fileid, revision_id) tuples)
            to whether they were referred to by the inventory of the
            revision_id that they contain. Note that if that revision_id was
            not part of the line_iterator's output then False will be given -
            even though it may actually refer to that key.
        """
        if not self.support_altered_by_hack:
            raise AssertionError(
                "_find_text_key_references only "
                "supported for branches which store inventory as unnested xml"
                ", not on %r" % self)
        result = {}

        # this code needs to read every new line in every inventory for the
        # inventories [revision_ids]. Seeing a line twice is ok. Seeing a line
        # not present in one of those inventories is unnecessary but not
        # harmful because we are filtering by the revision id marker in the
        # inventory lines : we only select file ids altered in one of those
        # revisions. We don't need to see all lines in the inventory because
        # only those added in an inventory in rev X can contain a revision=X
        # line.
        unescape_revid_cache = {}
        unescape_fileid_cache = {}

        # jam 20061218 In a big fetch, this handles hundreds of thousands
        # of lines, so it has had a lot of inlining and optimizing done.
        # Sorry that it is a little bit messy.
        # Move several functions to be local variables, since this is a long
        # running loop.
        search = self._file_ids_altered_regex.search
        unescape = _unescape_xml
        setdefault = result.setdefault
        for line, line_key in line_iterator:
            match = search(line)
            if match is None:
                continue
            # One call to match.group() returning multiple items is quite a
            # bit faster than 2 calls to match.group() each returning 1
            file_id, revision_id = match.group('file_id', 'revision_id')

            # Inlining the cache lookups helps a lot when you make 170,000
            # lines and 350k ids, versus 8.4 unique ids.
            # Using a cache helps in 2 ways:
            #   1) Avoids unnecessary decoding calls
            #   2) Re-uses cached strings, which helps in future set and
            #      equality checks.
            # (2) is enough that removing encoding entirely along with
            # the cache (so we are using plain strings) results in no
            # performance improvement.
            try:
                revision_id = unescape_revid_cache[revision_id]
            except KeyError:
                unescaped = unescape(revision_id)
                unescape_revid_cache[revision_id] = unescaped
                revision_id = unescaped

            # Note that unconditionally unescaping means that we deserialise
            # every fileid, which for general 'pull' is not great, but we don't
            # really want to have some many fulltexts that this matters anyway.
            # RBC 20071114.
            try:
                file_id = unescape_fileid_cache[file_id]
            except KeyError:
                unescaped = unescape(file_id)
                unescape_fileid_cache[file_id] = unescaped
                file_id = unescaped

            key = (file_id, revision_id)
            setdefault(key, False)
            if revision_id == line_key[-1]:
                result[key] = True
        return result


serializer_v8 = Serializer_v8()
