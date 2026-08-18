# -*- coding: utf-8 -*-
"""Renumber Mark of selected rebar in reading order (left-to-right, top-to-bottom),
based on the model's default (project) coordinate system.

pyNBT - Rebar.panel - Renumber Rebar
"""
__title__ = 'Renumber'
__author__ = 'NBT / pyNBT'
__doc__ = (
    "Renumber the 'Mark' parameter of selected rebar bars in reading order "
    "(top row first, left to right within each row), based on the project's "
    "default XY directions. Two modes: group identical bars under one number, "
    "or give every bar its own number."
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

from Autodesk.Revit.DB import Transaction, BuiltInParameter
from Autodesk.Revit.DB.Structure import Rebar, RebarInSystem

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


def get_bar_signature(document, elem):
    """Best-effort 'identical bar' signature: (diameter, shape_id, length).

    Used only for Mode A (group identical bars). Each lookup is defensive
    since not every rebar-like element exposes every property the same way
    (RebarInSystem has no discrete shape family, for example).
    """
    diameter = None
    try:
        bar_type = document.GetElement(elem.GetTypeId())
        if bar_type is not None:
            diameter = round(bar_type.BarDiameter, 5)
    except Exception:
        diameter = None

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
    """mode: 'group' or 'unique'. Returns dict {ElementId: mark_string} plus
    a parallel list of rows for the preview table, in the same order."""
    marks = {}
    preview_rows = []

    if mode == 'unique':
        n = start_number
        for elem in ordered_elems:
            mark_value = '{}{}'.format(prefix, n)
            marks[elem.Id] = mark_value
            preview_rows.append((elem, mark_value))
            n += 1
    else:
        group_number = {}
        next_n = [start_number]
        for elem in ordered_elems:
            sig = get_bar_signature(document, elem)
            if sig not in group_number:
                group_number[sig] = '{}{}'.format(prefix, next_n[0])
                next_n[0] += 1
            mark_value = group_number[sig]
            marks[elem.Id] = mark_value
            preview_rows.append((elem, mark_value))

    return marks, preview_rows


def apply_marks_to_model(document, marks):
    """Writes the Mark (BuiltInParameter.ALL_MODEL_MARK) of every element in
    `marks` inside a single Transaction. Returns (success_count, failed_count)."""
    t = Transaction(document, 'pyNBT - Renumber Rebar')
    t.Start()
    success = 0
    failed = 0
    try:
        for eid, mark_value in marks.items():
            elem = document.GetElement(eid)
            if elem is None:
                failed += 1
                continue
            param = elem.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
            if param is None or param.IsReadOnly:
                failed += 1
                continue
            param.Set(mark_value)
            success += 1
        t.Commit()
    except Exception:
        if t.HasStarted():
            t.RollBack()
        raise
    return success, failed


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

        return prefix, start_number, tol_mm, mode, layout_mode

    def _compute(self):
        inputs = self._read_inputs()
        if inputs is None:
            return None
        prefix, start_number, tol_mm, mode, layout_mode = inputs
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
        return marks, preview_rows, skipped_no_bbox, detected_layout

    # -- event handlers -------------------------------------------------
    def refresh_preview(self, sender, args):
        result = self._compute()
        if result is None:
            return
        marks, preview_rows, skipped_no_bbox, detected_layout = result
        self._last_marks = marks

        display_rows = ObservableCollection[object]()
        for i, (elem, mark_value) in enumerate(preview_rows, start=1):
            diameter_mm = None
            length_mm = None
            try:
                bar_type = doc.GetElement(elem.GetTypeId())
                if bar_type is not None:
                    diameter_mm = bar_type.BarDiameter * 304.8
            except Exception:
                pass
            try:
                length_mm = elem.TotalLength * 304.8
            except Exception:
                pass
            display_rows.Add(PreviewRow(i, elem, diameter_mm, length_mm, mark_value))

        self.ListPreview.ItemsSource = display_rows

        layout_label = LAYOUT_LABELS.get(detected_layout, detected_layout or '-')
        status = 'Detected layout: {} | {} bar(s) will be numbered ({} distinct mark(s)).'.format(
            layout_label, len(preview_rows), len(set(marks.values()))
        )
        if self.skipped:
            status += ' {} non-rebar element(s) skipped.'.format(self.skipped)
        if skipped_no_bbox:
            status += ' {} bar(s) skipped (no readable position).'.format(skipped_no_bbox)
        self.TxtStatus.Text = status

    def apply_marks(self, sender, args):
        result = self._compute()
        if result is None:
            return
        marks, preview_rows, skipped_no_bbox, detected_layout = result

        if not marks:
            forms.alert("No rebar to number.", title='pyNBT - Renumber Rebar')
            return

        try:
            success, failed = apply_marks_to_model(doc, marks)
        except Exception as ex:
            forms.alert("Error writing Mark to the model:\n{}".format(str(ex)), title='pyNBT - Renumber Rebar')
            logger.error('Renumber Rebar failed: %s', ex)
            return

        msg = 'Successfully numbered {} bar(s).'.format(success)
        if failed:
            msg += '\n{} bar(s) failed to write Mark (parameter locked or invalid).'.format(failed)
        forms.alert(msg, title='pyNBT - Renumber Rebar')
        self.TxtStatus.Text = msg
        self.refresh_preview(None, None)

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
