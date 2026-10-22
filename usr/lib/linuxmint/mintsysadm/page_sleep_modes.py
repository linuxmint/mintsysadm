#!/usr/bin/python3

from collections import namedtuple
import gi
import os
import re
import subprocess
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

import xapp.util

_ = xapp.util.l10n("mintsysadm")

SLEEP_FILE = "/etc/systemd/sleep.conf.d/90-mintsysadm.conf"
SLEEP_OPTIONS = {
    "switch_suspend": "AllowSuspend",
    "switch_hibernate": "AllowHibernation",
    "switch_suspend_then_hibernate": "AllowSuspendThenHibernate",
    "switch_hybrid_sleep": "AllowHybridSleep",
}

def read_file(path):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None

def get_swap_entries():
    """Return the swap areas systemd would consider for hibernation (partitions and files, no zram)."""
    entries = []
    for line in (read_file("/proc/swaps") or "").splitlines()[1:]:
        fields = line.split()
        if len(fields) < 5:
            continue
        path = fields[0].replace("\\040", " ")
        if fields[1] == "file":
            if path.endswith(" (deleted)"):
                continue
        elif fields[1] == "partition":
            if os.path.basename(path).startswith("zram"):
                continue
        else:
            continue
        entry = {
            "path": path,
            "swapfile": fields[1] == "file",
            "size": int(fields[2]) * 1024,
            "used": int(fields[3]) * 1024,
            "priority": int(fields[4]),
        }
        entry["location"] = get_swap_location(entry)
        entries.append(entry)
    return entries

def run(command):
    """Run command in English"""
    try:
        return subprocess.run(command, capture_output=True, text=True, env={"LANG": "C", "PATH": os.environ.get("PATH", "")}).stdout
    except OSError:
        return ""

def get_swap_location(entry):
    """Return (devno, offset in pages) of a swap area, as systemd computes them. None if unknown."""
    try:
        st = os.stat(entry["path"])
    except OSError:
        return None
    if not entry["swapfile"]:
        return st.st_rdev, 0

    btrfs = run(["stat", "-f", "-c", "%T", entry["path"]]).strip() == "btrfs"
    if btrfs:
        # st_dev is not the block device on btrfs
        source = run(["findmnt", "-no", "SOURCE", "-T", entry["path"]]).strip().split("[")[0]
        try:
            devno = os.stat(source).st_rdev
            offset = int(run(["btrfs", "inspect-internal", "map-swapfile", "-r", entry["path"]]).strip())
        except (OSError, ValueError):
            return None
    else:
        devno = st.st_dev
        output = run(["filefrag", "-v", entry["path"]])
        try:
            block_size = int(re.search(r"blocks of (\d+) bytes", output).group(1))
            extent = re.search(r"^\s*0:\s+\d+\.\.\s*\d+:\s+(\d+)\.\.", output, re.MULTILINE)
            offset = int(extent.group(1)) * block_size // 4096
        except (AttributeError, ValueError):
            return None
    return devno, offset

def read_resume_config():
    """Return the (devno, offset) set in /sys/power/resume and /sys/power/resume_offset."""
    try:
        major, minor = (read_file("/sys/power/resume") or "0:0").split(":")
        return os.makedev(int(major), int(minor)), int(read_file("/sys/power/resume_offset") or 0)
    except ValueError:
        return 0, 0

def get_available_modes():
    """Ask logind which sleep modes are available. Returns None if logind can't be reached."""
    modes = [
        ("CanSuspend", _("Suspend")),
        ("CanHibernate", _("Hibernate")),
        ("CanSuspendThenHibernate", _("Suspend then hibernate")),
        ("CanHybridSleep", _("Hybrid sleep")),
    ]
    available = []
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        for method, name in modes:
            result = bus.call_sync("org.freedesktop.login1", "/org/freedesktop/login1",
                                   "org.freedesktop.login1.Manager", method, None,
                                   GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, -1, None)
            # "challenge" means available but the user would be asked to authenticate
            if result.unpack()[0] in ("yes", "challenge"):
                available.append(name)
    except GLib.Error:
        return None
    return available

def get_suspend_modes():
    """Return the (id, name) of each suspend method offered by the kernel, and the id in use."""
    names = {
        "s2idle": _("Suspend-to-idle (s2idle)"),
        "deep": _("Suspend-to-RAM (S3)"),
        "shallow": _("Shallow (S1)"),
    }
    modes = []
    current = None
    for mode in (read_file("/sys/power/mem_sleep") or "").split():
        if mode.startswith("["):
            mode = mode.strip("[]")
            current = mode
        modes.append((mode, names.get(mode, mode)))
    return modes, current

