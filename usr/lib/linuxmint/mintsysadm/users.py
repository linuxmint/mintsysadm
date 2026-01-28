#!/usr/bin/python3

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AccountsService", "1.0")
from gi.repository import Gtk, GObject, Gio, Gdk, GdkPixbuf, AccountsService, GLib, Pango

import os
import cairo
import math
import pwd
import grp
import gettext
import shutil
import re
import subprocess
from random import randint
from setproctitle import setproctitle
import PIL
from PIL import Image
import pyudev
import re
import shutil
import subprocess
import xapp.SettingsWidgets as Xs
import xapp.threading as xt
import xapp.util
import xapp.widgets
gi.require_version("Gtk", "3.0")
from pathlib import Path
from datetime import datetime

_ = xapp.util.l10n("mintsysadm")


ICON_SIZE_DIALOG_PREVIEW = 128
ICON_SIZE_CHOOSE_BUTTON = 96
ICON_SIZE_FLOWBOX = 96
ICON_SIZE_CHOOSE_MENU = 48

class PrivHelper(object):
    """A helper for performing temporary privilege drops. Necessary for
    security when accessing user controlled files as root."""

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

priv_helper = PrivHelper()

class EditableEntry(Gtk.Stack):
    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_FIRST, None, (str,))
    }

    PAGE_BUTTON = "button"
    PAGE_ENTRY = "entry"

    def __init__(self):
        super(EditableEntry, self).__init__()

        self.label = Gtk.Label()
        self.entry = Gtk.Entry()
        self.button = Gtk.Button()
        self.button.set_alignment(0.5, 0.5)
        self.button.set_relief(Gtk.ReliefStyle.NONE)
        self.label = Gtk.Label()
        self.button.add(self.label)

        # Add to stack with names
        self.add_named(self.button, self.PAGE_BUTTON)
        self.add_named(self.entry, self.PAGE_ENTRY)

        self.set_visible_child_name(self.PAGE_BUTTON)
        self.set_transition_type(Gtk.StackTransitionType.NONE)  # No animation

        self.editable = False
        self.show_all()

        self.button.connect("released", self._on_button_clicked)
        self.button.connect("activate", self._on_button_clicked)
        self.entry.connect("activate", self._on_entry_validated)
        self.entry.connect("changed", self._on_entry_changed)

    def set_text(self, text):
        self.label.set_markup(f"<b>{text}</b>")
        self.entry.set_text(text)

    def _on_button_clicked(self, button):
        self.set_editable(True)

    def _on_entry_validated(self, entry):
        self.set_editable(False)
        self.emit("changed", entry.get_text())

    def _on_entry_changed(self, entry):
        self.label.set_markup(f"<b>{entry.get_text()}</b>")

    def set_editable(self, editable):
        if editable:
            self.set_visible_child_name(self.PAGE_ENTRY)
            self.entry.grab_focus()
        else:
            self.set_visible_child_name(self.PAGE_BUTTON)
        self.editable = editable

    def set_tooltip_text(self, tooltip):
        self.button.set_tooltip_text(tooltip)

    def get_editable(self):
        return self.editable

    def get_text(self):
        return self.entry.get_text()

class DimmedTable (Gtk.Table):
    def __init__ (self):
        super(DimmedTable, self).__init__()
        self.set_border_width(6)
        self.set_row_spacings(8)
        self.set_col_spacings(15)

    def add_labels(self, texts):
        row = 0
        for text in texts:
            if text is not None:
                label = Gtk.Label(text)
                label.set_alignment(1, 0.5)
                label.get_style_context().add_class("dim-label")
                self.attach(label, 0, 1, row, row+1, xoptions=Gtk.AttachOptions.EXPAND|Gtk.AttachOptions.FILL)
            row = row + 1

    def add_controls(self, controls):
        row = 0
        for control in controls:
            self.attach(control, 1, 2, row, row+1)
            row = row + 1

