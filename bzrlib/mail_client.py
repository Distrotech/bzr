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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

import errno
import os
import subprocess
import sys
import tempfile
import urllib

import bzrlib
from bzrlib import (
    email_message,
    errors,
    msgeditor,
    osutils,
    urlutils,
    registry
    )

mail_client_registry = registry.Registry()


class MailClient(object):
    """A mail client that can send messages with attachements."""

    def __init__(self, config):
        self.config = config

    def compose(self, prompt, to, subject, attachment, mime_subtype,
                extension, basename=None, body=None):
        """Compose (and possibly send) an email message

        Must be implemented by subclasses.

        :param prompt: A message to tell the user what to do.  Supported by
            the Editor client, but ignored by others
        :param to: The address to send the message to
        :param subject: The contents of the subject line
        :param attachment: An email attachment, as a bytestring
        :param mime_subtype: The attachment is assumed to be a subtype of
            Text.  This allows the precise subtype to be specified, e.g.
            "plain", "x-patch", etc.
        :param extension: The file extension associated with the attachment
            type, e.g. ".patch"
        :param basename: The name to use for the attachment, e.g.
            "send-nick-3252"
        """
        raise NotImplementedError

    def compose_merge_request(self, to, subject, directive, basename=None,
                              body=None):
        """Compose (and possibly send) a merge request

        :param to: The address to send the request to
        :param subject: The subject line to use for the request
        :param directive: A merge directive representing the merge request, as
            a bytestring.
        :param basename: The name to use for the attachment, e.g.
            "send-nick-3252"
        """
        prompt = self._get_merge_prompt("Please describe these changes:", to,
                                        subject, directive)
        self.compose(prompt, to, subject, directive,
            'x-patch', '.patch', basename, body)

    def _get_merge_prompt(self, prompt, to, subject, attachment):
        """Generate a prompt string.  Overridden by Editor.

        :param prompt: A string suggesting what user should do
        :param to: The address the mail will be sent to
        :param subject: The subject line of the mail
        :param attachment: The attachment that will be used
        """
        return ''


class Editor(MailClient):
    """DIY mail client that uses commit message editor"""

    supports_body = True

    def _get_merge_prompt(self, prompt, to, subject, attachment):
        """See MailClient._get_merge_prompt"""
        return (u"%s\n\n"
                u"To: %s\n"
                u"Subject: %s\n\n"
                u"%s" % (prompt, to, subject,
                         attachment.decode('utf-8', 'replace')))

    def compose(self, prompt, to, subject, attachment, mime_subtype,
                extension, basename=None, body=None):
        """See MailClient.compose"""
        if not to:
            raise errors.NoMailAddressSpecified()
        body = msgeditor.edit_commit_message(prompt, start_message=body)
        if body == '':
            raise errors.NoMessageSupplied()
        email_message.EmailMessage.send(self.config,
                                        self.config.username(),
                                        to,
                                        subject,
                                        body,
                                        attachment,
                                        attachment_mime_subtype=mime_subtype)
mail_client_registry.register('editor', Editor,
                              help=Editor.__doc__)


