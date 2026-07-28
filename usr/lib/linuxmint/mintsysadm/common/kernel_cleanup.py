#!/usr/bin/python3

"""Plan and perform conservative cleanup of installed kernel packages."""

import configparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cmp_to_key
import json
import os
from pathlib import Path
import re

import apt
import apt_pkg
from apt.progress.base import InstallProgress

from common.kernels import get_available_series, get_installed_series


CLEANUP_CONFIG_FILE = "/etc/linuxmint/mintsysadm/cleanup.conf"
CLEANUP_HISTORY_FILE = (
    "/var/lib/linuxmint/mintsysadm/kernel-cleanup-history.jsonl"
)
DEFAULT_RETAIN_COUNT = 2
HISTORY_LIMIT = 20

VERSIONED_KERNEL_PACKAGE_RE = re.compile(
    r"^linux-(?:"
    r"image(?:-unsigned)?|modules(?:-extra)?|headers|tools|cloud-tools|buildinfo|"
    r"main-modules-[a-z0-9][a-z0-9+.-]*"
    r")-"
    r"(?P<version>\d+\.\d+\.\d+(?:-\d+|\+deb\d+))"
    r"(?:-(?P<flavor>[^:]+))?"
    r"(?::[^:]+)?$"
)


@dataclass
class CleanupSettings:
    enabled: bool = True
    retain: int = DEFAULT_RETAIN_COUNT
    protected_kernels: set[str] = field(default_factory=set)


@dataclass
class CleanupCandidate:
    series_version: str
    flavor: str
    kernel_version: str
    packages: set[str] = field(default_factory=set)


class CleanupInstallProgress(InstallProgress):

    def __init__(self, callback):
        InstallProgress.__init__(self)
        self.callback = callback

    def report(self, message):
        if self.callback is not None:
            self.callback(message)

    def error(self, package, message):
        self.report("Error for %s: %s\n" % (package, message))

    def status_change(self, package, percent, status):
        self.report("%s\n" % status)

    def dpkg_status_change(self, package, status):
        self.report("%s: %s\n" % (package, status))

    def processing(self, package, stage):
        self.report("%s %s\n" % (stage.capitalize(), package))


def load_cleanup_settings(path=CLEANUP_CONFIG_FILE):
    settings = CleanupSettings()
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if not parser.has_section("Cleanup"):
        return settings

    settings.enabled = parser.getboolean(
        "Cleanup",
        "Enabled",
        fallback=True,
    )
    settings.retain = parser.getint(
        "Cleanup",
        "Retain",
        fallback=DEFAULT_RETAIN_COUNT,
    )
    if settings.retain < 1:
        settings.retain = 1
    settings.protected_kernels = set(
        parser.get(
            "Cleanup",
            "ProtectedKernels",
            fallback="",
        ).split()
    )
    return settings


def save_cleanup_settings(settings, path=CLEANUP_CONFIG_FILE):
    parser = configparser.ConfigParser()
    parser.add_section("Cleanup")
    parser.set("Cleanup", "Enabled", str(settings.enabled).lower())
    parser.set("Cleanup", "Retain", str(max(1, settings.retain)))
    parser.set(
        "Cleanup",
        "ProtectedKernels",
        " ".join(sorted(settings.protected_kernels)),
    )

    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as config_file:
        parser.write(config_file)


def get_installed_versioned_kernel_packages(cache=None):
    packages = []
    if cache is None:
        cache = apt.Cache()
    for package_name in cache.keys():
        package = cache[package_name]
        if not package.is_installed:
            continue
        match = VERSIONED_KERNEL_PACKAGE_RE.fullmatch(package_name)
        if match is None:
            continue
        packages.append(package_name)
    return packages


def prune_protected_kernels(settings, installed_packages=None):
    """Discard protections for kernels whose image is no longer installed."""
    if installed_packages is None:
        installed_packages = get_installed_versioned_kernel_packages()

    installed_kernels = set()
    for package_name in installed_packages:
        if not package_name.startswith((
            "linux-image-",
            "linux-image-unsigned-",
        )):
            continue
        match = VERSIONED_KERNEL_PACKAGE_RE.fullmatch(package_name)
        if match is None:
            continue
        identifier = match.group("version")
        flavor = match.group("flavor")
        if flavor:
            identifier += "-" + flavor
        installed_kernels.add(identifier)

    protected_kernels = settings.protected_kernels.intersection(
        installed_kernels
    )
    if protected_kernels == settings.protected_kernels:
        return False
    settings.protected_kernels = protected_kernels
    return True


