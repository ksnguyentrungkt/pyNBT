# -*- coding: utf-8 -*-
"""
pyNBT - Update

Downloads the latest pyNBT.extension files from the shared pyNBT GitHub
repository (public, read-only for everyone except the maintainer) and
copies them over the local install, then reloads pyRevit so the updated
tools become active right away - no manual copy/paste needed.

Works the same way for every team member: the repo is public, so no
GitHub login or token is required on this machine to run Update.
"""
import os
import sys
import shutil
import tempfile

import clr
clr.AddReference('System.IO.Compression.FileSystem')
from System.IO.Compression import ZipFile
from System.Net import WebClient

from pyrevit import forms

# --- Make the shared pyNBT lib importable ----------------------------------
SCRIPT_DIR = os.path.dirname(__file__)
EXTENSION_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
LIB_DIR = os.path.join(EXTENSION_ROOT, "lib")
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyNBT.compat import try_reload_pyrevit

# --- Constants ---------------------------------------------------------
TOOL_NAME = "pyNBT - Update"
REPO_ZIP_URL = "https://github.com/ksnguyentrungkt/pyNBT/archive/refs/heads/main.zip"
REPO_FOLDER_NAME = "pyNBT-main"  # top-level folder name inside the downloaded zip


# --- Standalone logic functions --------------------------------------------

def download_latest_zip(target_zip_path):
    """Download the latest snapshot of the pyNBT repo (main branch)."""
    client = WebClient()
    client.DownloadFile(REPO_ZIP_URL, target_zip_path)


def extract_zip(zip_path, extract_to_dir):
    """Extract the downloaded zip into a temp folder."""
    ZipFile.ExtractToDirectory(zip_path, extract_to_dir)


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
    """Full update flow: download -> extract -> copy over -> reload."""
    temp_dir = tempfile.mkdtemp(prefix="pyNBT_update_")
    zip_path = os.path.join(temp_dir, "pyNBT_latest.zip")
    extract_dir = os.path.join(temp_dir, "extracted")

    try:
        try:
            download_latest_zip(zip_path)
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
            "Update complete - {} file(s) updated.\n\n"
            "pyRevit has been reloaded automatically. "
            "The pyNBT tab is ready to use.".format(copied_count),
            title=TOOL_NAME
        )
    else:
        forms.alert(
            "Update complete - {} file(s) updated.\n\n"
            "Could not reload pyRevit automatically on this pyRevit "
            "version.\nPlease click pyRevit tab > Reload to finish.".format(copied_count),
            title=TOOL_NAME
        )


run_update()
