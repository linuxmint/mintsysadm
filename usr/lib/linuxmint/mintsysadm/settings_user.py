#!/usr/bin/python3
import PIL
import gi
import pexpect
import time
import shutil
import os
import sys
import subprocess
import setproctitle
import xapp.util
import pam
import glob
gi.require_version('Gtk', '3.0')
gi.require_version('XApp', '1.0')
gi.require_version('AccountsService', '1.0')
from gi.repository import AccountsService, GLib, Gtk, Gio
from common.user import generate_password, get_circular_pixbuf_from_path, get_password_strength
from common.widgets import EditableEntry
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
                    try:
                        pixbuf = get_circular_pixbuf_from_path(path, ICON_SIZE_CHOOSE_MENU)
                        image = Gtk.Image.new_from_pixbuf(pixbuf)
                    except:
                        image = Gtk.Image.new_from_icon_name("xsi-avatar-default-symbolic", ICON_SIZE_CHOOSE_MENU)
                        image.set_pixel_size(ICON_SIZE_CHOOSE_MENU)
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

        current_user = GLib.get_user_name()
        self.accountService = AccountsService.UserManager.get_default().get_user(current_user)
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
            self.user.set_icon_file(self.face_path)
            if os.path.exists(self.face_path):
                os.remove(self.face_path)
            image.save(self.face_path, "png")
            try:
                self.face_image.set_from_pixbuf(get_circular_pixbuf_from_path(self.face_path, ICON_SIZE_CHOOSE_BUTTON))
            except:
                self.face_image.set_from_icon_name("xsi-avatar-default-symbolic", Gtk.IconSize.DIALOG)

        dialog.destroy()

    def set_avatar(self, path):
        if os.path.exists(path):
            self.user.set_icon_file(path)
            try:
                self.face_image.set_from_pixbuf(get_circular_pixbuf_from_path(path, ICON_SIZE_CHOOSE_BUTTON))
            except:
                self.face_image.set_from_icon_name("xsi-avatar-default-symbolic", Gtk.IconSize.DIALOG)
            try:
                if os.path.exists(self.face_path):
                    os.remove(self.face_path)
                shutil.copy(path, self.face_path)
            except Exception as e:
                print(f"Error copying avatar in .face: {e}")
        else:
            self.user.set_icon_file("")
            self.face_image.set_from_icon_name("xsi-avatar-default-symbolic", Gtk.IconSize.DIALOG)
            if os.path.exists(self.face_path):
                os.remove(self.face_path)

    def update_preview_cb (self, dialog, preview):
        # Different widths make the dialog look really crappy as it resizes -
        # constrain the width and adjust the height to keep perspective.
        filename = dialog.get_preview_filename()
        if filename is not None:
            if os.path.isfile(filename):
                try:
                    pixbuf = get_circular_pixbuf_from_path(filename, ICON_SIZE_DIALOG_PREVIEW)
                    if pixbuf is not None:
                        preview.set_from_pixbuf(pixbuf)
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
        try:
            self.face_image.set_from_pixbuf(get_circular_pixbuf_from_path(user.get_icon_file(), ICON_SIZE_CHOOSE_BUTTON))
        except:
            self.face_image.set_from_icon_name("xsi-avatar-default-symbolic", Gtk.IconSize.DIALOG)
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

class PasswordError(Exception):
    """Exception raised when an incorrect password is supplied."""
    pass