def get_hibernation_methods():
    """Return the (id, name) of each hibernation method offered by the kernel, and the id in use."""
    names = {
        "platform": _("Platform (the firmware powers off)"),
        "shutdown": _("Shutdown (the kernel powers off)"),
    }
    methods = []
    current = None
    for method in (read_file("/sys/power/disk") or "").split():
        if method.startswith("["):
            method = method.strip("[]")
            current = method
        # The other values (reboot, suspend, test_resume) are not meant for normal use
        if method in names:
            methods.append((method, names[method]))
    return methods, current if current in names else None

# What a check reports (one line in the list of problems and information). ok is True (fine), False (problem) or None (information).
# action is (swap area, boot arguments) for the "use as resume device" button.
Report = namedtuple("Report", "ok title detail action", defaults=(None,))

def get_resume_arguments(entry):
    """Return the kernel arguments that make a swap area the resume device, or None if they can't be found."""
    if not entry["swapfile"]:
        if entry["path"].startswith("/dev/mapper/"):
            return ["resume=" + entry["path"]]
        uuid = run(["blkid", "-s", "UUID", "-o", "value", entry["path"]]).strip()
        return ["resume=UUID=" + uuid] if uuid else None

    # A swap file is identified by the file system that holds it and its offset in that file system
    uuid = run(["findmnt", "-no", "UUID", "-T", entry["path"]]).strip()
    if not uuid or entry["location"] is None:
        return None
    return ["resume=UUID=" + uuid, "resume_offset=%d" % entry["location"][1]]

