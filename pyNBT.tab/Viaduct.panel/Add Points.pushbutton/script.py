# -*- coding: utf-8 -*-
"""Add Points

Creates point markers at coordinates given as raw survey Easting/Northing/
Elevation (meters), converted with Trung's verified Excel survey formula
so the result matches what Revit's Spot Coordinate tool reports.

Target model is picked from a dropdown listing every Revit document
currently open in this session (Project or Family), not just whichever
window happens to be active:
  - Family document  -> creates a Reference Point.
  - Project document -> places an instance of "Add Points tool.rfa" (a
    hand-built Generic Model Adaptive family that ships next to this
    script, with its single Adaptive Point already sitting exactly on the
    sharp corner / (0,0,0) origin of its triangular marker) and moves
    that Adaptive Point to the computed coordinate - so the imported
    X,Y,Z is exactly that Adaptive Point, snappable with Spot Coordinate.
    No geometry is built by code anymore - the family file is the single
    source of truth for the marker's shape.

Both Family and Project targets share the same Origin conversion logic
(Internal Origin / Project Base Point / Survey Point / Custom survey
formula), defaulting to "Custom (Survey formula)" since that's the mode
matching Trung's real project workflow.

Input can come from an Excel file (sheet named "Data", columns X/Y/Z)
or be typed/edited directly in the table.

Runs on pyRevit's IronPython engine (no shebang = default engine) so it
stays a plain WPF code-behind window like every other pyNBT tool. Excel
reading is done with a small dependency-free .xlsx parser (System.IO.Compression
+ System.Xml) instead of openpyxl, since openpyxl needs the CPython engine
and mixing that with hand-built WPF windows triggers a pythonnet
"type initializer for 'Delegates'" crash in Revit's process.
"""

__title__ = "Add\nPoints"
__author__ = "pyNBT"
__doc__ = (
    "Import or type X,Y,Z coordinates (meters). Family: creates Reference "
    "Points. Project: places an Adaptive pyramid marker family. Default "
    "Origin mode 'Custom (Survey formula)' matches Trung's real workflow."
)

import os
import math
import datetime
import json
import clr

clr.AddReference("System")
clr.AddReference("System.Data")
clr.AddReference("System.Xml")
clr.AddReference("System.IO.Compression")
clr.AddReference("System.IO.Compression.FileSystem")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("WindowsBase")

from System.Data import DataTable
from System.IO.Compression import ZipFile
from System.Xml import XmlDocument, XmlNamespaceManager
from System.Windows import (
    Window, WindowStartupLocation, Thickness, HorizontalAlignment,
    VerticalAlignment, FontWeights, TextWrapping, GridLength, GridUnitType,
    Visibility,
)
from System.Windows.Controls import (
    Grid, RowDefinition, ColumnDefinition,
    Border, StackPanel, Orientation, TextBlock, TextBox, Button, DataGrid,
    DataGridTextColumn, DataGridLength, DataGridLengthUnitType,
    DataGridSelectionMode, ComboBox, ComboBoxItem,
)
from System.Windows.Data import Binding

from Autodesk.Revit.DB import (
    Transaction, XYZ, BuiltInParameter, BasePoint,
    AdaptiveComponentInstanceUtils, IFamilyLoadOptions, FamilySource,
    Family, FilteredElementCollector,
)

from pyrevit import forms, script, revit

try:
    string_types = basestring  # IronPython 2.7 (py2)
except NameError:
    string_types = str  # pragma: no cover - CPython fallback

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# ---------------------------------------------------------------------------
# Shared pyNBT modules (lib/pyNBT/compat.py, lib/pyNBT/theme.py). If either
# import fails, it means the extension's lib/pyNBT files are out of date -
# stop with a clear message instead of silently re-declaring duplicates.
# ---------------------------------------------------------------------------
try:
    from pyNBT.compat import m_to_internal, internal_to_m
except ImportError:
    forms.alert(
        "pyNBT.compat is missing 'm_to_internal' / 'internal_to_m'.\n"
        "Update lib/pyNBT/compat.py to the latest version, then reload pyRevit.",
        title="Add Points",
        exitscript=True,
    )

from pyNBT.theme import (
    CLR_HEADER, CLR_HEADER_TEXT, CLR_HEADER_SUB, CLR_BG, CLR_CARD,
    CLR_BORDER, CLR_FOOTER, CLR_TEXT, CLR_MUTED, CLR_APPLY, CLR_APPLY_TEXT,
    brush,
)

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()

TOOL_NAME = "Add Points"
TOOL_VERSION = "v3.2"
# Trung's hand-built adaptive marker family, shipped next to script.py.
# Its geometry (a small triangular block) and the single Adaptive Point
# sitting exactly on its sharp corner / (0,0,0) origin are entirely
# authored in the family editor - this tool only loads the file and
# places/moves instances, it never builds geometry by code.
PYRAMID_FAMILY_FILE = "Add Points tool.rfa"

ORIGIN_MODE_PROJECT_BASE = "Project Base Point"
ORIGIN_MODE_SURVEY = "Survey Point"
ORIGIN_MODE_INTERNAL = "Internal Origin"
ORIGIN_MODE_CUSTOM = "Custom (Survey formula)"

# Project documents can route through any of the 4 modes. Family documents
# have no Project Base Point / Survey Point concept (those Revit elements
# only exist in a project), so their dropdown only offers Internal Origin
# and Custom. Both lists default-select Custom - it's the mode that matches
# Trung's real project workflow (see the module docstring / project doc).
PROJECT_ORIGIN_MODES = [
    ORIGIN_MODE_PROJECT_BASE, ORIGIN_MODE_SURVEY, ORIGIN_MODE_INTERNAL,
    ORIGIN_MODE_CUSTOM,
]
FAMILY_ORIGIN_MODES = [ORIGIN_MODE_INTERNAL, ORIGIN_MODE_CUSTOM]

