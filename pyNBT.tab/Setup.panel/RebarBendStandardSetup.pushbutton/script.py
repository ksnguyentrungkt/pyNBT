# -*- coding: utf-8 -*-
"""Rebar Bend Standard Setup
Auto-configure the bend diameter settings (StandardBendDiameter,
StandardHookBendDiameter, StirrupTieBendDiameter, MaximumBendRadius) on
every Rebar Bar Type in the current project, based on a selected design
standard's bar-diameter multiplier table.

Intended use: run once at the start of a new project, before any rebar
is placed, so every Rebar Bar Type already carries the correct bend
diameter values instead of NBT entering them by hand one by one.

Data source: TCVN 5574:2018, Clause 10.3.7 "Cac thanh thep uon" (page
143), confirmed against the primary standard PDF. See
rebar_bend_standards.py for the full clause text and the design notes
on how it maps onto Revit's bend-diameter fields.
"""

import os
import sys

# make sure the sibling data file in this bundle folder is importable
_THIS_DIR = os.path.dirname(__file__)
if _THIS_DIR not in sys.path:
    sys.path.append(_THIS_DIR)

import rebar_bend_standards as rbs

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from Autodesk.Revit.DB import Transaction, FilteredElementCollector, UnitUtils, BuiltInParameter, Element
try:
    # Revit 2021+
    from Autodesk.Revit.DB import UnitTypeId
    _MM_UNIT = UnitTypeId.Millimeters
except ImportError:
    # Revit < 2021 fallback
    from Autodesk.Revit.DB import DisplayUnitType
    _MM_UNIT = DisplayUnitType.DUT_MILLIMETERS

from Autodesk.Revit.DB.Structure import RebarBarType, ReinforcementSettings

from System.Windows import (
    Window, WindowStartupLocation, Thickness, FontWeights, GridLength,
    GridUnitType, VerticalAlignment, TextWrapping
)
from System.Windows.Controls import (
    Grid, RowDefinition, ColumnDefinition, Border, StackPanel, TextBlock,
    Button, DataGrid, DataGridTextColumn, DataGridCheckBoxColumn,
    ComboBox, Orientation, ScrollViewer, DataGridLength, TextBox
)
from System.Windows.Data import Binding
from System.Windows.Media import Color, SolidColorBrush
from System.Windows.Interop import WindowInteropHelper
from System.Collections.ObjectModel import ObservableCollection

from pyrevit import forms

doc = __revit__.ActiveUIDocument.Document  # noqa

TOOL_NAME = "Rebar Bend Standard Setup"
TOOL_VERSION = "v3.1 (TCVN + Singapore verified)"

# ---------------------------------------------------------------------------
# 1. Theme constants (pyNBT palette)
# ---------------------------------------------------------------------------
CLR_HEADER = Color.FromRgb(30, 41, 59)
CLR_HEADER_TEXT = Color.FromRgb(255, 255, 255)
CLR_HEADER_SUB = Color.FromRgb(203, 213, 225)
CLR_ACCENT = Color.FromRgb(30, 41, 59)
CLR_BG = Color.FromRgb(248, 249, 250)
CLR_CARD = Color.FromRgb(255, 255, 255)
CLR_BORDER = Color.FromRgb(203, 213, 225)
CLR_FOOTER = Color.FromRgb(241, 245, 249)
CLR_TEXT = Color.FromRgb(30, 30, 30)
CLR_MUTED = Color.FromRgb(120, 120, 120)


def brush(color):
    return SolidColorBrush(color)


# ---------------------------------------------------------------------------
# 2. Standalone logic functions (no UI references)
# ---------------------------------------------------------------------------

def internal_to_mm(value_internal):
    try:
        return UnitUtils.ConvertFromInternalUnits(value_internal, _MM_UNIT)
    except Exception:
        return value_internal * 304.8


def mm_to_internal(value_mm):
    try:
        return UnitUtils.ConvertToInternalUnits(value_mm, _MM_UNIT)
    except Exception:
        return value_mm / 304.8


def fmt_mm(value_internal):
    if value_internal is None:
        return "-"
    try:
        return "{:.0f}".format(internal_to_mm(value_internal))
    except Exception:
        return "-"


def get_element_name(el):
    """Read an element's Name safely.

    IronPython has a known bug (seen across the pyRevit/Revit API
    community, not specific to this tool) where ElementType.Name -
    which RebarBarType inherits - throws "MissingMemberException: Name"
    when READ directly, even though writing to the same property
    (el.Name = "...") works fine. Root cause: IronPython's property
    binder gets confused between the base Element.Name (get;set;) and
    ElementType's override. Fix: read through the property descriptor's
    GetValue() instead of plain attribute access, with a parameter-based
    fallback as a last resort."""
    try:
        return Element.Name.GetValue(el)
    except Exception:
        try:
            return el.Name
        except Exception:
            try:
                p = el.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
                return p.AsString() if p is not None else "(unnamed)"
            except Exception:
                return "(unnamed)"