class SleepModesWidget:

    def __init__(self, builder, update_boot_arguments):
        self.builder = builder
        # Called as update_boot_arguments(prefixes, arguments) to replace boot arguments and update the boot menu
        self.update_boot_arguments = update_boot_arguments
        self.new_resume_device = None  # Used when the resume device is set to a new swap area

        self.load_sleep_options()
        self.refresh_lists()
        for switch_name in SLEEP_OPTIONS:
            self.builder.get_object(switch_name).connect("notify::active", self.save_sleep_options)
        for combo_name in ("combo_suspend_mode", "combo_hibernate_method"):
            self.builder.get_object(combo_name).connect("changed", lambda combo: self.save_sleep_options(combo, None))

    def load_sleep_options(self):
        # Same default as systemd for options missing from our drop-in
        values = {}
        if os.path.exists(SLEEP_FILE):
            with open(SLEEP_FILE, "r", errors="replace") as sleep_file:
                for line in sleep_file:
                    if "=" in line and not line.strip().startswith("#"):
                        key, value = line.split("=", 1)
                        values[key.strip()] = value.strip()

        for switch_name, option in SLEEP_OPTIONS.items():
            allowed = values.get(option, "yes").lower() not in ("no", "false", "0", "off")
            self.builder.get_object(switch_name).set_active(allowed)

        # The method in use is the kernel default, unless we've told systemd to use another one
        suspend_combo = self.builder.get_object("combo_suspend_mode")
        modes, current = get_suspend_modes()
        default_name = _("Default")
        if current is not None and "MemorySleepMode" not in values:
            default_name = _("Default — %s") % dict(modes)[current]
        suspend_combo.append("", default_name)
        for mode, name in modes:
            suspend_combo.append(mode, name)
        if not suspend_combo.set_active_id(values.get("MemorySleepMode", "")):
            suspend_combo.set_active_id("")
        self.update_suspend_dependents()

        hibernate_combo = self.builder.get_object("combo_hibernate_method")
        methods, current = get_hibernation_methods()
        default_name = _("Default")
        if current is not None and "HibernateMode" not in values:
            default_name = _("Default — %s") % dict(methods)[current]
        hibernate_combo.append("", default_name)
        for method, name in methods:
            hibernate_combo.append(method, name)
        if not hibernate_combo.set_active_id(values.get("HibernateMode", "")):
            hibernate_combo.set_active_id("")
        self.update_hibernate_method()

    def set_resume_arguments(self, button, action):
        path, arguments = action
        self.update_boot_arguments(("resume=", "resume_offset="), arguments)
        self.new_resume_device = path
        self.refresh_lists()

    def run_checks(self):
        """Run the checks for the sleep modes that are allowed, in the order they are shown."""
        suspend = self.builder.get_object("switch_suspend").get_active()
        hibernate = any(self.builder.get_object(name).get_active()
                        for name in ("switch_hibernate", "switch_suspend_then_hibernate", "switch_hybrid_sleep"))

        checks = []
        if suspend:
            checks += self.check_suspend()
        if hibernate:
            self.entries = get_swap_entries()
            resume_devno, resume_offset = read_resume_config()

            # The swap area used as resume device by the running system, if any
            self.current_resume_device = None
            for entry in self.entries:
                location = entry["location"]
                if (location is not None and location[0] == resume_devno
                        and (not entry["swapfile"] or location[1] == resume_offset)):
                    self.current_resume_device = entry["path"]
                    break

            # Choosing the swap area the system already uses cancels the pending change
            if self.new_resume_device == self.current_resume_device:
                self.new_resume_device = None

            checks += self.check_hibernation_support()
            checks += self.check_secure_boot()
            checks += self.check_swap()
            checks += self.check_resume()
        return checks

    def check_suspend(self):
        title = _("Suspend")
        kernel_state = (read_file("/sys/power/state") or "").split()
        if "mem" not in kernel_state and "freeze" not in kernel_state:
            return [Report(False, title, _("The kernel does not support suspend."))]

        modes, current = get_suspend_modes()
        if current is None:
            return [Report(True, title, _("The kernel supports suspend."))]
        return [Report(True, title, _("The kernel supports suspend. Method in use: %s.") % dict(modes)[current])]

    def check_hibernation_support(self):
        if "disk" in (read_file("/sys/power/state") or "").split():
            return [Report(True, _("Kernel support"), _("The kernel supports hibernation."))]
        return [Report(False, _("Kernel support"), _("The kernel does not support hibernation."))]

    def check_secure_boot(self):
        lockdown = re.search(r"\[(\w+)\]", read_file("/sys/kernel/security/lockdown") or "")
        locked = lockdown is not None and lockdown.group(1) != "none"
        secure_boot = False
        efivars = "/sys/firmware/efi/efivars"
        for name in os.listdir(efivars) if os.path.isdir(efivars) else []:
            if name.startswith("SecureBoot-"):
                try:
                    with open(os.path.join(efivars, name), "rb") as f:
                        secure_boot = f.read()[-1] == 1
                except OSError:
                    pass

        title = _("Secure Boot")
        if locked:
            return [Report(False, title, _("Secure Boot is enabled and the kernel lockdown prevents hibernation."))]
        if secure_boot:
            return [Report(True, title, _("Secure Boot is enabled but the kernel lockdown is not active."))]
        return [Report(True, title, _("Secure Boot is disabled."))]

    def check_swap(self):
        """One check per swap area, whether it is the resume device or not."""
        if not self.entries:
            return [Report(False, _("Swap"), _("No swap partition or swap file is in use."))]

        meminfo = read_file("/proc/meminfo") or ""
        match = re.search(r"^MemTotal:\s+(\d+) kB", meminfo, re.MULTILINE)
        ram = int(match.group(1)) * 1024 if match else 0

        resume_path = self.new_resume_device or self.current_resume_device
        checks = []
        for entry in self.entries:
            title = _("Swap area in %s") % entry["path"]
            sizes = {"swap": GLib.format_size(entry["size"]), "ram": GLib.format_size(ram)}

            # Offer to make it the resume device unless it already is (or is about to be)
            arguments = None if entry["path"] == resume_path else get_resume_arguments(entry)
            action = (entry["path"], arguments) if arguments else None

            if entry["size"] < ram:
                checks.append(Report(False, title,
                                    _("The swap area (%(swap)s) is smaller than the memory (%(ram)s).")
                                    % sizes, action))
            else:
                checks.append(Report(None, title, _("The swap area (%(swap)s) is at least as large as the memory (%(ram)s).") % sizes, action))
        return checks

    def check_resume(self):
        title = _("Resume device")

        if self.new_resume_device:
            return [Report(False, title, _("The resume device will be set to %s upon reboot.") % self.new_resume_device)]

        if "noresume" in (read_file("/proc/cmdline") or "").split():
            return [Report(False, title, _("The noresume kernel argument is set, which prevents hibernation."))]

        resume_devno, resume_offset = read_resume_config()
        if resume_devno == 0:
            return [Report(False, title, _("No resume device is set."))]

        # When a resume device is set, no other swap area is used for hibernation
        matching = None
        same_device = []
        for entry in self.entries:
            location = entry["location"]
            if location is None or location[0] != resume_devno:
                continue
            if not entry["swapfile"] or location[1] == resume_offset:
                matching = entry
                break
            same_device.append((entry, location[1]))

        if matching is None:
            if same_device:
                entry, offset = same_device[0]
                return [Report(False, title, _("The resume offset (%(current)d) does not match the start of %(path)s (%(expected)d).")
                              % {"current": resume_offset, "path": entry["path"], "expected": offset})]
            return [Report(False, title, _("The resume device (%(major)d:%(minor)d) does not match any swap partition or swap file in use.")
                          % {"major": os.major(resume_devno), "minor": os.minor(resume_devno)})]

        if matching["swapfile"]:
            return [Report(None, title, _("The resume device is set to the swap file %(path)s (offset %(offset)d).")
                          % {"path": matching["path"], "offset": resume_offset})]
        return [Report(None, title, _("The resume device is set to %s.") % matching["path"])]

    def refresh_lists(self):
        listbox = self.builder.get_object("power_checks_list")
        for child in listbox.get_children():
            listbox.remove(child)
        # Problems (False) and information (None), in the order of the checks
        shown = [report for report in self.run_checks() if report.ok is not True]
        if not any(report.ok is False for report in shown):
            shown.insert(0, Report(True, _("No problems detected"), _("This computer looks ready for the sleep modes above.")))
        for ok, title, detail, action in shown:
            box = Gtk.Box(spacing=12, margin=8)
            icon = {True: "xsi-emblem-ok-symbolic", False: "xsi-dialog-warning-symbolic", None: "xsi-dialog-information-symbolic"}[ok]
            box.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON), False, False, 0)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            label = Gtk.Label(label=title, xalign=0)
            text.pack_start(label, False, False, 0)
            label = Gtk.Label(label=detail, xalign=0, wrap=True)
            label.get_style_context().add_class("dim-label")
            text.pack_start(label, False, False, 0)
            box.pack_start(text, True, True, 0)
            if action:
                button = Gtk.Button(label=_("Set as resume device"), valign=Gtk.Align.CENTER)
                button.connect("clicked", self.set_resume_arguments, action)
                box.pack_start(button, False, False, 0)
            row = Gtk.ListBoxRow(activatable=False, selectable=False)
            row.add(box)
            listbox.add(row)
        listbox.show_all()

        # Sleep modes systemd reports as available (Can...=yes)
        listbox = self.builder.get_object("available_modes_list")
        for child in listbox.get_children():
            listbox.remove(child)
        available = get_available_modes()
        if available is None:
            available = [_("The system could not be asked which sleep modes are available.")]
        elif not available:
            available = [_("None")]
        for name in available:
            row = Gtk.ListBoxRow(activatable=False, selectable=False)
            row.add(Gtk.Label(label=name, xalign=0, margin=8))
            listbox.add(row)
        listbox.show_all()

    def update_suspend_dependents(self):
        # Suspend-then-hibernate, hybrid sleep and the suspend method all need suspend, so they are only usable when it is allowed
        suspend_allowed = self.builder.get_object("switch_suspend").get_active()
        for widget_name in ("switch_suspend_then_hibernate", "switch_hybrid_sleep"):
            widget = self.builder.get_object(widget_name)
            if not suspend_allowed:
                widget.set_active(False)
            widget.get_ancestor(Gtk.ListBoxRow).set_sensitive(suspend_allowed)
        # Nothing to choose when the firmware only offers one method
        combo = self.builder.get_object("combo_suspend_mode")
        combo.get_ancestor(Gtk.ListBoxRow).set_sensitive(suspend_allowed and len(get_suspend_modes()[0]) > 1)
        self.update_hibernate_method()

    def update_hibernate_method(self):
        # The hibernation method is used by hibernate and by the hibernate step of suspend-then-hibernate
        hibernates = (self.builder.get_object("switch_hibernate").get_active()
                      or self.builder.get_object("switch_suspend_then_hibernate").get_active())
        combo = self.builder.get_object("combo_hibernate_method")
        combo.get_ancestor(Gtk.ListBoxRow).set_sensitive(hibernates and len(get_hibernation_methods()[0]) > 1)

    def save_sleep_options(self, switch, gparam):
        if switch is self.builder.get_object("switch_suspend"):
            self.update_suspend_dependents()
        self.update_hibernate_method()

        # Hibernation is disabled by default (the package ships this file with everything set to "no")
        states = {option: self.builder.get_object(switch_name).get_active()
                  for switch_name, option in SLEEP_OPTIONS.items()}
        os.makedirs(os.path.dirname(SLEEP_FILE), exist_ok=True)
        with open(SLEEP_FILE, "w") as sleep_file:
            print("# Do not edit this file. It is generated by mintsysadm.", file=sleep_file)
            print("[Sleep]", file=sleep_file)
            for option, active in states.items():
                print("%s=%s" % (option, "yes" if active else "no"), file=sleep_file)
            suspend_mode = self.builder.get_object("combo_suspend_mode").get_active_id()
            if suspend_mode:
                print("MemorySleepMode=%s" % suspend_mode, file=sleep_file)
            hibernate_method = self.builder.get_object("combo_hibernate_method").get_active_id()
            if hibernate_method:
                print("HibernateMode=%s" % hibernate_method, file=sleep_file)

        self.refresh_lists()