class PasswordDialog(Gtk.Dialog):

    def __init__ (self, user, password_mask, group_mask, parent = None):
        super(PasswordDialog, self).__init__(None, parent)

        self.user = user
        self.password_mask = password_mask
        self.group_mask = group_mask

        self.set_modal(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_title(_("Change Password"))

        table = DimmedTable()
        table.add_labels([_("New password"), None, _("Confirm password")])

        self.new_password = Gtk.Entry()
        self.new_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-view-refresh-symbolic")
        self.new_password.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Generate a password"))
        self.new_password.connect("icon-release", self._on_new_password_icon_released)
        self.new_password.connect("changed", self._on_passwords_changed)
        table.attach(self.new_password, 1, 3, 0, 1)

        self.strengh_indicator = Gtk.ProgressBar()
        self.strengh_indicator.set_tooltip_text(_("Your new password needs to be at least 8 characters long"))
        self.strengh_indicator.set_fraction(0.0)
        table.attach(self.strengh_indicator, 1, 2, 1, 2, xoptions=Gtk.AttachOptions.EXPAND|Gtk.AttachOptions.FILL)
        self.strengh_indicator.set_size_request(-1, 1)

        self.strengh_label = Gtk.Label()
        self.strengh_label.set_tooltip_text(_("Your new password needs to be at least 8 characters long"))
        self.strengh_label.set_alignment(1, 0.5)
        table.attach(self.strengh_label, 2, 3, 1, 2)

        self.confirm_password = Gtk.Entry()
        self.confirm_password.connect("changed", self._on_passwords_changed)
        table.attach(self.confirm_password, 1, 3, 2, 3)

        self.show_password = Gtk.CheckButton(_("Show password"))
        self.show_password.connect('toggled', self._on_show_password_toggled)
        table.attach(self.show_password, 1, 3, 3, 4)

        self.set_border_width(6)

        box = self.get_content_area()
        box.add(table)
        self.show_all()

        self.infobar = Gtk.InfoBar()
        self.infobar.set_message_type(Gtk.MessageType.ERROR)
        label = Gtk.Label(_("An error occurred. Your password was not changed."))
        content = self.infobar.get_content_area()
        content.add(label)
        table.attach(self.infobar, 0, 3, 4, 5)

        self.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Change"), Gtk.ResponseType.OK, )

        self.set_passwords_visibility()
        self.set_response_sensitive(Gtk.ResponseType.OK, False)
        self.infobar.hide()

        self.connect("response", self._on_response)

    def _on_response(self, dialog, response_id):
        if response_id == Gtk.ResponseType.OK:
            self.change_password()
        else:
            self.destroy()

    def change_password(self):
        newpass = self.new_password.get_text()
        self.user.set_password(newpass, "")
        mask = self.group_mask.get_text()
        if "nopasswdlogin" in mask:
            subprocess.call(["gpasswd", "-d", self.user.get_user_name(), "nopasswdlogin"])
            mask = mask.split(", ")
            mask.remove("nopasswdlogin")
            mask = ", ".join(mask)
            self.group_mask.set_text(mask)
            self.password_mask.set_text('\u2022\u2022\u2022\u2022\u2022\u2022')
        self.destroy()

    def set_passwords_visibility(self):
        visible = self.show_password.get_active()
        self.new_password.set_visibility(visible)
        self.confirm_password.set_visibility(visible)

    def _on_new_password_icon_released(self, widget, icon_pos, event):
        self.infobar.hide()
        self.show_password.set_active(True)
        characters = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
        newpass = ""
        for i in range (8):
            index = randint(0, len(characters) -1)
            newpass = newpass + characters[index]

        self.new_password.set_text(newpass)
        self.confirm_password.set_text(newpass)
        self.check_passwords()

    def _on_show_password_toggled(self, widget):
        self.set_passwords_visibility()

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

class NewUserDialog(Gtk.Dialog):

    def __init__ (self, parent = None):
        super(NewUserDialog, self).__init__(None, parent)

        try:
            self.set_modal(True)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_title("")

            self.account_type_combo = Gtk.ComboBoxText()
            self.account_type_combo.append_text(_("Standard"))
            self.account_type_combo.append_text(_("Administrator"))
            self.account_type_combo.set_active(0)

            self.realname_entry = Gtk.Entry()
            self.realname_entry.connect("changed", self._on_info_changed)

            self.username_entry = Gtk.Entry()
            self.username_entry.connect("changed", self._on_info_changed)

            table = DimmedTable()
            table.add_labels([_("Account Type"), _("Full Name"), _("Username")])
            table.add_controls([self.account_type_combo, self.realname_entry, self.username_entry])

            self.set_border_width(6)

            box = self.get_content_area()
            box.add(table)
            self.show_all()

            self.add_buttons(_("Cancel"), Gtk.ResponseType.CANCEL, _("Add"), Gtk.ResponseType.OK, )
            self.set_response_sensitive(Gtk.ResponseType.OK, False)
            self.get_widget_for_response(Gtk.ResponseType.OK).get_style_context().add_class("suggested-action")

        except Exception as detail:
            print(detail)

    def user_exists(self, user_name):
        users = AccountsService.UserManager.get_default().list_users()

        for user in users:
            if user.get_user_name() == user_name:
                return True

        return False

    def _on_info_changed(self, widget):
        fullname = self.realname_entry.get_text()
        username = self.username_entry.get_text()
        valid = True
        if re.search('[^a-z0-9_-]', username):
            self.username_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-dialog-warning-symbolic")
            self.username_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Invalid username"))
            valid = False
        elif self.user_exists(username):
            self.username_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-dialog-warning-symbolic")
            self.username_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("A user with the name '%s' already exists.") % username)
            valid = False
        else:
            self.username_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, None)
        if username == "" or fullname == "":
            valid = False

        self.set_response_sensitive(Gtk.ResponseType.OK, valid)

