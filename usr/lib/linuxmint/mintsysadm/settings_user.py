#!/usr/bin/python3
import gi
import glob
import os
import pam
import pexpect
import PIL
import setproctitle
import shutil
import subprocess
import sys
import time
import xapp.util
gi.require_version('AccountsService', '1.0')
gi.require_version('Gtk', '3.0')
gi.require_version('XApp', '1.0')
from common.user import generate_password, get_password_strength, set_image_from_avatar
from common.widgets import DimmedTable, EditableEntry
from gi.repository import AccountsService, GLib, Gtk, Gio
from PIL import Image

setproctitle.setproctitle("mintsysadm-settings-user")

_ = xapp.util.l10n("mintsysadm")

ICON_SIZE_DIALOG_PREVIEW = 128
ICON_SIZE_CHOOSE_BUTTON = 128
ICON_SIZE_CHOOSE_MENU = 48

class MyApplication(Gtk.Application):
    # Main initialization routine
    def __init__(self, application_id, flags):
        Gtk.Application.__init__(self, application_id=application_id, flags=flags)
        self.connect("activate", self.activate)
        self.connect("command-line", self.on_command_line)
        self.app_window = None

    def on_command_line(self, app, command_line):
        self.activate(app)
        return 0

    def activate(self, application):
        if self.app_window is None:
            self.app_window = MainWindow(self)
            self.add_window(self.app_window.window)
        window = self.app_window.window
        window = self.get_windows()[0]
        window.present()
        window.show_all()