def get_bar_diameter_internal(bar_type):
    """Read a RebarBarType's diameter, in Revit internal units (feet),
    safely. NBT's v1.6 test showed EVERY row - including Revit's own
    pre-existing bar types (10M, 13M...) that definitely have a real
    diameter - reading as 0mm via bar_type.BarDiameter, while other
    properties on the very same object (StandardBendDiameter etc.) read
    fine. That means .BarDiameter itself is unreliable here (silently
    returns 0 rather than throwing, so the earlier try/except couldn't
    catch it) - route through the REBAR_BAR_DIAMETER parameter instead,
    the same one used (and confirmed working) for WRITING the diameter
    when creating new Bar Types."""
    try:
        param = bar_type.get_Parameter(BuiltInParameter.REBAR_BAR_DIAMETER)
        if param is not None:
            val = param.AsDouble()
            if val:
                return val
    except Exception:
        pass
    for attr in ("BarModelDiameter", "BarNominalDiameter", "BarDiameter"):
        val = safe_get(bar_type, attr)
        if val:
            return val
    return 0.0


def safe_get(obj, attr_name, default=None):
    """Read a .NET property defensively - one bad property (IronPython
    getter/setter binding quirks show up on more than just .Name; see
    get_element_name()) must never take down an entire row's worth of
    otherwise-good data."""
    try:
        return getattr(obj, attr_name)
    except Exception:
        return default


class BarTypeRow(object):
    """One row = one RebarBarType. Also used as the WPF DataGrid item -
    the Include / attribute names below are read by the XAML-less
    binding (Binding Path=\"...\") set up in build_window().

    Every field below is read independently (safe_get / its own
    try-except) rather than in one big block, so a single unreadable
    property can't silently make collect_bar_type_rows() drop the whole
    row - which is what made real, already-created Bar Types disappear
    from view in earlier versions of this tool."""

    def __init__(self, bar_type):
        self.bar_type = bar_type
        self.Include = True
        self.Name = get_element_name(bar_type)

        try:
            self.DiameterMM = round(internal_to_mm(get_bar_diameter_internal(bar_type)), 1)
        except Exception:
            self.DiameterMM = 0.0

        self.CurrentStandard = fmt_mm(safe_get(bar_type, "StandardBendDiameter"))
        self.CurrentHook = fmt_mm(safe_get(bar_type, "StandardHookBendDiameter"))
        self.CurrentStirrupTie = fmt_mm(safe_get(bar_type, "StirrupTieBendDiameter"))
        self.CurrentMaxBR = fmt_mm(safe_get(bar_type, "MaximumBendRadius"))

        self.NewStandard = "-"
        self.NewHook = "-"
        self.NewStirrupTie = "-"
        self.NewMaxBR = "-"
        self.Status = "No data"

        self._mult = None  # multiplier dict once computed

    def compute_preview(self, standard_name):
        mult = rbs.get_multipliers(standard_name, self.DiameterMM)
        self._mult = mult
        if mult is None:
            self.NewStandard = "-"
            self.NewHook = "-"
            self.NewStirrupTie = "-"
            self.NewMaxBR = "-"
            self.Status = "No data for this diameter"
        else:
            d_mm = self.DiameterMM
            self.NewStandard = "{:.0f}".format(mult["standard"] * d_mm)
            self.NewHook = "{:.0f}".format(mult["hook"] * d_mm)
            self.NewStirrupTie = "{:.0f}".format(mult["stirrup_tie"] * d_mm)
            self.NewMaxBR = "{:.0f}".format(mult["max_bend_radius"] * d_mm)
            self.Status = "Ready"


def collect_bar_type_rows():
    bar_types = FilteredElementCollector(doc).OfClass(RebarBarType).ToElements()
    rows = []
    for bt in bar_types:
        # BarTypeRow.__init__ now reads every field defensively on its
        # own (see safe_get / get_element_name) and should not raise -
        # this try/except is only a last-resort safety net so one truly
        # broken element can't crash the whole tool. Earlier versions
        # relied on THIS catch-and-continue as their only defense, which
        # is exactly what made real, already-created Bar Types silently
        # disappear whenever a single property read (e.g. .Name) failed.
        try:
            rows.append(BarTypeRow(bt))
        except Exception:
            continue
    rows.sort(key=lambda r: r.DiameterMM)
    return rows


def set_bar_diameter(bar_type, diameter_mm):
    """Write a diameter (mm) onto an EXISTING, already-valid RebarBarType.
    Goes through the REBAR_BAR_DIAMETER parameter (the same one the Type
    Properties dialog edits) first, since that's confirmed writable;
    falls back to the BarModelDiameter/BarNominalDiameter properties if
    the parameter route isn't available on this Revit version.

    Also always sets Model Bar Diameter (BarModelDiameter) to match
    (v2.7, per NBT). When a new Bar Type is created via Duplicate() from
    an existing seed type, it inherits the SEED's Model Bar Diameter -
    which this function used to leave untouched, so e.g. every VN D6..
    D32 type created from the same seed ended up sharing one leftover
    Model Bar Diameter value (28.7mm, from whatever bar the seed
    happened to be) instead of matching their own real Bar Diameter.
    Model Bar Diameter only drives the 3D solid's visual thickness, not
    schedules/calculations - but NBT needs it accurate too, since clash
    detection (model checking) measures the actual 3D geometry, and a
    mismatched Model Bar Diameter would make clash results wrong."""
    d_internal = mm_to_internal(diameter_mm)
    param = bar_type.get_Parameter(BuiltInParameter.REBAR_BAR_DIAMETER)
    if param is not None and not param.IsReadOnly:
        param.Set(d_internal)
    else:
        try:
            bar_type.BarNominalDiameter = d_internal
        except Exception:
            raise Exception("could not set diameter (no writable parameter/property found)")

    try:
        bar_type.BarModelDiameter = d_internal
    except Exception:
        pass  # keep whichever Model Bar Diameter it already had rather than fail the whole write