# Defaults for "Custom (Survey formula)", taken from Trung's own verified
# Excel formula (sheet "NBT" in Coordinates Segment Viaduct_points_ORG.xlsx):
#   G = (P-T)*COS(RADIANS(V)) + (Q-U)*SIN(RADIANS(V))
#   H = -(P-T)*SIN(RADIANS(V)) + (Q-U)*COS(RADIANS(V))
#   I = R
# where P,Q,R are raw survey Easting/Northing/Elevation, T,U are the fixed
# survey-coordinate origin, and V = 1 deg 29 min 52 sec converted to decimal
# degrees. Editable in the UI in case a different project uses different
# constants.
DEFAULT_CUSTOM_EASTING_M = 20819.4357966
DEFAULT_CUSTOM_NORTHING_M = 46900.9138767
DEFAULT_CUSTOM_ANGLE_DEG = 1.0 + 29.0 / 60.0 + 52.0 / 3600.0

# Every real project has its OWN Easting/Northing/Angle (T,U,V) - they come
# from that project's survey control data, not from Trung. So instead of a
# single hardcoded default, the tool lets Trung save one named preset per
# project (e.g. "Viaduct Segment", "Tower A Basement") and pick it again
# next time via a dropdown, instead of re-typing 3 numbers by hand every
# time he opens a different project. Presets are stored in this script's
# own pyRevit config (persists across Revit sessions, keyed by pyRevit
# user - not tied to any single .rvt file).
PRESET_NONE_LABEL = "(none - type manually)"


