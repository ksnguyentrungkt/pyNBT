# -*- coding: utf-8 -*-
"""
pyNBT.compat
Shared helpers to smooth over Revit API differences across versions.
Every pyNBT tool should import ElementId / unit-conversion helpers from
here instead of re-implementing them inline (see pynbt-tool-builder
skill, pattern #4).
"""
import os

from Autodesk.Revit.DB import ElementId, UnitUtils

try:
    from System import Int64
except Exception:
    Int64 = None


def make_eid(value):
    """Build an ElementId from a python int, safe across Revit 2024-2027+."""
    if Int64 is not None:
        try:
            return ElementId(Int64(value))
        except (TypeError, OverflowError):
            pass
    return ElementId(int(value))


def eid_int(element_id):
    """Return the integer value of an ElementId, across Revit versions.

    Revit 2024+ uses ElementId.Value (long).
    Older Revit uses ElementId.IntegerValue (int).
    """
    if element_id is None:
        return -1
    val = getattr(element_id, "Value", None)
    if val is None:
        val = getattr(element_id, "IntegerValue", None)
    return int(val) if val is not None else -1


def m_to_internal(value_m):
    """Convert a value in meters to Revit internal units (feet).

    Revit 2021+ uses ForgeTypeId (UnitTypeId.Meters).
    Older Revit uses the legacy DisplayUnitType enum (DUT_METERS).
    """
    try:
        from Autodesk.Revit.DB import UnitTypeId
        return UnitUtils.ConvertToInternalUnits(value_m, UnitTypeId.Meters)
    except ImportError:
        from Autodesk.Revit.DB import DisplayUnitType
        return UnitUtils.ConvertToInternalUnits(value_m, DisplayUnitType.DUT_METERS)


def internal_to_m(value_ft):
    """Convert a value in Revit internal units (feet) to meters."""
    try:
        from Autodesk.Revit.DB import UnitTypeId
        return UnitUtils.ConvertFromInternalUnits(value_ft, UnitTypeId.Meters)
    except ImportError:
        from Autodesk.Revit.DB import DisplayUnitType
        return UnitUtils.ConvertFromInternalUnits(value_ft, DisplayUnitType.DUT_METERS)


# ---------------------------------------------------------------------------
# Added for Wall Top Elevation tool: project-parameter creation helpers.
# Any tool that needs to create/bind a new project parameter should use
# these instead of branching on Revit version inline.
# ---------------------------------------------------------------------------

def get_number_spec():
    """Return the 'Number' spec/type to use when creating a new shared
    parameter Definition, whichever form this Revit version expects.

    Revit 2022+ uses SpecTypeId.Number (ForgeTypeId).
    Older Revit uses the legacy ParameterType.Number enum.
    """
    try:
        from Autodesk.Revit.DB import SpecTypeId
        return SpecTypeId.Number
    except ImportError:
        from Autodesk.Revit.DB import ParameterType
        return ParameterType.Number


def get_text_spec():
    """Return the 'Text' spec/type to use when creating a new shared
    parameter Definition, whichever form this Revit version expects.

    Revit 2022+ uses SpecTypeId.String.Text (ForgeTypeId).
    Older Revit uses the legacy ParameterType.Text enum.
    """
    try:
        from Autodesk.Revit.DB import SpecTypeId
        return SpecTypeId.String.Text
    except ImportError:
        from Autodesk.Revit.DB import ParameterType
        return ParameterType.Text


def definition_is_text(definition):
    """True if `definition`'s underlying data type is Text/String. Text
    parameters have no unit and no numeric rounding/format at all - Revit
    displays exactly the string that was Set(), always, on every version
    and regardless of Project Units. This is the most bulletproof way to
    guarantee a computed value displays EXACTLY as computed."""
    try:
        from Autodesk.Revit.DB import SpecTypeId
        return definition.GetDataType() == SpecTypeId.String.Text
    except Exception:
        try:
            from Autodesk.Revit.DB import ParameterType
            return definition.ParameterType == ParameterType.Text
        except Exception:
            return False


