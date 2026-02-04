#!/usr/bin/python3
import cairo
import gi
import math
import os
import random
import xapp.util
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf

_ = xapp.util.l10n("mintsysadm")

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
        if original_width != scaled_size or original_height != scaled_size:
            pixbuf = pixbuf.scale_simple(scaled_size, scaled_size, GdkPixbuf.InterpType.BILINEAR)

        # Ensure the pixbuf is square
        actual_size = min(pixbuf.get_width(), pixbuf.get_height())
        if pixbuf.get_width() != pixbuf.get_height():
            pixbuf = pixbuf.scale_simple(actual_size, actual_size, GdkPixbuf.InterpType.BILINEAR)

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