class MainWindow():

    def __init__(self, application):

        self.application = application

        gladefile = "/usr/share/mintsysadm/settings_user.ui"
        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("mintsysadm")
        self.builder.add_from_file(gladefile)
        self.window = self.builder.get_object("main_window")
        self.window.set_title(_("Account Details"))
        self.window.set_icon_name("preferences-desktop-user")

        self.face_button = self.builder.get_object("button_avatar")
        self.face_image = self.builder.get_object("image_avatar")
        self.face_image.set_size_request(ICON_SIZE_CHOOSE_BUTTON, ICON_SIZE_CHOOSE_BUTTON)
        self.face_button.connect("button-release-event", self.show_menu)

        self.menu = Gtk.Menu()
        row = 0
        col = 0
        num_cols = 6
        face_dirs = ["/usr/share/pixmaps/faces/linuxmint/"]
        for face_dir in face_dirs:
            if os.path.exists(face_dir):
                pictures = sorted(os.listdir(face_dir))
                for picture in pictures:
                    path = os.path.join(face_dir, picture)
                    image = Gtk.Image()
                    set_image_from_avatar(image, path, ICON_SIZE_CHOOSE_MENU, fallback_icon_size=ICON_SIZE_CHOOSE_MENU)
                    menuitem = Gtk.MenuItem()
                    menuitem.add(image)
                    menuitem.connect('activate', self.on_avatar_selected, path)
                    self.menu.attach(menuitem, col, col+1, row, row+1)
                    col = (col+1) % num_cols
                    if col == 0:
                        row = row + 1

        row = row + 1
        self.menu.attach(Gtk.SeparatorMenuItem(), 0, num_cols, row, row+1)

        if len(glob.glob("/dev/video*")) > 0:
            row = row + 1
            menuitem = Gtk.MenuItem.new_with_label(label=_("Take a photo..."))
            menuitem.connect('activate', self.on_take_picture)
            self.menu.attach(menuitem, 0, num_cols, row, row+1)

        row = row + 1
        menuitem = Gtk.MenuItem(label=_("Browse for more pictures..."))
        menuitem.connect('activate', self.on_browse_avatars)
        self.menu.attach(menuitem, 0, num_cols, row, row+1)

        row = row + 1
        face_remove_menuitem = Gtk.MenuItem(label=_("Remove picture"))
        face_remove_menuitem.connect('activate', self.on_avatar_removed)
        self.menu.attach(face_remove_menuitem, 0, num_cols, row, row+1)

        self.realname_entry = EditableEntry()
        self.realname_entry.connect("changed", self.on_realname_changed)

        self.entry_padding = self.realname_entry.entry.get_style_context().get_padding(Gtk.StateFlags.NORMAL).left
        self.builder.get_object("label_username").set_margin_start(self.entry_padding + 1)

        self.password_button_label = self.builder.get_object("label_password")
        self.password_button = self.builder.get_object("button_password")
        self.password_button.connect('clicked', self.on_password_button_clicked)

        self.builder.get_object("box_realname").add(self.realname_entry)

        self.accountService = AccountsService.UserManager.get_default().get_user(GLib.get_user_name())
        self.accountService.connect('notify::is-loaded', self.load_user)

        self.window.show()

    def on_realname_changed(self, widget, text):
        self.user.set_real_name(text)

    def on_password_button_clicked(self, widget):
        dialog = PasswordDialog(self.user, self.password_button_label, self.window)
        dialog.run()

    def on_browse_avatars(self, menuitem):
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
            image.thumbnail((96, 96), PIL.Image.LANCZOS)
            if os.path.exists(self.face_path):
                os.remove(self.face_path)
            image.save(self.face_path, "png")
            self.user.set_icon_file(self.face_path)
            set_image_from_avatar(self.face_image, self.face_path, ICON_SIZE_CHOOSE_BUTTON)
        dialog.destroy()

    def set_avatar(self, path):
        if os.path.exists(path):
            self.user.set_icon_file(path)
            set_image_from_avatar(self.face_image, path, ICON_SIZE_CHOOSE_BUTTON)
            try:
                if os.path.exists(self.face_path):
                    os.remove(self.face_path)
                shutil.copy(path, self.face_path)
            except Exception as e:
                print(f"Error copying avatar in .face: {e}")
        else:
            self.user.set_icon_file("")
            self.face_image.set_from_icon_name("xsi-avatar-default-symbolic", Gtk.IconSize.DIALOG)
            try:
                if os.path.exists(self.face_path):
                    os.remove(self.face_path)
            except Exception as e:
                print(f"Error removing .face file: {e}")

    def update_preview_cb (self, dialog, preview):
        # Different widths make the dialog look really crappy as it resizes -
        # constrain the width and adjust the height to keep perspective.
        filename = dialog.get_preview_filename()
        if filename is not None:
            if os.path.isfile(filename):
                try:
                    set_image_from_avatar(preview, filename, ICON_SIZE_DIALOG_PREVIEW)
                    self.frame.show()
                    return
                except:
                    print(f"Unable to generate preview for file '{filename}'")

        preview.clear()
        self.frame.hide()

    def on_avatar_selected(self, menuitem, path):
        self.set_avatar(path)

    def on_avatar_removed(self, menuitem):
        self.set_avatar("")

    def show_menu(self, widget, event):
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

    def load_user(self, user, param):
        self.user = user
        self.face_path = os.path.join(user.get_home_dir(), ".face")
        self.builder.get_object("label_username").set_text(user.get_user_name())
        self.realname_entry.set_text(user.get_real_name())
        set_image_from_avatar(self.face_image, user.get_icon_file(), ICON_SIZE_CHOOSE_BUTTON)
        if user.get_password_mode() == AccountsService.UserPasswordMode.REGULAR:
            self.password_button_label.set_text('\u2022\u2022\u2022\u2022\u2022\u2022')
        elif user.get_password_mode() == AccountsService.UserPasswordMode.NONE:
            self.password_button_label.set_markup("<b>%s</b>" % _("No password set"))
        else:
            self.password_button_label.set_text(_("Set at login"))

    def on_take_picture(self, menuitem):
        # streamer takes -t photos, uses /dev/video0
        if 0 != subprocess.call(["streamer", "-j90", "-t8", "-s800x600", "-o", "/tmp/temp-account-pic00.jpeg"]):
            print("Error: Webcam not available")
            return
        # Use the 8th frame (the webcam takes a few frames to "lighten up")
        image = Image.open("/tmp/temp-account-pic07.jpeg")
        # Crop the image to thumbnail size
        width, height = image.size
        if width > height:
            new_width = height
            new_height = height
        elif height > width:
            new_width = width
            new_height = width
        else:
            new_width = width
            new_height = height
        left = (width - new_width) / 2
        top = (height - new_height) / 2
        right = (width + new_width) / 2
        bottom = (height + new_height) / 2
        image = image.crop((left, top, right, bottom))
        image.thumbnail((512, 512), Image.LANCZOS)
        temp_path = self.face_path + ".tmp"
        image.save(temp_path, "png")
        self.set_avatar(temp_path)
        os.remove(temp_path)
        # Clean up temporary webcam files
        for temp_file in glob.glob("/tmp/temp-account-pic*.jpeg"):
            try:
                os.remove(temp_file)
            except:
                pass

