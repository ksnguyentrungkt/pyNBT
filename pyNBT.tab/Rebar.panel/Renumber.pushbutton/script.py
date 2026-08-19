# -*- coding: utf-8 -*-
"""Renumber a chosen parameter (default: 'Rebar Number') of selected rebar in
reading order (left-to-right, top-to-bottom), based on the model's default
(project) coordinate system.

pyNBT - Rebar.panel - Renumber Rebar
"""
__title__ = 'Renumber'
__author__ = 'NBT / pyNBT'
__doc__ = (
    "Renumber a chosen field (Rebar Number, Schedule Mark, Mark, Comments, "
    "or Partition) of selected rebar bars in reading order (top row first, "
    "left to right within each row), based on the project's default XY "
    "directions. Built and tested for Revit 2026, where 'Rebar Number' is a "
    "normal, directly-editable parameter (NBT confirmed 2026-08-19). Two "
    "modes: group identical bars under one number, or give every bar its "
    "own number."
)

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

import os
import math
from System.IO import FileStream, FileMode, FileAccess
from System.Windows.Markup import XamlReader
from System.Collections.ObjectModel import ObservableCollection
# NOTE: deliberately NOT using "import wpf" / IronPython.Wpf here - that
# assembly is missing on some pyRevit installs (confirmed on NBT's machine,
# 2026-08-18). XamlReader.Load + FindName() below only needs the three
# standard WPF assemblies above, so it works everywhere.

from Autodesk.Revit.DB import Transaction, StorageType
from Autodesk.Revit.DB.Structure import Rebar, RebarInSystem
# NOTE on 'Rebar Number' (v1.7-v1.14 history, resolved v1.15): on some Revit
# versions this native field is NOT a normal parameter - Revit computes it
# from its own per-Partition numbering sequences, so a plain param.Set()
# fails with IsReadOnly (confirmed on Revit 2025, hotfix #9). NBT confirmed
# (2026-08-19) that on Revit 2026 - the only version this tool targets from
# v1.15 on - 'Rebar Number' IS a normal, directly-editable parameter, so it
# is written the same way as Mark/Comments below (apply_marks_to_model()).
# is_rebar_number_directly_writable() still checks this per-run as a safety
# net rather than assuming it - see the note above that function.

from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()

XAML_FILE = os.path.join(os.path.dirname(__file__), 'ui.xaml')

MM_TO_FT = 1.0 / 304.8


# ---------------------------------------------------------------------------
# Compatibility helper
# (kept local for now - move into a shared pyNBT/compat.py once Phase 3
#  "Compatibility Layer" of the roadmap starts, per dqt-patterns.md #3)
# ---------------------------------------------------------------------------
def eid_int(element_id):
    """Get integer value of an ElementId, safe across Revit 2024-2027+."""
    if element_id is None:
        return -1
    val = getattr(element_id, 'Value', None)
    if val is None:
        val = getattr(element_id, 'IntegerValue', None)
    return int(val) if val is not None else -1


# ---------------------------------------------------------------------------
# Standalone business logic (no UI references)
# ---------------------------------------------------------------------------
def get_selected_bars(document, ui_document):
    """Return (bars, skipped_count) from the current Revit selection.

    Only Rebar and RebarInSystem elements are kept; everything else in the
    selection is counted as skipped so the user can see nothing was silently
    ignored.
    """
    sel_ids = ui_document.Selection.GetElementIds()
    bars = []
    skipped = 0
    for eid in sel_ids:
        elem = document.GetElement(eid)
        if elem is None:
            continue
        if isinstance(elem, Rebar) or isinstance(elem, RebarInSystem):
            bars.append(elem)
        else:
            skipped += 1
    return bars, skipped


def get_bbox_center(elem):
    """Center point (cx, cy) of the element's model bounding box, ignoring Z.

    Using the bounding-box center (not a single endpoint) is what makes
    diagonal/skewed bars sort correctly: the center already blends the bar's
    left-most and right-most extent into a single reading-order position.
    """
    try:
        bbox = elem.get_BoundingBox(None)
        if bbox is None:
            return None
        cx = (bbox.Min.X + bbox.Max.X) / 2.0
        cy = (bbox.Min.Y + bbox.Max.Y) / 2.0
        return (cx, cy)
    except Exception:
        return None


