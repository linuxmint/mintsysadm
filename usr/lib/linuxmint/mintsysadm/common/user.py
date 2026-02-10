#!/usr/bin/python3
import cairo
import gi
import math
import os
import random
import tempfile
import xapp.util
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf
from PIL import Image, ImageOps

_ = xapp.util.l10n("mintsysadm")

ICON_SIZE_DIALOG_PREVIEW = 128

def browse_avatar_dialog():
    """Show a file chooser dialog for browsing avatar images.
    Returns the selected file path or None if cancelled."""
    dialog = Gtk.FileChooserDialog(None, None, Gtk.FileChooserAction.OPEN, 
                                    (_("Cancel"), Gtk.ResponseType.CANCEL, 
                                     _("Open"), Gtk.ResponseType.OK))
    filter = Gtk.FileFilter()
    filter.set_name(_("Images"))
    filter.add_mime_type("image/png")
    filter.add_mime_type("image/jpeg")
    filter.add_mime_type("image/jpg")
    filter.add_mime_type("image/gif")
    filter.add_mime_type("image/bmp")
    filter.add_mime_type("image/tiff")
    filter.add_mime_type("image/webp")
    dialog.add_filter(filter)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    preview = Gtk.Image(visible=True)

    box.pack_start(preview, False, False, 0)
    dialog.set_preview_widget(box)
    dialog.set_preview_widget_active(True)
    dialog.set_use_preview_label(False)

    box.set_margin_start(24)
    box.set_margin_end(24)
    box.set_margin_top(24)
    box.set_margin_bottom(24)
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)
    box.set_size_request(ICON_SIZE_DIALOG_PREVIEW, -1)

    def update_preview_cb(dialog, preview):
        # Different widths make the dialog look really crappy as it resizes -
        # constrain the width and adjust the height to keep perspective.
        filename = dialog.get_preview_filename()
        if filename is not None:
            if os.path.isfile(filename):
                try:
                    set_image_from_avatar(preview, filename, ICON_SIZE_DIALOG_PREVIEW)
                    return
                except:
                    print(f"Unable to generate preview for file '{filename}'")
        preview.clear()

    dialog.connect("update-preview", update_preview_cb, preview)

    response = dialog.run()
    path = dialog.get_filename() if response == Gtk.ResponseType.OK else None
    dialog.destroy()
    return path

# Make a circular pixbuf and set the image with it
# use a a symbolic avatar icon and fallback size if it fails
def set_image_from_avatar(image, path, size, fallback_size=Gtk.IconSize.DIALOG):
    if path == "" or not os.path.exists(path):
        image.set_from_icon_name("xsi-avatar-default-symbolic", fallback_size)
        image.set_pixel_size(size)
        return
    scale = image.get_scale_factor()
    scaled_size = size * scale
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        pixbuf = pixbuf.apply_embedded_orientation()
        original_width = pixbuf.get_width()
        original_height = pixbuf.get_height()

        # Scale without distortion: cover the square then center-crop.
        if original_width != scaled_size or original_height != scaled_size:
            scale_factor = scaled_size / min(original_width, original_height)
            scaled_width = max(scaled_size, int(round(original_width * scale_factor)))
            scaled_height = max(scaled_size, int(round(original_height * scale_factor)))
            pixbuf = pixbuf.scale_simple(scaled_width, scaled_height, GdkPixbuf.InterpType.BILINEAR)

        if pixbuf.get_width() != pixbuf.get_height():
            offset_x = max(0, (pixbuf.get_width() - scaled_size) // 2)
            offset_y = max(0, (pixbuf.get_height() - scaled_size) // 2)
            pixbuf = pixbuf.new_subpixbuf(offset_x, offset_y, scaled_size, scaled_size)

        if pixbuf.get_width() != scaled_size or pixbuf.get_height() != scaled_size:
            pixbuf = pixbuf.scale_simple(scaled_size, scaled_size, GdkPixbuf.InterpType.BILINEAR)

        # Create a surface at the scaled size (physical pixels)
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        # Set device scale so cairo works in logical coordinates
        surface.set_device_scale(scale, scale)

        ctx = cairo.Context(surface)
        # Draw circular clipping path using logical coordinates (size, not scaled_size)
        radius = size // 2
        ctx.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
        ctx.arc(size - radius, radius, radius, 3 * math.pi / 2, 0)
        ctx.arc(size - radius, size - radius, radius, 0, math.pi / 2)
        ctx.arc(radius, size - radius, radius, math.pi / 2, math.pi)
        ctx.close_path()
        ctx.clip()
        # Scale down the pixbuf to logical size when drawing
        ctx.scale(1.0 / scale, 1.0 / scale)
        Gdk.cairo_set_source_pixbuf(ctx, pixbuf, 0, 0)
        ctx.paint()

        image.set_from_surface(surface)
    except Exception as e:
        image.set_from_icon_name("xsi-avatar-default-symbolic", fallback_size)
        image.set_pixel_size(size)

def set_avatar(user, path, image, size, fallback_size=Gtk.IconSize.DIALOG):
    print(f"Setting avatar '{path}' for user '{user.get_user_name()}'")
    user.set_icon_file(path)
    user.connect("changed", on_ac_user_changed, image, size, fallback_size)

def set_avatar_from_browsed_path(user, path, image, size, fallback_size=Gtk.IconSize.DIALOG):
    pil_image = Image.open(path)
    pil_image = ImageOps.exif_transpose(pil_image)
    # Preserve transparency when possible
    if pil_image.mode not in ("RGB", "RGBA"):
        print(f"Converting image from mode {pil_image.mode} to RGB")
        pil_image = pil_image.convert("RGBA" if "A" in pil_image.getbands() else "RGB")
    print(f"Selected image size: {pil_image.size}, mode: {pil_image.mode}")
    pil_image = ImageOps.fit(pil_image, (512, 512), method=Image.LANCZOS, centering=(0.5, 0.5))
    print(f"Resized image size: {pil_image.size}, mode: {pil_image.mode}")
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.png', delete=True) as temp_file:
        temp_path = temp_file.name
        pil_image.save(temp_path, "png")
        set_avatar(user, temp_path, image, size, fallback_size=fallback_size)

def on_ac_user_changed(user, image, size, fallback_size):
    path = user.get_icon_file()
    print(f"  --> New avatar path: '{path}'")
    set_image_from_avatar(image, path, size, fallback_size)

def generate_password():
    characters = "!@#$%^&*()_-+{}|:<>?=0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    newpass = ""
    for i in range(14):
        index = random.randint(0, len(characters) - 1)
        newpass = newpass + characters[index]
    return newpass

# Based on setPasswordStrength() in Mozilla Seamonkey, which is tri-licensed under MPL 1.1, GPL 2.0, and LGPL 2.1.
# Forked from Ubiquity validation.py
def get_password_strength(password):
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
    length = min(len(password), 4)
    digit = min(digit, 3)
    upper = min(upper, 3)
    symbol = min(symbol, 3)
    strength = (
        ((length * 0.1) - 0.2) +
        (digit * 0.1) +
        (symbol * 0.15) +
        (upper * 0.1))
    if strength > 1:
        strength = 1
    if strength < 0:
        strength = 0

    if len(password) < 8:
        text = _("Too short")
        fraction = 0.0
    elif strength < 0.5:
        text = _("Weak")
        fraction = 0.2
    elif strength < 0.75:
        text = _("Fair")
        fraction = 0.4
    elif strength < 0.9:
        text = _("Good")
        fraction = 0.6
    else:
        text = _("Strong")
        fraction = 1.0
    return text, fraction