class BodyExternalMailClient(MailClient):

    supports_body = True

    def _get_client_commands(self):
        """Provide a list of commands that may invoke the mail client"""
        if sys.platform == 'win32':
            import win32utils
            return [win32utils.get_app_path(i) for i in self._client_commands]
        else:
            return self._client_commands

    def compose(self, prompt, to, subject, attachment, mime_subtype,
                extension, basename=None, body=None):
        """See MailClient.compose.

        Writes the attachment to a temporary file, invokes _compose.
        """
        if basename is None:
            basename = 'attachment'
        pathname = osutils.mkdtemp(prefix='bzr-mail-')
        attach_path = osutils.pathjoin(pathname, basename + extension)
        outfile = open(attach_path, 'wb')
        try:
            outfile.write(attachment)
        finally:
            outfile.close()
        if body is not None:
            kwargs = {'body': body}
        else:
            kwargs = {}
        self._compose(prompt, to, subject, attach_path, mime_subtype,
                      extension, **kwargs)

    def _compose(self, prompt, to, subject, attach_path, mime_subtype,
                 extension, body=None, from_=None):
        """Invoke a mail client as a commandline process.

        Overridden by MAPIClient.
        :param to: The address to send the mail to
        :param subject: The subject line for the mail
        :param pathname: The path to the attachment
        :param mime_subtype: The attachment is assumed to have a major type of
            "text", but the precise subtype can be specified here
        :param extension: A file extension (including period) associated with
            the attachment type.
        :param body: Optional body text.
        :param from_: Optional From: header.
        """
        for name in self._get_client_commands():
            cmdline = [self._encode_path(name, 'executable')]
            if body is not None:
                kwargs = {'body': body}
            else:
                kwargs = {}
            if from_ is not None:
                kwargs['from_'] = from_
            cmdline.extend(self._get_compose_commandline(to, subject,
                                                         attach_path,
                                                         **kwargs))
            try:
                subprocess.call(cmdline)
            except OSError, e:
                if e.errno != errno.ENOENT:
                    raise
            else:
                break
        else:
            raise errors.MailClientNotFound(self._client_commands)

    def _get_compose_commandline(self, to, subject, attach_path, body):
        """Determine the commandline to use for composing a message

        Implemented by various subclasses
        :param to: The address to send the mail to
        :param subject: The subject line for the mail
        :param attach_path: The path to the attachment
        """
        raise NotImplementedError

    def _encode_safe(self, u):
        """Encode possible unicode string argument to 8-bit string
        in user_encoding. Unencodable characters will be replaced
        with '?'.

        :param  u:  possible unicode string.
        :return:    encoded string if u is unicode, u itself otherwise.
        """
        if isinstance(u, unicode):
            return u.encode(osutils.get_user_encoding(), 'replace')
        return u

    def _encode_path(self, path, kind):
        """Encode unicode path in user encoding.

        :param  path:   possible unicode path.
        :param  kind:   path kind ('executable' or 'attachment').
        :return:        encoded path if path is unicode,
                        path itself otherwise.
        :raise:         UnableEncodePath.
        """
        if isinstance(path, unicode):
            try:
                return path.encode(osutils.get_user_encoding())
            except UnicodeEncodeError:
                raise errors.UnableEncodePath(path, kind)
        return path


class ExternalMailClient(BodyExternalMailClient):
    """An external mail client."""

    supports_body = False


class Evolution(BodyExternalMailClient):
    """Evolution mail client."""

    _client_commands = ['evolution']

    def _get_compose_commandline(self, to, subject, attach_path, body=None):
        """See ExternalMailClient._get_compose_commandline"""
        message_options = {}
        if subject is not None:
            message_options['subject'] = subject
        if attach_path is not None:
            message_options['attach'] = attach_path
        if body is not None:
            message_options['body'] = body
        options_list = ['%s=%s' % (k, urlutils.escape(v)) for (k, v) in
                        sorted(message_options.iteritems())]
        return ['mailto:%s?%s' % (self._encode_safe(to or ''),
            '&'.join(options_list))]
mail_client_registry.register('evolution', Evolution,
                              help=Evolution.__doc__)


class Mutt(BodyExternalMailClient):
    """Mutt mail client."""

    _client_commands = ['mutt']

    def _get_compose_commandline(self, to, subject, attach_path, body=None):
        """See ExternalMailClient._get_compose_commandline"""
        message_options = []
        if subject is not None:
            message_options.extend(['-s', self._encode_safe(subject)])
        if attach_path is not None:
            message_options.extend(['-a',
                self._encode_path(attach_path, 'attachment')])
        if body is not None:
            fd, temp_file = tempfile.mkstemp(prefix="mutt-body-",
                                             suffix=".txt")
            try:
                os.write(fd, body)
            finally:
                os.close(fd)
            message_options.extend(['-i', temp_file])
        if to is not None:
            message_options.extend(['--', self._encode_safe(to)])
        return message_options
mail_client_registry.register('mutt', Mutt,
                              help=Mutt.__doc__)


class Thunderbird(BodyExternalMailClient):
    """Mozilla Thunderbird (or Icedove)

    Note that Thunderbird 1.5 is buggy and does not support setting
    "to" simultaneously with including a attachment.

    There is a workaround if no attachment is present, but we always need to
    send attachments.
    """

    _client_commands = ['thunderbird', 'mozilla-thunderbird', 'icedove',
        '/Applications/Mozilla/Thunderbird.app/Contents/MacOS/thunderbird-bin',
        '/Applications/Thunderbird.app/Contents/MacOS/thunderbird-bin']

    def _get_compose_commandline(self, to, subject, attach_path, body=None):
        """See ExternalMailClient._get_compose_commandline"""
        message_options = {}
        if to is not None:
            message_options['to'] = self._encode_safe(to)
        if subject is not None:
            message_options['subject'] = self._encode_safe(subject)
        if attach_path is not None:
            message_options['attachment'] = urlutils.local_path_to_url(
                attach_path)
        if body is not None:
            options_list = ['body=%s' % urllib.quote(self._encode_safe(body))]
        else:
            options_list = []
        options_list.extend(["%s='%s'" % (k, v) for k, v in
                        sorted(message_options.iteritems())])
        return ['-compose', ','.join(options_list)]