def get_bar_diameter_ft(document, elem):
    """Bar diameter in feet (Revit internal units), or None if unavailable.

    RebarBarType exposes the diameter under different property names
    depending on Revit version/how the type was set up (nominal size vs the
    diameter actually used to build the 3D geometry) - try each in order
    and use whichever one is present. (v1.2 only tried 'BarDiameter', which
    isn't a real RebarBarType property - that's why the Diameter column in
    the preview always showed '-'.)
    """
    try:
        bar_type = document.GetElement(elem.GetTypeId())
    except Exception:
        return None
    if bar_type is None:
        return None
    for prop_name in ('BarModelDiameter', 'BarNominalDiameter', 'BarDiameter'):
        val = getattr(bar_type, prop_name, None)
        if val is not None:
            try:
                return float(val)
            except Exception:
                continue
    return None


def get_bar_signature(document, elem):
    """Best-effort 'identical bar' signature: (diameter, shape_id, length).

    Used only for Mode A (group identical bars). Each lookup is defensive
    since not every rebar-like element exposes every property the same way
    (RebarInSystem has no discrete shape family, for example).
    """
    diameter = get_bar_diameter_ft(document, elem)
    if diameter is not None:
        diameter = round(diameter, 5)

    shape_id = None
    try:
        shape_id = eid_int(elem.GetShapeId())
    except Exception:
        try:
            shape_id = eid_int(elem.RebarShapeId)
        except Exception:
            shape_id = None

    length = None
    try:
        length = round(elem.TotalLength, 3)
    except Exception:
        length = None

    return (diameter, shape_id, length)


def order_grid(items, tol_ft):
    """items: list of (elem, cx, cy). Returns the SAME (elem, cx, cy) tuples,
    sorted top-row-first then left-to-right within each row, using tol_ft to
    decide whether two bars are 'on the same row'. Used for multi-row x
    multi-column layouts (e.g. a rebar mat in a slab). Kept as full tuples
    (not just elems) so it matches order_ring()/order_line()'s return shape -
    compute_reading_order() below extracts the element itself in one place."""
    if not items:
        return []

    sorted_by_y = sorted(items, key=lambda t: -t[2])  # descending Y = top first
    rows = []
    current_row = [sorted_by_y[0]]
    row_ref_y = sorted_by_y[0][2]

    for item in sorted_by_y[1:]:
        if (row_ref_y - item[2]) <= tol_ft:
            current_row.append(item)
        else:
            rows.append(current_row)
            current_row = [item]
            row_ref_y = item[2]
    rows.append(current_row)

    ordered = []
    for row in rows:
        row_sorted = sorted(row, key=lambda t: t[1])  # ascending X = left to right
        ordered.extend(row_sorted)
    return ordered


# ---------------------------------------------------------------------------
# Layout auto-detection (Ring / Line / Grid)
# ---------------------------------------------------------------------------
RING_GAP_THRESHOLD_RAD = math.radians(140)   # largest empty angular gap still
                                              # allowed to call the bars a closed ring
RING_HOLLOW_RATIO = 0.35                     # a bar closer than this fraction of the
                                              # mean radius to the centroid means the
                                              # middle isn't empty -> not a hollow ring
LINE_STRAIGHTNESS_RATIO = 0.15               # PCA minor/major eigenvalue ratio below
                                              # which the bars count as "one line"


def compute_centroid(items):
    n = float(len(items))
    mx = sum(t[1] for t in items) / n
    my = sum(t[2] for t in items) / n
    return mx, my


def compute_bearing_rad(dx, dy):
    """Compass bearing in radians: 0 = due North ('12 o'clock'), increasing
    clockwise (toward +X / East)."""
    b = math.atan2(dx, dy)
    if b < 0:
        b += 2 * math.pi
    return b


def has_interior_point(items, centroid):
    """True if at least one bar sits close to the centroid - i.e. the middle
    of the arrangement is NOT empty, so it can't be a hollow ring (this is
    what tells a rebar mat/grid apart from ties around a column, which both
    can otherwise look similar to the pure angular-gap test below)."""
    mx, my = centroid
    radii = [math.hypot(t[1] - mx, t[2] - my) for t in items]
    mean_r = sum(radii) / len(radii)
    if mean_r < 1e-9:
        return True  # every bar piled at the centroid
    return any(r < RING_HOLLOW_RATIO * mean_r for r in radii)


