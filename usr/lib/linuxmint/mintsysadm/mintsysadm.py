#!/usr/bin/python3
import apt
import datetime
import gi
import os
import setproctitle
import shutil
import subprocess
import xapp.widgets
import re
import xapp.threading as xt
import xapp.util

gi.require_version("Gtk", "3.0")
gi.require_version('GtkSource', '3.0')
gi.require_version('XApp', '1.0')
from gi.repository import Gtk, Gdk, GtkSource, Gio, XApp

setproctitle.setproctitle("mintsysadm")

_ = xapp.util.l10n("mintsysadm")

GRUB_FILE = "/etc/default/grub.d/98_mintsysadm.cfg"

class MyApplication(Gtk.Application):
    def __init__(self, application_id, flags):
        Gtk.Application.__init__(self, application_id=application_id, flags=flags)
        self.connect("activate", self.activate)

    def activate(self, application):
        windows = self.get_windows()
        if (len(windows) > 0):
            window = windows[0]
            window.present()
            window.show_all()
        else:
            window = MintSysadmWindow(self)
            self.add_window(window.window)
            window.window.show_all()

class MintSysadmWindow():

    def __init__(self, application):

        self.application = application

        gladefile = "/usr/share/mintsysadm/mintsysadm.ui"
        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("mintsysadm")
        self.builder.add_from_file(gladefile)
        self.window = self.builder.get_object("main_window")
        self.window.set_title(_("System Administration"))
        self.window.set_icon_name("mintsysadm")

        self.builder.get_object("grub_switch").connect("notify::active", self.grub_switch_toggled)

        # Fill in the boot page
        cmdline_args = subprocess.check_output("LANG=C cat /proc/cmdline", shell=True).decode("utf-8", errors='replace').split()
        args = []
        for arg in cmdline_args:
            if arg in ["ro"]:
                continue
            if arg.lower().startswith("boot_image=") or arg.lower().startswith("root="):
                continue
            args.append(arg)
        self.builder.get_object("label_cmdline").set_label(" ".join(args))

        self.boot_args_editor = xapp.widgets.ListEditor()
        self.boot_args_editor.set_allow_duplicates(False)
        self.boot_args_editor.set_allow_add(True, _("Add a boot argument"), _("If the argument is not recognized by the kernel it will be ignored."))
        self.boot_args_editor.set_allow_edit(True)
        self.boot_args_editor.set_allow_remove(True)
        self.boot_args_editor.set_sort_function(False) # Don't sort the list
        self.boot_args_editor.set_allow_ordering(True)
        self.boot_args_editor.set_validation_function(self.validate_boot_argument)
        self.builder.get_object("arguments_editor").add(self.boot_args_editor)
        self.get_boot_config()
        # self.load_boot()
        self.builder.get_object("button_boot_save").connect("clicked", self.save_boot_config)
        self.builder.get_object("grub_button_close").connect("clicked", self.close_grub_dialog)

        accel_group = Gtk.AccelGroup()
        self.window.add_accel_group(accel_group)

        # Menubar
        menu = self.builder.get_object("main_menu")

        item = Gtk.ImageMenuItem(label=_("Quit"))
        image = Gtk.Image.new_from_icon_name("xsi-exit-symbolic", Gtk.IconSize.MENU)
        item.set_image(image)
        item.connect('activate', self.on_menu_quit)
        key, mod = Gtk.accelerator_parse("<Control>Q")
        item.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
        key, mod = Gtk.accelerator_parse("<Control>W")
        item.add_accelerator("activate", accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
        menu.append(item)

        item = Gtk.ImageMenuItem()
        item.set_image(Gtk.Image.new_from_icon_name("xsi-help-about-symbolic", Gtk.IconSize.MENU))
        item.set_label(_("About"))
        item.connect("activate", self.open_about)
        menu.append(item)

        menu.show_all()

    def grub_switch_toggled(self, switch, gparam):
        # Disable "wait indefinitely" if the menu is hidden.
        if switch.get_active():
            self.builder.get_object("grub_timeout_spinner").get_adjustment().set_lower(-1.0)
        else:
            self.builder.get_object("grub_timeout_spinner").get_adjustment().set_lower(0.0)
        self.builder.get_object("grub_timeout_spinner").update()

    def get_boot_config(self):
        if not os.path.exists(GRUB_FILE):
            return
        menu_visible = False
        menu_timeout = 0

        # GRUB_DEFAULT and GRUB_SAVEDEFAULT are both required for the 'remember last' option
        # but since we're making this file we can assume if one is here the other is.
        savedefault_present = False
        boot_args = []
        with open(GRUB_FILE, "r") as grub_file:
            for line in grub_file:
                line = line.strip()
                if "GRUB_TIMEOUT=" in line:
                    print(line)
                    menu_timeout = float(line.split("=")[-1])
                    print(menu_timeout)
                elif line == "GRUB_TIMEOUT_STYLE=menu":
                    menu_visible = True
                elif line.startswith("GRUB_CMDLINE_LINUX_DEFAULT="):
                    match = re.search(r'GRUB_CMDLINE_LINUX_DEFAULT="\$GRUB_CMDLINE_LINUX_DEFAULT\s*([^"]*)"', line)
                    if match:
                        boot_args = match.group(1).strip().split()
                elif line.startswith("GRUB_SAVEDEFAULT="):
                    savedefault_present = True
        self.builder.get_object("grub_switch").set_active(menu_visible)
        self.builder.get_object("grub_remember_last_switch").set_active(savedefault_present)
        self.builder.get_object("grub_timeout_spinner").set_value(menu_timeout)
        self.boot_args_editor.set_strings(boot_args)

    def save_boot_config(self, button):
        menu = "hidden"
        if self.builder.get_object("grub_switch").get_active():
            menu = "menu"

        menu_timeout = self.builder.get_object("grub_timeout_spinner").get_value_as_int()
        boot_args = " ".join(self.boot_args_editor.get_strings())

        grub_text = f"""# Do not edit this file. It is generated by mintsysadm.
GRUB_TIMEOUT_STYLE={menu}
GRUB_TIMEOUT={menu_timeout}
GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT {boot_args}"
"""
        if self.builder.get_object("grub_remember_last_switch").get_active():
            grub_text += "GRUB_DEFAULT=saved\nGRUB_SAVEDEFAULT=true\n"

        with open(GRUB_FILE, "w") as grub_file:
            print(grub_text, file=grub_file)

        buf = self.builder.get_object("textview_grub_output").get_buffer()
        buf.set_text(_("Running update-grub...\n\n"))
        self.builder.get_object("grub_result_label").set_text(_("Processing..."))
        self.update_grub()
        self.grub_dialog = self.builder.get_object("grub_dialog")
        self.grub_dialog.show()
        self.grub_dialog.run()
        self.grub_dialog.hide()

    def close_grub_dialog(self, button):
        self.grub_dialog.hide()

    @xt.run_async
    def update_grub(self):
        proc = subprocess.Popen(["/usr/sbin/update-grub"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            self.update_grub_output(line)
        proc.wait()
        rc = proc.returncode
        if rc == 0:
            self.update_grub_success(True)
        else:
            self.update_grub_success(False)

    @xt.run_idle
    def update_grub_success(self, success):
        label = self.builder.get_object("grub_result_label")
        if success:
            label.set_markup("<span foreground='green'>%s</span>" % _("Success"))
        else:
            label.set_markup("<span foreground='red'>%s</span>" % _("Error"))

    @xt.run_idle
    def update_grub_output(self, line):
        buffer = self.builder.get_object("textview_grub_output").get_buffer()
        end_iter = buffer.get_end_iter()
        buffer.insert(end_iter, line)
        textview = self.builder.get_object("textview_grub_output")
        textview.scroll_to_iter(end_iter, 0.0, False, 0.0, 1.0)

    def validate_boot_argument(self, text):
        if " " in text:
            return _("Boot arguments cannot include space characters.")
        # None means no error
        return None

    def open_about(self, widget):
        dlg = Gtk.AboutDialog()
        dlg.set_transient_for(self.window)
        dlg.set_title(_("About"))
        dlg.set_program_name("mintsysadm")
        dlg.set_comments(_("System Administration"))
        try:
            h = open('/usr/share/common-licenses/GPL', encoding="utf-8")
            s = h.readlines()
            gpl = ""
            for line in s:
                gpl += line
            h.close()
            dlg.set_license(gpl)
        except Exception as e:
            print (e)

        dlg.set_version("__DEB_VERSION__")
        dlg.set_icon_name("mintsysadm")
        dlg.set_logo_icon_name("mintsysadm")
        dlg.set_website("https://www.github.com/linuxmint/mintsysadm")
        def close(w, res):
            if res == Gtk.ResponseType.CANCEL or res == Gtk.ResponseType.DELETE_EVENT:
                w.destroy()
        dlg.connect("response", close)
        dlg.show()

    def on_menu_quit(self, widget):
        self.application.quit()

if __name__ == "__main__":
    application = MyApplication("com.linuxmint.sysadm", Gio.ApplicationFlags.FLAGS_NONE)
    application.run()
