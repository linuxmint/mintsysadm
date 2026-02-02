#!/usr/bin/python3
import datetime
import gi
import os
import PIL
import re
import shutil
import subprocess
import xapp.SettingsWidgets as xs
import xapp.threading as xt
import xapp.util
gi.require_version("AccountsService", "1.0")
gi.require_version("Gtk", "3.0")
from common.user import PrivHelper, generate_password, get_circular_pixbuf_from_path, get_password_strength
from common.widgets import DimmedTable, EditableEntry
from gi.repository import Gtk, Gdk, AccountsService

priv_helper = PrivHelper()

_ = xapp.util.l10n("mintsysadm")

ICON_SIZE_DIALOG_PREVIEW = 128
ICON_SIZE_CHOOSE_BUTTON = 96
ICON_SIZE_FLOWBOX = 96
ICON_SIZE_CHOOSE_MENU = 48

class NewUserDialog(Gtk.Dialog):

    def __init__ (self, parent = None):
        super(NewUserDialog, self).__init__(None, parent)

        try:
            self.set_modal(True)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_title("")

            self.realname_entry = Gtk.Entry()
            self.realname_entry.connect("changed", self._on_info_changed)

            self.username_entry = Gtk.Entry()
            self.username_entry.connect("changed", self._on_info_changed)

            self.administrator_switch = Gtk.Switch()
            self.administrator_switch.set_halign(Gtk.Align.START)

            self.encrypt_home_switch = Gtk.Switch()
            self.encrypt_home_switch.set_halign(Gtk.Align.START)
            self.encrypt_home_switch.connect("state-set", self._on_encrypt_switch_changed)

            self.password_entry = Gtk.Entry()
            self.password_entry.set_visibility(True)
            self.password_entry.set_text(generate_password())
            self.password_entry.connect("changed", self._on_info_changed)

            self.password_explanation = Gtk.Label()
            self.password_explanation.set_markup("<small>%s</small>" % _("A password is needed to encrypt the home directory. Make sure to communicate it to the user."))
            self.password_explanation.set_alignment(0.0, 0.5)
            self.password_explanation.set_line_wrap(True)
            self.password_explanation.set_size_request(300, -1)
            self.password_explanation.get_style_context().add_class("dim-label")

            table = DimmedTable()
            table.add_labels([_("Full Name"), _("Username"), _("Administrator"), _("Encrypted Home Directory"), _("Password")])
            table.add_controls([self.realname_entry, self.username_entry, self.administrator_switch, self.encrypt_home_switch, self.password_entry])

            # Add explanation spanning both columns
            table.attach(self.password_explanation, 0, 2, 5, 6, yoptions=Gtk.AttachOptions.SHRINK, ypadding=0)

            # Get password row widgets to hide them by default
            children = table.get_children()
            # Find the password label (it's the label in column 0, row 4)
            for child in children:
                top_attach = table.child_get_property(child, "top-attach")
                left_attach = table.child_get_property(child, "left-attach")
                if top_attach == 4 and left_attach == 0:
                    self.password_label = child
                    break

            self.password_label.set_no_show_all(True)
            self.password_entry.set_no_show_all(True)
            self.password_explanation.set_no_show_all(True)
            self.password_label.hide()
            self.password_entry.hide()
            self.password_explanation.hide()

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
            self.username_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("Invalid username. Only lowercase letters, numbers, hyphens and underscores are allowed."))
            valid = False
        elif self.user_exists(username):
            self.username_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-dialog-warning-symbolic")
            self.username_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("This username is already taken."))
            valid = False
        else:
            self.username_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, None)
        if username == "" or fullname == "":
            valid = False
        if self.encrypt_home_switch.get_active():
            password = self.password_entry.get_text()
            if len(password) < 8:
                self.password_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "xsi-dialog-warning-symbolic")
                self.password_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, _("The password must be at least 8 characters long."))
                valid = False
            else:
                self.password_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, None)

        self.set_response_sensitive(Gtk.ResponseType.OK, valid)

    def _on_encrypt_switch_changed(self, switch, state):
        if state:
            self.password_label.show()
            self.password_entry.show()
            self.password_explanation.show()
        else:
            self.password_label.hide()
            self.password_entry.hide()
            self.password_explanation.hide()
            self.password_entry.set_text("")
        self._on_info_changed(None)
        return False

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
        else:
            self.confirm_password.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, None)
        self.check_passwords()

    def check_passwords(self):
        new_password = self.new_password.get_text()
        confirm_password = self.confirm_password.get_text()
        if len(new_password) >= 8 and new_password == confirm_password:
            self.set_response_sensitive(Gtk.ResponseType.OK, True)
        else:
            self.set_response_sensitive(Gtk.ResponseType.OK, False)

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

        self.menu = Gtk.Menu()

        separator = Gtk.SeparatorMenuItem()
        face_browse_menuitem = Gtk.MenuItem(_("Browse for more pictures..."))
        face_browse_menuitem.connect('activate', self._on_face_browse_menuitem_activated)
        self.face_button.connect("button-release-event", self.menu_display)

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
                    menuitem.connect('activate', self._on_face_menuitem_activated, path)
                    self.menu.attach(menuitem, col, col+1, row, row+1)
                    col = (col+1) % num_cols
                    if col == 0:
                        row = row + 1

        row = row + 1

        self.menu.attach(separator, 0, num_cols, row, row+1)
        self.menu.attach(face_browse_menuitem, 0, num_cols, row+2, row+3)

        face_remove_menuitem = Gtk.MenuItem(_("Remove picture"))
        face_remove_menuitem.connect('activate', self._on_face_remove_menuitem_activated)
        self.menu.attach(face_remove_menuitem, 0, num_cols, row+3, row+4)

        self.account_type_switch = self.builder.get_object("switch_user_admin")
        self.switch_handler_id = self.account_type_switch.connect("state-set", self._on_accounttype_state_set)

        self.realname_entry = EditableEntry()
        self.realname_entry.connect("changed", self._on_realname_changed)

        self.entry_padding = self.realname_entry.entry.get_style_context().get_padding(Gtk.StateFlags.NORMAL).left
        self.builder.get_object("label_user_last_login").set_margin_start(self.entry_padding + 1)
        self.builder.get_object("label_username").set_margin_start(self.entry_padding + 1)

        self.password_button_label = self.builder.get_object("label_user_password")
        self.password_button = self.builder.get_object("button_user_password")
        self.password_button.connect('clicked', self._on_password_button_clicked)

        self.builder.get_object("box_user_avatar").add(self.face_button)
        self.builder.get_object("box_user_realname").add(self.realname_entry)

        self.accountService = AccountsService.UserManager.get_default()
        self.accountService.connect('notify::is-loaded', self.on_accounts_service_ready)
        self.accountService.connect('user-removed', self.on_accounts_service_ready)

    @xt.run_async
    def load(self):
        pass
        # do long things
        # self.update_ui(...)

    @xt.run_idle
    def update_ui(self, info, section):
        for (key, value) in info:
            widget = xs.SettingsWidget()
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
        dialog = PasswordDialog(self.user, self.password_button_label, self.window)
        dialog.run()

    def _on_accounttype_state_set(self, switch, state):
        if state:
            self.user.set_account_type(AccountsService.UserAccountType.ADMINISTRATOR)
        else:
            self.user.set_account_type(AccountsService.UserAccountType.STANDARD)
        return False

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
            image.thumbnail((96, 96), PIL.Image.LANCZOS)
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
                self.face_image.set_from_pixbuf(get_circular_pixbuf_from_path(face_path, ICON_SIZE_CHOOSE_BUTTON))
            except:
                self.face_image.set_from_icon_name("xsi-avatar-default-symbolic", Gtk.IconSize.DIALOG)

        dialog.destroy()

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

    def _on_face_menuitem_activated(self, menuitem, path):
        if os.path.exists(path):
            self.user.set_icon_file(path)
            try:
                self.face_image.set_from_pixbuf(get_circular_pixbuf_from_path(path, ICON_SIZE_CHOOSE_BUTTON))
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

    def _on_face_remove_menuitem_activated(self, menuitem):
        self.user.set_icon_file("")
        self.face_image.set_from_icon_name("xsi-avatar-default-symbolic", Gtk.IconSize.DIALOG)
        face_path = os.path.join(self.user.get_home_dir(), ".face")
        try:
            priv_helper.drop_privs(self.user)
            if os.path.exists(face_path):
                os.remove(face_path)
        except Exception as e:
            print(f"Error removing avatar: {e}")
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

        self.builder.get_object("label_username").set_text(user.get_user_name())

        login_time = self.user.get_login_time()
        if login_time == 0:
            self.builder.get_object("label_user_last_login").set_text(_("Never"))
        else:
            date = datetime.datetime.fromtimestamp(login_time)
            date_str = date.strftime("%Y.%m.%d")
            self.builder.get_object("label_user_last_login").set_text(date_str)

        self.realname_entry.set_text(user.get_real_name())

        if user.get_password_mode() == AccountsService.UserPasswordMode.REGULAR:
            self.password_button_label.set_text('\u2022\u2022\u2022\u2022\u2022\u2022')
        elif user.get_password_mode() == AccountsService.UserPasswordMode.NONE:
            self.password_button_label.set_markup("<b>%s</b>" % _("No password set"))
        else:
            self.password_button_label.set_text(_("Set at login"))

        self.account_type_switch.handler_block(self.switch_handler_id)
        if user.get_account_type() == AccountsService.UserAccountType.ADMINISTRATOR:
            self.account_type_switch.set_active(True)
        else:
            self.account_type_switch.set_active(False)
        self.account_type_switch.handler_unblock(self.switch_handler_id)

        try:
            self.face_image.set_from_pixbuf(get_circular_pixbuf_from_path(user.get_icon_file(), ICON_SIZE_CHOOSE_BUTTON))
        except:
            self.face_image.set_from_icon_name("xsi-avatar-default-symbolic", Gtk.IconSize.DIALOG)

        # Count the number of connections for the currently logged-in user
        connections = int(subprocess.check_output(["w", "-h", user.get_user_name()]).decode("utf-8").count("\n"))
        if connections > 0:
            self.builder.get_object("button_user_remove").set_sensitive(False)
            self.builder.get_object("button_user_remove").set_tooltip_text(_("This user is currently logged in."))
        else:
            self.builder.get_object("button_user_remove").set_sensitive(True)
            self.builder.get_object("button_user_remove").set_tooltip_text("")

        if os.path.exists("/home/.ecryptfs/%s" % user.get_user_name()):
            self.password_button.set_sensitive(False)
            self.password_button.set_tooltip_text(_("The user's home directory is encrypted. To preserve access to the encrypted directory, only the user should change this password."))
            self.builder.get_object("switch_user_encrypted").set_active(True)
        else:
            self.password_button.set_sensitive(True)
            self.password_button.set_tooltip_text("")
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
            pixbuf = get_circular_pixbuf_from_path(user.get_icon_file(), ICON_SIZE_FLOWBOX)
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
            if dialog.administrator_switch.get_active():
                account_type = AccountsService.UserAccountType.ADMINISTRATOR
            else:
                account_type = AccountsService.UserAccountType.STANDARD
            fullname = dialog.realname_entry.get_text()
            username = dialog.username_entry.get_text()
            try:
                new_user = self.accountService.create_user(username, fullname, account_type)
                # Set password and encrypt home if requested
                if dialog.encrypt_home_switch.get_active():
                    password = dialog.password_entry.get_text()
                    new_user.set_password(password, "")
                    new_user.set_password_mode(AccountsService.UserPasswordMode.REGULAR)
                    # Encrypt home directory - pass password securely via stdin
                    proc = subprocess.Popen(["ecryptfs-migrate-home", "-u", username],
                                          stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE)
                    proc.communicate(password.encode())
                    # Remove backup directory (new user, so it's empty anyway)
                    import glob
                    backup_dirs = glob.glob(f"/home/{username}.[A-Za-z0-9]*")
                    for backup_dir in backup_dirs:
                        if os.path.isdir(backup_dir):
                            # Verify the directory is owned by the user before deleting
                            stat_info = os.stat(backup_dir)
                            if stat_info.st_uid == new_user.get_uid():
                                shutil.rmtree(backup_dir)
                else:
                    new_user.set_password_mode(AccountsService.UserPasswordMode.NONE)

                # Add to sudo group if Administrator
                if dialog.administrator_switch.get_active():
                    subprocess.call(["usermod", "-a", "-G", "sudo", username])

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