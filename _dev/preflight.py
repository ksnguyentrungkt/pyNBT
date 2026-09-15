#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pyNBT preflight validator.

Runs BEFORE a new/updated pushbutton is committed into
E:\\101.Revit Add-Ins\\pyNBT.extension on NBT's machine. This runs in
Claude's own cloud sandbox (Python 3) against a local draft copy of the
tool folder — it is a proxy check, not a real IronPython 2.7 execution, but
it catches the common mistakes (missing file, bad bundle.yaml, wrong icon
size, disallowed import, obvious syntax error, duplicate tool name) before
NBT ever has to open Revit.

Usage:
    python3 preflight.py <path-to-ToolName.pushbutton> [--register TOOL_REGISTER.md]

Exit code 0 = all checks passed (still means "written, not yet tested in
Revit" — the 3-step Revit test in rules/01-structure.md §5 still applies).
Exit code 1 = at least one FAIL.
"""
import argparse
import ast
import os
import re
import sys

try:
    import yaml
except ImportError:
    yaml = None

try:
    from PIL import Image
except ImportError:
    Image = None

REQUIRED_ICON_SIZE = (96, 96)

# Safe-Stack, per rules/01-structure.md section 4
DISALLOWED_IMPORTS = {
    "pandas": "Python-3-only, cannot run under IronPython 2.7",
    "numpy": "Python-3-only, cannot run under IronPython 2.7",
    "openpyxl": "Python-3-only, cannot run under IronPython 2.7",
    "win32com": "COM automation - known to break in pyRevit's IronPython",
    "comtypes": "COM automation - known to break in pyRevit's IronPython",
    "pythoncom": "COM automation - known to break in pyRevit's IronPython",
}

results = []  # list of (level, message) where level in OK/WARN/FAIL


def log(level, message):
    results.append((level, message))
    print("[{0}] {1}".format(level, message))


def check_files_exist(tool_dir):
    script_path = os.path.join(tool_dir, "script.py")
    bundle_path = os.path.join(tool_dir, "bundle.yaml")
    icon_path = os.path.join(tool_dir, "icon.png")

    if os.path.isfile(script_path):
        log("OK", "script.py exists")
    else:
        log("FAIL", "script.py is missing")

    if os.path.isfile(bundle_path):
        log("OK", "bundle.yaml exists")
    else:
        log("FAIL", "bundle.yaml is missing")

    if os.path.isfile(icon_path):
        log("OK", "icon.png exists")
    else:
        log("FAIL", "icon.png is missing")

    return script_path, bundle_path, icon_path


def check_bundle_yaml(bundle_path):
    if not os.path.isfile(bundle_path):
        return
    with open(bundle_path, "r", encoding="utf-8") as f:
        raw = f.read()

    if yaml is not None:
        try:
            data = yaml.safe_load(raw)
        except Exception as ex:
            log("FAIL", "bundle.yaml did not parse as YAML: {0}".format(ex))
            return
        if not isinstance(data, dict):
            log("FAIL", "bundle.yaml did not parse into a mapping")
            return
        if data.get("title"):
            log("OK", "bundle.yaml has a title")
        else:
            log("FAIL", "bundle.yaml is missing 'title'")
        if data.get("tooltip"):
            log("OK", "bundle.yaml has a tooltip")
        else:
            log("FAIL", "bundle.yaml is missing 'tooltip'")
    else:
        # fallback: naive key presence check
        if re.search(r"^title\s*:", raw, re.MULTILINE):
            log("OK", "bundle.yaml has a title (naive check, PyYAML unavailable)")
        else:
            log("FAIL", "bundle.yaml is missing 'title' (naive check)")
        if re.search(r"^tooltip\s*:", raw, re.MULTILINE):
            log("OK", "bundle.yaml has a tooltip (naive check)")
        else:
            log("FAIL", "bundle.yaml is missing 'tooltip' (naive check)")


def check_icon(icon_path):
    if not os.path.isfile(icon_path):
        return
    if Image is None:
        log("WARN", "Pillow not available - cannot verify icon.png size/mode")
        return
    try:
        with Image.open(icon_path) as im:
            if im.size == REQUIRED_ICON_SIZE:
                log("OK", "icon.png is {0}x{1}".format(*im.size))
            else:
                log(
                    "WARN",
                    "icon.png is {0}x{1}, expected {2}x{3} (supersample at "
                    "384x384 then LANCZOS-downsample per icon-guide)".format(
                        im.size[0], im.size[1], *REQUIRED_ICON_SIZE
                    ),
                )
            if im.mode != "RGBA":
                log("WARN", "icon.png mode is {0}, expected RGBA".format(im.mode))
    except Exception as ex:
        log("FAIL", "icon.png is not a valid image: {0}".format(ex))


def check_script_syntax_and_imports(script_path):
    if not os.path.isfile(script_path):
        return
    with open(script_path, "r", encoding="utf-8") as f:
        source = f.read()

    try:
        tree = ast.parse(source, filename=script_path)
    except SyntaxError as ex:
        log(
            "FAIL",
            "script.py has a syntax error at line {0}: {1} "
            "(proxy check via Python 3 grammar - IronPython 2.7 may differ "
            "slightly, but this almost always means a real typo)".format(
                ex.lineno, ex.msg
            ),
        )
        return

    log("OK", "script.py passed AST syntax check (Python-3-grammar proxy for IronPython 2.7)")

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])

    flagged = [name for name in imported if name in DISALLOWED_IMPORTS]
    if flagged:
        for name in flagged:
            log("FAIL", "script.py imports '{0}' - {1}".format(name, DISALLOWED_IMPORTS[name]))
    else:
        log("OK", "no disallowed imports found (Safe-Stack check)")

    has_transaction = "Transaction(" in source
    has_rollback = "RollBack" in source
    if has_transaction and not has_rollback:
        log(
            "WARN",
            "script.py opens a Transaction( but no RollBack found anywhere "
            "in the file - confirm the error path rolls back",
        )
    elif has_transaction and has_rollback:
        log("OK", "Transaction(...) + RollBack both present")


def check_duplicate(tool_dir, register_path):
    if not register_path or not os.path.isfile(register_path):
        log("WARN", "no TOOL_REGISTER.md given/found - skipped duplicate check")
        return
    tool_folder_name = os.path.basename(os.path.normpath(tool_dir))
    tool_key = tool_folder_name.replace(".pushbutton", "").strip().lower()

    with open(register_path, "r", encoding="utf-8") as f:
        register_text = f.read().lower()

    if tool_key and tool_key in register_text:
        log(
            "WARN",
            "a tool matching '{0}' already appears in TOOL_REGISTER.md - "
            "confirm this is meant to update it, not duplicate it".format(
                tool_folder_name
            ),
        )
    else:
        log("OK", "no matching name found in TOOL_REGISTER.md")


def main():
    parser = argparse.ArgumentParser(description="pyNBT preflight validator")
    parser.add_argument("tool_dir", help="path to the ToolName.pushbutton folder")
    parser.add_argument("--register", help="path to TOOL_REGISTER.md", default=None)
    args = parser.parse_args()

    tool_dir = args.tool_dir
    print("=" * 60)
    print("pyNBT PREFLIGHT - {0}".format(os.path.basename(os.path.normpath(tool_dir))))
    print("=" * 60)

    if not os.path.isdir(tool_dir):
        print("[FAIL] folder does not exist: {0}".format(tool_dir))
        sys.exit(1)

    script_path, bundle_path, icon_path = check_files_exist(tool_dir)
    check_bundle_yaml(bundle_path)
    check_icon(icon_path)
    check_script_syntax_and_imports(script_path)
    check_duplicate(tool_dir, args.register)

    print("-" * 60)
    fails = [m for lvl, m in results if lvl == "FAIL"]
    warns = [m for lvl, m in results if lvl == "WARN"]

    if fails:
        print("[FAILURE] {0} error(s), {1} warning(s)".format(len(fails), len(warns)))
        sys.exit(1)
    else:
        print("[SUCCESS] 0 errors, {0} warning(s)".format(len(warns)))
        print("Status: written, not yet tested in Revit.")
        sys.exit(0)


if __name__ == "__main__":
    main()