def detect_ring(items, centroid):
    """True if the bars roughly surround the centroid on all sides with an
    empty middle (circular, square, hexagonal, or any other closed-loop
    arrangement of ties/stirrups)."""
    if len(items) < 3:
        return False
    if has_interior_point(items, centroid):
        return False
    mx, my = centroid
    bearings = sorted(compute_bearing_rad(t[1] - mx, t[2] - my) for t in items)
    gaps = [bearings[i + 1] - bearings[i] for i in range(len(bearings) - 1)]
    gaps.append(2 * math.pi - bearings[-1] + bearings[0])
    return max(gaps) < RING_GAP_THRESHOLD_RAD


def order_ring(items, centroid):
    """Sort clockwise starting from the bar closest to 12 o'clock (due North)
    relative to the centroid."""
    mx, my = centroid
    decorated = []
    for t in items:
        bearing = compute_bearing_rad(t[1] - mx, t[2] - my)
        decorated.append((bearing, t))
    decorated.sort(key=lambda d: d[0])
    start_idx = min(
        range(len(decorated)),
        key=lambda i: min(decorated[i][0], 2 * math.pi - decorated[i][0])
    )
    rotated = decorated[start_idx:] + decorated[:start_idx]
    return [d[1] for d in rotated]


def pca_axis_and_ratio(items):
    """Principal direction (radians) of the point cloud, plus the ratio of
    minor/major spread (near 0 = points are essentially collinear)."""
    mx, my = compute_centroid(items)
    n = float(len(items))
    var_x = sum((t[1] - mx) ** 2 for t in items) / n
    var_y = sum((t[2] - my) ** 2 for t in items) / n
    cov_xy = sum((t[1] - mx) * (t[2] - my) for t in items) / n
    theta = 0.5 * math.atan2(2 * cov_xy, var_x - var_y)
    common = math.sqrt(((var_x - var_y) / 2.0) ** 2 + cov_xy ** 2)
    mean_var = (var_x + var_y) / 2.0
    lambda1 = mean_var + common
    lambda2 = mean_var - common
    ratio = (lambda2 / lambda1) if lambda1 > 1e-12 else 0.0
    return theta, ratio


def detect_line(items):
    """True if the bars are essentially a single path (straight, diagonal, or
    lightly wobbling), i.e. spread along one dominant direction only."""
    if len(items) < 2:
        return True
    _, ratio = pca_axis_and_ratio(items)
    return ratio < LINE_STRAIGHTNESS_RATIO


def order_line(items):
    """Project every bar onto the dominant direction and sort along it.
    Oriented left-to-right for a mostly-horizontal line, top-to-bottom for a
    mostly-vertical one, so a straight/diagonal/gently-wobbling line all read
    in a natural order."""
    theta, _ = pca_axis_and_ratio(items)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    if abs(cos_t) >= abs(sin_t):
        if cos_t < 0:
            cos_t, sin_t = -cos_t, -sin_t
    else:
        if sin_t > 0:
            cos_t, sin_t = -cos_t, -sin_t
    decorated = [(t[1] * cos_t + t[2] * sin_t, t) for t in items]
    decorated.sort(key=lambda d: d[0])
    return [d[1] for d in decorated]


LAYOUT_LABELS = {
    'ring': 'Ring',
    'line': 'Line',
    'grid': 'Grid',
}


def compute_reading_order(items, tol_ft, layout_mode='auto'):
    """items: list of (elem, cx, cy). layout_mode: 'auto' | 'ring' | 'line' | 'grid'.
    Returns (ordered_elems, detected_layout_key)."""
    if not items:
        return [], None

    if layout_mode in ('ring', 'line', 'grid'):
        chosen = layout_mode
    else:
        centroid = compute_centroid(items)
        if detect_ring(items, centroid):
            chosen = 'ring'
        elif detect_line(items):
            chosen = 'line'
        else:
            chosen = 'grid'

    if chosen == 'ring':
        ordered = order_ring(items, compute_centroid(items))
    elif chosen == 'line':
        ordered = order_line(items)
    else:
        ordered = order_grid(items, tol_ft)

    return [t[0] for t in ordered], chosen