def _load_presets():
    """Returns {preset_name: {"easting":.., "northing":.., "angle":..}}
    saved so far, or {} if none saved yet / config unreadable."""
    cfg = script.get_config()
    raw = getattr(cfg, "custom_origin_presets_json", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_presets(presets):
    cfg = script.get_config()
    cfg.custom_origin_presets_json = json.dumps(presets)
    script.save_config()


# ---------------------------------------------------------------------------
# 2. STANDALONE LOGIC - no UI references, pure Revit API / data work
# ---------------------------------------------------------------------------

def list_open_documents():
    """Returns [(label, Document), ...] for every non-linked Revit document
    open in this session (so the tool can target something other than
    whichever window happens to be active)."""
    app = doc.Application
    items = []
    for d in app.Documents:
        try:
            if d.IsLinked:
                continue
        except Exception:
            pass
        kind = "Family" if d.IsFamilyDocument else "Project"
        items.append(("[{}] {}".format(kind, d.Title), d))
    return items


def _base_point_transform(bp):
    """Returns (origin XYZ, rotation angle in radians) for a given
    BasePoint element, in internal (feet) coordinates. Falls back to
    (0,0,0), 0.0 if `bp` is None."""
    if bp is None:
        return XYZ.Zero, 0.0
    origin = bp.Position
    angle = 0.0
    try:
        p = bp.get_Parameter(BuiltInParameter.BASEPOINT_ANGLETON_PARAM)
        if p:
            angle = p.AsDouble()
    except Exception:
        angle = 0.0
    return origin, angle


def get_origin_transform(document, origin_mode):
    """Returns (origin XYZ, rotation angle in radians) used to convert an
    (X,Y,Z) offset Trung typed into an absolute internal-coordinate point,
    for the chosen reference:
      - "Project Base Point": BasePoint.GetProjectBasePoint(document).
      - "Survey Point": BasePoint.GetSurveyPoint(document) - use this if
        Trung's source coordinates were captured with Spot Coordinate set
        to Shared Coordinates instead of Project.
      - "Internal Origin": no offset/rotation at all (identity) - a
        guaranteed-correct baseline to sanity-check against when neither
        Base Point nor Survey Point readings match the source Excel data."""
    if origin_mode == ORIGIN_MODE_INTERNAL:
        return XYZ.Zero, 0.0
    if origin_mode == ORIGIN_MODE_SURVEY:
        return _base_point_transform(BasePoint.GetSurveyPoint(document))
    return _base_point_transform(BasePoint.GetProjectBasePoint(document))


def survey_to_internal_point(x_m, y_m, z_m, easting_m, northing_m, angle_deg):
    """Converts a raw survey Easting/Northing/Elevation (x_m, y_m, z_m) into
    a Revit internal-coordinate XYZ, using the exact formula from Trung's
    verified Excel sheet:
        local_x = (x - easting) * cos(angle) + (y - northing) * sin(angle)
        local_y = -(x - easting) * sin(angle) + (y - northing) * cos(angle)
        local_z = z   (elevation passes through unchanged - never rotates)
    The result (local_x, local_y, local_z) is treated as a direct internal
    coordinate (matching how Dynamo's Point.ByCoordinates consumed the
    equivalent G/H/I columns with no further transform)."""
    angle = math.radians(angle_deg)
    dx = x_m - easting_m
    dy = y_m - northing_m
    local_x = dx * math.cos(angle) + dy * math.sin(angle)
    local_y = -dx * math.sin(angle) + dy * math.cos(angle)
    return XYZ(m_to_internal(local_x), m_to_internal(local_y), m_to_internal(z_m))


def offset_to_internal_point(origin, angle, dx_ft, dy_ft, dz_ft):
    """Rotates an (dx, dy) offset by `angle` (radians) around Z and adds it
    to `origin`, returning the resulting absolute internal-coordinate XYZ."""
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    rx = dx_ft * cos_a - dy_ft * sin_a
    ry = dx_ft * sin_a + dy_ft * cos_a
    return XYZ(origin.X + rx, origin.Y + ry, origin.Z + dz_ft)


def parse_coordinate(value):
    """Parses a single X/Y/Z cell value (from Excel or manual entry) into
    a float. Raises ValueError with a readable reason on failure."""
    if value is None:
        raise ValueError("Missing value")
    if isinstance(value, string_types):
        text = value.strip()
        if text == "":
            raise ValueError("Missing value")
        try:
            return float(text)
        except ValueError:
            raise ValueError("Not a number: '{}'".format(text))
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError("Not a number: '{}'".format(value))


def _col_ref_to_index(cell_ref):
    """'C5' -> 3 (1-based column index, from the leading letters of a cell
    reference like Excel's 'A1' notation)."""
    letters = ""
    for ch in cell_ref:
        if ch.isalpha():
            letters += ch
        else:
            break
    idx = 0
    for ch in letters.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx


def _load_xml_entry(archive, entry_name):
    entry = archive.GetEntry(entry_name)
    if entry is None:
        return None
    stream = entry.Open()
    try:
        xml_doc = XmlDocument()
        xml_doc.Load(stream)
        return xml_doc
    finally:
        stream.Close()


def read_excel_points(filepath):
    """Reads sheet 'Data' from an .xlsx file, auto-detecting the X, Y, Z
    columns by header name (case-insensitive). Returns a list of
    {"x":.., "y":.., "z":..} raw cell values (not yet validated as floats).
    Raises ValueError with a readable message on structural problems.

    Implemented with plain .NET zip/XML APIs (an .xlsx is a zip of XML
    files) instead of a Python Excel library, so it runs on pyRevit's
    IronPython engine with no extra dependency to install."""
    archive = ZipFile.OpenRead(filepath)
    try:
        wb_xml = _load_xml_entry(archive, "xl/workbook.xml")
        if wb_xml is None:
            raise ValueError("Not a valid .xlsx file (missing workbook.xml).")

        wb_ns = XmlNamespaceManager(wb_xml.NameTable)
        wb_ns.AddNamespace("m", NS_MAIN)
        wb_ns.AddNamespace("r", NS_DOC_REL)

        sheet_node = None
        for node in wb_xml.SelectNodes("//m:sheets/m:sheet", wb_ns):
            name_attr = node.Attributes["name"]
            if name_attr is not None and name_attr.Value.strip().lower() == "data":
                sheet_node = node
                break
        if sheet_node is None:
            raise ValueError("Workbook has no sheet named 'Data'.")

        rid_attr = sheet_node.Attributes.GetNamedItem("id", NS_DOC_REL)
        if rid_attr is None:
            raise ValueError("Could not read sheet reference for 'Data'.")
        rid_value = rid_attr.Value

        rels_xml = _load_xml_entry(archive, "xl/_rels/workbook.xml.rels")
        if rels_xml is None:
            raise ValueError("Workbook is missing its relationships part.")
        rels_ns = XmlNamespaceManager(rels_xml.NameTable)
        rels_ns.AddNamespace("rel", NS_PKG_REL)

        target = None
        for rel_node in rels_xml.SelectNodes("//rel:Relationship", rels_ns):
            if rel_node.Attributes["Id"].Value == rid_value:
                target = rel_node.Attributes["Target"].Value
                break
        if target is None:
            raise ValueError("Could not resolve the file path for sheet 'Data'.")

        sheet_path = "xl/" + target.lstrip("/")

        shared_strings = []
        ss_xml = _load_xml_entry(archive, "xl/sharedStrings.xml")
        if ss_xml is not None:
            ss_ns = XmlNamespaceManager(ss_xml.NameTable)
            ss_ns.AddNamespace("m", NS_MAIN)
            for si in ss_xml.SelectNodes("//m:si", ss_ns):
                parts = [t.InnerText for t in si.SelectNodes(".//m:t", ss_ns)]
                shared_strings.append("".join(parts))

        sheet_xml = _load_xml_entry(archive, sheet_path)
        if sheet_xml is None:
            raise ValueError("Could not read data for sheet 'Data'.")
        sheet_ns = XmlNamespaceManager(sheet_xml.NameTable)
        sheet_ns.AddNamespace("m", NS_MAIN)

        def cell_value(cell_node):
            t_attr = cell_node.Attributes["t"]
            t_val = t_attr.Value if t_attr is not None else None
            if t_val == "s":
                v_node = cell_node.SelectSingleNode("m:v", sheet_ns)
                if v_node is None:
                    return None
                idx = int(v_node.InnerText)
                return shared_strings[idx] if 0 <= idx < len(shared_strings) else None
            if t_val == "inlineStr":
                is_node = cell_node.SelectSingleNode("m:is/m:t", sheet_ns)
                return is_node.InnerText if is_node is not None else None
            v_node = cell_node.SelectSingleNode("m:v", sheet_ns)
            return v_node.InnerText if v_node is not None else None

        row_nodes = sheet_xml.SelectNodes("//m:sheetData/m:row", sheet_ns)
        if row_nodes is None or row_nodes.Count == 0:
            raise ValueError("Sheet 'Data' is empty.")

        col_map = {}
        for cell_node in row_nodes[0].SelectNodes("m:c", sheet_ns):
            ref_attr = cell_node.Attributes["r"]
            if ref_attr is None:
                continue
            value = cell_value(cell_node)
            if value is None:
                continue
            key = value.strip().upper()
            if key in ("X", "Y", "Z"):
                col_map[key] = _col_ref_to_index(ref_attr.Value)

        missing = [k for k in ("X", "Y", "Z") if k not in col_map]
        if missing:
            raise ValueError(
                "Missing column(s) in 'Data' sheet: {}".format(", ".join(missing))
            )

        rows = []
        for i in range(1, row_nodes.Count):
            row_values = {}
            for cell_node in row_nodes[i].SelectNodes("m:c", sheet_ns):
                ref_attr = cell_node.Attributes["r"]
                if ref_attr is None:
                    continue
                row_values[_col_ref_to_index(ref_attr.Value)] = cell_value(cell_node)

            x_val = row_values.get(col_map["X"])
            y_val = row_values.get(col_map["Y"])
            z_val = row_values.get(col_map["Z"])
            if x_val is None and y_val is None and z_val is None:
                continue  # skip fully blank row
            rows.append({"x": x_val, "y": y_val, "z": z_val})
        return rows
    finally:
        archive.Dispose()


def create_point_marker_family(document, pt):
    """Creates a Reference Point at `pt` in a Family document (unchanged
    from v1 - families have no Project Base Point / Adaptive Component
    concept to route through)."""
    return document.FamilyCreate.NewReferencePoint(pt)


def _pyramid_family_path():
    """Absolute path to "Add Points tool.rfa" - Trung's own hand-built
    adaptive marker family, shipped next to this script.py so the tool is
    self-contained (no template auto-detection, no auto-generated
    geometry)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), PYRAMID_FAMILY_FILE)


class _FamilyLoadOptions(IFamilyLoadOptions):
    """Always confirms the load/reload of "Add Points tool.rfa" without
    prompting, whether it's a brand-new load or the family is already in
    the target document. Note: `overwriteParameterValues = True` here is
    NOT about overwriting Trung's geometry (the file on disk never
    changes) - it's the flag Revit's API uses to reliably hand back the
    Family reference on a repeat load. Declining it (False) caused
    LoadFamily to intermittently return no Family object on the 2nd+ run
    in the same document - this is the fix for that."""

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues.Value = True
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        source.Value = FamilySource.Family
        overwriteParameterValues.Value = True
        return True


def _find_loaded_family_by_filename(document):
    """Fallback used only if LoadFamily's out-parameter Family reference
    comes back empty despite a successful load: scans already-loaded
    families in `document` for one whose name matches the .rfa file name
    (Revit's default family name is the file name unless Trung renamed it
    inside the Family Editor)."""
    expected = os.path.splitext(PYRAMID_FAMILY_FILE)[0]
    collector = FilteredElementCollector(document).OfClass(Family)
    for fam in collector:
        try:
            if fam.Name == expected:
                return fam
        except Exception:
            continue
    return None


def get_or_create_pyramid_symbol(document):
    """Loads (or reuses an already-loaded copy of) "Add Points tool.rfa"
    into `document` and returns an active FamilySymbol from it. The
    family's geometry (a small triangular marker with its single Adaptive
    Point sitting exactly on the sharp corner / family origin) is entirely
    authored by Trung in the family editor - this tool never builds or
    modifies that geometry, only loads the file and places instances."""
    family_path = _pyramid_family_path()
    if not os.path.exists(family_path):
        raise ValueError(
            "Missing '{}' next to script.py - this file should ship "
            "alongside the Add Points tool. Ask Claude to resend it.".format(
                PYRAMID_FAMILY_FILE
            )
        )

    t_load = Transaction(document, "Load Add Points tool family")
    t_load.Start()
    try:
        loaded, family = document.LoadFamily(family_path, _FamilyLoadOptions())
    except Exception:
        if t_load.HasStarted():
            t_load.RollBack()
        raise
    t_load.Commit()

    if family is None:
        # Belt-and-suspenders: the load itself may still have succeeded
        # even if LoadFamily's out-parameter came back empty (seen when
        # the family was already present in the document) - look it up
        # directly before giving up.
        family = _find_loaded_family_by_filename(document)

    if family is None:
        raise ValueError(
            "Could not load '{}' into the target document.".format(PYRAMID_FAMILY_FILE)
        )

    symbol_ids = list(family.GetFamilySymbolIds())
    if not symbol_ids:
        raise ValueError(
            "'{}' loaded but contains no family type.".format(PYRAMID_FAMILY_FILE)
        )
    symbol = document.GetElement(symbol_ids[0])

    if not symbol.IsActive:
        t_act = Transaction(document, "Activate Add Points tool symbol")
        t_act.Start()
        symbol.Activate()
        document.Regenerate()
        t_act.Commit()
    return symbol


def create_adaptive_pyramid(document, symbol, point):
    """Places one instance of the adaptive pyramid marker family and moves
    its single Adaptive Point to `point` (already an internal-coordinate
    XYZ) - so the imported X,Y,Z becomes exactly that Adaptive Point, the
    corner of the triangular block, matching how the earlier Dynamo/family
    workflow identified the "real" input coordinate."""
    instance = AdaptiveComponentInstanceUtils.CreateAdaptiveComponentInstance(document, symbol)
    point_ids = AdaptiveComponentInstanceUtils.GetInstancePlacementPointElementRefIds(instance)
    if point_ids.Count < 1:
        raise ValueError("Adaptive family has no placement points.")
    rp = document.GetElement(point_ids[0])
    rp.Position = point
    return instance.Id


def create_points(document, rows, origin_mode=ORIGIN_MODE_CUSTOM, custom_origin=None):
    """rows: list of dict {"x":.., "y":.., "z":..} raw values (string/number).

    Family document -> Reference Points. Project document -> instances of
    "Add Points tool.rfa" (Trung's hand-built Adaptive family), with the
    family's single Adaptive Point moved to the computed coordinate.

    Both document types share the SAME origin-conversion logic:
      - ORIGIN_MODE_CUSTOM: `custom_origin` = (easting_m, northing_m, angle_deg)
        and each row's raw X,Y,Z is treated as an absolute SURVEY coordinate,
        converted with `survey_to_internal_point` (Trung's verified Excel
        formula). Available for both Family and Project.
      - ORIGIN_MODE_INTERNAL: raw X,Y,Z used directly as an internal
        coordinate, no offset/rotation. Available for both Family and
        Project.
      - ORIGIN_MODE_PROJECT_BASE / ORIGIN_MODE_SURVEY: offset+rotate from
        that BasePoint element. Project documents only (Family documents
        have no such element).

    Runs all rows in a single Transaction; per-row failures are recorded
    in the returned list without stopping the rest of the batch.

    Also returns a debug dict {"origin_m": (x,y,z) or None, "angle_deg": ..}
    describing the resolved origin used, in meters/degrees, so Trung can
    verify what the tool actually found instead of guessing."""
    is_family = document.IsFamilyDocument
    is_custom = origin_mode == ORIGIN_MODE_CUSTOM
    is_internal = origin_mode == ORIGIN_MODE_INTERNAL

    origin, angle = (XYZ.Zero, 0.0)
    if not is_family and not is_custom and not is_internal:
        origin, angle = get_origin_transform(document, origin_mode)

    if is_custom:
        easting_m, northing_m, angle_deg = custom_origin

    symbol = None
    if not is_family:
        symbol = get_or_create_pyramid_symbol(document)

    results = []
    t = Transaction(document, "pyNBT - {}".format(TOOL_NAME))
    t.Start()
    try:
        for row in rows:
            try:
                x_m = parse_coordinate(row.get("x"))
                y_m = parse_coordinate(row.get("y"))
                z_m = parse_coordinate(row.get("z"))

                if is_custom:
                    pt = survey_to_internal_point(x_m, y_m, z_m, easting_m, northing_m, angle_deg)
                elif is_internal:
                    pt = XYZ(m_to_internal(x_m), m_to_internal(y_m), m_to_internal(z_m))
                else:
                    dx_ft = m_to_internal(x_m)
                    dy_ft = m_to_internal(y_m)
                    dz_ft = m_to_internal(z_m)
                    pt = offset_to_internal_point(origin, angle, dx_ft, dy_ft, dz_ft)

                if is_family:
                    create_point_marker_family(document, pt)
                else:
                    create_adaptive_pyramid(document, symbol, pt)

                results.append(("Success", ""))
            except Exception as row_ex:
                results.append(("Failed", str(row_ex)))
        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        logger.error("Add Points transaction failed: {}".format(ex))
        results = [("Failed", "Transaction rolled back: {}".format(ex))] * len(rows)

    if is_custom:
        debug_info = {
            "origin_m": (custom_origin[0], custom_origin[1], 0.0),
            "angle_deg": custom_origin[2],
        }
    elif is_internal:
        debug_info = {"origin_m": (0.0, 0.0, 0.0), "angle_deg": 0.0}
    else:
        debug_info = {
            "origin_m": (internal_to_m(origin.X), internal_to_m(origin.Y), internal_to_m(origin.Z)),
            "angle_deg": math.degrees(angle),
        }
    return results, debug_info


# ---------------------------------------------------------------------------
# 3. UI - orchestrates the table + import + create actions
# ---------------------------------------------------------------------------

def _row_dg(height=GridLength(1, GridUnitType.Auto)):
    rd = RowDefinition()
    rd.Height = height
    return rd


def _col_dg(width=GridLength(1, GridUnitType.Star)):
    cd = ColumnDefinition()
    cd.Width = width
    return cd


def _make_btn(text, bg_color, fg_color, width=120, height=32):
    btn = Button()
    btn.Content = text
    btn.Width = width
    btn.Height = height
    btn.Background = brush(bg_color)
    btn.Foreground = brush(fg_color)
    btn.BorderThickness = Thickness(0)
    btn.Margin = Thickness(6, 0, 0, 0)
    btn.FontWeight = FontWeights.SemiBold
    return btn


def _make_label(text):
    tb = TextBlock()
    tb.Text = text
    tb.Foreground = brush(CLR_TEXT)
    tb.VerticalAlignment = VerticalAlignment.Center
    tb.Margin = Thickness(0, 0, 6, 0)
    return tb


def _make_small_box(text, width=90):
    box = TextBox()
    box.Text = text
    box.Width = width
    box.VerticalAlignment = VerticalAlignment.Center
    box.Margin = Thickness(0, 0, 12, 0)
    return box


class AddPointsWindow(Window):
    def __init__(self):
        Window.__init__(self)
        self.Title = TOOL_NAME
        self.Width = 860
        self.Height = 630
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.Background = brush(CLR_BG)

        self.table = self._build_table()
        self._next_no = 1
        self._doc_items = list_open_documents()
        self.target_doc = doc

        root = Grid()
        root.RowDefinitions.Add(_row_dg())
        root.RowDefinitions.Add(_row_dg(GridLength(1, GridUnitType.Star)))
        root.RowDefinitions.Add(_row_dg())

        header = self._build_header()
        root.Children.Add(header)
        Grid.SetRow(header, 0)

        content = self._build_content()
        root.Children.Add(content)
        Grid.SetRow(content, 1)

        footer = self._build_footer()
        root.Children.Add(footer)
        Grid.SetRow(footer, 2)

        self.Content = root
        self._refresh_mode_ui()

    # -- DataTable model -----------------------------------------------
    def _build_table(self):
        dt = DataTable()
        dt.Columns.Add("No", int)
        dt.Columns.Add("X", str)
        dt.Columns.Add("Y", str)
        dt.Columns.Add("Z", str)
        dt.Columns.Add("Status", str)
        return dt

    def _add_row(self, x="", y="", z="", status=""):
        row = self.table.NewRow()
        row["No"] = self._next_no
        row["X"] = x
        row["Y"] = y
        row["Z"] = z
        row["Status"] = status
        self.table.Rows.Add(row)
        self._next_no += 1

    def _clear_rows(self):
        self.table.Rows.Clear()
        self._next_no = 1

    # -- Header -----------------------------------------------------
    def _build_header(self):
        border = Border()
        border.Background = brush(CLR_HEADER)
        border.Padding = Thickness(20, 14, 20, 14)

        grid = Grid()
        grid.ColumnDefinitions.Add(_col_dg())
        grid.ColumnDefinitions.Add(_col_dg(GridLength(1, GridUnitType.Auto)))

        left = StackPanel()
        title = TextBlock()
        title.Text = TOOL_NAME
        title.FontSize = 20
        title.FontWeight = FontWeights.Bold
        title.Foreground = brush(CLR_HEADER_TEXT)
        subtitle = TextBlock()
        subtitle.Text = (
            "Family: Reference Point | Project: Adaptive pyramid marker "
            "(coordinates in meters, converted using the Origin picked below)"
        )
        subtitle.FontSize = 12
        subtitle.Foreground = brush(CLR_HEADER_SUB)
        subtitle.Margin = Thickness(0, 4, 0, 0)
        subtitle.TextWrapping = TextWrapping.Wrap
        left.Children.Add(title)
        left.Children.Add(subtitle)

        badge = TextBlock()
        badge.Text = "{} {}".format(TOOL_NAME, TOOL_VERSION)
        badge.FontSize = 11
        badge.Foreground = brush(CLR_HEADER_SUB)
        badge.VerticalAlignment = VerticalAlignment.Center

        grid.Children.Add(left)
        Grid.SetColumn(left, 0)
        grid.Children.Add(badge)
        Grid.SetColumn(badge, 1)

        border.Child = grid
        return border

    # -- Content ------------------------------------------------------
    def _build_content(self):
        border = Border()
        border.Background = brush(CLR_CARD)
        border.Margin = Thickness(16)
        border.Padding = Thickness(16)
        border.BorderBrush = brush(CLR_BORDER)
        border.BorderThickness = Thickness(1)

        grid = Grid()
        grid.RowDefinitions.Add(_row_dg())
        grid.RowDefinitions.Add(_row_dg())
        grid.RowDefinitions.Add(_row_dg())
        grid.RowDefinitions.Add(_row_dg(GridLength(1, GridUnitType.Star)))
        grid.RowDefinitions.Add(_row_dg())

        # top bar: target model + origin reference (left) / Add-Delete row (right)
        top_bar = Grid()
        top_bar.ColumnDefinitions.Add(_col_dg())
        top_bar.ColumnDefinitions.Add(_col_dg(GridLength(1, GridUnitType.Auto)))
        top_bar.Margin = Thickness(0, 0, 0, 4)

        left_panel = StackPanel()
        left_panel.Orientation = Orientation.Horizontal

        left_panel.Children.Add(_make_label("Target model:"))
        self.doc_combo = ComboBox()
        self.doc_combo.Width = 240
        self.doc_combo.Margin = Thickness(0, 0, 16, 0)
        default_index = 0
        for idx, (label, d) in enumerate(self._doc_items):
            item = ComboBoxItem()
            item.Content = label
            self.doc_combo.Items.Add(item)
            if d.Equals(doc):
                default_index = idx
        if self._doc_items:
            self.doc_combo.SelectedIndex = default_index
        self.doc_combo.SelectionChanged += self.on_target_doc_changed
        left_panel.Children.Add(self.doc_combo)

        left_panel.Children.Add(_make_label("Origin:"))
        self.origin_combo = ComboBox()
        self.origin_combo.Width = 190
        left_panel.Children.Add(self.origin_combo)

        top_bar.Children.Add(left_panel)
        Grid.SetColumn(left_panel, 0)

        row_btns = StackPanel()
        row_btns.Orientation = Orientation.Horizontal
        row_btns.HorizontalAlignment = HorizontalAlignment.Right

        btn_add = _make_btn("Add Row", CLR_CARD, CLR_TEXT, width=100)
        btn_add.BorderBrush = brush(CLR_BORDER)
        btn_add.BorderThickness = Thickness(1)
        btn_add.Click += self.on_add_row

        btn_del = _make_btn("Delete Row", CLR_CARD, CLR_TEXT, width=100)
        btn_del.BorderBrush = brush(CLR_BORDER)
        btn_del.BorderThickness = Thickness(1)
        btn_del.Click += self.on_delete_row

        row_btns.Children.Add(btn_add)
        row_btns.Children.Add(btn_del)

        top_bar.Children.Add(row_btns)
        Grid.SetColumn(row_btns, 1)

        grid.Children.Add(top_bar)
        Grid.SetRow(top_bar, 0)

        # Custom (Survey formula) row: named presets (one per project) +
        # the raw Easting/Northing/Angle inputs. Only shown when "Custom
        # (Survey formula)" is the selected Origin mode.
        self.custom_panel = StackPanel()
        self.custom_panel.Orientation = Orientation.Horizontal
        self.custom_panel.Margin = Thickness(0, 4, 0, 8)

        self.custom_panel.Children.Add(_make_label("Preset:"))
        self.preset_combo = ComboBox()
        self.preset_combo.Width = 170
        self.preset_combo.Margin = Thickness(0, 0, 8, 0)
        self.custom_panel.Children.Add(self.preset_combo)

        btn_save_preset = _make_btn("Save preset...", CLR_CARD, CLR_TEXT, width=110, height=26)
        btn_save_preset.BorderBrush = brush(CLR_BORDER)
        btn_save_preset.BorderThickness = Thickness(1)
        btn_save_preset.Click += self.on_save_preset
        self.custom_panel.Children.Add(btn_save_preset)

        btn_delete_preset = _make_btn("Delete", CLR_CARD, CLR_TEXT, width=80, height=26)
        btn_delete_preset.BorderBrush = brush(CLR_BORDER)
        btn_delete_preset.BorderThickness = Thickness(1)
        btn_delete_preset.Margin = Thickness(6, 0, 16, 0)
        btn_delete_preset.Click += self.on_delete_preset
        self.custom_panel.Children.Add(btn_delete_preset)

        self.custom_panel.Children.Add(_make_label("Easting T (m):"))
        self.txt_easting = _make_small_box(str(DEFAULT_CUSTOM_EASTING_M), width=110)
        self.custom_panel.Children.Add(self.txt_easting)

        self.custom_panel.Children.Add(_make_label("Northing U (m):"))
        self.txt_northing = _make_small_box(str(DEFAULT_CUSTOM_NORTHING_M), width=110)
        self.custom_panel.Children.Add(self.txt_northing)

        self.custom_panel.Children.Add(_make_label("Angle V (deg):"))
        self.txt_angle = _make_small_box(str(DEFAULT_CUSTOM_ANGLE_DEG), width=90)
        self.custom_panel.Children.Add(self.txt_angle)

        grid.Children.Add(self.custom_panel)
        Grid.SetRow(self.custom_panel, 1)

        # mode indicator line
        self.mode_label = TextBlock()
        self.mode_label.FontSize = 11
        self.mode_label.FontWeight = FontWeights.SemiBold
        self.mode_label.Foreground = brush(CLR_MUTED)
        self.mode_label.Margin = Thickness(0, 0, 0, 8)
        grid.Children.Add(self.mode_label)
        Grid.SetRow(self.mode_label, 2)

        # data grid
        self.grid_view = DataGrid()
        self.grid_view.ItemsSource = self.table.DefaultView
        self.grid_view.AutoGenerateColumns = False
        self.grid_view.CanUserAddRows = False
        self.grid_view.CanUserDeleteRows = False
        self.grid_view.SelectionMode = DataGridSelectionMode.Extended
        self.grid_view.RowHeight = 28

        col_no = DataGridTextColumn()
        col_no.Header = "No"
        col_no.Binding = Binding("No")
        col_no.IsReadOnly = True
        col_no.Width = DataGridLength(50)

        col_x = DataGridTextColumn()
        col_x.Header = "X (m)"
        col_x.Binding = Binding("X")

        col_y = DataGridTextColumn()
        col_y.Header = "Y (m)"
        col_y.Binding = Binding("Y")

        col_z = DataGridTextColumn()
        col_z.Header = "Z (m)"
        col_z.Binding = Binding("Z")

        col_status = DataGridTextColumn()
        col_status.Header = "Status"
        col_status.Binding = Binding("Status")
        col_status.IsReadOnly = True
        col_status.Width = DataGridLength(220)

        for c in (col_x, col_y, col_z):
            c.Width = DataGridLength(1, DataGridLengthUnitType.Star)

        self.grid_view.Columns.Add(col_no)
        self.grid_view.Columns.Add(col_x)
        self.grid_view.Columns.Add(col_y)
        self.grid_view.Columns.Add(col_z)
        self.grid_view.Columns.Add(col_status)

        grid.Children.Add(self.grid_view)
        Grid.SetRow(self.grid_view, 3)

        # summary line
        self.summary_text = TextBlock()
        self.summary_text.Text = "No points created yet."
        self.summary_text.Foreground = brush(CLR_MUTED)
        self.summary_text.Margin = Thickness(0, 8, 0, 0)
        grid.Children.Add(self.summary_text)
        Grid.SetRow(self.summary_text, 4)

        border.Child = grid
        return border

    # -- Footer ---------------------------------------------------------
    def _build_footer(self):
        border = Border()
        border.Background = brush(CLR_FOOTER)
        border.Padding = Thickness(16, 10, 16, 10)

        grid = Grid()
        grid.ColumnDefinitions.Add(_col_dg())
        grid.ColumnDefinitions.Add(_col_dg(GridLength(1, GridUnitType.Auto)))

        # left: import excel button + signature
        left = StackPanel()
        left.Orientation = Orientation.Horizontal

        btn_import = _make_btn("Import Excel...", CLR_CARD, CLR_TEXT, width=140)
        btn_import.BorderBrush = brush(CLR_BORDER)
        btn_import.BorderThickness = Thickness(1)
        btn_import.Margin = Thickness(0, 0, 12, 0)
        btn_import.Click += self.on_import_excel
        left.Children.Add(btn_import)

        self.sig_text = TextBlock()
        self.sig_text.Foreground = brush(CLR_MUTED)
        self.sig_text.FontSize = 11
        self.sig_text.VerticalAlignment = VerticalAlignment.Center
        left.Children.Add(self.sig_text)

        # right: reset / close / create
        right = StackPanel()
        right.Orientation = Orientation.Horizontal

        btn_reset = _make_btn("Reset", CLR_CARD, CLR_TEXT, width=90)
        btn_reset.BorderBrush = brush(CLR_BORDER)
        btn_reset.BorderThickness = Thickness(1)
        btn_reset.Click += self.on_reset

        btn_close = _make_btn("Close", CLR_CARD, CLR_TEXT, width=90)
        btn_close.BorderBrush = brush(CLR_BORDER)
        btn_close.BorderThickness = Thickness(1)
        btn_close.Click += self.on_close

        btn_create = _make_btn("Create Points", CLR_APPLY, CLR_APPLY_TEXT, width=140)
        btn_create.Click += self.on_create_points

        right.Children.Add(btn_reset)
        right.Children.Add(btn_close)
        right.Children.Add(btn_create)

        grid.Children.Add(left)
        Grid.SetColumn(left, 0)
        grid.Children.Add(right)
        Grid.SetColumn(right, 1)

        border.Child = grid
        return border

    # -- Mode / target document -------------------------------------------
    def _populate_origin_combo(self):
        """Rebuilds the Origin dropdown's items for the current target
        document: Family documents only offer Internal Origin / Custom
        (they have no Project Base Point or Survey Point); Project
        documents offer all 4. Keeps the current selection if it's still
        valid in the new list, otherwise defaults to "Custom (Survey
        formula)" - the mode that matches Trung's real workflow."""
        is_family = self.target_doc.IsFamilyDocument
        modes = FAMILY_ORIGIN_MODES if is_family else PROJECT_ORIGIN_MODES

        previous = None
        if self.origin_combo.SelectedItem is not None:
            previous = self.origin_combo.SelectedItem.Content

        self.origin_combo.SelectionChanged -= self.on_origin_changed
        self.origin_combo.Items.Clear()
        for mode in modes:
            item = ComboBoxItem()
            item.Content = mode
            self.origin_combo.Items.Add(item)
        if previous in modes:
            self.origin_combo.SelectedIndex = modes.index(previous)
        else:
            self.origin_combo.SelectedIndex = modes.index(ORIGIN_MODE_CUSTOM)
        self.origin_combo.SelectionChanged += self.on_origin_changed

    def _populate_preset_combo(self):
        """Rebuilds the preset dropdown from saved config, and auto-selects
        (then fills the T/U/V boxes from) a preset whose name matches the
        current target document's title exactly, if one exists - so
        re-opening the same project's model tends to "just work" without
        Trung having to remember which preset to pick."""
        self._presets = _load_presets()
        names = sorted(self._presets.keys())

        self.preset_combo.SelectionChanged -= self.on_preset_changed
        self.preset_combo.Items.Clear()
        none_item = ComboBoxItem()
        none_item.Content = PRESET_NONE_LABEL
        self.preset_combo.Items.Add(none_item)
        for name in names:
            item = ComboBoxItem()
            item.Content = name
            self.preset_combo.Items.Add(item)

        match_index = 0
        title = self.target_doc.Title.strip().lower()
        for idx, name in enumerate(names, start=1):
            if name.strip().lower() == title:
                match_index = idx
                break
        self.preset_combo.SelectedIndex = match_index
        self.preset_combo.SelectionChanged += self.on_preset_changed
        self._apply_selected_preset()

    def _apply_selected_preset(self):
        item = self.preset_combo.SelectedItem
        name = item.Content if item is not None else None
        if not name or name == PRESET_NONE_LABEL:
            return
        preset = self._presets.get(name)
        if not preset:
            return
        self.txt_easting.Text = str(preset.get("easting", DEFAULT_CUSTOM_EASTING_M))
        self.txt_northing.Text = str(preset.get("northing", DEFAULT_CUSTOM_NORTHING_M))
        self.txt_angle.Text = str(preset.get("angle", DEFAULT_CUSTOM_ANGLE_DEG))

    def _refresh_mode_ui(self):
        is_family = self.target_doc.IsFamilyDocument
        if is_family:
            self.mode_label.Text = "Mode: Family Editor -> creates Reference Points"
        else:
            self.mode_label.Text = "Mode: Project -> places 'Add Points tool.rfa' Adaptive marker family"

        self._populate_origin_combo()
        self._populate_preset_combo()
        self._update_custom_visibility()

        self.sig_text.Text = "{} {} | {} | {}".format(
            TOOL_NAME, TOOL_VERSION, self.target_doc.Title,
            datetime.datetime.now().strftime("%Y-%m-%d"),
        )

    def _update_custom_visibility(self):
        """Shows the Easting/Northing/Angle boxes whenever "Custom (Survey
        formula)" is the selected Origin mode - available for both Family
        and Project targets."""
        origin_item = self.origin_combo.SelectedItem
        origin_mode = origin_item.Content if origin_item is not None else None
        show_custom = origin_mode == ORIGIN_MODE_CUSTOM
        self.custom_panel.Visibility = (
            Visibility.Visible if show_custom else Visibility.Collapsed
        )

    def on_target_doc_changed(self, sender, args):
        idx = self.doc_combo.SelectedIndex
        if idx < 0 or idx >= len(self._doc_items):
            return
        _, selected_doc = self._doc_items[idx]
        self.target_doc = selected_doc
        self._refresh_mode_ui()

    def on_origin_changed(self, sender, args):
        self._update_custom_visibility()

    def on_preset_changed(self, sender, args):
        self._apply_selected_preset()

    def on_save_preset(self, sender, args):
        try:
            easting_m = float(self.txt_easting.Text.strip())
            northing_m = float(self.txt_northing.Text.strip())
            angle_deg = float(self.txt_angle.Text.strip())
        except (ValueError, AttributeError):
            forms.alert(
                "Easting / Northing / Angle must be numbers before saving a preset.",
                title=TOOL_NAME,
            )
            return

        name = forms.ask_for_string(
            default=self.target_doc.Title,
            prompt="Preset name (e.g. project name):",
            title=TOOL_NAME,
        )
        if not name:
            return
        name = name.strip()
        if not name:
            return

        self._presets[name] = {
            "easting": easting_m, "northing": northing_m, "angle": angle_deg,
        }
        _save_presets(self._presets)
        self._populate_preset_combo()

        for idx in range(self.preset_combo.Items.Count):
            if self.preset_combo.Items[idx].Content == name:
                self.preset_combo.SelectionChanged -= self.on_preset_changed
                self.preset_combo.SelectedIndex = idx
                self.preset_combo.SelectionChanged += self.on_preset_changed
                break

        forms.alert("Saved preset '{}'.".format(name), title=TOOL_NAME)

    def on_delete_preset(self, sender, args):
        item = self.preset_combo.SelectedItem
        name = item.Content if item is not None else None
        if not name or name == PRESET_NONE_LABEL:
            forms.alert("Select a saved preset first.", title=TOOL_NAME)
            return
        confirmed = forms.alert(
            "Delete preset '{}'?".format(name), title=TOOL_NAME, yes=True, no=True,
        )
        if not confirmed:
            return
        self._presets.pop(name, None)
        _save_presets(self._presets)
        self._populate_preset_combo()

    # -- Event handlers --------------------------------------------------
    def on_add_row(self, sender, args):
        self._add_row()

    def on_delete_row(self, sender, args):
        selected = list(self.grid_view.SelectedItems)
        for row_view in selected:
            self.table.Rows.Remove(row_view.Row)

    def on_reset(self, sender, args):
        result = forms.alert(
            "Clear all rows in the table?", title=TOOL_NAME, yes=True, no=True
        )
        if result:
            self._clear_rows()
            self.summary_text.Text = "No points created yet."

    def on_close(self, sender, args):
        self.Close()

    def on_import_excel(self, sender, args):
        filepath = forms.pick_file(file_ext="xlsx")
        if not filepath:
            return
        try:
            rows = read_excel_points(filepath)
        except ValueError as ex:
            forms.alert(str(ex), title=TOOL_NAME)
            return
        except Exception as ex:
            forms.alert("Could not read file:\n{}".format(ex), title=TOOL_NAME)
            return

        if not rows:
            forms.alert("No data rows found in sheet 'Data'.", title=TOOL_NAME)
            return

        self._clear_rows()
        for r in rows:
            self._add_row(x=str(r["x"]), y=str(r["y"]), z=str(r["z"]))

        self.summary_text.Text = "Imported {} row(s) from {}.".format(
            len(rows), os.path.basename(filepath)
        )

    def on_create_points(self, sender, args):
        row_count = self.table.Rows.Count
        if row_count == 0:
            forms.alert("Add or import at least one point first.", title=TOOL_NAME)
            return

        target_doc = self.target_doc
        origin_item = self.origin_combo.SelectedItem
        origin_mode = origin_item.Content if origin_item is not None else ORIGIN_MODE_CUSTOM

        custom_origin = None
        if origin_mode == ORIGIN_MODE_CUSTOM:
            try:
                easting_m = float(self.txt_easting.Text.strip())
                northing_m = float(self.txt_northing.Text.strip())
                angle_deg = float(self.txt_angle.Text.strip())
            except (ValueError, AttributeError):
                forms.alert(
                    "Easting / Northing / Angle must be numbers.",
                    title=TOOL_NAME,
                )
                return
            custom_origin = (easting_m, northing_m, angle_deg)

        rows_data = []
        for row in self.table.Rows:
            rows_data.append({"x": row["X"], "y": row["Y"], "z": row["Z"]})

        try:
            results, debug_info = create_points(
                target_doc, rows_data, origin_mode, custom_origin
            )
        except ValueError as ex:
            forms.alert(str(ex), title=TOOL_NAME)
            return

        success_count = 0
        for row, (status, message) in zip(self.table.Rows, results):
            row["Status"] = status if not message else "{}: {}".format(status, message)
            if status == "Success":
                success_count += 1

        self.table.AcceptChanges()
        fail_count = row_count - success_count

        origin_note = ""
        if debug_info.get("origin_m") is not None:
            ox, oy, oz = debug_info["origin_m"]
            if origin_mode == ORIGIN_MODE_CUSTOM:
                origin_note = (
                    " | {} used Easting(T)={:.4f} Northing(U)={:.4f} m, "
                    "angle(V)={:.4f} deg".format(
                        origin_mode, ox, oy, debug_info["angle_deg"]
                    )
                )
            else:
                origin_note = " | {} resolved to X={:.4f} Y={:.4f} Z={:.4f} m, angle={:.4f} deg".format(
                    origin_mode, ox, oy, oz, debug_info["angle_deg"]
                )

        self.summary_text.Text = "Created {} point(s), {} failed. Target: {}{}".format(
            success_count, fail_count, target_doc.Title, origin_note
        )


if __name__ == "__main__":
    if doc is None:
        forms.alert("No active document.", title=TOOL_NAME)
    else:
        window = AddPointsWindow()
        window.ShowDialog()