class UsersWidget(Gtk.Box):
    def __init__(self, window):
        super().__init__()
        self.window = window
        gladefile = "/usr/share/mintsysadm/mintsysadm.ui"
        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("mintsysadm")
        self.builder.add_from_file(gladefile)
        self.main_box = self.builder.get_object("box_users")
        self.stack = self.builder.get_object("users_stack")
        self.user = None # Currently edited user
        self.add(self.main_box)
        self.show_all()

        self.usernames = [] # Keep track of loaded usernames

        # self.window.set_title(_("Users"))

        self.builder.get_object("button_add_user").connect("clicked", self.on_user_addition)
        self.builder.get_object("button_user_back").connect("clicked", self.on_back_clicked)
        self.builder.get_object("button_user_remove").connect("clicked", self.on_remove_clicked)

        self.users_flowbox = self.builder.get_object("users_flowbox")
        self.users_flowbox.connect("child-activated", self.on_user_selected)
        self.users_flowbox.set_sort_func(self.sort_by_name)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .user-card {
                padding: 12px;
                border-radius: 12px;
                border: 2px solid transparent;
                transition: all 200ms;
            }
            .user-card.hover {
                border-color: alpha(@theme_selected_bg_color, 1.0);
            }
            """)

        screen = Gdk.Screen.get_default()
        style_context = Gtk.StyleContext()
        style_context.add_provider_for_screen(screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.face_button = Gtk.Button()
        self.face_button.set_relief(Gtk.ReliefStyle.NONE)
        self.face_image = Gtk.Image()
        self.face_image.set_size_request(ICON_SIZE_CHOOSE_BUTTON, ICON_SIZE_CHOOSE_BUTTON)
        self.face_button.set_image(self.face_image)
        self.face_button.set_alignment(0.0, 0.5)
        self.face_button.set_tooltip_text(_("Click to change the picture"))

        self.menu = Gtk.Menu()

        separator = Gtk.SeparatorMenuItem()
        face_browse_menuitem = Gtk.MenuItem(_("Browse for more pictures..."))
        face_browse_menuitem.connect('activate', self._on_face_browse_menuitem_activated)
        self.face_button.connect("button-release-event", self.menu_display)

        row = 0
        col = 0
        num_cols = 4
        face_dirs = ["/usr/share/cinnamon/faces"]
        for face_dir in face_dirs:
            if os.path.exists(face_dir):
                pictures = sorted(os.listdir(face_dir))
                for picture in pictures:
                    path = os.path.join(face_dir, picture)
                    try:
                        pixbuf = self.get_pixbuf_from_path(path, ICON_SIZE_CHOOSE_MENU)
                        image = Gtk.Image.new_from_pixbuf(pixbuf)
                    except:
                        image = Gtk.Image.new_from_icon_name("xsi-avatar-default-symbolic", ICON_SIZE_CHOOSE_MENU)
                        image.set_pixel_size(ICON_SIZE_CHOOSE_MENU)
                    menuitem = Gtk.MenuItem()
                    menuitem.add(image)
                    menuitem.connect('activate', self._on_face_menuitem_activated, path)
                    self.menu.attach(menuitem, col, col+1, row, row+1)
                    col = (col+1) % num_cols
                    if col == 0:
                        row = row + 1

        row = row + 1

        self.menu.attach(separator, 0, 4, row, row+1)
        self.menu.attach(face_browse_menuitem, 0, 4, row+2, row+3)

        self.account_type_combo = Gtk.ComboBoxText()
        self.account_type_combo.append_text(_("Standard"))
        self.account_type_combo.append_text(_("Administrator"))
        self.account_type_combo.connect("changed", self._on_accounttype_changed)

        self.realname_entry = EditableEntry()
        self.realname_entry.connect("changed", self._on_realname_changed)
        self.realname_entry.set_tooltip_text(_("Click to change the name"))

        self.entry_padding = self.realname_entry.entry.get_style_context().get_padding(Gtk.StateFlags.NORMAL).left
        self.builder.get_object("label_user_last_login").set_margin_start(self.entry_padding + 1)
        self.builder.get_object("label_username").set_margin_start(self.entry_padding + 1)

        self.password_mask = Gtk.Label()
        self.password_mask.set_alignment(0.0, 0.5)
        self.password_button = Gtk.Button()
        self.password_button.add(self.password_mask)
        self.password_button.set_relief(Gtk.ReliefStyle.NONE)
        self.password_button.set_tooltip_text(_("Click to change the password"))
        self.password_button.connect('activate', self._on_password_button_clicked)
        self.password_button.connect('released', self._on_password_button_clicked)

        box = Gtk.Box()
        box.pack_start(self.face_button, False, False, 0)


        self.builder.get_object("box_user_avatar").add(box)
        self.builder.get_object("box_user_realname").add(self.realname_entry)


        # self.builder.get_object("user_grid").attach(box, 1, 0, 1, 1)
        self.builder.get_object("user_grid").attach(self.account_type_combo, 1, 1, 1, 1)
        # self.builder.get_object("user_grid").attach(self.realname_entry, 1, 2, 1, 1)
        self.builder.get_object("user_grid").attach(self.password_button, 1, 3, 1, 1)

        self.accountService = AccountsService.UserManager.get_default()
        self.accountService.connect('notify::is-loaded', self.on_accounts_service_ready)
        self.accountService.connect('user-removed', self.on_accounts_service_ready)

    @xt.run_async
    def load(self):
        pass
        # gl = detect_opengl()
        # es = detect_gles()
        # vk = detect_vulkan_acceleration()
        # pci_id = get_default_gpu_id()
        # device = get_pci_device(pci_id)
        # vendor = device.get("ID_VENDOR_FROM_DATABASE") or ""
        # name = device.get("ID_MODEL_FROM_DATABASE") or ""
        # driver = device.driver
        # driver_version = get_gpu_driver_version(driver)
        # video = detect_video_acceleration()
        # if len(video) == 0:
        #     video = _("Disabled (Software rendering)")
        # else:
        #     video = _("Enabled (%s)") % ", ".join(video)
        # info_gpu = []
        # info_gpu.append([_('Brand'), vendor])
        # info_gpu.append([_('Name'), name])
        # info_gpu.append([_('PCI ID'), pci_id])
        # info_gpu.append([_('Driver'), driver])
        # if driver_version:
        #     info_gpu.append([_('Driver Version'), driver_version])
        # info_acceleration = []
        # info_acceleration.append(['OpenGL', bool_to_accel(gl)])
        # info_acceleration.append(['OpenGL ES', bool_to_accel(es)])
        # info_acceleration.append(['Vulkan', bool_to_accel(vk)])
        # info_acceleration.append([_('Video Playback'), video])
        # self.update_ui(info_gpu, self.section_gpu)
        # self.update_ui(info_acceleration, self.section_acceleration)

    @xt.run_idle
    def update_ui(self, info, section):
        for (key, value) in info:
            widget = Xs.SettingsWidget()
            widget.set_spacing(40)
            labelKey = Gtk.Label.new(key)
            widget.pack_start(labelKey, False, False, 0)
            labelKey.get_style_context().add_class("dim-label")
            labelValue = Gtk.Label.new(value)
            labelValue.set_selectable(True)
            labelValue.set_line_wrap(True)
            widget.pack_end(labelValue, False, False, 0)
            section.add_row(widget)
        self.show_all()


    def sort_by_name(self, child1, child2):
        name1 = child1.get_child().user_data.get_real_name().lower()
        name2 = child2.get_child().user_data.get_real_name().lower()
        return (name1 > name2) - (name1 < name2)

    def _on_password_button_clicked(self, widget):
        dialog = PasswordDialog(self.user, self.password_mask, self.groups_label, self.window)
        response = dialog.run()

    def _on_accounttype_changed(self, combobox):
        if self.account_type_combo.get_active() == 1:
            self.user.set_account_type(AccountsService.UserAccountType.ADMINISTRATOR)
        else:
            self.user.set_account_type(AccountsService.UserAccountType.STANDARD)

    def _on_realname_changed(self, widget, text):
        self.user.set_real_name(text)

    def _on_face_browse_menuitem_activated(self, menuitem):
        dialog = Gtk.FileChooserDialog(None, None, Gtk.FileChooserAction.OPEN, (_("Cancel"), Gtk.ResponseType.CANCEL, _("Open"), Gtk.ResponseType.OK))
        filter = Gtk.FileFilter()
        filter.set_name(_("Images"))
        filter.add_mime_type("image/*")
        dialog.add_filter(filter)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.frame = Gtk.Frame(visible=False, no_show_all=True)
        preview = Gtk.Image(visible=True)

        box.pack_start(self.frame, False, False, 0)
        self.frame.add(preview)
        dialog.set_preview_widget(box)
        dialog.set_preview_widget_active(True)
        dialog.set_use_preview_label(False)

        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_size_request(ICON_SIZE_DIALOG_PREVIEW, -1)

        dialog.connect("update-preview", self.update_preview_cb, preview)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            path = dialog.get_filename()
            image = PIL.Image.open(path)
            image.thumbnail((96, 96), Image.LANCZOS)
            face_path = os.path.join(self.user.get_home_dir(), ".face")
            try:
                try:
                    os.remove(face_path)
                except OSError:
                    pass
                priv_helper.drop_privs(self.user)
                image.save(face_path, "png")
            finally:
                priv_helper.restore_privs()
            self.user.set_icon_file(face_path)

            try:
                self.face_image.set_from_pixbuf(self.get_pixbuf_from_path(face_path, ICON_SIZE_CHOOSE_BUTTON))
            except:
                self.face_image.set_from_icon_name("xsi-avatar-default-symbolic", Gtk.IconSize.DIALOG)

        dialog.destroy()

    def get_pixbuf_from_path(self, path, size):
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
        size = min(pixbuf.get_width(), pixbuf.get_height())
        # Scale to square if needed
        if pixbuf.get_width() != pixbuf.get_height():
            pixbuf = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
        radius = size // 2
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        ctx = cairo.Context(surface)
        # Create rounded rectangle path
        ctx.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
        ctx.arc(width - radius, radius, radius, 3 * math.pi / 2, 0)
        ctx.arc(width - radius, height - radius, radius, 0, math.pi / 2)
        ctx.arc(radius, height - radius, radius, math.pi / 2, math.pi)
        ctx.close_path()
        ctx.clip()
        Gdk.cairo_set_source_pixbuf(ctx, pixbuf, 0, 0)
        ctx.paint()
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)

    def update_preview_cb (self, dialog, preview):
        # Different widths make the dialog look really crappy as it resizes -
        # constrain the width and adjust the height to keep perspective.
        filename = dialog.get_preview_filename()
        if filename is not None:
            if os.path.isfile(filename):
                try:
                    pixbuf = self.get_pixbuf_from_path(filename, ICON_SIZE_DIALOG_PREVIEW)
                    if pixbuf is not None:
                        preview.set_from_pixbuf(pixbuf)
                        self.frame.show()
                        return
                except:
                    print("Unable to generate preview for file '%s' - %s\n" % (filename, e.message))

        preview.clear()
        self.frame.hide()

    def _on_face_menuitem_activated(self, menuitem, path):
        if os.path.exists(path):
            self.user.set_icon_file(path)

            try:
                self.face_image.set_from_pixbuf(self.get_pixbuf_from_path(path, ICON_SIZE_CHOOSE_BUTTON))
            except:
                self.face_image.set_from_icon_name("xsi-avatar-default-symbolic", Gtk.IconSize.DIALOG)

            face_path = os.path.join(self.user.get_home_dir(), ".face")
            try:
                try:
                    os.remove(face_path)
                except OSError:
                    pass
                priv_helper.drop_privs(self.user)
                shutil.copy(path, face_path)
            finally:
                priv_helper.restore_privs()

    def menu_display(self, widget, event):
        if event.button == 1:
            self.menu.popup(None, None, self.popup_menu_below_button, self.face_button, event.button, event.time)
            self.menu.show_all()

    def popup_menu_below_button (self, *args):
        # the introspection for GtkMenuPositionFunc seems to change with each Gtk version,
        # this is a workaround to make sure we get the menu and the widget
        menu = args[0]
        widget = args[-1]

        # here I get the coordinates of the button relative to
        # window (self.window)
        button_x, button_y = widget.get_allocation().x, widget.get_allocation().y

        # now convert them to X11-relative
        unused_var, window_x, window_y = widget.get_window().get_origin()
        x = window_x + button_x
        y = window_y + button_y

        # now move the menu below the button
        y += widget.get_allocation().height

        push_in = True # push_in is True so all menu is always inside screen
        return x, y, push_in

    def on_accounts_service_ready(self, user, param):
        self.load_users()

    def load_users(self):
        self.user = None
        self.stack.set_visible_child_name("page_users")
        self.users_flowbox.foreach(self.users_flowbox.remove)
        self.usernames = []
        users = self.accountService.list_users()
        for user in users:
            self.usernames.append(user.get_user_name())
            self.add_user_widget(user)

    def load_user(self, user):
        self.user = user
        self.password_button.set_sensitive(True)
        self.password_button.set_tooltip_text("")

        self.builder.get_object("label_username").set_text(user.get_user_name())

        login_time = self.user.get_login_time()
        if login_time == 0:
            self.builder.get_object("label_user_last_login").set_text(_("Never"))
        else:
            date = datetime.fromtimestamp(login_time)
            date_str = date.strftime("%Y.%m.%d")
            self.builder.get_object("label_user_last_login").set_text(date_str)

        self.realname_entry.set_text(user.get_real_name())

        if user.get_password_mode() == AccountsService.UserPasswordMode.REGULAR:
            self.password_mask.set_text('\u2022\u2022\u2022\u2022\u2022\u2022')
        elif user.get_password_mode() == AccountsService.UserPasswordMode.NONE:
            self.password_mask.set_markup("<b>%s</b>" % _("No password set"))
        else:
            self.password_mask.set_text(_("Set at login"))

        if user.get_account_type() == AccountsService.UserAccountType.ADMINISTRATOR:
            self.account_type_combo.set_active(1)
        else:
            self.account_type_combo.set_active(0)

        try:
            self.face_image.set_from_pixbuf(self.get_pixbuf_from_path(user.get_icon_file(), ICON_SIZE_CHOOSE_BUTTON))
        except:
            self.face_image.set_from_icon_name("xsi-avatar-default-symbolic", Gtk.IconSize.DIALOG)

        # Count the number of connections for the currently logged-in user
        connections = int(subprocess.check_output(["w", "-h", user.get_user_name()]).decode("utf-8").count("\n"))
        if connections > 0:
            self.builder.get_object("button_user_remove").set_sensitive(False)
            self.builder.get_object("button_user_remove").set_tooltip_text(_("This user is currently logged in"))
        else:
            self.builder.get_object("button_user_remove").set_sensitive(True)
            self.builder.get_object("button_user_remove").set_tooltip_text("")

        if os.path.exists("/home/.ecryptfs/%s" % user.get_user_name()):
            self.password_button.set_sensitive(False)
            self.password_button.set_tooltip_text(_("The user's home directory is encrypted. To preserve access to the encrypted directory, only the user should change this password."))
            self.builder.get_object("switch_user_encrypted").set_active(True)
        else:
            self.builder.get_object("switch_user_encrypted").set_active(False)

        self.stack.set_visible_child_name("page_user")


    def on_back_clicked(self, button):
        self.load_users()

    def on_remove_clicked(self, button):
        username = f"`<b>{self.user.get_user_name()}</b>`"
        message = _("Are you sure you want to permanently delete %s and all the files associated with this user?") % username
        d = Gtk.MessageDialog(self.window,
                              Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
                              Gtk.MessageType.QUESTION,
                              Gtk.ButtonsType.YES_NO,
                              message)
        d.set_markup(message)
        d.set_default_response(Gtk.ResponseType.NO)
        d.get_widget_for_response(Gtk.ResponseType.YES).get_style_context().add_class("destructive-action")
        r = d.run()
        d.destroy()
        if r == Gtk.ResponseType.YES:
            result = self.accountService.delete_user_async(self.user, True, None, None)

    def add_user_widget(self, user):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        try:
            pixbuf = self.get_pixbuf_from_path(user.get_icon_file(), ICON_SIZE_FLOWBOX)
            image = Gtk.Image.new_from_pixbuf(pixbuf)
        except:
            image = Gtk.Image.new_from_icon_name("xsi-avatar-default-symbolic", ICON_SIZE_FLOWBOX)
            image.set_pixel_size(ICON_SIZE_FLOWBOX)

        box.pack_start(image, False, False, 0)

        # Name
        name_label = Gtk.Label(label=user.get_real_name())
        box.pack_start(name_label, False, False, 0)

        # Username (secondary text)
        user_label = Gtk.Label(label=user.get_user_name())
        user_label.get_style_context().add_class("dim-label")
        box.pack_start(user_label, False, False, 0)

        # Admin or not
        if user.get_account_type() == AccountsService.UserAccountType.ADMINISTRATOR:
            admin_box = Gtk.Box()
            image = Gtk.Image.new_from_icon_name("xsi-dialog-password-symbolic", Gtk.IconSize.MENU)
            admin_label = Gtk.Label(label=_("Administrator"))
            admin_label.get_style_context().add_class("dim-label")
            admin_box.pack_start(image, False, False, 6)
            admin_box.pack_start(admin_label, False, False, 0)
            box.pack_start(admin_box, False, False, 0)

        event_box = Gtk.EventBox()
        event_box.add(box)
        event_box.connect("enter-notify-event", lambda w, e: box.get_style_context().add_class("hover"))
        event_box.connect("leave-notify-event", lambda w, e: box.get_style_context().remove_class("hover"))
        event_box.show_all()

        event_box.user_data = user
        box.get_style_context().add_class("user-card")

        self.users_flowbox.add(event_box)

#USER CALLBACKS

    def on_user_selected(self, flowbox, child):
        if child is None:
            self.stack.set_visible_child_name("page_users")
            return
        box = child.get_child()
        user = box.user_data
        self.load_user(user)

    def on_user_addition(self, event):
        dialog = NewUserDialog(self.window)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            if dialog.account_type_combo.get_active() == 1:
                account_type = AccountsService.UserAccountType.ADMINISTRATOR
            else:
                account_type = AccountsService.UserAccountType.STANDARD
            fullname = dialog.realname_entry.get_text()
            username = dialog.username_entry.get_text()
            try:
                new_user = self.accountService.create_user(username, fullname, account_type)
                new_user.set_password_mode(AccountsService.UserPasswordMode.NONE)
                # Add the user to his/her own group and sudo if Administrator was selected
                if dialog.account_type_combo.get_active() == 1:
                    subprocess.call(["usermod", username, "-G", "%s,sudo,nopasswdlogin" % username])
                else:
                    subprocess.call(["usermod", username, "-G", "%s,nopasswdlogin" % username])
                self.load_users()
            except:
                print("Failed to create user.")
        dialog.destroy()

# -------------------------------------------------------------------
# Standalone test window
# -------------------------------------------------------------------

if __name__ == "__main__":
    window = Gtk.Window()
    viewer = UsersWidget(window)
    viewer.load()
    window.add(viewer)
    window.set_default_size(800, 400)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()