def assign_marks(document, ordered_elems, mode, prefix, start_number):
    """mode: 'group' or 'unique'. Returns dict {ElementId: (number_int, mark_str)}
    plus a parallel list of (elem, mark_str) rows for the preview table, in
    the same order. Both the plain number and the prefixed text are kept so
    apply_marks_to_model() can pick whichever matches the target parameter's
    actual StorageType (Integer field like 'Rebar Number' vs String field
    like 'Mark')."""
    marks = {}
    preview_rows = []

    if mode == 'unique':
        n = start_number
        for elem in ordered_elems:
            mark_str = '{}{}'.format(prefix, n)
            marks[elem.Id] = (n, mark_str)
            preview_rows.append((elem, mark_str))
            n += 1
    else:
        group_number = {}
        next_n = [start_number]
        for elem in ordered_elems:
            sig = get_bar_signature(document, elem)
            if sig not in group_number:
                group_number[sig] = next_n[0]
                next_n[0] += 1
            n = group_number[sig]
            mark_str = '{}{}'.format(prefix, n)
            marks[elem.Id] = (n, mark_str)
            preview_rows.append((elem, mark_str))

    return marks, preview_rows


def find_param_by_name(elem, name):
    """Look up a parameter on `elem` by its displayed name (e.g. 'Rebar
    Number', 'Mark', 'Schedule Mark') without guessing a BuiltInParameter
    enum - Revit's native parameter names are far more reliable than the
    enum mapping, which has already been wrong twice in this tool's history
    (Mark vs Rebar Number). Tries the fast built-in lookup first, then falls
    back to a manual scan of every instance parameter in case LookupParameter
    misses it for some element types."""
    try:
        param = elem.LookupParameter(name)
        if param is not None:
            return param
    except Exception:
        pass
    try:
        for p in elem.Parameters:
            try:
                if p.Definition is not None and p.Definition.Name == name:
                    return p
            except Exception:
                continue
    except Exception:
        pass
    return None


def apply_marks_to_model(document, marks, target_param_name):
    """Writes `target_param_name` (looked up by display name, e.g. 'Rebar
    Number') on every element in `marks` inside a single Transaction.

    marks: {ElementId: (number_int, mark_str)}. The parameter's actual
    StorageType decides what gets written: Integer/Double fields (like the
    native 'Rebar Number') get the plain number, String fields (like 'Mark'
    or 'Comments') get the prefix+number text. This is what makes the tool
    work correctly against both kinds of field instead of only 'Mark'.

    Returns (success_count, failed_count, fail_reasons, error_samples).
    fail_reasons is a dict of counts by cause: not_found / read_only /
    unsupported_type / other. error_samples is a short list (up to 5) of
    literal Revit exception text for whatever failed under 'other' - always
    surfaced to the user instead of a bare count (same reasoning as the
    NumberingSchema write path below: a count alone was not enough to
    diagnose past failures, see hotfix #13).
    """
    t = Transaction(document, 'pyNBT - Renumber Rebar')
    t.Start()
    success = 0
    failed = 0
    fail_reasons = {'not_found': 0, 'read_only': 0, 'unsupported_type': 0, 'other': 0}
    error_samples = []
    try:
        for eid, value in marks.items():
            number_val, mark_str = value
            elem = document.GetElement(eid)
            if elem is None:
                failed += 1
                fail_reasons['not_found'] += 1
                continue
            param = find_param_by_name(elem, target_param_name)
            if param is None:
                failed += 1
                fail_reasons['not_found'] += 1
                continue
            if param.IsReadOnly:
                failed += 1
                fail_reasons['read_only'] += 1
                continue
            try:
                storage = param.StorageType
                if storage == StorageType.String:
                    param.Set(mark_str)
                elif storage == StorageType.Integer:
                    param.Set(int(number_val))
                elif storage == StorageType.Double:
                    param.Set(float(number_val))
                else:
                    failed += 1
                    fail_reasons['unsupported_type'] += 1
                    continue
                success += 1
            except Exception as ex:
                failed += 1
                fail_reasons['other'] += 1
                if len(error_samples) < 5:
                    error_samples.append('element {}: {}'.format(eid_int(eid), str(ex)))
                continue
        t.Commit()
    except Exception:
        if t.HasStarted():
            t.RollBack()
        raise
    return success, failed, fail_reasons, error_samples