def create_default_bar_types(doc, diam_list=None, prefix="D"):
    """Used when the project has NO Rebar Bar Type at all (or is missing
    some of the standard sizes).

    diam_list: list of diameters (mm) to ensure exist. Defaults to
    rbs.DEFAULT_BAR_DIAMETERS_MM (the generic bootstrap set) when not
    given - e.g. main()'s empty-project prompt, before any Standard has
    been picked yet. Callers that already know which Standard is active
    (ensure_standard_sizes_auto(), the manual add-diameter box) should
    pass the diameter list for THAT standard instead, since different
    standards use different bar size ranges (v2.3).

    prefix: the Bar Type NAME prefix, e.g. "D" for Vietnam's D10/D25 or
    "T" for Singapore's T10/T25 (v3.1) - each Standard is meant to name
    its Bar Types the way they're actually labelled/sold in that country
    (see rebar_bend_standards.py::STANDARD_NAME_PREFIX). Defaults to "D"
    for backward compatibility with the original bootstrap call in
    main() (no Standard picked yet at that point).

    History: v1.1 built these from a raw RebarBarType.Create(doc) shell
    and looked like it silently failed. v1.2/v1.3 switched to
    CreateDefaultRebarBarType + Duplicate() but still reported "no bar
    type found" afterward. The real root cause, found on the v1.4 test,
    was that collect_bar_type_rows() was silently dropping every row
    whenever ANY single property read on it failed (originally .Name) -
    so earlier runs likely DID create real Bar Types, the tool just
    couldn't see them afterward and kept trying again, leaving stray
    D8..D32 types behind that then collided ("name already in use") on
    the next attempt.

    v1.5 fixes this at the source and makes the whole operation
    idempotent: it snapshots existing names FIRST, only creates/
    duplicates names that are genuinely missing, and reuses any already-
    valid existing Bar Type as the Duplicate() source instead of always
    minting a fresh seed via CreateDefaultRebarBarType. Re-running this
    on a project that already has some or all of the standard sizes is
    now a safe no-op for those names rather than an error.

    Returns (created_names, already_existing_names, failed_descriptions).
    """
    created = []
    already_had = []
    failed = []

    t = Transaction(doc, "pyNBT - {} - Create default Bar Types".format(TOOL_NAME))
    t.Start()
    try:
        diam_list = list(diam_list) if diam_list else list(rbs.DEFAULT_BAR_DIAMETERS_MM)
        targets = []  # [(name, diameter_mm), ...] in order
        for d_mm in diam_list:
            name = "{}{}".format(prefix, int(d_mm) if float(d_mm).is_integer() else d_mm)
            targets.append((name, d_mm))

        existing_by_name = {}
        for bt in FilteredElementCollector(doc).OfClass(RebarBarType).ToElements():
            nm = get_element_name(bt)
            if nm:
                existing_by_name[nm] = bt

        # Any already-valid Bar Type (even one not in our target list) is
        # a fine Duplicate() source - avoids minting a brand new seed
        # via CreateDefaultRebarBarType when the document already has
        # something usable.
        seed_bt = next(iter(existing_by_name.values()), None)

        for name, d_mm in targets:
            if name in existing_by_name:
                already_had.append(name)
                continue

            try:
                if seed_bt is None:
                    seed_id = RebarBarType.CreateDefaultRebarBarType(doc)
                    doc.Regenerate()
                    seed_bt = doc.GetElement(seed_id)
                    if seed_bt is None:
                        raise Exception("CreateDefaultRebarBarType returned no usable element")
                    try:
                        seed_bt.Name = name
                    except Exception as ex:
                        raise Exception("could not name the seed type: {}".format(str(ex)))
                    set_bar_diameter(seed_bt, d_mm)
                else:
                    new_bt = seed_bt.Duplicate(name)
                    set_bar_diameter(new_bt, d_mm)
                created.append(name)
                existing_by_name[name] = True  # so nothing later re-targets this name
            except Exception as ex:
                failed.append("{} ({})".format(name, str(ex)))

        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        forms.alert("Error while creating default Bar Types: {}".format(str(ex)), title=TOOL_NAME)
        return [], [], []

    # Verify what actually persisted - don't trust that the API calls
    # above simply not raising an exception means the elements are real.
    # (No doc.Regenerate() here: Transaction.Commit() already regenerates
    # internally, and Regenerate() itself throws "Modification of the
    # document is forbidden" when called outside an open transaction.)
    existing_names_after = set(
        get_element_name(bt) for bt in FilteredElementCollector(doc).OfClass(RebarBarType).ToElements())
    verified = [n for n in created if n in existing_names_after]
    not_verified = [n for n in created if n not in existing_names_after]
    if not_verified:
        failed.extend(
            "{} (reported created but not found in document after commit)".format(n)
            for n in not_verified)

    return verified, already_had, failed