mail_client_registry.register('thunderbird', Thunderbird,
                              help=Thunderbird.__doc__)


class KMail(ExternalMailClient):
    """KDE mail client."""

    _client_commands = ['kmail']

    def _get_compose_commandline(self, to, subject, attach_path):
        """See ExternalMailClient._get_compose_commandline"""
        message_options = []
        if subject is not None:
            message_options.extend(['-s', self._encode_safe(subject)])
        if attach_path is not None:
            message_options.extend(['--attach',
                self._encode_path(attach_path, 'attachment')])
        if to is not None:
            message_options.extend([self._encode_safe(to)])
        return message_options
mail_client_registry.register('kmail', KMail,
                              help=KMail.__doc__)


class Claws(ExternalMailClient):
    """Claws mail client."""

    supports_body = True

    _client_commands = ['claws-mail']

    def _get_compose_commandline(self, to, subject, attach_path, body=None,
                                 from_=None):
        """See ExternalMailClient._get_compose_commandline"""
        compose_url = []
        if from_ is not None:
            compose_url.append('from=' + urllib.quote(from_))
        if subject is not None:
            # Don't use urllib.quote_plus because Claws doesn't seem
            # to recognise spaces encoded as "+".
            compose_url.append(
                'subject=' + urllib.quote(self._encode_safe(subject)))
        if body is not None:
            compose_url.append(
                'body=' + urllib.quote(self._encode_safe(body)))
        # to must be supplied for the claws-mail --compose syntax to work.
        if to is None:
            raise errors.NoMailAddressSpecified()
        compose_url = 'mailto:%s?%s' % (
            self._encode_safe(to), '&'.join(compose_url))
        # Collect command-line options.
        message_options = ['--compose', compose_url]
        if attach_path is not None:
            message_options.extend(
                ['--attach', self._encode_path(attach_path, 'attachment')])
        return message_options

    def _compose(self, prompt, to, subject, attach_path, mime_subtype,
                 extension, body=None, from_=None):
        """See ExternalMailClient._compose"""
        if from_ is None:
            from_ = self.config.get_user_option('email')
        super(Claws, self)._compose(prompt, to, subject, attach_path,
                                    mime_subtype, extension, body, from_)


mail_client_registry.register('claws', Claws,
                              help=Claws.__doc__)


class XDGEmail(BodyExternalMailClient):
    """xdg-email attempts to invoke the user's preferred mail client"""

    _client_commands = ['xdg-email']

    def _get_compose_commandline(self, to, subject, attach_path, body=None):
        """See ExternalMailClient._get_compose_commandline"""
        if not to:
            raise errors.NoMailAddressSpecified()
        commandline = [self._encode_safe(to)]
        if subject is not None:
            commandline.extend(['--subject', self._encode_safe(subject)])
        if attach_path is not None:
            commandline.extend(['--attach',
                self._encode_path(attach_path, 'attachment')])
        if body is not None:
            commandline.extend(['--body', self._encode_safe(body)])
        return commandline
mail_client_registry.register('xdg-email', XDGEmail,
                              help=XDGEmail.__doc__)