# ---------------------------------------------------------------------------
# 'Rebar Number' support check.
#
# v1.7-v1.13 of this tool wrote 'Rebar Number' through Revit's own
# partition-based NumberingSchema/ChangeNumber API, because on Revit 2025
# that field is computed/locked (a plain Parameter.Set() fails with
# IsReadOnly - hotfix #9). NBT confirmed (2026-08-19) that every test so far
# was actually run on Revit 2026, where 'Rebar Number' is a normal, directly-
# editable parameter - and NBT has decided (2026-08-19) to only use this
# tool on Revit 2026 going forward. v1.15 removes the NumberingSchema
# machinery entirely to keep the tool simple and maintainable: it now only
# ever writes 'Rebar Number' the same way as Mark/Comments, via
# apply_marks_to_model() above. is_rebar_number_directly_writable() below is
# kept as a safety check so the tool fails with a clear message (instead of
# a confusing Revit error) if it's ever pointed at a document/Revit version
# where the field turns out to be locked again. The full partition-based
# implementation (Conflict Handling / evict / first-time numbering /
# Regenerate()) still exists in the v1.13 files already delivered to NBT, in
# case a future Revit downgrade or another project ever needs it back.
# ---------------------------------------------------------------------------
def is_rebar_number_directly_writable(elem):
    """True if 'Rebar Number' behaves as a normal, directly-editable
    parameter on this element - confirmed by NBT (2026-08-19): on Revit
    2026, typing straight into the 'Rebar Number' field in the Properties
    panel works exactly like 'Mark'/'Comments', no read-only lock."""
    param = find_param_by_name(elem, 'Rebar Number')
    if param is None:
        return False
    try:
        return not param.IsReadOnly
    except Exception:
        return False


class PreviewRow(object):
    """Simple data-bindable row for the preview ListView."""
    def __init__(self, index, elem, diameter_mm, length_mm, new_mark):
        self.No = index
        self.ElementId = elem.Id.IntegerValue if hasattr(elem.Id, 'IntegerValue') else eid_int(elem.Id)
        self.Diameter = '{:.0f} mm'.format(diameter_mm) if diameter_mm is not None else '-'
        self.Length = '{:.0f} mm'.format(length_mm) if length_mm is not None else '-'
        self.NewMark = new_mark


def load_xaml_window(xaml_path):
    """Load a WPF Window from a XAML file via XamlReader (no IronPython.Wpf
    dependency). Returns the live System.Windows.Window instance; named
    elements are retrieved afterwards with window.FindName('...')."""
    stream = FileStream(xaml_path, FileMode.Open, FileAccess.Read)
    try:
        return XamlReader.Load(stream)
    finally:
        stream.Close()