def get_kernel_package_version(package_name):
    match = VERSIONED_KERNEL_PACKAGE_RE.fullmatch(package_name)
    if match is None:
        return ""
    return match.group("version")


def _is_running_kernel(kernel_version, flavor, running_release):
    expected_release = kernel_version
    if flavor:
        if flavor != "unknown":
            expected_release += "-" + flavor
    return expected_release == running_release


def _get_series_states(series_list):
    states = {}
    for series in series_list:
        key = (series.version, series.flavor)
        if key not in states:
            states[key] = {
                "tracked": False,
                "versions": set(),
            }
        state = states[key]
        if series.tracked:
            state["tracked"] = True
        state["versions"].update(series.installed_versions)
    return states


def _get_packages_for_candidate(
    installed_packages,
    kernel_version,
    flavor,
    protected_versions,
):
    packages = set()
    for package_name in installed_packages:
        match = VERSIONED_KERNEL_PACKAGE_RE.fullmatch(package_name)
        if match is None:
            continue
        if match.group("version") != kernel_version:
            continue

        package_flavor = match.group("flavor")
        if package_flavor is None:
            if kernel_version not in protected_versions:
                packages.add(package_name)
            continue
        if package_flavor == "common":
            if kernel_version not in protected_versions:
                packages.add(package_name)
            continue
        if package_flavor == flavor:
            packages.add(package_name)
    return packages


def get_cleanup_candidates(
    series_list,
    retain,
    running_release,
    installed_packages=None,
    protected_kernels=None,
):
    """Return installed versions that may be removed, without changing APT."""
    if installed_packages is None:
        installed_packages = get_installed_versioned_kernel_packages()
    if retain < 1:
        retain = 1
    if protected_kernels is None:
        protected_kernels = set()

    states = _get_series_states(series_list)
    removable_versions = []
    protected_versions = set()

    for key in states:
        state = states[key]
        versions = list(state["versions"])
        versions.sort(
            key=cmp_to_key(apt_pkg.version_compare),
            reverse=True,
        )

        kept = set()
        if state["tracked"]:
            index = 0
            for kernel_version in versions:
                if index >= retain:
                    break
                kept.add(kernel_version)
                index += 1

        for kernel_version in versions:
            kernel_identifier = kernel_version
            if key[1]:
                kernel_identifier += "-" + key[1]
            if kernel_identifier in protected_kernels:
                protected_versions.add(kernel_version)
                continue
            if kernel_version in kept:
                protected_versions.add(kernel_version)
                continue
            if _is_running_kernel(
                kernel_version,
                key[1],
                running_release,
            ):
                protected_versions.add(kernel_version)
                continue
            removable_versions.append(
                (key[0], key[1], kernel_version)
            )

    candidates = []
    for series_version, flavor, kernel_version in removable_versions:
        packages = _get_packages_for_candidate(
            installed_packages,
            kernel_version,
            flavor,
            protected_versions,
        )
        if not packages:
            continue
        candidates.append(
            CleanupCandidate(
                series_version=series_version,
                flavor=flavor,
                kernel_version=kernel_version,
                packages=packages,
            )
        )
    return candidates


def get_tracked_meta_packages(series_list):
    meta_packages = set()
    for series in series_list:
        if not series.tracked:
            continue
        meta_packages.update(series.meta_packages)
    return meta_packages


def get_tracked_series_history(series_list):
    tracked_series = []
    identifiers = set()
    for series in series_list:
        if not series.tracked:
            continue
        identifier = (
            series.version,
            series.flavor,
            series.track,
            series.edge,
        )
        if identifier in identifiers:
            continue
        identifiers.add(identifier)
        tracked_series.append(
            {
                "version": series.version,
                "flavor": series.flavor,
                "track": series.track,
                "edge": series.edge,
                "manually_tracked": series.manually_tracked,
            }
        )
    return tracked_series