def get_data_param_group():
    """Return the parameter group ('Data') to use when binding a new
    parameter, whichever form this Revit version expects.

    Revit 2022+ uses GroupTypeId.Data (ForgeTypeId).
    Older Revit uses the legacy BuiltInParameterGroup enum.
    """
    try:
        from Autodesk.Revit.DB import GroupTypeId
        return GroupTypeId.Data
    except ImportError:
        from Autodesk.Revit.DB import BuiltInParameterGroup
        return BuiltInParameterGroup.PG_DATA


def get_dimensions_param_group():
    """Return the parameter group that shows up as 'Dimensions' in the
    Revit Properties palette, whichever form this Revit version expects.

    Revit 2022+ uses GroupTypeId.Geometry (ForgeTypeId).
    Older Revit uses the legacy BuiltInParameterGroup.PG_GEOMETRY enum.
    Both display as 'Dimensions' in the Revit UI.
    """
    try:
        from Autodesk.Revit.DB import GroupTypeId
        return GroupTypeId.Geometry
    except ImportError:
        from Autodesk.Revit.DB import BuiltInParameterGroup
        return BuiltInParameterGroup.PG_GEOMETRY


def param_is_length(param):
    """True if an existing Parameter's underlying data type is Length
    (e.g. someone else already created a same-named parameter as a Length
    type instead of Number). Used so a tool can decide whether to convert
    a value to internal feet before calling Parameter.Set(), instead of
    silently writing a wrong number into a Length parameter."""
    definition = param.Definition
    try:
        from Autodesk.Revit.DB import SpecTypeId
        return definition.GetDataType() == SpecTypeId.Length
    except Exception:
        try:
            from Autodesk.Revit.DB import ParameterType
            return definition.ParameterType == ParameterType.Length
        except Exception:
            return False


def get_group_label(definition):
    """Return the human-readable parameter group name (e.g. 'Dimensions',
    'Data', 'Text') that `definition` is CURRENTLY bound under, across
    Revit versions. Used to tell a tool user exactly where an existing
    parameter lives in the Properties palette, instead of them having to
    hunt for it group by group."""
    from Autodesk.Revit.DB import LabelUtils
    try:
        group_id = definition.GetGroupTypeId()  # Revit 2022+
        return LabelUtils.GetLabelForGroup(group_id)
    except Exception:
        try:
            return LabelUtils.GetLabelFor(definition.ParameterGroup)  # older Revit
        except Exception:
            return "Unknown"


def definition_is_length(definition):
    """Same check as param_is_length(), but taking a Definition directly
    (used when we only have the bound Definition, not a Parameter instance
    on a specific element)."""
    try:
        from Autodesk.Revit.DB import SpecTypeId
        return definition.GetDataType() == SpecTypeId.Length
    except Exception:
        try:
            from Autodesk.Revit.DB import ParameterType
            return definition.ParameterType == ParameterType.Length
        except Exception:
            return False


def build_meters_format_options(accuracy=0.001):
    """Build a FormatOptions object that forces a Length parameter's
    display to ALWAYS show meters with the given rounding accuracy,
    regardless of whatever unit the project's overall Project Units
    setting uses for Length (which could be mm, ft-in, etc). This is what
    makes a value like 32.250 m show as '32.250' instead of '32250.0'
    when the project's Length display unit happens to be millimeters."""
    from Autodesk.Revit.DB import FormatOptions
    try:
        from Autodesk.Revit.DB import UnitTypeId
        options = FormatOptions(UnitTypeId.Meters)
    except ImportError:
        from Autodesk.Revit.DB import DisplayUnitType
        options = FormatOptions(DisplayUnitType.DUT_METERS)
    # UseDefault must be explicitly turned off, otherwise Revit keeps
    # following the project's overall Project Units setting and silently
    # ignores the custom unit/accuracy set above.
    try:
        options.UseDefault = False
    except Exception:
        pass
    try:
        options.Accuracy = accuracy
    except Exception:
        pass
    return options


def build_number_format_options(accuracy=0.001):
    """Build a FormatOptions object that forces a Number (dimensionless)
    parameter to round its display to the given accuracy (default 0.001,
    i.e. 3 decimal places) instead of showing Revit's default long
    decimal tail (e.g. '32.250000'). Number has no unit, so this only
    controls rounding/decimal places, never a unit conversion."""
    from Autodesk.Revit.DB import FormatOptions
    try:
        from Autodesk.Revit.DB import UnitTypeId
        options = FormatOptions(UnitTypeId.General)
    except ImportError:
        # older Revit: Number parameters have no DisplayUnitType at all -
        # the default FormatOptions() still supports Accuracy/UseDefault.
        options = FormatOptions()
    try:
        options.UseDefault = False
    except Exception:
        pass
    try:
        options.Accuracy = accuracy
    except Exception:
        pass
    return options