def set_rebar_shape_hooks_in_shape(doc, use_hooks_in_shape=True):
    """Pre-configure the project's Reinforcement Settings > Rebar Shape >
    "Rebar Shape defines End Treatments" so Revit's own first-time prompt
    ("Rebar Shape definitions will include hooks and will ignore end
    treatments. These options can be changed under Reinforcement
    Settings, and should be set before adding any Rebar elements to the
    project.") never needs to ask - NBT wants this configured up front,
    same spirit as the rest of this tool (run once before any rebar is
    placed on a new project).

    use_hooks_in_shape=True matches clicking OK on that Revit prompt
    (legacy behaviour: hooks are baked into the Rebar Shape and the
    newer separate "End Treatments" feature is ignored).

    Revit only allows changing this BEFORE any Rebar / AreaReinforcement
    / PathReinforcement / RebarContainer element exists in the project -
    if the project already has any, ReinforcementSettings raises and this
    re-raises with a clearer message for the caller to show.

    Returns (changed, current_value) - changed=False means it already
    matched use_hooks_in_shape, nothing was written.
    """
    settings = ReinforcementSettings.GetReinforcementSettings(doc)
    if settings.RebarShapeDefinesEndTreatments == use_hooks_in_shape:
        return False, use_hooks_in_shape

    t = Transaction(doc, "pyNBT - {} - Set Rebar Shape setting".format(TOOL_NAME))
    t.Start()
    try:
        settings.RebarShapeDefinesEndTreatments = use_hooks_in_shape
        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        raise Exception(
            "Could not change this setting - Revit only allows it BEFORE any "
            "Rebar / Area Reinforcement / Path Reinforcement element exists "
            "in this project. ({})".format(str(ex)))
    return True, use_hooks_in_shape


def apply_bend_standard(rows, standard_name):
    """Writes computed values into every checked, valid row.
    Returns (applied_names, skipped_names)."""
    applied = []
    skipped = []

    t = Transaction(doc, "pyNBT - {}".format(TOOL_NAME))
    t.Start()
    try:
        for row in rows:
            if not row.Include:
                continue
            mult = rbs.get_multipliers(standard_name, row.DiameterMM)
            if mult is None:
                skipped.append(row.Name)
                continue
            bt = row.bar_type
            d_internal = get_bar_diameter_internal(bt)
            bt.StandardBendDiameter = mult["standard"] * d_internal
            bt.StandardHookBendDiameter = mult["hook"] * d_internal
            bt.StirrupTieBendDiameter = mult["stirrup_tie"] * d_internal
            bt.MaximumBendRadius = mult["max_bend_radius"] * d_internal
            try:
                # Also correct Model Bar Diameter to match the real Bar
                # Diameter (v2.7) - a Bar Type created earlier via
                # Duplicate() from a seed can carry over the SEED's
                # Model Bar Diameter instead of its own (NBT found D10/
                # D25 both showing a stray 28.7mm here). Model Bar
                # Diameter drives the actual 3D solid, which clash
                # detection measures, so Apply also re-syncs it here for
                # every checked row - fixing Bar Types created by an
                # older version of this tool, not just new ones.
                bt.BarModelDiameter = d_internal
            except Exception:
                pass
            applied.append(row.Name)
        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        forms.alert("Error while applying: {}".format(str(ex)), title=TOOL_NAME)
        return [], []

    return applied, skipped


def fix_model_bar_diameter_all(doc):
    """Force Model Bar Diameter to match the real Bar Diameter on EVERY
    Rebar Bar Type found in the project - not only the ones ticked/
    Applied through the bend-standard workflow above.

    Why this exists (v2.8): apply_bend_standard() already re-syncs Model
    Bar Diameter for whichever rows are checked when Apply is clicked,
    but NBT found the stray value is not limited to whatever he happened
    to test - e.g. D32 still showed Model Bar Diameter=28.7mm even
    though its Standard Bend Diameter (256mm = 8x32) was already
    correct, meaning that row had never been re-Applied since the v2.7
    fix went in. This function is a standalone, always-safe sweep across
    every Bar Type in the document: it does NOT touch Standard/Hook/Tie
    Bend Diameter or Maximum Bend Radius at all, only
    Model Bar Diameter <- (that same type's own) Bar Diameter.

    Returns (fixed_names, already_ok_names, failed_descriptions).
    """
    fixed = []
    already_ok = []
    failed = []

    bar_types = FilteredElementCollector(doc).OfClass(RebarBarType).ToElements()

    t = Transaction(doc, "pyNBT - {} - Fix Model Bar Diameter (All)".format(TOOL_NAME))
    t.Start()
    try:
        for bt in bar_types:
            name = get_element_name(bt)
            try:
                d_internal = get_bar_diameter_internal(bt)
                if not d_internal:
                    failed.append("{} (no readable Bar Diameter)".format(name))
                    continue
                current_model = safe_get(bt, "BarModelDiameter")
                # ~0.05mm tolerance so float rounding never reports a
                # false mismatch and re-writes a value that is already
                # effectively correct.
                if (current_model is not None
                        and abs(internal_to_mm(current_model) - internal_to_mm(d_internal)) < 0.05):
                    already_ok.append(name)
                    continue
                bt.BarModelDiameter = d_internal
                fixed.append(name)
            except Exception as ex:
                failed.append("{} ({})".format(name, str(ex)))
        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        forms.alert("Error while fixing Model Bar Diameter: {}".format(str(ex)), title=TOOL_NAME)
        return [], [], []

    return fixed, already_ok, failed


# ---------------------------------------------------------------------------
# 3. UI
# ---------------------------------------------------------------------------

def _row_def(height="Auto"):
    rd = RowDefinition()
    if height == "*":
        rd.Height = GridLength(1, GridUnitType.Star)
    else:
        rd.Height = GridLength(1, GridUnitType.Auto)
    return rd


def _col_def(width="Auto"):
    cd = ColumnDefinition()
    if width == "*":
        cd.Width = GridLength(1, GridUnitType.Star)
    elif width == "Auto":
        cd.Width = GridLength(1, GridUnitType.Auto)
    else:
        cd.Width = GridLength(width)
    return cd