class PasswordDialog(Gtk.Dialog):

    def __init__ (self, user, password_label, parent = None):
        super(PasswordDialog, self).__init__(None, parent)

        self.user = user
        self.password_label = password_label

        self.set_modal(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_title(_("Change Password"))

        table = DimmedTable()
        table.add_labels([_("Current password"), _("New password"), None, _("Confirm password")])

        self.current_password = Gtk.Entry()
        self.current_password.set_visibility(False)
        self.current_password.connect("changed", self.on_passwords_changed)
        if self.user.get_password_mode() == AccountsService.UserPasswordMode.NONE:
            self.current_password.set_sensitive(False)
        table.attach(self.current_password, 1, 3, 0, 1)

        self.new_password = Gtk.Entry()
        self.new_password.set_visibility(False)
        self.new_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-view-reveal-symbolic")
        self.new_password.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Show password"))
        self.new_password.connect("icon-release", self._on_new_password_icon_released)
        self.new_password.connect("changed", self.on_passwords_changed)
        table.attach(self.new_password, 1, 3, 1, 2)

        self.strengh_indicator = Gtk.ProgressBar()
        self.strengh_indicator.set_tooltip_text(_("The password must be at least 8 characters long."))
        self.strengh_indicator.set_fraction(0.0)
        table.attach(self.strengh_indicator, 1, 2, 2, 3, xoptions=Gtk.AttachOptions.EXPAND|Gtk.AttachOptions.FILL)
        self.strengh_indicator.set_size_request(-1, 1)

        self.strengh_label = Gtk.Label()
        self.strengh_label.set_tooltip_text(_("The password must be at least 8 characters long."))
        self.strengh_label.set_alignment(1, 0.5)
        table.attach(self.strengh_label, 2, 3, 2, 3)

        self.confirm_password = Gtk.Entry()
        self.confirm_password.set_visibility(False)
        self.confirm_password.connect("changed", self.on_passwords_changed)
        table.attach(self.confirm_password, 1, 3, 3, 4)

        self.set_border_width(6)

        box = self.get_content_area()
        box.add(table)
        self.show_all()

        self.infobar = Gtk.InfoBar()
        self.infobar.set_message_type(Gtk.MessageType.ERROR)
        label = Gtk.Label.new()
        content = self.infobar.get_content_area()
        content.add(label)
        table.attach(self.infobar, 0, 3, 5, 6)

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

    def show_error_in_infobar(self, message):
        label = self.infobar.get_content_area().get_children()[0]
        label.set_text(message)
        self.infobar.show_all()

    def hide_infobar(self):
        self.infobar.hide()

    def change_password(self):
        print("Changing password...")
        oldpass = self.current_password.get_text()
        newpass = self.new_password.get_text()
        try:
            auth = pam.pam()
            if auth.authenticate(GLib.get_user_name(), oldpass, 'common-auth'):
                print("Password is OK.")
            else:
                print("Password is not OK.")
                self.show_error_in_infobar(_("Wrong password"))
                return
        except Exception as e:
            print("PAM error", e)
            self.show_error_in_infobar(_("Internal Error"))
            return

        passwd = pexpect.spawn("/usr/bin/passwd")
        if oldpass != "":
            # passwd only asks for the password if there's one set already
            time.sleep(0.5)
            passwd.sendline(oldpass)
        time.sleep(0.5)
        passwd.sendline(newpass)
        time.sleep(0.5)
        passwd.sendline(newpass)
        time.sleep(0.5)
        passwd.close()

        if passwd.exitstatus is None or passwd.exitstatus > 0:
            self.show_error_in_infobar(_("An error occurred. Your password was not changed."))
        else:
            self.password_label.set_text('\u2022\u2022\u2022\u2022\u2022\u2022')
            self.destroy()

    def update_password_icon(self):
        visible = self.new_password.get_visibility()
        if visible:
            self.new_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-view-conceal-symbolic")
            self.new_password.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Hide password"))
        else:
            self.new_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-view-reveal-symbolic")
            self.new_password.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Show password"))

    def _on_new_password_icon_released(self, widget, icon_pos, event):
        visible = not self.new_password.get_visibility()
        self.new_password.set_visibility(visible)
        self.confirm_password.set_visibility(visible)
        self.update_password_icon()

    def on_passwords_changed(self, widget):
        self.hide_infobar()
        problem_found = False
        current_password = self.current_password.get_text()
        new_password = self.new_password.get_text()
        confirm_password = self.confirm_password.get_text()
        if len(new_password) < 8 or len(confirm_password) < 8:
            problem_found = True
        text, fraction = get_password_strength(new_password)
        self.strengh_label.set_text(text)
        self.strengh_indicator.set_fraction(fraction)
        if new_password != confirm_password:
            self.confirm_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-dialog-warning-symbolic")
            self.confirm_password.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Passwords do not match"))
            self.confirm_password.set_tooltip_text(_("Passwords do not match"))
            problem_found = True
        else:
            self.confirm_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, None)
            self.confirm_password.set_tooltip_text("")
        if problem_found:
            self.set_response_sensitive(Gtk.ResponseType.OK, False)
        else:
            self.set_response_sensitive(Gtk.ResponseType.OK, True)

if __name__ == "__main__":
    application = MyApplication("com.linuxmint.sysadm.user", Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    application.run(sys.argv)