def force_definition_format(definition, format_options):
    """Force `definition` (an InternalDefinition already bound in the
    project) to use `format_options` instead of following the project's
    overall Project Units setting. Must be called inside an open
    Transaction. Returns True if the override was actually applied, False
    if this Revit version or this kind of Definition doesn't support a
    per-parameter format override (caller should surface that instead of
    assuming it silently worked)."""
    try:
        definition.SetFormatOptions(format_options)
        return True
    except Exception:
        return False


def find_binding_categories_for_name(doc, name):
    """Look through the WHOLE project's parameter bindings (any category)
    for a Definition named `name`. Returns a list of category names it is
    currently bound to, or None if no parameter with that name is bound
    anywhere in the project yet.

    Used to detect the case where someone else already created a same-named
    parameter bound to OTHER categories (not the one a tool wants) - trying
    to bind a second, differently-sourced Definition under the same name
    is what Revit usually rejects with a same-name conflict."""
    it = doc.ParameterBindings.ForwardIterator()
    it.Reset()
    while it.MoveNext():
        definition = it.Key
        if definition.Name == name:
            binding = it.Current
            return [cat.Name for cat in binding.Categories]
    return None


# ---------------------------------------------------------------------------
# Added for Update tool: pyRevit session reload helper.
# ---------------------------------------------------------------------------

def try_reload_pyrevit():
    """Attempt to trigger a full pyRevit session reload (reloads every
    extension, including this one) so a freshly-downloaded update becomes
    active immediately, without the user having to click pyRevit's own
    Reload button by hand.

    The reload entry point is an internal pyRevit API that has moved
    across pyRevit versions, so this tries a few known locations in turn
    and returns False (instead of raising) if none of them work - the
    caller should then tell the user to reload manually via the pyRevit
    tab's own Reload button."""
    try:
        from pyrevit.loader.sessionmgr import reload_pyrevit
        reload_pyrevit()
        return True
    except Exception:
        pass
    try:
        from pyrevit.session import reload_pyrevit
        reload_pyrevit()
        return True
    except Exception:
        pass
    try:
        from pyrevit.loader import sessionmgr
        sessionmgr.reload_pyrevit()
        return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Added for Update tool: HTTP download + zip extraction helpers.
#
# Revit 2025+ runs on .NET 8 (CoreCLR) instead of .NET Framework, which
# changes which BCL assemblies are auto-loaded/importable - System.Net's
# WebClient and System.IO.Compression's ZipFile do not resolve the same
# way on every Revit version. Both helpers below try Python's own stdlib
# first (pure Python, no CLR assembly involved, most portable), then fall
# back through a few different CLR assembly names.
# ---------------------------------------------------------------------------

def download_file(url, dest_path):
    """Download the file at `url` to `dest_path`, trying a few different
    HTTP mechanisms in turn until one works."""
    try:
        import urllib
        urllib.urlretrieve(url, dest_path)
        return
    except Exception:
        pass

    import clr
    for assembly_name in ("System.Net.Requests", "System", "System.Net"):
        try:
            clr.AddReference(assembly_name)
            from System.Net import WebClient
            WebClient().DownloadFile(url, dest_path)
            return
        except Exception:
            continue

    try:
        clr.AddReference('System.Net.Http')
        from System.Net.Http import HttpClient
        from System import Uri
        from System.IO import File
        response_bytes = HttpClient().GetByteArrayAsync(Uri(url)).Result
        File.WriteAllBytes(dest_path, response_bytes)
        return
    except Exception as ex:
        raise Exception(
            "Could not download {} - tried urllib, WebClient and "
            "HttpClient, all failed. Last error: {}".format(url, str(ex))
        )


