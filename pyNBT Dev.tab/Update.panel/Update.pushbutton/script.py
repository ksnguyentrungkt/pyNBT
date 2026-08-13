# -*- coding: utf-8 -*-
"""
pyNBT - Update

Checks the latest version number published on the shared pyNBT GitHub
repository against the version installed on this machine. If they match,
reports "already up to date" and stops - no download, no disruptive
reload. If they differ, downloads the latest pyNBT.extension files,
copies them over the local install, then reloads pyRevit so the updated
tools become active right away - no manual copy/paste needed.

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

from pyNBT.compat import try_reload_pyrevit, download_file, extract_zip, fetch_text

# --- Constants ---------------------------------------------------------
TOOL_NAME = "pyNBT - Update"
REPO_ZIP_URL = "https://github.com/ksnguyentrungkt/pyNBT/archive/refs/heads/main.zip"
REPO_VERSION_URL = "https://raw.githubusercontent.com/ksnguyentrungkt/pyNBT/main/VERSION"
REPO_FOLDER_NAME = "pyNBT-main"  # top-level folder name inside the downloaded zip
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
    """Full update flow: check version -> (skip if same) -> download ->
    extract -> copy over -> reload."""
    local_version = get_local_version()

    try:
        remote_version = fetch_text(REPO_VERSION_URL)
    except Exception as ex:
        forms.alert(
            "Could not check the latest version on GitHub.\n\n"
            "Check your internet connection and try again.\n\n"
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

    temp_dir = tempfile.mkdtemp(prefix="pyNBT_update_")
    zip_path = os.path.join(temp_dir, "pyNBT_latest.zip")
    extract_dir = os.path.join(temp_dir, "extracted")

    try:
        try:
            download_file(REPO_ZIP_URL, zip_path)
        except Exception as ex:
            forms.alert(
                "Could not download the latest files from GitHub.\n\n"
                "Check your internet connection and try again.\n\n"
                "Error: {}".format(str(ex)),
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

        source_root = os.path.join(extract_dir, REPO_FOLDER_NAME)
        if not os.path.isdir(source_root):
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