def prepare_candidate(cache, candidate, tracked_meta_packages):
    """Mark and verify one candidate in an APT cache."""
    for package_name in candidate.packages:
        if package_name not in cache:
            continue
        package = cache[package_name]
        if package.is_installed:
            package.mark_delete(auto_fix=True, purge=True)

    removals = set()
    for package in cache.get_changes():
        if not package.marked_delete:
            continue
        removals.add(package.name)

    endangered_metas = removals.intersection(tracked_meta_packages)
    if endangered_metas:
        packages = sorted(endangered_metas)
        reason = "would remove tracked metapackage(s): " + ", ".join(packages)
        return None, reason

    non_kernel_packages = sorted(
        package_name
        for package_name in removals
        if VERSIONED_KERNEL_PACKAGE_RE.fullmatch(package_name) is None
    )
    if non_kernel_packages:
        reason = "would remove non-kernel package(s): " + ", ".join(
            non_kernel_packages
        )
        return None, reason
    return removals, ""


def simulate_candidate(candidate, tracked_meta_packages):
    """Return the full APT removal set, or a reason the candidate is unsafe."""
    cache = apt.Cache()
    return prepare_candidate(cache, candidate, tracked_meta_packages)


def _candidate_to_dict(candidate):
    return {
        "series": candidate.series_version,
        "flavor": candidate.flavor,
        "version": candidate.kernel_version,
        "packages": sorted(candidate.packages),
    }


def append_cleanup_history(record, path=CLEANUP_HISTORY_FILE):
    history = read_cleanup_history(path)
    history.append(record)
    history = history[-HISTORY_LIMIT:]

    history_path = Path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("w", encoding="utf-8") as history_file:
        for entry in history:
            history_file.write(json.dumps(entry, sort_keys=True))
            history_file.write("\n")


def read_cleanup_history(path=CLEANUP_HISTORY_FILE):
    history = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return history
    for line in lines:
        try:
            history.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return history


def report_cleanup_progress(callback, message):
    if callback is not None:
        callback(message)


def run_cleanup(progress_callback=None):
    settings = load_cleanup_settings()
    if prune_protected_kernels(settings):
        save_cleanup_settings(settings)
    report_cleanup_progress(
        progress_callback,
        "Looking for older kernel versions...\n",
    )
    series_list = get_installed_series(get_available_series())
    running_release = os.uname().release
    candidates = get_cleanup_candidates(
        series_list,
        settings.retain,
        running_release,
        protected_kernels=settings.protected_kernels,
    )
    tracked_meta_packages = get_tracked_meta_packages(series_list)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "retain": settings.retain,
        "running_kernel": running_release,
        "tracked_series": get_tracked_series_history(series_list),
        "removed": [],
        "skipped": [],
        "status": "completed",
    }

    if not candidates:
        report_cleanup_progress(
            progress_callback,
            "No kernel packages need to be removed.\n",
        )

    for candidate in candidates:
        report_cleanup_progress(
            progress_callback,
            "\nChecking kernel %s...\n" % candidate.kernel_version,
        )
        cache = apt.Cache()
        removals, reason = prepare_candidate(
            cache,
            candidate,
            tracked_meta_packages,
        )
        if removals is None:
            report_cleanup_progress(
                progress_callback,
                "Skipped: %s\n" % reason,
            )
            item = _candidate_to_dict(candidate)
            item["reason"] = reason
            record["skipped"].append(item)
            continue

        try:
            report_cleanup_progress(
                progress_callback,
                "Removing packages:\n",
            )
            for package_name in sorted(removals):
                report_cleanup_progress(
                    progress_callback,
                    "  %s\n" % package_name,
                )
            install_progress = CleanupInstallProgress(progress_callback)
            cache.commit(install_progress=install_progress)
            item = _candidate_to_dict(candidate)
            item["packages"] = sorted(removals)
            record["removed"].append(item)
        except Exception as error:
            report_cleanup_progress(
                progress_callback,
                "Error: %s\n" % error,
            )
            item = _candidate_to_dict(candidate)
            item["reason"] = str(error)
            record["skipped"].append(item)
            record["status"] = "failed"
            break

    append_cleanup_history(record)
    if record["status"] == "completed":
        report_cleanup_progress(
            progress_callback,
            "\nCleanup completed.\n",
        )
    else:
        report_cleanup_progress(
            progress_callback,
            "\nCleanup did not complete.\n",
        )
    print(json.dumps(record, indent=2, sort_keys=True))
    return record
