#!/usr/bin/python3

from datetime import datetime
from functools import cmp_to_key
import gi
import os
import pwd
import subprocess
from urllib.parse import quote
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango

import apt
import aptkit.simpleclient
import xapp.util
import xapp.threading as xt
import apt_pkg

from common.kernels import (
    get_available_series,
    get_installed_series,
    set_series_manually_tracked,
)
from common.kernel_cleanup import (
    CleanupCandidate,
    CleanupSettings,
    VERSIONED_KERNEL_PACKAGE_RE,
    get_cleanup_candidates,
    get_installed_versioned_kernel_packages,
    get_kernel_package_version,
    get_tracked_meta_packages,
    load_cleanup_settings,
    prune_protected_kernels,
    read_cleanup_history,
    run_cleanup,
    save_cleanup_settings,
    simulate_candidate,
)


_ = xapp.util.l10n("mintsysadm")


class KernelsWidget:

    def __init__(self, parent_window, builder):
        self.parent_window = parent_window
        self.builder = builder
        self.apt_client = None

        self.tracked_row_css = Gtk.CssProvider()
        self.tracked_row_css.load_from_data(
            b"""
            .tracked-kernel-series {
                background-color: alpha(@theme_selected_bg_color, 0.12);
                border-left: 3px solid @theme_selected_bg_color;
            }
            """
        )

        self.builder.get_object("label_active_kernel").set_text(
            os.uname().release
        )
        self.tracking_warning = self.builder.get_object(
            "infobar_tracking_warning"
        )

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.set_header_func(self.add_row_separator)
        self.listbox.connect("row_activated", self.on_row_activated)
        self.builder.get_object("scrolledview_kernels").add(self.listbox)

        self.cleanup_switch = self.builder.get_object(
            "switch_cleanup_enabled"
        )
        self.retain_spin = self.builder.get_object(
            "spinner_num_kernels_to_keep"
        )
        self.retain_spin.set_adjustment(Gtk.Adjustment(
            value=2,
            lower=1,
            upper=10,
            step_increment=1,
            page_increment=1,
        ))
        self.builder.get_object("button_history").connect(
            "clicked",
            self.on_history_clicked,
        )
        self.builder.get_object("button_cleanup").connect(
            "clicked",
            self.on_cleanup_clicked,
        )
        self.builder.get_object("button_all_kernels").connect(
            "clicked",
            self.on_all_kernels_clicked,
        )

        settings = load_cleanup_settings()
        self.cleanup_switch.set_active(settings.enabled)
        self.retain_spin.set_value(settings.retain)
        self.cleanup_switch.connect(
            "notify::active",
            self.on_cleanup_settings_changed,
        )
        self.retain_spin.connect(
            "value-changed",
            self.on_cleanup_settings_changed,
        )
        self.load_series()

    def get_available_kernels(self):
        cache = apt.Cache()
        settings = load_cleanup_settings()
        if prune_protected_kernels(settings):
            save_cleanup_settings(settings)
        protected_kernels = settings.protected_kernels
        kernels = []
        seen = set()
        running_release = os.uname().release

        for package_name in cache.keys():
            match = VERSIONED_KERNEL_PACKAGE_RE.fullmatch(package_name)
            if match is None or not package_name.startswith("linux-image-"):
                continue

            package = cache[package_name]
            if not package.is_installed and package.candidate is None:
                continue

            if package_name.startswith("linux-image-unsigned-"):
                signed_name = package_name.replace(
                    "linux-image-unsigned-",
                    "linux-image-",
                    1,
                )
                if signed_name in cache and cache[signed_name].candidate is not None:
                    continue

            kernel_version = match.group("version")
            flavor = match.group("flavor") or ""
            identifier = (kernel_version, flavor)
            if identifier in seen:
                continue
            seen.add(identifier)

            installed = package.is_installed
            package_version = package.installed or package.candidate
            changelog_version = package_version.version
            if ":" in changelog_version:
                changelog_version = changelog_version.split(":", 1)[1]
            debian_origin = next(
                (
                    origin
                    for origin in package_version.origins
                    if (origin.origin or "").lower() == "debian"
                ),
                None,
            )
            if debian_origin is not None:
                source_name = package_version.source_name
                component = debian_origin.component or "main"
                if source_name.startswith("lib"):
                    prefix = source_name[:4]
                else:
                    prefix = source_name[0]
                changelog_url = (
                    "https://metadata.ftp-master.debian.org/changelogs/"
                    f"/{component}/{prefix}/{source_name}/"
                    f"{source_name}_{changelog_version}_changelog"
                )
                bug_reports_url = (
                    "https://bugs.debian.org/cgi-bin/pkgreport.cgi?pkg="
                    f"{quote(package_name, safe='')}"
                )
            else:
                changelog_version = changelog_version.split("~", 1)[0]
                changelog_url = (
                    "https://changelogs.ubuntu.com/changelogs/pool/main/l/"
                    f"linux/linux_{changelog_version}/changelog"
                )
                bug_reports_url = (
                    "https://launchpad.net/ubuntu/+source/linux/+bugs"
                    f"?field.searchtext={kernel_version}"
                )
            active = self._is_kernel_active(
                kernel_version,
                flavor,
                running_release,
            )
            kernels.append({
                "version": kernel_version,
                "flavor": flavor,
                "image": package_name,
                "installed": installed,
                "active": active,
                "available": package.candidate is not None,
                "protected": self.get_kernel_identifier(
                    kernel_version,
                    flavor,
                ) in protected_kernels,
                "bug_reports_url": bug_reports_url,
                "changelog_url": changelog_url,
            })
        return kernels

    @staticmethod
    def get_kernel_identifier(kernel_version, flavor):
        identifier = kernel_version
        if flavor:
            identifier += "-" + flavor
        return identifier

    @staticmethod
    def _is_kernel_active(kernel_version, flavor, running_release):
        release = kernel_version
        if flavor:
            release += "-" + flavor
        return release == running_release

    def get_kernel_packages(self, kernel):
        cache = apt.Cache()
        packages = set()
        for package_name in cache.keys():
            match = VERSIONED_KERNEL_PACKAGE_RE.fullmatch(package_name)
            if match is None:
                continue
            if match.group("version") != kernel["version"]:
                continue

            flavor = match.group("flavor")
            if flavor not in (None, "common", kernel["flavor"]):
                continue

            if package_name.startswith("linux-image-"):
                if package_name != kernel["image"]:
                    continue

            package = cache[package_name]
            if package.is_installed or package.candidate is not None:
                packages.add(package_name)
        return packages

    def on_all_kernels_clicked(self, button):
        dialog = Gtk.Dialog(
            title=_("All kernels"),
            transient_for=self.parent_window,
            modal=True,
        )
        dialog.set_border_width(12)
        dialog.set_default_size(700, 500)
        dialog.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        protect_button = dialog.add_button(
            _("Protect"),
            Gtk.ResponseType.APPLY,
        )
        protect_button.set_tooltip_text(
            _("Protected kernels are never removed during cleanup.")
        )
        protect_button.set_sensitive(False)
        action_button = dialog.add_button(_("Install"), Gtk.ResponseType.OK)
        action_button.set_sensitive(False)

        content_area = dialog.get_content_area()
        content_area.set_spacing(8)
        kernels = self.get_available_kernels()

        filters_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        filters_box.pack_start(Gtk.Label(label=_("Series")), False, False, 0)
        series_combo = Gtk.ComboBoxText()
        series_combo.append("", _("All"))
        series = {".".join(kernel["version"].split(".")[:2]) for kernel in kernels}
        for value in sorted(
                series,
                key=cmp_to_key(apt_pkg.version_compare),
                reverse=True):
            series_combo.append(value, value)
        series_combo.set_active_id("")
        filters_box.pack_start(series_combo, False, False, 0)

        filters_box.pack_start(Gtk.Label(label=_("Flavor")), False, False, 0)
        flavor_combo = Gtk.ComboBoxText()
        flavor_combo.append("", _("All"))
        flavors = sorted({kernel["flavor"] for kernel in kernels})
        for flavor in flavors:
            flavor_combo.append(flavor, flavor)
        if "generic" in flavors:
            flavor_combo.set_active_id("generic")
        elif "amd64" in flavors:
            flavor_combo.set_active_id("amd64")
        else:
            flavor_combo.set_active_id("")
        filters_box.pack_start(flavor_combo, False, False, 0)

        filters_box.pack_start(Gtk.Label(label=_("Status")), False, False, 0)
        status_combo = Gtk.ComboBoxText()
        status_combo.append("", _("All"))
        status_combo.append("active", _("Active"))
        status_combo.append("active-protected", _("Active, protected"))
        status_combo.append("installed", _("Installed"))
        status_combo.append(
            "installed-protected",
            _("Installed, protected"),
        )
        status_combo.append("available", _("Available"))
        status_combo.set_active_id("")
        filters_box.pack_start(status_combo, False, False, 0)
        content_area.pack_start(filters_box, False, False, 0)

        store = Gtk.ListStore(str, str, str, bool, bool, object, int, str)
        for kernel in kernels:
            if kernel["active"]:
                if kernel["protected"]:
                    status = _("Active, protected")
                else:
                    status = _("Active")
                status_rank = 0
            elif kernel["installed"]:
                if kernel["protected"]:
                    status = _("Installed, protected")
                else:
                    status = _("Installed")
                status_rank = 1
            else:
                status = _("Available")
                status_rank = 2
            store.append((
                kernel["version"],
                kernel["flavor"],
                status,
                kernel["installed"],
                kernel["active"],
                kernel,
                status_rank,
                ".".join(kernel["version"].split(".")[:2]),
            ))
        filtered = store.filter_new()

        def filter_kernel(model, tree_iter, _data=None):
            selected_series = series_combo.get_active_id() or ""
            selected_flavor = flavor_combo.get_active_id() or ""
            selected_status = status_combo.get_active_id() or ""
            if selected_series and model.get_value(tree_iter, 7) != selected_series:
                return False
            if selected_flavor and model.get_value(tree_iter, 1) != selected_flavor:
                return False
            if selected_status:
                kernel = model.get_value(tree_iter, 5)
                if kernel["active"]:
                    status_id = "active"
                elif kernel["installed"]:
                    status_id = "installed"
                else:
                    status_id = "available"
                if kernel["protected"]:
                    status_id += "-protected"
                if status_id != selected_status:
                    return False
            return True

        filtered.set_visible_func(filter_kernel)
        for combo in (series_combo, flavor_combo, status_combo):
            combo.connect("changed", lambda widget: filtered.refilter())

        sorted_model = Gtk.TreeModelSort(model=filtered)
        sorted_model.set_sort_func(0, self.compare_kernel_versions)
        sorted_model.set_sort_func(6, self.compare_kernel_status)
        sorted_model.set_sort_column_id(6, Gtk.SortType.ASCENDING)

        treeview = Gtk.TreeView(model=sorted_model)
        for title, column_id, sort_column_id in (
            (_("Version"), 0, 0),
            (_("Flavor"), 1, 1),
            (_("Status"), 2, 6),
        ):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=column_id)
            column.set_sort_column_id(sort_column_id)
            treeview.append_column(column)

        selection = treeview.get_selection()

        links_column = Gtk.TreeViewColumn(_("Links"))
        bug_reports_renderer = Gtk.CellRendererText(
            text=_("Bug reports"),
            underline=Pango.Underline.SINGLE,
            foreground="#3584e4",
            xpad=6,
        )
        changelog_renderer = Gtk.CellRendererText(
            text=_("Changelog"),
            underline=Pango.Underline.SINGLE,
            foreground="#3584e4",
            xpad=6,
        )
        links_column.pack_start(bug_reports_renderer, False)
        links_column.pack_start(changelog_renderer, False)
        treeview.append_column(links_column)

        def link_clicked(widget, event):
            if event.button != 1:
                return False
            result = treeview.get_path_at_pos(int(event.x), int(event.y))
            if result is None:
                return False
            path, column, cell_x, _cell_y = result
            if column is not links_column:
                return False

            model = treeview.get_model()
            kernel = model.get_value(model.get_iter(path), 5)
            for renderer, url_key in (
                (bug_reports_renderer, "bug_reports_url"),
                (changelog_renderer, "changelog_url"),
            ):
                x_offset, width = links_column.cell_get_position(renderer)
                if x_offset <= cell_x < x_offset + width:
                    self.open_url_as_user(kernel[url_key])
                    return True
            return False

        treeview.connect("button-release-event", link_clicked)

        def selection_changed(tree_selection):
            model, tree_iter = tree_selection.get_selected()
            if tree_iter is None:
                action_button.set_sensitive(False)
                protect_button.set_sensitive(False)
                return
            installed = model.get_value(tree_iter, 3)
            active = model.get_value(tree_iter, 4)
            kernel = model.get_value(tree_iter, 5)
            action_button.set_label(_("Remove") if installed else _("Install"))
            action_button.set_sensitive(not active)
            protect_button.set_label(
                _("Unprotect") if kernel["protected"] else _("Protect")
            )
            protect_button.set_sensitive(installed)

        selection.connect("changed", selection_changed)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(
            Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC,
        )
        scrolled_window.set_shadow_type(Gtk.ShadowType.IN)
        scrolled_window.add(treeview)
        content_area.pack_start(scrolled_window, True, True, 0)

        dialog.show_all()
        kernel = None
        while True:
            response = dialog.run()
            model, tree_iter = selection.get_selected()
            if response != Gtk.ResponseType.APPLY:
                if response == Gtk.ResponseType.OK and tree_iter is not None:
                    kernel = model.get_value(tree_iter, 5)
                break
            if tree_iter is None:
                continue

            selected_kernel = model.get_value(tree_iter, 5)
            if not selected_kernel["installed"]:
                continue
            settings = load_cleanup_settings()
            identifier = self.get_kernel_identifier(
                selected_kernel["version"],
                selected_kernel["flavor"],
            )
            selected_kernel["protected"] = not selected_kernel["protected"]
            if selected_kernel["protected"]:
                settings.protected_kernels.add(identifier)
            else:
                settings.protected_kernels.discard(identifier)
            save_cleanup_settings(settings)

            for row in store:
                if row[5] is not selected_kernel:
                    continue
                if selected_kernel["active"]:
                    row[2] = (
                        _("Active, protected")
                        if selected_kernel["protected"]
                        else _("Active")
                    )
                else:
                    row[2] = (
                        _("Installed, protected")
                        if selected_kernel["protected"]
                        else _("Installed")
                    )
                break
            protect_button.set_label(
                _("Unprotect")
                if selected_kernel["protected"]
                else _("Protect")
            )
            filtered.refilter()
        dialog.destroy()

        if kernel is None:
            return
        if kernel["installed"]:
            self.remove_individual_kernel(kernel)
        else:
            cache = apt.Cache()
            packages = sorted(
                package_name
                for package_name in self.get_kernel_packages(kernel)
                if not cache[package_name].is_installed
                if package_name.startswith((
                    "linux-image-",
                    "linux-modules-",
                    "linux-headers-",
                ))
            )
            if packages:
                self.start_package_transaction(packages, True)

    @staticmethod
    def open_url_as_user(url):
        try:
            uid = int(
                os.environ.get("PKEXEC_UID")
                or os.environ.get("SUDO_UID")
                or "-1"
            )
            user = pwd.getpwuid(uid)
        except (KeyError, TypeError, ValueError):
            return True

        runtime_dir = f"/run/user/{uid}"
        environment = [
            f"HOME={user.pw_dir}",
            f"USER={user.pw_name}",
            f"LOGNAME={user.pw_name}",
            f"XDG_RUNTIME_DIR={runtime_dir}",
            f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime_dir}/bus",
        ]
        for variable in (
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "XAUTHORITY",
            "XDG_CURRENT_DESKTOP",
            "DESKTOP_SESSION",
        ):
            value = os.environ.get(variable)
            if value:
                environment.append(f"{variable}={value}")

        subprocess.Popen(
            [
                "runuser",
                "-u",
                user.pw_name,
                "--",
                "env",
                *environment,
                "xdg-open",
                url,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True

    @staticmethod
    def compare_kernel_versions(model, first, second, _data=None):
        first_version = model.get_value(first, 0)
        second_version = model.get_value(second, 0)
        comparison = apt_pkg.version_compare(first_version, second_version)
        if comparison != 0:
            return comparison
        first_flavor = model.get_value(first, 1)
        second_flavor = model.get_value(second, 1)
        return (first_flavor > second_flavor) - (first_flavor < second_flavor)

    @staticmethod
    def compare_kernel_status(model, first, second, _data=None):
        first_rank = model.get_value(first, 6)
        second_rank = model.get_value(second, 6)
        if first_rank != second_rank:
            return first_rank - second_rank
        return -KernelsWidget.compare_kernel_versions(model, first, second)

    def remove_individual_kernel(self, kernel):
        packages = self.get_kernel_packages(kernel)
        candidate = CleanupCandidate(
            series_version=".".join(kernel["version"].split(".")[:2]),
            flavor=kernel["flavor"],
            kernel_version=kernel["version"],
            packages=packages,
        )
        series_list = get_installed_series(get_available_series())
        removals, reason = simulate_candidate(
            candidate,
            get_tracked_meta_packages(series_list),
        )
        if removals is None:
            self.show_message(
                Gtk.MessageType.WARNING,
                _("This kernel cannot be removed."),
                reason,
            )
            return

        dialog = Gtk.MessageDialog(
            transient_for=self.parent_window,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CANCEL,
            text=_("Remove kernel %s?") % kernel["version"],
        )
        dialog.format_secondary_text(
            _("The following packages will be removed:\n%s")
            % "\n".join(sorted(removals))
        )
        dialog.add_button(_("Remove"), Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self.start_package_transaction(sorted(removals), False)

    def on_cleanup_settings_changed(self, widget, parameter=None):
        settings = load_cleanup_settings()
        settings.enabled = self.cleanup_switch.get_active()
        settings.retain = self.retain_spin.get_value_as_int()
        save_cleanup_settings(settings)

    def on_cleanup_clicked(self, button):
        available_series = get_available_series()
        series_list = get_installed_series(available_series)
        candidates = get_cleanup_candidates(
            series_list,
            self.retain_spin.get_value_as_int(),
            os.uname().release,
            protected_kernels=load_cleanup_settings().protected_kernels,
        )
        if not candidates:
            self.show_message(
                Gtk.MessageType.INFO,
                _("There are no older kernels to remove."),
            )
            return

        tracked_meta_packages = get_tracked_meta_packages(series_list)
        removal_packages = {}
        for candidate in candidates:
            removals, _reason = simulate_candidate(
                candidate,
                tracked_meta_packages,
            )
            if removals is None:
                continue
            for package_name in sorted(removals):
                if package_name not in removal_packages:
                    removal_packages[package_name] = candidate.kernel_version
        if not removal_packages:
            self.show_message(
                Gtk.MessageType.INFO,
                _("There are no older kernels to remove."),
            )
            return

        kept_rows = []
        installed_packages = get_installed_versioned_kernel_packages()
        for package_name in sorted(installed_packages):
            if package_name in removal_packages:
                continue
            kernel_version = get_kernel_package_version(package_name)
            kept_rows.append((kernel_version, package_name))

        dialog = Gtk.MessageDialog(
            transient_for=self.parent_window,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CANCEL,
            text=_("Remove older kernels?"),
        )
        dialog.set_border_width(12)
        dialog.format_secondary_text(
            _(
                "The following packages will be removed. The active kernel "
                "and tracked metapackages are protected."
            )
        )

        removal_rows = []
        for package_name in sorted(removal_packages):
            kernel_version = removal_packages[package_name]
            removal_rows.append((kernel_version, package_name))

        content_area = dialog.get_content_area()
        self.add_package_table(
            content_area,
            _("Packages to keep"),
            kept_rows,
        )
        self.add_package_table(
            content_area,
            _("Packages to remove"),
            removal_rows,
        )

        dialog.add_button(_("Clean up"), Gtk.ResponseType.OK)
        dialog.show_all()
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return

        self.show_cleanup_output()

    def show_cleanup_output(self):
        self.cleanup_dialog = Gtk.Dialog(
            title=_("Cleaning up kernels"),
            transient_for=self.parent_window,
            modal=True,
        )
        self.cleanup_dialog.set_border_width(12)
        self.cleanup_dialog.set_default_size(600, 400)

        content_area = self.cleanup_dialog.get_content_area()
        content_area.set_spacing(12)
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(
            Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC,
        )
        scrolled_window.set_shadow_type(Gtk.ShadowType.IN)
        content_area.pack_start(scrolled_window, True, True, 0)

        self.cleanup_output = Gtk.TextView()
        self.cleanup_output.set_editable(False)
        self.cleanup_output.set_cursor_visible(False)
        self.cleanup_output.set_monospace(True)
        self.cleanup_output.set_left_margin(3)
        self.cleanup_output.set_right_margin(3)
        self.cleanup_output.get_buffer().set_text(
            _("Starting kernel cleanup...\n\n")
        )
        scrolled_window.add(self.cleanup_output)

        result_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
        )
        content_area.pack_start(result_box, False, False, 0)
        result_title = Gtk.Label(label=_("Result:"))
        result_box.pack_start(result_title, False, False, 0)
        self.cleanup_result = Gtk.Label()
        self.cleanup_result.set_markup("<b>%s</b>" % _("Processing..."))
        result_box.pack_start(self.cleanup_result, False, False, 0)

        self.cleanup_close_button = self.cleanup_dialog.add_button(
            _("Close"),
            Gtk.ResponseType.CLOSE,
        )
        self.cleanup_close_button.set_sensitive(False)
        self.cleanup_dialog.show_all()
        self.run_cleanup_with_output()
        self.cleanup_dialog.run()
        self.cleanup_dialog.destroy()
        self.cleanup_dialog = None
        self.load_series()

    @xt.run_async
    def run_cleanup_with_output(self):
        success = False
        try:
            record = run_cleanup(self.update_cleanup_output)
            if record["status"] == "completed":
                success = True
        except Exception as exception:
            self.update_cleanup_output("Error: %s\n" % exception)
        self.cleanup_finished(success)

    @xt.run_idle
    def update_cleanup_output(self, line):
        if self.cleanup_dialog is None:
            return
        buffer = self.cleanup_output.get_buffer()
        end_iter = buffer.get_end_iter()
        buffer.insert(end_iter, line)
        self.cleanup_output.scroll_to_iter(
            end_iter,
            0.0,
            False,
            0.0,
            1.0,
        )

    @xt.run_idle
    def cleanup_finished(self, success):
        if self.cleanup_dialog is None:
            return
        if success:
            self.cleanup_result.set_markup(
                "<span foreground='green'><b>%s</b></span>" % _("Success")
            )
        else:
            self.cleanup_result.set_markup(
                "<span foreground='red'><b>%s</b></span>" % _("Error")
            )
        self.cleanup_close_button.set_sensitive(True)

    def add_package_table(self, container, title, rows):
        label = Gtk.Label()
        label.set_markup("<b>%s</b>" % title)
        label.set_xalign(0)
        container.pack_start(label, False, False, 0)

        package_store = Gtk.ListStore(str, str)
        sorted_rows = sorted(rows, key=cmp_to_key(self.compare_package_rows))
        for kernel_version, package_name in sorted_rows:
            package_store.append((kernel_version, package_name))

        package_view = Gtk.TreeView(model=package_store)
        package_view.set_headers_visible(False)
        version_renderer = Gtk.CellRendererText()
        version_column = Gtk.TreeViewColumn(
            "",
            version_renderer,
            text=0,
        )
        package_view.append_column(version_column)
        package_renderer = Gtk.CellRendererText()
        package_column = Gtk.TreeViewColumn(
            "",
            package_renderer,
            text=1,
        )
        package_column.set_expand(True)
        package_view.append_column(package_column)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(
            Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC,
        )
        scrolled_window.set_shadow_type(Gtk.ShadowType.IN)
        scrolled_window.set_min_content_height(140)
        scrolled_window.set_min_content_width(550)
        scrolled_window.add(package_view)
        container.pack_start(scrolled_window, True, True, 0)

    def compare_package_rows(self, first, second):
        version_comparison = apt_pkg.version_compare(first[0], second[0])
        if version_comparison != 0:
            return -version_comparison
        if first[1] < second[1]:
            return -1
        if first[1] > second[1]:
            return 1
        return 0

    def on_history_clicked(self, button):
        history = read_cleanup_history()
        if not history:
            self.show_message(
                Gtk.MessageType.INFO,
                _("Kernel cleanup history"),
                _("No cleanup has run yet."),
            )
            return

        dialog = Gtk.Dialog(
            title=_("Kernel cleanup history"),
            transient_for=self.parent_window,
            modal=True,
        )
        dialog.set_border_width(12)
        dialog.set_default_size(650, 400)
        dialog.add_button(_("Close"), Gtk.ResponseType.CLOSE)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(
            Gtk.PolicyType.NEVER,
            Gtk.PolicyType.AUTOMATIC,
        )
        scrolled_window.set_shadow_type(Gtk.ShadowType.IN)
        dialog.get_content_area().pack_start(
            scrolled_window,
            True,
            True,
            0,
        )

        history_list = Gtk.ListBox()
        history_list.set_selection_mode(Gtk.SelectionMode.NONE)
        history_list.set_activate_on_single_click(True)
        history_list.set_header_func(self.add_row_separator)
        history_list.connect(
            "row-activated",
            self.on_history_row_activated,
        )
        scrolled_window.add(history_list)

        for entry in reversed(history):
            history_list.add(self.create_history_row(entry))

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def create_history_row(self, entry):
        row = Gtk.ListBoxRow()
        row.set_activatable(True)
        row.set_selectable(False)

        outer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row.add(outer_box)

        header_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
        )
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(10)
        header_box.set_margin_bottom(10)
        outer_box.pack_start(header_box, False, False, 0)

        date_label = Gtk.Label()
        date_label.set_markup(
            "<b>%s</b>" % self.format_history_date(entry.get("timestamp", ""))
        )
        date_label.set_xalign(0)
        header_box.pack_start(date_label, True, True, 0)

        removed_packages = set()
        removed = entry.get("removed", [])
        for item in removed:
            packages = item.get("packages", [])
            for package_name in packages:
                removed_packages.add(package_name)

        skipped = entry.get("skipped", [])
        summary = _("%d packages removed") % len(removed_packages)
        if skipped:
            summary += _(", %d skipped") % len(skipped)
        summary_label = Gtk.Label(label=summary)
        summary_label.get_style_context().add_class("dim-label")
        header_box.pack_end(summary_label, False, False, 0)

        row.revealer = Gtk.Revealer()
        row.revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        row.revealer.set_transition_duration(150)
        outer_box.pack_start(row.revealer, False, False, 0)

        details_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
        )
        details_box.set_margin_start(12)
        details_box.set_margin_end(12)
        details_box.set_margin_bottom(10)
        row.revealer.add(details_box)

        info_grid = Gtk.Grid()
        info_grid.set_column_spacing(12)
        info_grid.set_row_spacing(4)
        details_box.pack_start(info_grid, False, False, 0)
        self.add_history_info(
            info_grid,
            0,
            _("Active kernel:"),
            entry.get("running_kernel", _("Unknown")),
        )
        self.add_history_info(
            info_grid,
            1,
            _("Tracked series:"),
            self.format_tracked_series(entry),
        )
        self.add_history_info(
            info_grid,
            2,
            _("Versions kept per tracked series:"),
            str(entry.get("retain", _("Unknown"))),
        )

        package_rows = []
        for item in removed:
            kernel_version = item.get("version", "")
            packages = item.get("packages", [])
            for package_name in packages:
                package_rows.append((kernel_version, package_name))
        package_rows = sorted(
            package_rows,
            key=cmp_to_key(self.compare_package_rows),
        )

        package_store = Gtk.ListStore(str, str)
        for kernel_version, package_name in package_rows:
            package_store.append((kernel_version, package_name))

        package_view = Gtk.TreeView(model=package_store)
        version_renderer = Gtk.CellRendererText()
        version_column = Gtk.TreeViewColumn(
            _("Kernel version"),
            version_renderer,
            text=0,
        )
        package_view.append_column(version_column)
        package_renderer = Gtk.CellRendererText()
        package_column = Gtk.TreeViewColumn(
            _("Package name"),
            package_renderer,
            text=1,
        )
        package_column.set_expand(True)
        package_view.append_column(package_column)

        package_window = Gtk.ScrolledWindow()
        package_window.set_policy(
            Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC,
        )
        package_window.set_shadow_type(Gtk.ShadowType.IN)
        package_window.set_min_content_height(140)
        package_window.add(package_view)
        details_box.pack_start(package_window, True, True, 0)
        return row

    def add_history_info(self, grid, row, title, value):
        title_label = Gtk.Label()
        title_label.set_text(title)
        title_label.set_xalign(0)
        grid.attach(title_label, 0, row, 1, 1)
        value_label = Gtk.Label(label=value)
        value_label.set_xalign(0)
        value_label.set_line_wrap(True)
        value_label.set_selectable(True)
        grid.attach(value_label, 1, row, 1, 1)

    def format_tracked_series(self, entry):
        if "tracked_series" not in entry:
            return _("Unknown")
        tracked_series = entry["tracked_series"]
        if not tracked_series:
            return _("None")

        labels = []
        for series in tracked_series:
            label = series.get("version", "")
            flavor = series.get("flavor", "")
            if flavor:
                if flavor != "generic":
                    label += " (%s)" % flavor.upper()
            track = series.get("track", "")
            if track == "ga":
                label += " " + _("LTS")
            elif track:
                label += " " + track.upper()
            if series.get("edge", False):
                label += " " + _("Edge")
            if series.get("manually_tracked", False):
                label += " " + _("(manually tracked)")
            labels.append(label)
        return ", ".join(labels)

    def format_history_date(self, timestamp):
        try:
            date = datetime.fromisoformat(timestamp)
            date = date.astimezone()
            return date.strftime("%A %d %B %Y, %H:%M")
        except (TypeError, ValueError):
            return _("Unknown date")

    def on_history_row_activated(self, listbox, row):
        revealed = row.revealer.get_reveal_child()
        row.revealer.set_reveal_child(not revealed)

    def show_message(self, message_type, text, secondary_text=""):
        dialog = Gtk.MessageDialog(
            transient_for=self.parent_window,
            modal=True,
            message_type=message_type,
            buttons=Gtk.ButtonsType.CLOSE,
            text=text,
        )
        if secondary_text:
            dialog.format_secondary_text(secondary_text)
        dialog.run()
        dialog.destroy()

    def set_window_sensitive(self, sensitive):
        if self.parent_window is not None:
            self.parent_window.set_sensitive(sensitive)
        else:
            self.builder.get_object("page_kernels").set_sensitive(sensitive)

    def load_series(self):
        for child in self.listbox.get_children():
            child.destroy()

        available_series = get_available_series()
        series_list = get_installed_series(available_series)
        has_tracked_series = False
        for series in series_list:
            if series.tracked:
                has_tracked_series = True
            self.listbox.add(self.create_series_row(series))

        if has_tracked_series:
            self.tracking_warning.hide()
        else:
            self.tracking_warning.show()
        self.listbox.show_all()

    def create_series_row(self, series):
        row = Gtk.ListBoxRow()
        row.set_activatable(True)
        row.set_selectable(False)
        if series.tracked:
            style_context = row.get_style_context()
            style_context.add_class("tracked-kernel-series")
            style_context.add_provider(
                self.tracked_row_css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        outer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row.add(outer_box)

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(10)
        header_box.set_margin_bottom(10)
        outer_box.pack_start(header_box, False, False, 0)

        if series.track == "ga":
            track = _("LTS")
        else:
            track = series.track.upper()
        if series.edge:
            track += " " + _("Edge")

        heading = Gtk.Label()
        heading.set_xalign(0)
        if track:
            heading.set_markup(f"<b>{series.name} {track}</b>")
        else:
            heading.set_markup(f"<b>{series.name}</b>")
        header_box.pack_start(heading, True, True, 0)

        hint = ""
        description = ""
        if series.track == "ga":
            hint = _("Long Term Support")
            description = _("This is the most stable series. Recommended for most users.")
        elif series.track == "hwe":
            if not series.edge:
                hint = _("Hardware Enablement")
                description = _("This series is recommended for newer hardware.")
            else:
                description = _("This series sometimes provides newer kernels than the HWE series but without support for proprietary drivers.")
        else:
            description = _("This series is not officially supported.")
        if hint:
            hint_label = Gtk.Label()
            hint_label.set_markup("<i>%s</i>" % hint)
            hint_label.get_style_context().add_class("dim-label")
            header_box.set_center_widget(hint_label)

        if series.tracked:
            tracked_label = Gtk.Label()
            if series.manually_tracked:
                tracked_text = _("Manually tracked")
            else:
                tracked_text = _("Tracked")
            tracked_label.set_markup("<b>%s</b>" % tracked_text)
            tracked_label.set_xalign(1)
            header_box.pack_end(tracked_label, False, False, 0)

        row.revealer = Gtk.Revealer()
        row.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        row.revealer.set_transition_duration(150)
        outer_box.pack_start(row.revealer, False, False, 0)

        details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        details_box.set_margin_start(20)
        details_box.set_margin_end(20)
        details_box.set_margin_bottom(10)
        row.revealer.add(details_box)

        meta_label = Gtk.Label(label=description)
        meta_label.set_xalign(0)
        meta_label.set_line_wrap(True)
        meta_label.get_style_context().add_class("dim-label")
        details_box.pack_start(meta_label, False, False, 0)

        installed_versions = sorted(
            series.installed_versions,
            key=cmp_to_key(apt_pkg.version_compare),
        )
        installed_text = _("Installed kernels: %d") % len(installed_versions)
        installed_label = Gtk.Label(label=installed_text)
        installed_label.set_xalign(0)
        installed_label.set_line_wrap(True)
        installed_label.set_selectable(True)
        details_box.pack_start(installed_label, False, False, 0)

        if series.meta_packages:
            button_box = Gtk.ButtonBox(orientation=Gtk.Orientation.HORIZONTAL)
            button_box.set_layout(Gtk.ButtonBoxStyle.END)
            button_box.set_margin_top(6)
            details_box.pack_start(button_box, False, False, 0)

            if series.tracked:
                action_button = Gtk.Button(label=_("Untrack"))
                action_button.get_style_context().add_class("destructive-action")
                action_button.connect("clicked", self.on_untrack_clicked, series)
            else:
                action_button = Gtk.Button(label=_("Track"))
                action_button.get_style_context().add_class("suggested-action")
                action_button.connect("clicked", self.on_track_clicked, series)
            button_box.add(action_button)
        else:
            button_box = Gtk.ButtonBox(orientation=Gtk.Orientation.HORIZONTAL)
            button_box.set_layout(Gtk.ButtonBoxStyle.END)
            button_box.set_margin_top(6)
            details_box.pack_start(button_box, False, False, 0)

            if series.manually_tracked:
                action_button = Gtk.Button(label=_("Mark as untracked"))
                action_button.connect(
                    "clicked",
                    self.on_mark_untracked_clicked,
                    series,
                )
            else:
                action_button = Gtk.Button(label=_("Mark as tracked"))
                action_button.connect(
                    "clicked",
                    self.on_mark_tracked_clicked,
                    series,
                )
            button_box.add(action_button)

        return row

    def on_row_activated(self, listbox, row):
        revealed = row.revealer.get_reveal_child()
        row.revealer.set_reveal_child(not revealed)

    def add_row_separator(self, row, previous_row):
        if previous_row is not None:
            if row.get_header() is None:
                separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                row.set_header(separator)

    def on_track_clicked(self, button, series):
        button.set_sensitive(False)
        packages = sorted(series.meta_packages)
        self.start_package_transaction(packages, True)

    def on_untrack_clicked(self, button, series):
        dialog = Gtk.MessageDialog(
            transient_for=self.parent_window,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CANCEL,
            text=_("Stop tracking the %s series?") % series.name,
        )
        dialog.format_secondary_text(
            _(
                "Without tracking, this computer will no longer receive "
                "security updates for this kernel series."
            )
        )
        dialog.add_button(_("Untrack"), Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return

        button.set_sensitive(False)
        packages = sorted(series.meta_packages)
        self.start_package_transaction(packages, False)

    def on_mark_tracked_clicked(self, button, series):
        dialog = Gtk.MessageDialog(
            transient_for=self.parent_window,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CANCEL,
            text=_("Mark the %s series as tracked?") % series.name,
        )
        dialog.format_secondary_text(
            _(
                "This series will be considered manually tracked. It is your "
                "responsibility to ensure that an appropriate metapackage is "
                "installed or that kernel updates are handled another way."
            )
        )
        dialog.add_button(_("Mark as tracked"), Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return

        set_series_manually_tracked(series, True)
        self.load_series()

    def on_mark_untracked_clicked(self, button, series):
        set_series_manually_tracked(series, False)
        self.load_series()

    def start_package_transaction(self, packages, install):
        self.set_window_sensitive(False)

        self.apt_client = aptkit.simpleclient.SimpleAPTClient(self.parent_window)
        self.apt_client.set_finished_callback(self.on_transaction_finished)
        self.apt_client.set_cancelled_callback(self.on_transaction_finished)
        if install:
            self.apt_client.install_packages(packages)
        else:
            self.apt_client.remove_packages(packages)

    def on_transaction_finished(self, transaction=None, exit_state=None):
        self.set_window_sensitive(True)
        self.load_series()


def main():
    builder = Gtk.Builder()
    builder.set_translation_domain("mintsysadm")
    builder.add_from_file("/usr/share/mintsysadm/mintsysadm.ui")
    window = builder.get_object("main_window")
    KernelsWidget(window, builder)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