def extract_zip(zip_path, extract_to_dir):
    """Extract the zip file at `zip_path` into `extract_to_dir`, trying
    Python's built-in zipfile module first, falling back to .NET's
    ZipFile if this IronPython build's zlib support is unavailable."""
    try:
        import zipfile
        zf = zipfile.ZipFile(zip_path, 'r')
        try:
            zf.extractall(extract_to_dir)
        finally:
            zf.close()
        return
    except Exception:
        pass

    import clr
    for assembly_name in ("System.IO.Compression.FileSystem",
                           "System.IO.Compression.ZipFile",
                           "System.IO.Compression"):
        try:
            clr.AddReference(assembly_name)
            from System.IO.Compression import ZipFile
            ZipFile.ExtractToDirectory(zip_path, extract_to_dir)
            return
        except Exception:
            continue

    raise Exception(
        "Could not extract {} - tried Python zipfile and .NET ZipFile, "
        "all failed.".format(zip_path)
    )


def fetch_text(url):
    """Fetch the text content at `url` and return it as a string (stripped
    of surrounding whitespace/newlines), trying a few different HTTP
    mechanisms in turn. Used for small, lightweight checks (e.g. reading a
    one-line VERSION file) where downloading to a temp file first, like
    download_file() does, would be wasteful."""
    try:
        import urllib2
        return urllib2.urlopen(url).read().strip()
    except Exception:
        pass

    import clr
    for assembly_name in ("System.Net.Requests", "System", "System.Net"):
        try:
            clr.AddReference(assembly_name)
            from System.Net import WebClient
            return WebClient().DownloadString(url).strip()
        except Exception:
            continue

    try:
        clr.AddReference('System.Net.Http')
        from System.Net.Http import HttpClient
        from System import Uri
        return HttpClient().GetStringAsync(Uri(url)).Result.strip()
    except Exception as ex:
        raise Exception(
            "Could not fetch {} - tried urllib2, WebClient and HttpClient, "
            "all failed. Last error: {}".format(url, str(ex))
        )


# ---------------------------------------------------------------------------
# Added for Update tool v2: GitHub Releases API support.
#
# The GitHub REST API requires a User-Agent header on every request (it
# rejects requests without one), which plain urllib2.urlopen(url) does not
# send by default - so this needs an explicit Request object with headers,
# unlike the simpler fetch_text() above.
# ---------------------------------------------------------------------------

def fetch_json(url):
    """Fetch `url` and parse the response body as JSON, returning a plain
    dict/list. Used to read the GitHub Releases API (which returns JSON,
    not plain text). Tries the same cascading HTTP mechanisms as
    fetch_text()/download_file(), each with an explicit User-Agent header
    since the GitHub API rejects requests that don't send one."""
    import json

    try:
        import urllib2
        request = urllib2.Request(url, headers={"User-Agent": "pyNBT-Update"})
        raw_text = urllib2.urlopen(request).read()
        return json.loads(raw_text)
    except Exception:
        pass

    import clr
    for assembly_name in ("System.Net.Requests", "System", "System.Net"):
        try:
            clr.AddReference(assembly_name)
            from System.Net import WebClient
            client = WebClient()
            client.Headers.Add("User-Agent", "pyNBT-Update")
            raw_text = client.DownloadString(url)
            return json.loads(raw_text)
        except Exception:
            continue

    try:
        clr.AddReference('System.Net.Http')
        from System.Net.Http import HttpClient
        from System import Uri
        http_client = HttpClient()
        http_client.DefaultRequestHeaders.Add("User-Agent", "pyNBT-Update")
        raw_text = http_client.GetStringAsync(Uri(url)).Result
        return json.loads(raw_text)
    except Exception as ex:
        raise Exception(
            "Could not fetch {} - tried urllib2, WebClient and HttpClient, "
            "all failed. Last error: {}".format(url, str(ex))
        )


def find_single_subdir(path):
    """Return the full path of the ONE subdirectory inside `path`, or None
    if there isn't exactly one. A GitHub zip download (branch archive or
    release zipball) always extracts to a single top-level folder, but its
    exact name varies (e.g. 'pyNBT-main' for a branch, 'ksnguyentrungkt-
    pyNBT-<shortsha>' for a release zipball) - this avoids hard-coding that
    name and breaking if the naming pattern ever changes."""
    try:
        entries = [
            name for name in os.listdir(path)
            if os.path.isdir(os.path.join(path, name))
        ]
    except Exception:
        return None
    if len(entries) == 1:
        return os.path.join(path, entries[0])
    return None
