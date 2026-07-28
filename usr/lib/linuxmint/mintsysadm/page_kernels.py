#!/usr/bin/python3

from datetime import datetime
from functools import cmp_to_key
import gi
import os
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

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
    CleanupSettings,
    get_cleanup_candidates,
    get_installed_versioned_kernel_packages,
    get_kernel_package_version,
    get_tracked_meta_packages,
    load_cleanup_settings,
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

    def on_cleanup_settings_changed(self, widget, parameter=None):
        settings = CleanupSettings(
            enabled=self.cleanup_switch.get_active(),
            retain=self.retain_spin.get_value_as_int(),
        )
        save_cleanup_settings(settings)

    def on_cleanup_clicked(self, button):
        available_series = get_available_series()
        series_list = get_installed_series(available_series)
        candidates = get_cleanup_candidates(
            series_list,
            self.retain_spin.get_value_as_int(),
            os.uname().release,
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