class PasswordDialog(Gtk.Dialog):

    def __init__ (self, user, password_label, parent = None):
        super(PasswordDialog, self).__init__(None, parent)

        self.user = user
        self.password_label = password_label

        self.correct_current_password = False # Flag to remember if the current password is correct or not

        self.set_modal(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_title(_("Change Password"))

        table = Gtk.Table(6, 3)
        table.set_border_width(6)
        table.set_row_spacings(8)
        table.set_col_spacings(15)

        label = Gtk.Label.new(_("Current password"))
        label.set_alignment(1, 0.5)
        table.attach(label, 0, 1, 0, 1)

        label = Gtk.Label.new(_("New password"))
        label.set_alignment(1, 0.5)
        table.attach(label, 0, 1, 1, 2)

        label = Gtk.Label.new(_("Confirm password"))
        label.set_alignment(1, 0.5)
        table.attach(label, 0, 1, 3, 4)

        self.current_password = Gtk.Entry()
        self.current_password.set_visibility(False)
        self.current_password.connect("focus-out-event", self._on_current_password_changed)
        table.attach(self.current_password, 1, 3, 0, 1)

        self.new_password = Gtk.Entry()
        self.new_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "view-refresh")
        self.new_password.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Generate a password"))
        self.new_password.set_tooltip_text(_("Generate a password"))
        self.new_password.connect("icon-release", self._on_new_password_icon_released)
        self.new_password.connect("changed", self._on_passwords_changed)
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
        self.confirm_password.connect("changed", self._on_passwords_changed)
        table.attach(self.confirm_password, 1, 3, 3, 4)

        self.show_password = Gtk.CheckButton(label=_("Show password"))
        self.show_password.connect('toggled', self._on_show_password_toggled)
        table.attach(self.show_password, 1, 3, 4, 5)

        self.set_border_width(6)

        box = self.get_content_area()
        box.add(table)
        self.show_all()

        self.infobar = Gtk.InfoBar()
        self.infobar.set_message_type(Gtk.MessageType.ERROR)
        label = Gtk.Label.new(_("An error occurred. Your password was not changed."))
        content = self.infobar.get_content_area()
        content.add(label)
        table.attach(self.infobar, 0, 3, 5, 6)

        self.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, _("Change"), Gtk.ResponseType.OK, )

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
        print("Changing password...")
        oldpass = self.current_password.get_text()
        newpass = self.new_password.get_text()
        passwd = pexpect.spawn("/usr/bin/passwd")
        # passwd only asks for the old password when there already is one set.
        if oldpass == "":
            time.sleep(0.5)
            passwd.sendline(newpass)
            time.sleep(0.5)
            passwd.sendline(newpass)
        else:
            time.sleep(0.5)
            passwd.sendline(oldpass)
            time.sleep(0.5)
            passwd.sendline(newpass)
            time.sleep(0.5)
            passwd.sendline(newpass)
        time.sleep(0.5)
        passwd.close()

        if passwd.exitstatus is None or passwd.exitstatus > 0:
            self.infobar.show_all()
        else:
            self.destroy()

    def set_passwords_visibility(self):
        visible = self.show_password.get_active()
        self.new_password.set_visibility(visible)
        self.confirm_password.set_visibility(visible)

    def _on_new_password_icon_released(self, widget, icon_pos, event):
        self.infobar.hide()
        self.show_password.set_active(True)
        newpass = generate_password()
        self.new_password.set_text(newpass)
        self.confirm_password.set_text(newpass)
        self.check_passwords()

    def _on_show_password_toggled(self, widget):
        self.set_passwords_visibility()

    def _get_pam_service(self):
        import os
        if os.path.exists('/etc/pam.d/system-auth'):
            return 'system-auth'
        elif os.path.exists('/etc/pam.d/common-auth'):
            return 'common-auth'
        else:
            return 'login'

    def _on_current_password_changed(self, widget, event):
        self.infobar.hide()
        try:
            service = self._get_pam_service()
            if not pam.pam().authenticate(GLib.get_user_name(), self.current_password.get_text(), service):
                raise PasswordError("Invalid password")
        except PasswordError:
            self.new_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-dialog-warning-symbolic")
            self.current_password.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Wrong password"))
            self.current_password.set_tooltip_text(_("Wrong password"))
            self.correct_current_password = False
        except:
            self.current_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-dialog-warning-symbolic")
            self.current_password.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Internal Error"))
            self.current_password.set_tooltip_text(_("Internal Error"))
            self.correct_current_password = False
            raise
        else:
            self.current_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, None)
            self.current_password.set_tooltip_text("")
            self.correct_current_password = True
            self.check_passwords()

    def _on_passwords_changed(self, widget):
        self.infobar.hide()
        new_password = self.new_password.get_text()
        confirm_password = self.confirm_password.get_text()
        text, fraction = get_password_strength(new_password)
        self.strengh_label.set_text(text)
        self.strengh_indicator.set_fraction(fraction)
        if new_password != confirm_password:
            self.confirm_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-dialog-warning-symbolic")
            self.confirm_password.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Passwords do not match"))
            self.confirm_password.set_tooltip_text(_("Passwords do not match"))
        else:
            self.confirm_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, None)
            self.confirm_password.set_tooltip_text("")
        self.check_passwords()

    def check_passwords(self):
        if self.correct_current_password:
            new_password = self.new_password.get_text()
            confirm_password = self.confirm_password.get_text()
            if len(new_password) >= 8 and new_password == confirm_password:
                self.set_response_sensitive(Gtk.ResponseType.OK, True)
            else:
                self.set_response_sensitive(Gtk.ResponseType.OK, False)

    def pam_conv(self, auth, query_list, userData):
        resp = []
        for i in range(len(query_list)):
            query, type = query_list[i]
            val = self.current_password.get_text()
            resp.append((val, 0))
        return resp

if __name__ == "__main__":
    application = MyApplication("com.linuxmint.sysadm.user", Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    application.run(sys.argv)
