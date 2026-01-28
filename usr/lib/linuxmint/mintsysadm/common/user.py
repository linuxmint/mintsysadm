#!/usr/bin/python3
import subprocess
import cairo
import gi
import math
import os
import pwd
import random
import xapp.util

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf
from common.widgets import DimmedTable

_ = xapp.util.l10n("mintsysadm")


# A helper to drop privileges. Necessary for
# security when accessing/creating user controlled files as root.
class PrivHelper():

    def __init__(self):
        self.orig_uid = os.getuid()
        self.orig_gid = os.getgid()
        self.orig_groups = os.getgroups()

    def drop_privs(self, user):
        uid = user.get_uid()
        # the user's main group id
        gid = pwd.getpwuid(uid).pw_gid

        # initialize the user's supplemental groups and main group
        os.initgroups(user.get_user_name(), gid)
        os.setegid(gid)
        os.seteuid(uid)

    def restore_privs(self):
        os.seteuid(self.orig_uid)
        os.setegid(self.orig_gid)
        os.setgroups(self.orig_groups)

class PasswordDialog(Gtk.Dialog):

    def __init__ (self, user, password_mask, parent = None):
        super(PasswordDialog, self).__init__(None, parent)

        self.user = user
        self.password_mask = password_mask

        self.set_modal(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_title(_("Change Password"))

        table = DimmedTable()
        table.add_labels([_("New password"), None, _("Confirm password")])

        self.new_password = Gtk.Entry()
        self.new_password.set_visibility(False)
        self.new_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-view-reveal-symbolic")
        self.new_password.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Show password"))
        self.new_password.connect("icon-release", self._on_new_password_icon_released)
        self.new_password.connect("changed", self._on_passwords_changed)
        table.attach(self.new_password, 1, 3, 0, 1)

        self.strengh_indicator = Gtk.ProgressBar()
        self.strengh_indicator.set_tooltip_text(_("The password must be at least 8 characters long."))
        self.strengh_indicator.set_fraction(0.0)
        table.attach(self.strengh_indicator, 1, 2, 1, 2, xoptions=Gtk.AttachOptions.EXPAND|Gtk.AttachOptions.FILL)
        self.strengh_indicator.set_size_request(-1, 1)

        self.strengh_label = Gtk.Label()
        self.strengh_label.set_tooltip_text(_("The password must be at least 8 characters long."))
        self.strengh_label.set_alignment(1, 0.5)
        table.attach(self.strengh_label, 2, 3, 1, 2)

        self.confirm_password = Gtk.Entry()
        self.confirm_password.set_visibility(False)
        self.confirm_password.connect("changed", self._on_passwords_changed)
        table.attach(self.confirm_password, 1, 3, 2, 3)

        self.set_border_width(6)

        box = self.get_content_area()
        box.add(table)
        self.show_all()

        self.infobar = Gtk.InfoBar()
        self.infobar.set_message_type(Gtk.MessageType.ERROR)
        label = Gtk.Label(_("An error occurred. The password was not changed."))
        content = self.infobar.get_content_area()
        content.add(label)
        table.attach(self.infobar, 0, 3, 4, 5)

        self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        self.add_button(_("Generate a password"), Gtk.ResponseType.NONE)
        self.add_button(_("Change"), Gtk.ResponseType.OK)

        self.get_widget_for_response(Gtk.ResponseType.OK).get_style_context().add_class("suggested-action")

        self.update_password_icon()
        self.set_response_sensitive(Gtk.ResponseType.OK, False)
        self.infobar.hide()

        self.connect("response", self._on_response)

    def _on_response(self, dialog, response_id):
        if response_id == Gtk.ResponseType.OK:
            self.change_password()
        elif response_id == Gtk.ResponseType.NONE:
            # Generate button clicked
            self.infobar.hide()
            newpass = generate_password()
            self.new_password.set_text(newpass)
            self.confirm_password.set_text(newpass)
            self.new_password.set_visibility(True)
            self.confirm_password.set_visibility(True)
            self.update_password_icon()
            return True  # Keep dialog open
        else:
            self.destroy()

    def change_password(self):
        newpass = self.new_password.get_text()
        self.user.set_password(newpass, "")
        subprocess.call(["gpasswd", "-d", self.user.get_user_name(), "nopasswdlogin"])
        self.password_mask.set_text('\u2022\u2022\u2022\u2022\u2022\u2022')
        self.destroy()

    def _on_new_password_icon_released(self, widget, icon_pos, event):
        visible = not self.new_password.get_visibility()
        self.new_password.set_visibility(visible)
        self.confirm_password.set_visibility(visible)
        self.update_password_icon()

    def update_password_icon(self):
        visible = self.new_password.get_visibility()
        if visible:
            self.new_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-view-conceal-symbolic")
            self.new_password.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Hide password"))
        else:
            self.new_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-view-reveal-symbolic")
            self.new_password.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Show password"))

    # Based on setPasswordStrength() in Mozilla Seamonkey, which is tri-licensed under MPL 1.1, GPL 2.0, and LGPL 2.1.
    # Forked from Ubiquity validation.py
    def password_strength(self, password):
        upper = lower = digit = symbol = 0
        for char in password:
            if char.isdigit():
                digit += 1
            elif char.islower():
                lower += 1
            elif char.isupper():
                upper += 1
            else:
                symbol += 1
        length = len(password)
        length = min(length,4)
        digit = min(digit,3)
        upper = min(upper,3)
        symbol = min(symbol,3)
        strength = (
            ((length * 0.1) - 0.2) +
            (digit * 0.1) +
            (symbol * 0.15) +
            (upper * 0.1))
        if strength > 1:
            strength = 1
        if strength < 0:
            strength = 0
        return strength

    def _on_passwords_changed(self, widget):
        self.infobar.hide()
        new_password = self.new_password.get_text()
        confirm_password = self.confirm_password.get_text()
        strength = self.password_strength(new_password)
        if new_password != confirm_password:
            self.confirm_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-dialog-warning-symbolic")
            self.confirm_password.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Passwords do not match"))
        else:
            self.confirm_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, None)
        if len(new_password) < 8:
            self.strengh_label.set_text(_("Too short"))
            self.strengh_indicator.set_fraction(0.0)
        elif strength < 0.5:
            self.strengh_label.set_text(_("Weak"))
            self.strengh_indicator.set_fraction(0.2)
        elif strength < 0.75:
            self.strengh_label.set_text(_("Fair"))
            self.strengh_indicator.set_fraction(0.4)
        elif strength < 0.9:
            self.strengh_label.set_text(_("Good"))
            self.strengh_indicator.set_fraction(0.6)
        else:
            self.strengh_label.set_text(_("Strong"))
            self.strengh_indicator.set_fraction(1.0)
        self.check_passwords()

    def check_passwords(self):
        new_password = self.new_password.get_text()
        confirm_password = self.confirm_password.get_text()
        if len(new_password) >= 8 and new_password == confirm_password:
            self.set_response_sensitive(Gtk.ResponseType.OK, True)
        else:
            self.set_response_sensitive(Gtk.ResponseType.OK, False)

# Create a circular pixbuf from an image path
# throws an exeption if the pixbuf cannot be created
def get_circular_pixbuf_from_path(path, size):
	pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
	size = min(pixbuf.get_width(), pixbuf.get_height())
	if pixbuf.get_width() != pixbuf.get_height():
		pixbuf = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
	radius = size // 2
	width = pixbuf.get_width()
	height = pixbuf.get_height()
	surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
	ctx = cairo.Context(surface)
	ctx.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
	ctx.arc(width - radius, radius, radius, 3 * math.pi / 2, 0)
	ctx.arc(width - radius, height - radius, radius, 0, math.pi / 2)
	ctx.arc(radius, height - radius, radius, math.pi / 2, math.pi)
	ctx.close_path()
	ctx.clip()
	Gdk.cairo_set_source_pixbuf(ctx, pixbuf, 0, 0)
	ctx.paint()
	return Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)

def generate_password():
    characters = "!@#$%^&*()_-+{}|:<>?=0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    newpass = ""
    for i in range(14):
        index = random.randint(0, len(characters) - 1)
        newpass = newpass + characters[index]
    return newpass