class EmacsMail(ExternalMailClient):
    """Call emacsclient to have a mail buffer.

    This only work for emacs >= 22.1 due to recent -e/--eval support.

    The good news is that this implementation will work with all mail
    agents registered against ``mail-user-agent``. So there is no need
    to instantiate ExternalMailClient for each and every GNU Emacs
    MUA.

    Users just have to ensure that ``mail-user-agent`` is set according
    to their tastes.
    """

    _client_commands = ['emacsclient']

    def _prepare_send_function(self):
        """Write our wrapper function into a temporary file.

        This temporary file will be loaded at runtime in
        _get_compose_commandline function.

        This function does not remove the file.  That's a wanted
        behaviour since _get_compose_commandline won't run the send
        mail function directly but return the eligible command line.
        Removing our temporary file here would prevent our sendmail
        function to work.  (The file is deleted by some elisp code
        after being read by Emacs.)
        """

        _defun = r"""(defun bzr-add-mime-att (file)
  "Attach FILE to a mail buffer as a MIME attachment."
  (let ((agent mail-user-agent))
    (if (and file (file-exists-p file))
        (cond
         ((eq agent 'sendmail-user-agent)
          (progn
            (mail-text)
            (newline)
            (if (functionp 'etach-attach)
              (etach-attach file)
              (mail-attach-file file))))
         ((or (eq agent 'message-user-agent)
              (eq agent 'gnus-user-agent)
              (eq agent 'mh-e-user-agent))
          (progn
            (mml-attach-file file "text/x-patch" "BZR merge" "inline")))
         ((eq agent 'mew-user-agent)
          (progn
            (mew-draft-prepare-attachments)
            (mew-attach-link file (file-name-nondirectory file))
            (let* ((nums (mew-syntax-nums))
                   (syntax (mew-syntax-get-entry mew-encode-syntax nums)))
              (mew-syntax-set-cd syntax "BZR merge")
              (mew-encode-syntax-print mew-encode-syntax))
            (mew-header-goto-body)))
         (t
          (message "Unhandled MUA, report it on bazaar@lists.canonical.com")))
      (error "File %s does not exist." file))))
"""

        fd, temp_file = tempfile.mkstemp(prefix="emacs-bzr-send-",
                                         suffix=".el")
        try:
            os.write(fd, _defun)
        finally:
            os.close(fd) # Just close the handle but do not remove the file.
        return temp_file

    def _get_compose_commandline(self, to, subject, attach_path):
        commandline = ["--eval"]

        _to = "nil"
        _subject = "nil"

        if to is not None:
            _to = ("\"%s\"" % self._encode_safe(to).replace('"', '\\"'))
        if subject is not None:
            _subject = ("\"%s\"" %
                        self._encode_safe(subject).replace('"', '\\"'))

        # Funcall the default mail composition function
        # This will work with any mail mode including default mail-mode
        # User must tweak mail-user-agent variable to tell what function
        # will be called inside compose-mail.
        mail_cmd = "(compose-mail %s %s)" % (_to, _subject)
        commandline.append(mail_cmd)

        # Try to attach a MIME attachment using our wrapper function
        if attach_path is not None:
            # Do not create a file if there is no attachment
            elisp = self._prepare_send_function()
            lmmform = '(load "%s")' % elisp
            mmform  = '(bzr-add-mime-att "%s")' % \
                self._encode_path(attach_path, 'attachment')
            rmform = '(delete-file "%s")' % elisp
            commandline.append(lmmform)
            commandline.append(mmform)
            commandline.append(rmform)

        return commandline
mail_client_registry.register('emacsclient', EmacsMail,
                              help=EmacsMail.__doc__)


class MAPIClient(BodyExternalMailClient):
    """Default Windows mail client launched using MAPI."""

    def _compose(self, prompt, to, subject, attach_path, mime_subtype,
                 extension, body=None):
        """See ExternalMailClient._compose.

        This implementation uses MAPI via the simplemapi ctypes wrapper
        """
        from bzrlib.util import simplemapi
        try:
            simplemapi.SendMail(to or '', subject or '', body or '',
                                attach_path)
        except simplemapi.MAPIError, e:
            if e.code != simplemapi.MAPI_USER_ABORT:
                raise errors.MailClientNotFound(['MAPI supported mail client'
                                                 ' (error %d)' % (e.code,)])
mail_client_registry.register('mapi', MAPIClient,
                              help=MAPIClient.__doc__)


class DefaultMail(MailClient):
    """Default mail handling.  Tries XDGEmail (or MAPIClient on Windows),
    falls back to Editor"""

    supports_body = True

    def _mail_client(self):
        """Determine the preferred mail client for this platform"""
        if osutils.supports_mapi():
            return MAPIClient(self.config)
        else:
            return XDGEmail(self.config)

    def compose(self, prompt, to, subject, attachment, mime_subtype,
                extension, basename=None, body=None):
        """See MailClient.compose"""
        try:
            return self._mail_client().compose(prompt, to, subject,
                                               attachment, mimie_subtype,
                                               extension, basename, body)
        except errors.MailClientNotFound:
            return Editor(self.config).compose(prompt, to, subject,
                          attachment, mimie_subtype, extension, body)

    def compose_merge_request(self, to, subject, directive, basename=None,
                              body=None):
        """See MailClient.compose_merge_request"""
        try:
            return self._mail_client().compose_merge_request(to, subject,
                    directive, basename=basename, body=body)
        except errors.MailClientNotFound:
            return Editor(self.config).compose_merge_request(to, subject,
                          directive, basename=basename, body=body)
mail_client_registry.register('default', DefaultMail,
                              help=DefaultMail.__doc__)
mail_client_registry.default_key = 'default'