def _text_col(header, path, width=80, readonly=True):
    col = DataGridTextColumn()
    col.Header = header
    col.Binding = Binding(path)
    col.Width = DataGridLength(width)
    col.IsReadOnly = readonly
    return col


def build_window(rows, standard_names):
    win = Window()
    win.Title = TOOL_NAME
    win.Width = 980
    win.Height = 620
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen
    win.Background = brush(CLR_BG)

    # Parent this WPF window to Revit's own main window (v2.6). A raw
    # System.Windows.Window shown via ShowDialog() without an explicit
    # Owner runs on its own top-level HWND, disconnected from Revit's
    # window hierarchy - on some Revit builds (NBT hit this on Revit
    # 2026.4 with "Accelerated Graphics Tech Preview" enabled) closing
    # such an unparented modal dialog can crash the host application.
    # Explicitly setting Owner via WindowInteropHelper ties this
    # window's lifecycle to Revit's main window, which is the standard
    # pyRevit/Revit-API guidance for raw WPF dialogs and avoids that
    # class of interop crash on close. Wrapped defensively since
    # MainWindowHandle needs a recent-enough Revit API and this must
    # never be the reason the tool itself fails to open.
    try:
        WindowInteropHelper(win).Owner = __revit__.MainWindowHandle
    except Exception:
        pass

    root = Grid()
    root.RowDefinitions.Add(_row_def("Auto"))   # header
    root.RowDefinitions.Add(_row_def("Auto"))   # warning banner
    root.RowDefinitions.Add(_row_def("*"))      # content
    root.RowDefinitions.Add(_row_def("Auto"))   # footer
    win.Content = root

    # --- Header -------------------------------------------------------
    header = Border()
    header.Background = brush(CLR_HEADER)
    header.Padding = Thickness(16, 12, 16, 12)
    Grid.SetRow(header, 0)
    header_grid = Grid()
    header_grid.ColumnDefinitions.Add(_col_def("*"))
    header_grid.ColumnDefinitions.Add(_col_def("Auto"))

    title_panel = StackPanel()
    title = TextBlock()
    title.Text = "pyNBT — " + TOOL_NAME
    title.Foreground = brush(CLR_HEADER_TEXT)
    title.FontSize = 18
    title.FontWeight = FontWeights.Bold
    sub = TextBlock()
    sub.Text = "Set bend diameter standards on every Rebar Bar Type in this project"
    sub.Foreground = brush(CLR_HEADER_SUB)
    sub.FontSize = 12
    title_panel.Children.Add(title)
    title_panel.Children.Add(sub)
    Grid.SetColumn(title_panel, 0)
    header_grid.Children.Add(title_panel)

    badge = TextBlock()
    badge.Text = TOOL_VERSION
    badge.Foreground = brush(CLR_HEADER_SUB)
    badge.FontSize = 11
    badge.VerticalAlignment = VerticalAlignment.Bottom
    Grid.SetColumn(badge, 1)
    header_grid.Children.Add(badge)

    header.Child = header_grid
    root.Children.Add(header)

    # --- Warning banner -------------------------------------------------
    warn = Border()
    warn.Background = brush(CLR_FOOTER)
    warn.BorderBrush = brush(CLR_BORDER)
    warn.BorderThickness = Thickness(0, 1, 0, 1)
    warn.Padding = Thickness(16, 6, 16, 6)
    Grid.SetRow(warn, 1)
    warn_text = TextBlock()
    warn_text.Text = ("Sources: TCVN 5574:2018 Clause 10.3.7 (p.143) for Vietnam; "
                       "EN 1992-1-1 Table 8.1N / BS 8666:2020 Table 2 for Singapore. "
                       "Pick the entry that matches the actual bar surface type and "
                       "the project's governing standard before applying.")
    warn_text.Foreground = brush(CLR_MUTED)
    warn_text.FontSize = 11
    warn_text.TextWrapping = TextWrapping.Wrap
    warn.Child = warn_text
    root.Children.Add(warn)

    # --- Content --------------------------------------------------------
    content = Grid()
    Grid.SetRow(content, 2)
    content.Margin = Thickness(16, 12, 16, 12)
    content.ColumnDefinitions.Add(_col_def(360))
    content.ColumnDefinitions.Add(_col_def("*"))

    # LEFT: selection list ------------------------------------------------
    left = Border()
    left.Background = brush(CLR_CARD)
    left.BorderBrush = brush(CLR_BORDER)
    left.BorderThickness = Thickness(1)
    left.Padding = Thickness(10)
    left.Margin = Thickness(0, 0, 8, 0)
    Grid.SetColumn(left, 0)

    left_panel = StackPanel()

    combo_label = TextBlock()
    combo_label.Text = "Standard"
    combo_label.FontWeight = FontWeights.Bold
    combo_label.Margin = Thickness(0, 0, 0, 4)
    left_panel.Children.Add(combo_label)

    combo = ComboBox()
    for name in standard_names:
        combo.Items.Add(name)
    combo.SelectedIndex = 0
    combo.Margin = Thickness(0, 0, 0, 8)
    left_panel.Children.Add(combo)

    btn_row = StackPanel()
    btn_row.Orientation = Orientation.Horizontal
    btn_row.Margin = Thickness(0, 0, 0, 8)
    btn_all = Button()
    btn_all.Content = "Select All"
    btn_all.Padding = Thickness(8, 4, 8, 4)
    btn_all.Margin = Thickness(0, 0, 6, 0)
    btn_none = Button()
    btn_none.Content = "Select None"
    btn_none.Padding = Thickness(8, 4, 8, 4)
    btn_row.Children.Add(btn_all)
    btn_row.Children.Add(btn_none)
    left_panel.Children.Add(btn_row)

    def diam_list_for_standard(standard_name):
        """Which preset diameter list to offer/create for the currently
        selected Standard - each Standard can have its own bar-size range
        (v2.3). Falls back to the generic bootstrap list if this Standard
        has no dedicated entry in rebar_bend_standards.py."""
        return rbs.STANDARD_DIAMETERS_MM.get(standard_name, rbs.DEFAULT_BAR_DIAMETERS_MM)

    def name_prefix_for_standard(standard_name):
        """Bar Type NAME prefix for the currently selected Standard - e.g.
        "D" for Vietnam (D10, D25) vs "T" for Singapore (T10, T25), so
        each Standard's Bar Types are named the way they're actually
        labelled/sold in that country, and two Standards' Bar Types never
        collide under a shared numeric name even when their size lists
        overlap (v3.1). Falls back to "D" if this Standard has no
        dedicated entry in rebar_bend_standards.py::STANDARD_NAME_PREFIX."""
        return rbs.STANDARD_NAME_PREFIX.get(standard_name, "D")

    custom_row = Grid()
    custom_row.Margin = Thickness(0, 0, 0, 8)
    custom_row.ColumnDefinitions.Add(_col_def("*"))
    custom_row.ColumnDefinitions.Add(_col_def("Auto"))
    txt_custom_dia = TextBox()
    txt_custom_dia.Padding = Thickness(4, 3, 4, 3)
    txt_custom_dia.Margin = Thickness(0, 0, 6, 0)
    txt_custom_dia.ToolTip = "Custom diameter (mm), e.g. 14"
    Grid.SetColumn(txt_custom_dia, 0)
    custom_row.Children.Add(txt_custom_dia)
    btn_add_custom = Button()
    btn_add_custom.Content = "+ Add diameter"
    btn_add_custom.Padding = Thickness(8, 4, 8, 4)
    Grid.SetColumn(btn_add_custom, 1)
    custom_row.Children.Add(btn_add_custom)
    left_panel.Children.Add(custom_row)

    list_label = TextBlock()
    list_label.Text = "Rebar Bar Types found: {}".format(len(rows))
    list_label.Foreground = brush(CLR_MUTED)
    list_label.FontSize = 11
    list_label.Margin = Thickness(0, 0, 0, 4)
    left_panel.Children.Add(list_label)

    items = ObservableCollection[object]()
    for r in rows:
        items.Add(r)

    left_grid = DataGrid()
    left_grid.ItemsSource = items
    left_grid.AutoGenerateColumns = False
    left_grid.CanUserAddRows = False
    left_grid.RowHeaderWidth = 0
    left_grid.Height = 310
    chk_col = DataGridCheckBoxColumn()
    chk_col.Header = "Use"
    chk_col.Binding = Binding("Include")
    chk_col.Width = DataGridLength(40)
    left_grid.Columns.Add(chk_col)
    left_grid.Columns.Add(_text_col("Bar Type", "Name", 160))
    left_grid.Columns.Add(_text_col("Dia (mm)", "DiameterMM", 70))
    left_panel.Children.Add(left_grid)

    left.Child = left_panel
    content.Children.Add(left)

    # RIGHT: preview -------------------------------------------------------
    right = Border()
    right.Background = brush(CLR_CARD)
    right.BorderBrush = brush(CLR_BORDER)
    right.BorderThickness = Thickness(1)
    right.Padding = Thickness(10)
    Grid.SetColumn(right, 1)

    right_panel = StackPanel()
    preview_label = TextBlock()
    preview_label.Text = "Preview — Current vs New bend diameter (mm)"
    preview_label.FontWeight = FontWeights.Bold
    preview_label.Margin = Thickness(0, 0, 0, 6)
    right_panel.Children.Add(preview_label)

    right_grid = DataGrid()
    right_grid.ItemsSource = items
    right_grid.AutoGenerateColumns = False
    right_grid.CanUserAddRows = False
    right_grid.RowHeaderWidth = 0
    right_grid.Height = 420
    right_grid.Columns.Add(_text_col("Bar Type", "Name", 110))
    right_grid.Columns.Add(_text_col("Dia", "DiameterMM", 55))
    right_grid.Columns.Add(_text_col("Cur.Std", "CurrentStandard", 60))
    right_grid.Columns.Add(_text_col("New Std", "NewStandard", 60))
    right_grid.Columns.Add(_text_col("Cur.Hook", "CurrentHook", 65))
    right_grid.Columns.Add(_text_col("New Hook", "NewHook", 65))
    right_grid.Columns.Add(_text_col("Cur.Tie", "CurrentStirrupTie", 60))
    right_grid.Columns.Add(_text_col("New Tie", "NewStirrupTie", 60))
    right_grid.Columns.Add(_text_col("Status", "Status", 130))
    right_panel.Children.Add(right_grid)

    right.Child = right_panel
    content.Children.Add(right)

    root.Children.Add(content)

    # --- Footer -----------------------------------------------------------
    footer = Border()
    footer.Background = brush(CLR_FOOTER)
    footer.Padding = Thickness(16, 8, 16, 8)
    Grid.SetRow(footer, 3)
    footer_grid = Grid()
    footer_grid.ColumnDefinitions.Add(_col_def("*"))
    footer_grid.ColumnDefinitions.Add(_col_def("Auto"))

    status = TextBlock()
    status.Text = "{} {} | {} | ready".format(TOOL_NAME, TOOL_VERSION, doc.Title)
    status.Foreground = brush(CLR_MUTED)
    status.FontSize = 11
    status.VerticalAlignment = VerticalAlignment.Center
    Grid.SetColumn(status, 0)
    footer_grid.Children.Add(status)

    btns = StackPanel()
    btns.Orientation = Orientation.Horizontal
    Grid.SetColumn(btns, 1)
    btn_rebar_shape = Button()
    btn_rebar_shape.Content = "Set Rebar Shape (skip Revit prompt)"
    btn_rebar_shape.Padding = Thickness(10, 6, 10, 6)
    btn_rebar_shape.Margin = Thickness(0, 0, 16, 0)
    btn_close = Button()
    btn_close.Content = "Close"
    btn_close.Padding = Thickness(14, 6, 14, 6)
    btn_close.Margin = Thickness(0, 0, 8, 0)
    btn_apply = Button()
    btn_apply.Content = "Apply"
    btn_apply.Padding = Thickness(18, 6, 18, 6)
    btn_apply.Background = brush(CLR_ACCENT)
    btn_apply.Foreground = brush(CLR_HEADER_TEXT)
    btn_apply.FontWeight = FontWeights.Bold
    btns.Children.Add(btn_rebar_shape)
    btns.Children.Add(btn_close)
    btns.Children.Add(btn_apply)
    footer_grid.Children.Add(btns)

    footer.Child = footer_grid
    root.Children.Add(footer)

    # --- wiring -------------------------------------------------------
    def refresh_preview():
        standard_name = combo.SelectedItem
        for r in rows:
            r.compute_preview(standard_name)
        left_grid.Items.Refresh()
        right_grid.Items.Refresh()

    def on_combo_changed(sender, args):
        ensure_standard_sizes_auto(combo.SelectedItem)

    def on_select_all(sender, args):
        for r in rows:
            r.Include = True
        left_grid.Items.Refresh()

    def on_select_none(sender, args):
        for r in rows:
            r.Include = False
        left_grid.Items.Refresh()

    def on_close(sender, args):
        win.Close()

    def on_set_rebar_shape(sender, args):
        ok = forms.alert(
            "Set 'Rebar Shape defines End Treatments' = Yes for this project?\n\n"
            "This matches clicking OK on Revit's own first-time prompt ('Rebar "
            "Shape definitions will include hooks and will ignore end "
            "treatments') - once set here, Revit will not need to ask again "
            "when you place the first Rebar element.\n\n"
            "Only works BEFORE any Rebar / Area Reinforcement / Path "
            "Reinforcement element exists in this project.",
            title=TOOL_NAME, yes=True, no=True)
        if not ok:
            return
        try:
            changed, _ = set_rebar_shape_hooks_in_shape(doc, True)
        except Exception as ex:
            forms.alert(str(ex), title=TOOL_NAME)
            return
        if changed:
            status.Text = "{} {} | {} | Rebar Shape setting: hooks-in-shape (Yes)".format(
                TOOL_NAME, TOOL_VERSION, doc.Title)
            forms.alert("Done - set to Yes. Revit will not show that prompt for this project anymore.",
                        title=TOOL_NAME)
        else:
            forms.alert("Already set to Yes - nothing to change.", title=TOOL_NAME)

    def refresh_rows_from_document():
        """Re-collect Bar Types from the document and rebuild the grids in
        place. Mutates the existing `rows` list / `items` collection rather
        than rebinding the names, so every closure above that already
        captured them (refresh_preview, on_select_all, on_apply, ...) keeps
        seeing the up-to-date data - IronPython 2.7 closures can mutate an
        enclosing-scope object but cannot rebind the name itself."""
        rows[:] = collect_bar_type_rows()
        items.Clear()
        for r in rows:
            items.Add(r)
        list_label.Text = "Rebar Bar Types found: {}".format(len(rows))
        refresh_preview()

    def ensure_standard_sizes_auto(standard_name):
        """Auto-create any Bar Type this Standard needs but doesn't have
        yet (v2.4, per NBT's request: picking a Standard in the dropdown
        should add its sizes right away, without a separate manual
        button click). v3.0: this is now the ONLY way sizes get created -
        the old standalone "Create Standard Sizes" button was removed and
        this function is also called once right after the window is
        built (see the bottom of build_window()), so the initially-
        selected Standard gets its sizes created immediately on open too,
        not just on a later dropdown change. Runs quietly - no
        confirmation popup, since the underlying create is additive-only/
        idempotent (never touches an existing Bar Type, only adds names
        that are genuinely missing). Only the status bar is updated, and
        only real failures still pop an alert."""
        d_list = diam_list_for_standard(standard_name)
        prefix = name_prefix_for_standard(standard_name)
        created, already_had, failed = create_default_bar_types(doc, diam_list=d_list, prefix=prefix)
        refresh_rows_from_document()
        if created:
            status.Text = "{} {} | {} | auto-created {} for '{}': {}".format(
                TOOL_NAME, TOOL_VERSION, doc.Title, len(created), standard_name, ", ".join(created))
        if failed:
            forms.alert(
                "Some size(s) could not be auto-created for '{}':\n{}".format(
                    standard_name, "\n".join(failed)),
                title=TOOL_NAME)

    def _report_create_result(created, already_had, failed):
        msg_parts = []
        if created:
            msg_parts.append("Created {}: {}".format(len(created), ", ".join(created)))
        if already_had:
            msg_parts.append("Already existed {}: {}".format(len(already_had), ", ".join(already_had)))
        msg = "\n".join(msg_parts) if msg_parts else "Nothing was created."
        if failed:
            msg += "\nFailed: {}".format(", ".join(failed))
        forms.alert(msg, title=TOOL_NAME)
        refresh_rows_from_document()

    def on_add_custom(sender, args):
        raw = (txt_custom_dia.Text or "").strip().replace(",", ".")
        try:
            d_mm = float(raw)
            if d_mm <= 0:
                raise ValueError()
        except ValueError:
            forms.alert("Enter a valid diameter in mm (e.g. 14 or 14.5).", title=TOOL_NAME)
            return
        prefix = name_prefix_for_standard(combo.SelectedItem)
        created, already_had, failed = create_default_bar_types(doc, diam_list=[d_mm], prefix=prefix)
        txt_custom_dia.Text = ""
        _report_create_result(created, already_had, failed)

    def on_apply(sender, args):
        standard_name = combo.SelectedItem
        n_checked = len([r for r in rows if r.Include])
        if n_checked == 0:
            forms.alert("No Bar Type is selected.", title=TOOL_NAME)
            return
        ok = forms.alert(
            "Apply '{}' bend diameters to {} Bar Type(s)?\n"
            "This overwrites existing values.\n\n"
            "Also fixes Model Bar Diameter to match Bar Diameter on EVERY "
            "Rebar Bar Type in this project (not just the ones checked "
            "above) - v2.9.".format(standard_name, n_checked),
            title=TOOL_NAME, yes=True, no=True)
        if not ok:
            return
        applied, skipped = apply_bend_standard(rows, standard_name)
        # v2.9: also sweep-fix Model Bar Diameter across EVERY Bar Type in
        # the project (not just the checked/applied ones) in the same
        # click - NBT found the model-diameter mismatch wasn't limited to
        # whichever rows he happened to Apply/tick, so this no longer
        # needs a separate button; it just always runs alongside Apply.
        fixed, already_ok, failed_model = fix_model_bar_diameter_all(doc)
        refresh_preview()
        msg = "Applied to {} Bar Type(s).".format(len(applied))
        if skipped:
            msg += "\nSkipped (no data for diameter): {}".format(", ".join(skipped))
        msg += "\n\nModel Bar Diameter: fixed {}, already correct {}.".format(
            len(fixed), len(already_ok))
        if failed_model:
            msg += "\nModel Bar Diameter failed: {}".format(", ".join(failed_model))
        forms.alert(msg, title=TOOL_NAME)
        status.Text = "{} {} | {} | last run: {} applied, {} skipped, {} model-dia fixed".format(
            TOOL_NAME, TOOL_VERSION, doc.Title, len(applied), len(skipped), len(fixed))

    combo.SelectionChanged += on_combo_changed
    btn_all.Click += on_select_all
    btn_none.Click += on_select_none
    btn_add_custom.Click += on_add_custom
    btn_rebar_shape.Click += on_set_rebar_shape
    btn_close.Click += on_close
    btn_apply.Click += on_apply

    # v3.0: auto-create the initially-selected Standard's missing sizes
    # right away on open, not just on a later dropdown change - this is
    # what replaces the old "Create Standard Sizes" button entirely.
    # ensure_standard_sizes_auto() already calls refresh_rows_from_document()
    # -> refresh_preview() internally, so no separate refresh_preview()
    # call is needed here.
    ensure_standard_sizes_auto(combo.SelectedItem)
    return win