# ---------------------------------------------------------------------------
# UI controller - only orchestrates: reads UI state, calls the logic above.
# Not a Window subclass (see load_xaml_window) - holds a reference to the
# loaded window instead, and wires events manually.
# ---------------------------------------------------------------------------
class RenumberRebarController(object):
    def __init__(self):
        self.window = load_xaml_window(XAML_FILE)

        # named controls declared in ui.xaml (x:Name="...")
        self.CmbTargetParam = self.window.FindName('CmbTargetParam')
        self.RbGroup = self.window.FindName('RbGroup')
        self.RbUnique = self.window.FindName('RbUnique')
        self.TxtPrefix = self.window.FindName('TxtPrefix')
        self.TxtStartNumber = self.window.FindName('TxtStartNumber')
        self.TxtTolerance = self.window.FindName('TxtTolerance')
        self.CmbLayout = self.window.FindName('CmbLayout')
        self.ListPreview = self.window.FindName('ListPreview')
        self.TxtStatus = self.window.FindName('TxtStatus')
        self.BtnRefresh = self.window.FindName('BtnRefresh')
        self.BtnApply = self.window.FindName('BtnApply')
        self.BtnClose = self.window.FindName('BtnClose')

        # wire events manually (ui.xaml has no Click="..." handlers)
        self.BtnRefresh.Click += self.refresh_preview
        self.BtnApply.Click += self.apply_marks
        self.BtnClose.Click += self.close_window

        self.bars, self.skipped = get_selected_bars(doc, uidoc)
        self._last_marks = None

        if not self.bars:
            forms.alert(
                "No rebar found in the current selection.\n"
                "Please select rebar in Revit first, then run this tool again.",
                title='pyNBT - Renumber Rebar'
            )
            return

        self.TxtStatus.Text = '{} valid rebar selected.'.format(len(self.bars))
        if self.skipped:
            self.TxtStatus.Text += ' ({} non-rebar element(s) skipped.)'.format(self.skipped)

        self.refresh_preview(None, None)

    def show(self):
        self.window.ShowDialog()

    # -- helpers ------------------------------------------------------
    def _read_inputs(self):
        target_item = self.CmbTargetParam.SelectedItem
        target_param_name = target_item.Tag if target_item is not None else 'Rebar Number'

        prefix = self.TxtPrefix.Text.strip() if self.TxtPrefix.Text else ''

        try:
            start_number = int(self.TxtStartNumber.Text.strip())
        except Exception:
            forms.alert("Start Number must be an integer, e.g. 1.", title='pyNBT - Renumber Rebar')
            return None

        try:
            tol_mm = float(self.TxtTolerance.Text.strip())
            if tol_mm <= 0:
                raise ValueError
        except Exception:
            forms.alert("Row Tolerance must be a positive number (mm), e.g. 150.", title='pyNBT - Renumber Rebar')
            return None

        mode = 'group' if self.RbGroup.IsChecked else 'unique'

        layout_item = self.CmbLayout.SelectedItem
        layout_mode = layout_item.Tag if layout_item is not None else 'auto'

        return prefix, start_number, tol_mm, mode, layout_mode, target_param_name

    def _compute(self):
        inputs = self._read_inputs()
        if inputs is None:
            return None
        prefix, start_number, tol_mm, mode, layout_mode, target_param_name = inputs
        tol_ft = tol_mm * MM_TO_FT

        items = []
        skipped_no_bbox = 0
        for elem in self.bars:
            center = get_bbox_center(elem)
            if center is None:
                skipped_no_bbox += 1
                continue
            items.append((elem, center[0], center[1]))

        ordered, detected_layout = compute_reading_order(items, tol_ft, layout_mode)
        marks, preview_rows = assign_marks(doc, ordered, mode, prefix, start_number)

        # confirm 'Rebar Number' is directly writable on this document (true
        # on Revit 2026, the only version this tool targets from v1.15 on -
        # NBT confirmed 2026-08-19) - checked on a real selected bar, never
        # assumed, so Apply can refuse clearly instead of failing confusingly
        # if this tool is ever pointed at an older Revit document/version
        # where the field is locked again (see is_rebar_number_directly_writable()).
        rebar_direct_writable = False
        if target_param_name == 'Rebar Number' and preview_rows:
            rebar_direct_writable = is_rebar_number_directly_writable(preview_rows[0][0])

        return (marks, preview_rows, skipped_no_bbox, detected_layout,
                target_param_name, rebar_direct_writable)

    # -- event handlers -------------------------------------------------
    def refresh_preview(self, sender, args):
        result = self._compute()
        if result is None:
            return
        (marks, preview_rows, skipped_no_bbox, detected_layout,
         target_param_name, rebar_direct_writable) = result
        self._last_marks = marks

        display_rows = ObservableCollection[object]()
        for i, (elem, mark_value) in enumerate(preview_rows, start=1):
            diameter_ft = get_bar_diameter_ft(doc, elem)
            diameter_mm = diameter_ft * 304.8 if diameter_ft is not None else None
            length_mm = None
            try:
                length_mm = elem.TotalLength * 304.8
            except Exception:
                pass
            display_rows.Add(PreviewRow(i, elem, diameter_mm, length_mm, mark_value))

        self.ListPreview.ItemsSource = display_rows

        layout_label = LAYOUT_LABELS.get(detected_layout, detected_layout or '-')
        mark_strs = [row[1] for row in preview_rows]
        first_mark = mark_strs[0] if mark_strs else '-'
        status = 'Target: {} | Detected layout: {} | First value: {} | {} bar(s) will be numbered ({} distinct value(s)).'.format(
            target_param_name, layout_label, first_mark, len(preview_rows), len(set(mark_strs))
        )
        if self.skipped:
            status += ' {} non-rebar element(s) skipped.'.format(self.skipped)
        if skipped_no_bbox:
            status += ' {} bar(s) skipped (no readable position).'.format(skipped_no_bbox)

        # warn up front if the chosen parameter is numeric and a prefix is
        # set - text can't be written into an Integer/Double field, so the
        # prefix would silently be dropped at Apply time
        prefix_text = self.TxtPrefix.Text.strip() if self.TxtPrefix.Text else ''
        sample_param = find_param_by_name(self.bars[0], target_param_name) if self.bars else None
        if prefix_text and sample_param is not None and sample_param.StorageType != StorageType.String:
            status += ' Note: "{}" is a numeric field - the prefix "{}" will be ignored, only the number is written.'.format(
                target_param_name, prefix_text
            )
        # warn up front if the chosen parameter is read-only on this element -
        # EXCEPT "Rebar Number", which gets its own dedicated warning below
        # (via rebar_direct_writable) since a locked "Rebar Number" is a
        # known, explained case, not a generic read-only field
        if sample_param is not None and sample_param.IsReadOnly and target_param_name != 'Rebar Number':
            status += ' Warning: "{}" is read-only on this element - Apply will fail. Choose a different Target Parameter.'.format(
                target_param_name
            )
        if target_param_name == 'Rebar Number':
            if rebar_direct_writable:
                status += ' Note: "Rebar Number" is written directly (like Mark/Comments).'
            else:
                status += (
                    ' Warning: this Revit document has "Rebar Number" locked (older Revit '
                    'version behavior) - this tool build only supports writing it directly. '
                    'Choose a different Target Parameter, or contact NBT/pyNBT for an update.'
                )
        self.TxtStatus.Text = status

    def apply_marks(self, sender, args):
        result = self._compute()
        if result is None:
            return
        (marks, preview_rows, skipped_no_bbox, detected_layout,
         target_param_name, rebar_direct_writable) = result

        if not marks:
            forms.alert("No rebar to number.", title='pyNBT - Renumber Rebar')
            return

        is_rebar_number = (target_param_name == 'Rebar Number')

        if is_rebar_number and not rebar_direct_writable:
            # This tool build (v1.15+) only supports writing 'Rebar Number'
            # directly (Revit 2026 behavior, confirmed by NBT 2026-08-19) -
            # the older partition-based NumberingSchema workaround was
            # removed to keep the tool simple, since NBT only uses this tool
            # on Revit 2026. Refuse clearly instead of attempting something
            # that would just fail with a confusing Revit error.
            forms.alert(
                "\"Rebar Number\" is locked on this document (older Revit version "
                "behavior) - this tool build only supports writing it directly "
                "(Revit 2026). Choose a different Target Parameter, or ask "
                "NBT/pyNBT for an update that supports this Revit version again.",
                title='pyNBT - Renumber Rebar'
            )
            return

        try:
            success, failed, fail_reasons, error_samples = apply_marks_to_model(doc, marks, target_param_name)
        except Exception as ex:
            forms.alert(
                "Error writing '{}' to the model:\n{}".format(target_param_name, str(ex)),
                title='pyNBT - Renumber Rebar'
            )
            logger.error('Renumber Rebar failed: %s', ex)
            return

        msg = 'Successfully wrote "{}" for {} bar(s).'.format(target_param_name, success)
        if failed:
            reasons = []
            if fail_reasons.get('not_found'):
                reasons.append('{} bar(s) have no "{}" parameter'.format(fail_reasons['not_found'], target_param_name))
            if fail_reasons.get('read_only'):
                reasons.append('{} bar(s) have "{}" locked/read-only'.format(fail_reasons['read_only'], target_param_name))
            if fail_reasons.get('unsupported_type'):
                reasons.append('{} bar(s) have an unsupported parameter type'.format(fail_reasons['unsupported_type']))
            if fail_reasons.get('other'):
                reasons.append('{} bar(s) failed for another reason'.format(fail_reasons['other']))
            msg += '\n{} bar(s) failed:\n- {}'.format(failed, '\n- '.join(reasons))
            if error_samples:
                msg += '\n\nRevit error detail(s):\n- {}'.format('\n- '.join(error_samples))
                logger.error('Renumber Rebar (%s) errors: %s', target_param_name, '; '.join(error_samples))
        forms.alert(msg, title='pyNBT - Renumber Rebar')
        self.window.Close()

    def close_window(self, sender, args):
        self.window.Close()


if __name__ == '__main__':
    if not uidoc.Selection.GetElementIds():
        forms.alert(
            "Please select rebar in Revit before running this tool.",
            title='pyNBT - Renumber Rebar'
        )
        script.exit()

    controller = RenumberRebarController()
    if controller.bars:
        controller.show()
