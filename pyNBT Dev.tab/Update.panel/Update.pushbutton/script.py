# -*- coding: utf-8 -*-
"""
pyNBT - Update

Checks the latest PUBLISHED RELEASE on the shared pyNBT GitHub repository
(not just the latest commit on main) against the version installed on this
machine. If they match, reports "already up to date" and stops - no
download, no disruptive reload. If they differ, downloads that release's
files, copies them over the local install, then reloads pyRevit so the
updated tools become active right away - no manual copy/paste needed.

Using a GitHub Release (instead of whatever is currently on the main
branch) as the update source means Trung can push and test work-in-progress
commits on main at any time without affecting the team - a machine only
sees a new version to update to once Trung explicitly publishes a Release
on GitHub for it.

Works the same way for every team member: the repo is public, so no
GitHub login or token is required on this machine to run Update.
"""
import os
import sys
import shutil
import tempfile

from pyrevit import forms

# --- Make the shared pyNBT lib importable ----------------------------------
SCRIPT_DIR = os.path.dirname(__file__)
EXTENSION_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
LIB_DIR = os.path.join(EXTENSION_ROOT, "lib")
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyNBT.compat import (
    try_reload_pyrevit, download_file, extract_zip, fetch_json,
    find_single_subdir,
)

# --- Constants ---------------------------------------------------------
TOOL_NAME = "pyNBT - Update"
REPO_OWNER = "ksnguyentrungkt"
REPO_NAME = "pyNBT"
REPO_LATEST_RELEASE_API = "https://api.github.com/repos/{}/{}/releases/latest".format(
    REPO_OWNER, REPO_NAME)
REPO_TAG_ZIP_TEMPLATE = "https://github.com/{}/{}/archive/refs/tags/{{tag}}.zip".format(
    REPO_OWNER, REPO_NAME)
LOCAL_VERSION_FILE = os.path.join(EXTENSION_ROOT, "VERSION")


# --- Standalone logic functions --------------------------------------------

def get_local_version():
    """Return the version string currently installed on this machine, or
    'unknown' if no VERSION file exists yet (e.g. very first install)."""
    try:
        with open(LOCAL_VERSION_FILE, 'r') as f:
            return f.read().strip()
    except Exception:
        return "unknown"


def set_local_version(version_string):
    """Write `version_string` into the local VERSION file, creating it if
    needed. Called right after a successful update so the next Update
    click compares against the release just installed."""
    with open(LOCAL_VERSION_FILE, 'w') as f:
        f.write(version_string)


def copy_tree_overwrite(source_dir, dest_dir):
    """Recursively copy every file from source_dir into dest_dir,
    overwriting existing files and creating folders as needed.
    Local-only files that don't exist in source_dir are left untouched.
    Returns the number of files copied."""
    copied_count = 0
    for root, _dirs, files in os.walk(source_dir):
        rel_path = os.path.relpath(root, source_dir)
        dest_folder = dest_dir if rel_path == "." else os.path.join(dest_dir, rel_path)
        if not os.path.exists(dest_folder):
            os.makedirs(dest_folder)
        for file_name in files:
            src_file = os.path.join(root, file_name)
            dst_file = os.path.join(dest_folder, file_name)
            shutil.copy2(src_file, dst_file)
            copied_count += 1
    return copied_count


def run_update():
    """Full update flow: check latest published release -> (skip if same
    version already installed) -> download that release -> extract ->
    copy over -> reload."""
    local_version = get_local_version()

    try:
        release_data = fetch_json(REPO_LATEST_RELEASE_API)
        remote_version = release_data["tag_name"]
    except Exception as ex:
        forms.alert(
            "Could not check the latest published version on GitHub.\n\n"
            "This can also mean no version has been published yet - "
            "check with Trung.\n\n"
            "Error: {}".format(str(ex)),
            title=TOOL_NAME
        )
        return

    if local_version == remote_version:
        forms.alert(
            "You already have the latest version (v{}).\n"
            "No update needed.".format(local_version),
            title=TOOL_NAME
        )
        return

    # A new version exists - ask before touching anything. Nothing is
    # downloaded or changed until the user explicitly confirms here.
    user_confirmed = forms.alert(
        "A new version is available.\n\n"
        "Current version: v{}\n"
        "New version: v{}\n\n"
        "Update now?".format(local_version, remote_version),
        title=TOOL_NAME,
        yes=True, no=True
    )
    if not user_confirmed:
        return

    temp_dir = tempfile.mkdtemp(prefix="pyNBT_update_")
    zip_path = os.path.join(temp_dir, "pyNBT_latest.zip")
    extract_dir = os.path.join(temp_dir, "extracted")
    download_url = REPO_TAG_ZIP_TEMPLATE.format(tag=remote_version)

    try:
        try:
            download_file(download_url, zip_path)
        except Exception as ex:
            forms.alert(
                "Could not download the latest published version from "
                "GitHub.\n\nCheck your internet connection and try "
                "again.\n\nError: {}".format(str(ex)),
                title=TOOL_NAME
            )
            return

        try:
            extract_zip(zip_path, extract_dir)
        except Exception as ex:
            forms.alert(
                "Downloaded the update but could not extract it.\n\n"
                "Error: {}".format(str(ex)),
                title=TOOL_NAME
            )
            return

        source_root = find_single_subdir(extract_dir)
        if not source_root:
            forms.alert(
                "The downloaded update package has an unexpected structure.\n"
                "No local files were changed.",
                title=TOOL_NAME
            )
            return

        try:
            copied_count = copy_tree_overwrite(source_root, EXTENSION_ROOT)
        except Exception as ex:
            forms.alert(
                "Downloaded the update but could not copy the files into "
                "place.\n\nError: {}".format(str(ex)),
                title=TOOL_NAME
            )
            return

        # The downloaded release's own VERSION file (if any) may be stale
        # or missing - always stamp the CONFIRMED release tag we actually
        # downloaded, so the next Update click compares correctly.
        try:
            set_local_version(remote_version)
        except Exception:
            pass
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    reload_ok = try_reload_pyrevit()

    if reload_ok:
        forms.alert(
            "Updated from v{} to v{} - {} file(s) updated.\n\n"
            "pyRevit has been reloaded automatically. "
            "The pyNBT tab is ready to use.".format(
                local_version, remote_version, copied_count),
            title=TOOL_NAME
        )
    else:
        forms.alert(
            "Updated from v{} to v{} - {} file(s) updated.\n\n"
            "Could not reload pyRevit automatically on this pyRevit "
            "version.\nPlease click pyRevit tab > Reload to finish.".format(
                local_version, remote_version, copied_count),
            title=TOOL_NAME
        )


run_update()