# ---------------------------------------------------------------------------
# 4. Entry point
# ---------------------------------------------------------------------------

def main():
    rows = collect_bar_type_rows()

    if not rows:
        # Happens on a clean/purged template with zero Rebar Bar Type -
        # offer to create a starter set instead of dead-ending here.
        diam_list = ", ".join(str(d) for d in rbs.DEFAULT_BAR_DIAMETERS_MM)
        create_now = forms.alert(
            "No Rebar Bar Type found in this project.\n\n"
            "This happens on a clean or purged template. Create {} standard "
            "Bar Type(s) now ({} mm) so there is something to set bend "
            "diameters on?".format(len(rbs.DEFAULT_BAR_DIAMETERS_MM), diam_list),
            title=TOOL_NAME, yes=True, no=True)
        if not create_now:
            return

        created, already_had, failed = create_default_bar_types(doc)
        msg_parts = []
        if created:
            msg_parts.append("Created {}: {}".format(len(created), ", ".join(created)))
        if already_had:
            msg_parts.append("Already existed {}: {}".format(len(already_had), ", ".join(already_had)))
        msg = "\n".join(msg_parts) if msg_parts else "Nothing was created."
        if failed:
            msg += "\nFailed: {}".format(", ".join(failed))
        forms.alert(msg, title=TOOL_NAME)

        rows = collect_bar_type_rows()
        if not rows:
            forms.alert(
                "Still no Rebar Bar Type could be created - stopping. "
                "Check the error above and report it back.",
                title=TOOL_NAME)
            return

    standard_names = list(rbs.STANDARDS.keys())
    win = build_window(rows, standard_names)
    win.ShowDialog()


main()
