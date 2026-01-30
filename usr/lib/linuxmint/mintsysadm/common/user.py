#!/usr/bin/python3
import subprocess
import cairo
import gi
import math
import os
import pwd
import random
import xapp.util

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf
from common.widgets import DimmedTable

_ = xapp.util.l10n("mintsysadm")

# A helper to drop privileges. Necessary for
# security when accessing/creating user controlled files as root.
class PrivHelper():

    def __init__(self):
        self.orig_uid = os.getuid()
        self.orig_gid = os.getgid()
        self.orig_groups = os.getgroups()

    def drop_privs(self, user):
        uid = user.get_uid()
        # the user's main group id
        gid = pwd.getpwuid(uid).pw_gid

        # initialize the user's supplemental groups and main group
        os.initgroups(user.get_user_name(), gid)
        os.setegid(gid)
        os.seteuid(uid)

    def restore_privs(self):
        os.seteuid(self.orig_uid)
        os.setegid(self.orig_gid)
        os.setgroups(self.orig_groups)

# Create a circular pixbuf from an image path
# throws an exeption if the pixbuf cannot be created
def get_circular_pixbuf_from_path(path, size):
	pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
	size = min(pixbuf.get_width(), pixbuf.get_height())
	if pixbuf.get_width() != pixbuf.get_height():
		pixbuf = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
	radius = size // 2
	width = pixbuf.get_width()
	height = pixbuf.get_height()
	surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
	ctx = cairo.Context(surface)
	ctx.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
	ctx.arc(width - radius, radius, radius, 3 * math.pi / 2, 0)
	ctx.arc(width - radius, height - radius, radius, 0, math.pi / 2)
	ctx.arc(radius, height - radius, radius, math.pi / 2, math.pi)
	ctx.close_path()
	ctx.clip()
	Gdk.cairo_set_source_pixbuf(ctx, pixbuf, 0, 0)
	ctx.paint()
	return Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)

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