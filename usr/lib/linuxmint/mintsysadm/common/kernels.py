#!/usr/bin/python3

"""Discover installed kernel image and header packages."""

from collections import defaultdict
from dataclasses import dataclass, field
import os
from pathlib import Path
import re

import apt


IS_LMDE = os.path.exists("/usr/share/doc/debian-system-adjustments/copyright")
MANUALLY_TRACKED_SERIES_FILE = "/etc/linuxmint/mintsysadm/kernels.conf"

KERNEL_PACKAGE_RE = re.compile(
    r"^linux-(?P<kind>image|headers)-"
    r"(?:unsigned-)?"
    r"(?P<version>\d+\.\d+\.\d+(?:-\d+|\+deb\d+))"
    r"(?:-(?P<flavor>[^:]+))?"
    r"(?::[^:]+)?$"
)

HWE_META_RE = re.compile(
    r"^linux-generic-hwe-(?P<release>\d+\.\d+)(?P<edge>-edge)?$"
)


@dataclass
class Series:
    name: str
    version: str
    flavor: str
    installed_packages: set[str] = field(default_factory=set)
    installed_versions: set[str] = field(default_factory=set)
    track: str = ""
    edge: bool = False
    tracked: bool = False
    manually_tracked: bool = False
    meta_packages: set[str] = field(default_factory=set)


def get_installed_kernel_or_header_packages():
    """Return installed, versioned kernel image and header packages."""
    pkgs = []
    cache = apt.Cache()
    for name in cache.keys():
        package = cache[name]
        if not package.is_installed:
            continue
        match = KERNEL_PACKAGE_RE.fullmatch(name)
        if match is None:
            continue
        pkgs.append(name)
    return sorted(pkgs)


def get_installed_series(series_list=None):
    """Attach installed kernel/header packages to available Series objects."""
    if series_list is None:
        series_list = get_available_series()

    packages = get_installed_kernel_or_header_packages()
    flavored_packages = defaultdict(set)
    common_headers = defaultdict(set)

    for package_name in packages:
        match = KERNEL_PACKAGE_RE.fullmatch(package_name)
        version = match.group("version")
        series_version = ".".join(version.split(".")[:2])
        flavor = match.group("flavor")
        kind = match.group("kind")

        is_common_header = False
        if flavor is None:
            is_common_header = True
        elif kind == "headers":
            if flavor == "common":
                is_common_header = True

        if is_common_header:
            common_headers[(series_version, version)].add(package_name)
        else:
            flavored_packages[(series_version, flavor, version)].add(package_name)

    # Flavor-independent headers belong to every matching flavored package set.
    for (series_version, version), header_packages in common_headers.items():
        matching_keys = []
        for key in flavored_packages:
            if key[0] != series_version:
                continue
            if key[2] != version:
                continue
            matching_keys.append(key)

        if matching_keys:
            for key in matching_keys:
                flavored_packages[key].update(header_packages)
        else:
            flavored_packages[(series_version, "", version)].update(header_packages)

    packages_by_series = defaultdict(set)
    versions_by_series = defaultdict(set)
    for (series_version, flavor, kernel_version), package_names in flavored_packages.items():
        packages_by_series[(series_version, flavor)].update(package_names)
        versions_by_series[(series_version, flavor)].add(kernel_version)

    def version_key(value):
        key = []
        for part in value.split("."):
            key.append(int(part))
        return tuple(key)

    def installed_series_key(item):
        series_version = item[0][0]
        flavor = item[0][1]
        return (version_key(series_version), flavor)

    for (series_version, flavor), package_names in sorted(
        packages_by_series.items(),
        key=installed_series_key,
    ):
        name = series_version
        if flavor:
            if flavor != "generic":
                name = f"{series_version} {flavor.upper()}"

        series_flavor = flavor
        if not series_flavor:
            series_flavor = "unknown"

        matching_series = []
        for series in series_list:
            if series.version != series_version:
                continue
            if series.flavor != series_flavor:
                continue
            matching_series.append(series)

        if matching_series:
            for series in matching_series:
                series.installed_packages.update(package_names)
                series.installed_versions.update(
                    versions_by_series[(series_version, flavor)]
                )
        else:
            series = Series(
                name=name,
                version=series_version,
                flavor=series_flavor,
                installed_packages=set(package_names),
                installed_versions=set(
                    versions_by_series[(series_version, flavor)]
                ),
            )
            series_list.append(series)

    manually_tracked_series = get_manually_tracked_series()
    for series in series_list:
        if series.meta_packages:
            continue
        identifier = get_series_identifier(series)
        if identifier in manually_tracked_series:
            series.tracked = True
            series.manually_tracked = True

    return sorted(series_list, key=version_key_for_series)


