#!/usr/bin/python3
import gi
import xapp.util

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GObject

_ = xapp.util.l10n("mintsysadm")

# An entry that can be switched between label and entry modes
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

# A table with dimmed labels
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