def get_series_identifier(series):
    return "%s:%s" % (series.version, series.flavor)


def get_manually_tracked_series():
    tracked_series = set()
    try:
        content = Path(MANUALLY_TRACKED_SERIES_FILE).read_text(encoding="utf-8")
    except OSError:
        return tracked_series

    for line in content.splitlines():
        identifier = line.strip()
        if identifier:
            tracked_series.add(identifier)
    return tracked_series


def set_series_manually_tracked(series, tracked):
    tracked_series = get_manually_tracked_series()
    identifier = get_series_identifier(series)
    if tracked:
        tracked_series.add(identifier)
    else:
        tracked_series.discard(identifier)

    path = Path(MANUALLY_TRACKED_SERIES_FILE)
    if tracked_series:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(sorted(tracked_series)) + "\n"
        path.write_text(content, encoding="utf-8")
    elif path.exists():
        path.unlink()


def get_ubuntu_base_release():
    """Return the Ubuntu base release recorded on the filesystem."""
    text = Path("/etc/upstream-release/lsb-release").read_text(encoding="utf-8")
    match = re.search(r"^DISTRIB_RELEASE=(.+)$", text, re.MULTILINE)
    if match is None:
        return ""
    return match.group(1).strip('"')


def get_available_series():
    """Return series advertised by Ubuntu or Debian kernel metapackages."""
    cache = apt.Cache()
    ubuntu_release = ""
    if not IS_LMDE:
        ubuntu_release = get_ubuntu_base_release()
    available = {}

    meta_package_names = []
    for name in cache.keys():
        is_meta = False
        if IS_LMDE:
            if name == "linux-image-amd64":
                is_meta = True
            elif name == "linux-headers-amd64":
                is_meta = True
        else:
            if name == "linux-generic":
                is_meta = True
            else:
                hwe_match = HWE_META_RE.fullmatch(name)
                if hwe_match:
                    if hwe_match.group("release") == ubuntu_release:
                        is_meta = True

        if is_meta:
            meta_package_names.append(name)

    for package_name in meta_package_names:
        candidate = cache[package_name].candidate
        if candidate is None:
            continue

        version_match = re.match(r"(\d+\.\d+)", candidate.version)
        if version_match is None:
            continue
        version = version_match.group(1)

        if package_name.startswith("linux-generic"):
            flavor = "generic"
            if package_name == "linux-generic":
                track = "ga"
                edge = False
            else:
                hwe_match = HWE_META_RE.fullmatch(package_name)
                track = "hwe"
                edge = hwe_match.group("edge") is not None
        else:
            flavor = "amd64"
            track = "ga"
            edge = False

        key = (version, flavor, track, edge)
        if key not in available:
            label = version
            if flavor != "generic":
                label += f" ({flavor})"
            available[key] = Series(
                name=label,
                version=version,
                flavor=flavor,
                track=track,
                edge=edge,
            )

        series = available[key]
        series.meta_packages.add(package_name)

    for series in available.values():
        series.tracked = True
        for package_name in series.meta_packages:
            if not cache[package_name].is_installed:
                series.tracked = False
                break

    return sorted(available.values(), key=version_key_for_series)


def version_key_for_series(series):
    version_parts = []
    for part in series.version.split("."):
        version_parts.append(int(part))

    return (
        -version_parts[0],
        -version_parts[1],
        series.name,
    )


def format_available_series(series):
    if series.track == "ga":
        track = "LTS"
    elif series.track:
        track = series.track.upper()
    else:
        track = ""
    if series.edge:
        track += " EDGE"
    packages = sorted(series.meta_packages)
    text = series.version
    if track:
        text += f" - {track}"
    if packages:
        text += f" - {', '.join(packages)}"
    return text


def print_series(series):
    print(format_available_series(series))
    print("  Installed packages:")
    if series.installed_packages:
        for package in sorted(series.installed_packages):
            print(f"    {package}")
    else:
        print("    None")


if __name__ == "__main__":
    available_series = get_available_series()
    all_series = get_installed_series(available_series)
    for series in all_series:
        print_series(series)
