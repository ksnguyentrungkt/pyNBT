# -*- coding: utf-8 -*-
"""
pyNBT - Datum Control

Batch show/hide the end bubble (Left/Right for Level, Left/Right/Top/
Bottom for Grid) for multiple Grids or Levels at once, in the active
view.

The 2D/3D extent-type feature (DatumExtentType) was removed per Trung's
request (v1.2.0) -- this tool now only controls end-bubble visibility.

Input:
    - If the user already has Grid(s)/Level(s) selected before launching
      the tool, the list is filtered to that selection.
    - Otherwise the list auto-populates with every Grid / Level visible
      in the active view.

Left/Right/Top/Bottom for each element is determined automatically from
the angle of the datum line in the active view:
    - angle from the view's horizontal axis <= 45 deg  -> horizontal
      datum -> ends are Left / Right
    - angle from the view's horizontal axis  > 45 deg  -> vertical
      datum -> ends are Top / Bottom
This matches how Trung reads a diagonal Grid on a drawing: a Grid drawn
closer to vertical (angle > 45 deg) behaves like a vertical line
(Top/Bottom ends); a Grid drawn closer to horizontal (angle <= 45 deg)
behaves like a horizontal line (Left/Right ends).

Output: direct edits to the active view (wrapped in Transactions). The
preview list refreshes its status column right after every action.
"""

__title__ = "Datum\nControl"
__author__ = "pyNBT"
__doc__ = (
    "Batch show/hide the end bubble for Grids and Levels in the "
    "active view. Excel-style row selection: click, Shift-click for a "
    "range, Ctrl-click to toggle one."
)
__version__ = "1.3.0"

import sys
import os
import math

# --- Revit / WPF assemblies MUST be loaded before importing pyNBT.theme,
# since theme.py itself imports from System.Windows.Media. ----------------
import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("WindowsBase")

# --- shared pyNBT lib (compat.py / theme.py) -------------------------------
_here = os.path.dirname(__file__)
_ext_root = os.path.dirname(os.path.dirname(os.path.dirname(_here)))
_lib_dir = os.path.join(_ext_root, "lib")
if _lib_dir not in sys.path:
    sys.path.append(_lib_dir)

from pyNBT.compat import make_eid, eid_int  # noqa: F401  (kept for parity with other pyNBT tools)
from pyNBT import theme

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    Grid,
    Level,
    DatumEnds,
    DatumExtentType,
    Transaction,
)

# WPF types are spread across System.Windows / System.Windows.Controls /
# System.Windows.Input, and the exact split can differ across .NET/IronPython
# builds. Resolve every name dynamically against all three so this can't
# break again on a namespace guess.
import System.Windows
import System.Windows.Controls
import System.Windows.Input


def _wpf(name):
    for ns in (System.Windows, System.Windows.Controls, System.Windows.Input):
        if hasattr(ns, name):
            return getattr(ns, name)
    raise ImportError("pyNBT Datum Control: cannot resolve WPF type '{0}'".format(name))


Window = _wpf("Window")
WindowStartupLocation = _wpf("WindowStartupLocation")
Thickness = _wpf("Thickness")
HorizontalAlignment = _wpf("HorizontalAlignment")
VerticalAlignment = _wpf("VerticalAlignment")
GridLength = _wpf("GridLength")
GridUnitType = _wpf("GridUnitType")
FontWeights = _wpf("FontWeights")
TextWrapping = _wpf("TextWrapping")
CornerRadius = _wpf("CornerRadius")

WpfGrid = _wpf("Grid")
RowDefinition = _wpf("RowDefinition")
ColumnDefinition = _wpf("ColumnDefinition")
Border = _wpf("Border")
StackPanel = _wpf("StackPanel")
Orientation = _wpf("Orientation")
TextBlock = _wpf("TextBlock")
Button = _wpf("Button")
ScrollViewer = _wpf("ScrollViewer")
ScrollBarVisibility = _wpf("ScrollBarVisibility")
TabControl = _wpf("TabControl")
TabItem = _wpf("TabItem")

Cursors = _wpf("Cursors")
Keyboard = _wpf("Keyboard")
ModifierKeys = _wpf("ModifierKeys")

from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()


# =========================================================================
# Standalone Revit-logic functions (no UI references)
# =========================================================================

def get_end_points(elem, view):
    """Return (p0, p1) XYZ endpoints of the datum as drawn in `view`,
    where p0 corresponds to DatumEnds.End0 and p1 to DatumEnds.End1.
    Returns None if the element has no meaningful line in this view
    (e.g. a Level in a plan view, or a view type that doesn't support
    datum end curves)."""
    for extent in (DatumExtentType.Model, DatumExtentType.ViewSpecific):
        try:
            curves = list(elem.GetCurvesInView(extent, view))
        except Exception:
            curves = None
        if curves:
            try:
                p0 = curves[0].GetEndPoint(0)
                p1 = curves[-1].GetEndPoint(1)
                return p0, p1
            except Exception:
                continue
    return None


def classify_orientation(view, p0, p1):
    """Return 'H' (Left/Right ends) or 'V' (Top/Bottom ends).

    Rule (per Trung): angle between the datum direction and the view's
    horizontal axis > 45 deg -> treat as vertical (Top/Bottom);
    <= 45 deg -> treat as horizontal (Left/Right)."""
    try:
        right = view.RightDirection
        up = view.UpDirection
        d = p1 - p0
        dx = d.DotProduct(right)
        dy = d.DotProduct(up)
    except Exception:
        return None
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return None
    angle = math.degrees(math.atan2(abs(dy), abs(dx)))
    return "V" if angle > 45.0 else "H"


def map_ends(view, p0, p1, orientation):
    """Return {DatumEnds.End0: label, DatumEnds.End1: label}."""
    if orientation == "H":
        right = view.RightDirection
        v0, v1 = p0.DotProduct(right), p1.DotProduct(right)
        if v0 <= v1:
            return {DatumEnds.End0: "Left", DatumEnds.End1: "Right"}
        return {DatumEnds.End0: "Right", DatumEnds.End1: "Left"}
    else:
        up = view.UpDirection
        v0, v1 = p0.DotProduct(up), p1.DotProduct(up)
        if v0 <= v1:
            return {DatumEnds.End0: "Bottom", DatumEnds.End1: "Top"}
        return {DatumEnds.End0: "Top", DatumEnds.End1: "Bottom"}


def get_orientation_and_ends(elem, view):
    """Return (orientation, {end: label}) or (None, {}) if unavailable."""
    pts = get_end_points(elem, view)
    if pts is None:
        return None, {}
    p0, p1 = pts
    orientation = classify_orientation(view, p0, p1)
    if orientation is None:
        return None, {}
    return orientation, map_ends(view, p0, p1, orientation)


def get_end_for_side(elem, view, side):
    """Return the DatumEnds matching `side` ('Left'/'Right'/'Top'/'Bottom')
    for this element in this view, or None if this element's current
    orientation doesn't have that side."""
    _, ends = get_orientation_and_ends(elem, view)
    for end, label in ends.items():
        if label == side:
            return end
    return None


def get_bubble_summary(elem, view):
    """Return a short status string for the two relevant sides of this
    element, e.g. 'L:ON R:OFF' or 'T:ON B:ON', or 'N/A'."""
    orientation, ends = get_orientation_and_ends(elem, view)
    if orientation is None:
        return "N/A"
    parts = []
    order = ["Left", "Right"] if orientation == "H" else ["Top", "Bottom"]
    for label in order:
        end = None
        for e, l in ends.items():
            if l == label:
                end = e
                break
        if end is None:
            continue
        try:
            visible = elem.IsBubbleVisibleInView(end, view)
        except Exception:
            visible = None
        state = "ON" if visible else ("OFF" if visible is not None else "?")
        parts.append("{0}:{1}".format(label[0], state))
    return " ".join(parts) if parts else "N/A"


def collect_elements(doc, uidoc, view, of_class):
    """Selection-first, active-view-fallback collection."""
    try:
        sel_ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        sel_ids = []
    if sel_ids:
        elems = []
        for eid in sel_ids:
            e = doc.GetElement(eid)
            if e is not None and isinstance(e, of_class):
                elems.append(e)
        if elems:
            return sorted(elems, key=lambda x: _safe_name(x))
    collector = (
        FilteredElementCollector(doc, view.Id)
        .OfClass(of_class)
        .WhereElementIsNotElementType()
    )
    return sorted(list(collector), key=lambda x: _safe_name(x))


def _safe_name(elem):
    try:
        return elem.Name
    except Exception:
        return "<{0}>".format(elem.Id)


def set_bubble_for_elements(view, elements, side, visible):
    ok, skipped = [], []
    for elem in elements:
        end = get_end_for_side(elem, view, side)
        if end is None:
            skipped.append(elem)
            continue
        try:
            if visible:
                elem.ShowBubbleInView(end, view)
            else:
                elem.HideBubbleInView(end, view)
            ok.append(elem)
        except Exception:
            skipped.append(elem)
    return ok, skipped


# =========================================================================
# UI helpers
# =========================================================================

def _row_def(height="auto"):
    r = RowDefinition()
    r.Height = GridLength(1, GridUnitType.Auto) if height == "auto" else GridLength(1, GridUnitType.Star)
    return r


def _col_def(width="star", px=0):
    c = ColumnDefinition()
    if width == "auto":
        c.Width = GridLength(1, GridUnitType.Auto)
    elif width == "px":
        c.Width = GridLength(px)
    else:
        c.Width = GridLength(1, GridUnitType.Star)
    return c


def _text(s, size=12, weight=None, color=None, wrap=False):
    t = TextBlock()
    t.Text = s
    t.FontSize = size
    if weight:
        t.FontWeight = weight
    t.Foreground = theme.brush(color if color else theme.CLR_TEXT)
    if wrap:
        t.TextWrapping = TextWrapping.Wrap
    return t


def _btn(text, bg, fg, handler=None, height=30, min_width=64):
    b = Button()
    b.Content = text
    b.Background = theme.brush(bg)
    b.Foreground = theme.brush(fg)
    b.BorderThickness = Thickness(0)
    b.Padding = Thickness(8, 4, 8, 4)
    b.Margin = Thickness(3)
    b.FontWeight = FontWeights.SemiBold
    b.FontSize = 11
    b.Height = height
    b.MinWidth = min_width
    b.Cursor = Cursors.Hand
    if handler:
        b.Click += handler
    return b


def _group_label(text):
    t = _text(text.upper(), size=10, weight=FontWeights.Bold, color=theme.CLR_MUTED)
    t.Margin = Thickness(4, 10, 0, 2)
    return t


# =========================================================================
# Row (one Grid or Level in the preview list)
# =========================================================================

class DatumRow(object):
    """One Grid/Level row. Selection works like an Excel row header:
    click = select only this row, Shift+click = select the range from
    the last-clicked row to this one, Ctrl+click = toggle just this row
    without touching the others."""

    def __init__(self, elem, view, on_click=None):
        self.elem = elem
        self.view = view
        self.on_click = on_click
        self.selected = True  # default: everything selected, like before
        self.border = None

        self.name_text = _text(_safe_name(elem), size=12)
        self.name_text.VerticalAlignment = VerticalAlignment.Center

        self.status_text = _text("", size=11)
        self.status_text.VerticalAlignment = VerticalAlignment.Center
        self.status_text.HorizontalAlignment = HorizontalAlignment.Right

        self.refresh_status()

    def refresh_status(self):
        self.status_text.Text = get_bubble_summary(self.elem, self.view)

    def build_ui(self):
        row = WpfGrid()
        row.ColumnDefinitions.Add(_col_def("star"))
        row.ColumnDefinitions.Add(_col_def("auto"))
        WpfGrid.SetColumn(self.name_text, 0)
        WpfGrid.SetColumn(self.status_text, 1)
        row.Children.Add(self.name_text)
        row.Children.Add(self.status_text)

        border = Border()
        border.Padding = Thickness(8, 6, 8, 6)
        border.BorderBrush = theme.brush(theme.CLR_BORDER)
        border.BorderThickness = Thickness(0, 0, 0, 1)
        border.Cursor = Cursors.Hand
        border.Child = row
        border.MouseLeftButtonDown += self._on_mouse_down
        self.border = border
        self.apply_selection_style()
        return border

    def _on_mouse_down(self, sender, args):
        if self.on_click:
            self.on_click(self, args)

    def apply_selection_style(self):
        if self.border is None:
            return
        if self.selected:
            self.border.Background = theme.brush(theme.CLR_ACCENT)
            self.name_text.Foreground = theme.brush(theme.CLR_HEADER_TEXT)
            self.status_text.Foreground = theme.brush(theme.CLR_HEADER_SUB)
        else:
            self.border.Background = theme.brush(theme.CLR_CARD)
            self.name_text.Foreground = theme.brush(theme.CLR_TEXT)
            self.status_text.Foreground = theme.brush(theme.CLR_MUTED)


# =========================================================================
# Main window
# =========================================================================

class DatumControlWindow(Window):
    def __init__(self):
        self.view = doc.ActiveView
        self.grid_rows = []
        self.level_rows = []
        self.grid_list_panel = None
        self.level_list_panel = None
        self.grid_status_line = None
        self.level_status_line = None
        self._anchors = {"Grid": None, "Level": None}

        self._build_ui()
        self._load_data("Grid")
        self._load_data("Level")

    # ---------------------------------------------------------------
    # Data
    # ---------------------------------------------------------------
    def _load_data(self, kind):
        of_class = Grid if kind == "Grid" else Level
        elements = collect_elements(doc, uidoc, self.view, of_class)
        rows = [DatumRow(e, self.view, on_click=self._make_row_click_handler(kind))
                for e in elements]
        self._anchors[kind] = None
        if kind == "Grid":
            self.grid_rows = rows
            self._rebuild_list(self.grid_list_panel, rows)
        else:
            self.level_rows = rows
            self._rebuild_list(self.level_list_panel, rows)

    def _rebuild_list(self, panel, rows):
        if panel is None:
            return
        panel.Children.Clear()
        if not rows:
            panel.Children.Add(_text(
                "No elements found (nothing selected and none visible "
                "in the active view).", size=12, color=theme.CLR_MUTED, wrap=True))
            return
        for r in rows:
            panel.Children.Add(r.build_ui())

    def _selected_elements(self, rows):
        return [r.elem for r in rows if r.selected]

    def _refresh_all_status(self, rows):
        for r in rows:
            r.refresh_status()

    def _make_row_click_handler(self, kind):
        def on_click(row, args):
            self._on_row_click(kind, row)
        return on_click

    def _on_row_click(self, kind, row):
        """Excel-style row selection: plain click selects only this row,
        Shift+click selects the range from the anchor to this row,
        Ctrl+click toggles just this row and moves the anchor here."""
        rows = self.grid_rows if kind == "Grid" else self.level_rows
        if row not in rows:
            return
        index = rows.index(row)
        anchor = self._anchors.get(kind)

        try:
            mods = Keyboard.Modifiers
            shift = bool(mods & ModifierKeys.Shift)
            ctrl = bool(mods & ModifierKeys.Control)
        except Exception:
            shift = False
            ctrl = False

        if shift and anchor is not None:
            lo, hi = min(anchor, index), max(anchor, index)
            if ctrl:
                for i, r in enumerate(rows):
                    if lo <= i <= hi:
                        r.selected = True
            else:
                for i, r in enumerate(rows):
                    r.selected = (lo <= i <= hi)
        elif ctrl:
            row.selected = not row.selected
            self._anchors[kind] = index
        else:
            for r in rows:
                r.selected = False
            row.selected = True
            self._anchors[kind] = index

        for r in rows:
            r.apply_selection_style()

    # ---------------------------------------------------------------
    # UI construction
    # ---------------------------------------------------------------
    def _build_ui(self):
        self.Title = "pyNBT - Datum Control"
        self.Width = 820
        self.Height = 620
        self.MinWidth = 700
        self.MinHeight = 480
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.Background = theme.brush(theme.CLR_BG)

        root = WpfGrid()
        root.RowDefinitions.Add(_row_def("auto"))   # header
        root.RowDefinitions.Add(_row_def("star"))   # content
        root.RowDefinitions.Add(_row_def("auto"))   # footer

        root.Children.Add(self._build_header())
        WpfGrid.SetRow(root.Children[root.Children.Count - 1], 0)

        tabs = self._build_tabs()
        root.Children.Add(tabs)
        WpfGrid.SetRow(tabs, 1)

        footer = self._build_footer()
        root.Children.Add(footer)
        WpfGrid.SetRow(footer, 2)

        self.Content = root

    def _build_header(self):
        border = Border()
        border.Background = theme.brush(theme.CLR_HEADER)
        border.Padding = Thickness(16, 12, 16, 12)

        grid = WpfGrid()
        grid.ColumnDefinitions.Add(_col_def("star"))
        grid.ColumnDefinitions.Add(_col_def("auto"))

        left = StackPanel()
        title = _text("Datum Control", size=16, weight=FontWeights.Bold,
                       color=theme.CLR_HEADER_TEXT)
        subtitle = _text(
            "Batch show/hide the end bubble for Grids and Levels in "
            "the active view.",
            size=11, color=theme.CLR_HEADER_SUB, wrap=True)
        subtitle.Margin = Thickness(0, 2, 0, 0)
        left.Children.Add(title)
        left.Children.Add(subtitle)

        badge = _text("v{0}".format(__version__), size=11,
                       weight=FontWeights.SemiBold, color=theme.CLR_HEADER_SUB)
        badge.VerticalAlignment = VerticalAlignment.Center

        WpfGrid.SetColumn(left, 0)
        WpfGrid.SetColumn(badge, 1)
        grid.Children.Add(left)
        grid.Children.Add(badge)

        border.Child = grid
        return border

    def _build_tabs(self):
        tabs = TabControl()
        tabs.Margin = Thickness(12, 12, 12, 12)
        tabs.Background = theme.brush(theme.CLR_BG)

        grid_tab = TabItem()
        grid_tab.Header = "Grid"
        grid_tab.Content = self._build_tab_content("Grid")

        level_tab = TabItem()
        level_tab.Header = "Level"
        level_tab.Content = self._build_tab_content("Level")

        tabs.Items.Add(grid_tab)
        tabs.Items.Add(level_tab)
        return tabs

    def _build_tab_content(self, kind):
        content = WpfGrid()
        content.ColumnDefinitions.Add(_col_def("star"))
        content.ColumnDefinitions.Add(_col_def("px", 260))

        # ---- LEFT: selection list ----
        left_border = Border()
        left_border.Background = theme.brush(theme.CLR_CARD)
        left_border.BorderBrush = theme.brush(theme.CLR_BORDER)
        left_border.BorderThickness = Thickness(1)
        left_border.CornerRadius = CornerRadius(6)
        left_border.Margin = Thickness(0, 0, 8, 0)

        left_dock = WpfGrid()
        left_dock.RowDefinitions.Add(_row_def("auto"))
        left_dock.RowDefinitions.Add(_row_def("auto"))
        left_dock.RowDefinitions.Add(_row_def("star"))

        header_row = WpfGrid()
        header_row.ColumnDefinitions.Add(_col_def("star"))
        header_row.ColumnDefinitions.Add(_col_def("auto"))
        header_row.ColumnDefinitions.Add(_col_def("auto"))
        header_row.ColumnDefinitions.Add(_col_def("auto"))
        header_row.Margin = Thickness(8, 8, 8, 4)

        count_label = _text("", size=11, color=theme.CLR_MUTED)
        sel_all_btn = _btn("All", theme.CLR_CARD, theme.CLR_TEXT, min_width=44, height=24)
        sel_none_btn = _btn("None", theme.CLR_CARD, theme.CLR_TEXT, min_width=44, height=24)
        refresh_btn = _btn("Refresh", theme.CLR_CARD, theme.CLR_TEXT, min_width=64, height=24)
        for b in (sel_all_btn, sel_none_btn, refresh_btn):
            b.BorderBrush = theme.brush(theme.CLR_BORDER)
            b.BorderThickness = Thickness(1)

        WpfGrid.SetColumn(count_label, 0)
        WpfGrid.SetColumn(sel_all_btn, 1)
        WpfGrid.SetColumn(sel_none_btn, 2)
        WpfGrid.SetColumn(refresh_btn, 3)
        header_row.Children.Add(count_label)
        header_row.Children.Add(sel_all_btn)
        header_row.Children.Add(sel_none_btn)
        header_row.Children.Add(refresh_btn)

        hint = _text(
            "Click = select 1  |  Shift+Click = range  |  Ctrl+Click = toggle",
            size=9, color=theme.CLR_MUTED)
        hint.Margin = Thickness(8, 0, 8, 4)

        scroll = ScrollViewer()
        scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        scroll.Margin = Thickness(4, 0, 4, 8)
        list_panel = StackPanel()
        scroll.Content = list_panel

        left_dock.Children.Add(header_row)
        WpfGrid.SetRow(header_row, 0)
        left_dock.Children.Add(hint)
        WpfGrid.SetRow(hint, 1)
        left_dock.Children.Add(scroll)
        WpfGrid.SetRow(scroll, 2)
        left_border.Child = left_dock

        # ---- RIGHT: quick-action buttons ----
        right_border = Border()
        right_border.Background = theme.brush(theme.CLR_CARD)
        right_border.BorderBrush = theme.brush(theme.CLR_BORDER)
        right_border.BorderThickness = Thickness(1)
        right_border.CornerRadius = CornerRadius(6)

        right_panel = StackPanel()
        right_panel.Margin = Thickness(10, 8, 10, 8)

        sides = ["Left", "Right"] if kind == "Level" else ["Left", "Right", "Top", "Bottom"]
        right_panel.Children.Add(_group_label("End Bubble"))
        for side in sides:
            row = WpfGrid()
            row.ColumnDefinitions.Add(_col_def("star"))
            row.ColumnDefinitions.Add(_col_def("star"))
            show_btn = _btn("Show " + side, theme.CLR_APPLY, theme.CLR_APPLY_TEXT, height=30)
            hide_btn = _btn("Hide " + side, theme.CLR_CARD, theme.CLR_TEXT, height=30)
            hide_btn.BorderBrush = theme.brush(theme.CLR_BORDER)
            hide_btn.BorderThickness = Thickness(1)
            WpfGrid.SetColumn(show_btn, 0)
            WpfGrid.SetColumn(hide_btn, 1)
            row.Children.Add(show_btn)
            row.Children.Add(hide_btn)
            right_panel.Children.Add(row)

            show_btn.Click += self._make_bubble_handler(kind, side, True)
            hide_btn.Click += self._make_bubble_handler(kind, side, False)

        status_line = _text("", size=11, color=theme.CLR_MUTED, wrap=True)
        status_line.Margin = Thickness(0, 14, 0, 0)
        right_panel.Children.Add(status_line)

        right_border.Child = right_panel

        WpfGrid.SetColumn(left_border, 0)
        WpfGrid.SetColumn(right_border, 1)
        content.Children.Add(left_border)
        content.Children.Add(right_border)

        # wire handlers / keep references
        if kind == "Grid":
            self.grid_list_panel = list_panel
            self.grid_status_line = status_line
        else:
            self.level_list_panel = list_panel
            self.level_status_line = status_line

        sel_all_btn.Click += self._make_select_handler(kind, True)
        sel_none_btn.Click += self._make_select_handler(kind, False)
        refresh_btn.Click += self._make_refresh_handler(kind)

        self._update_count_label(kind, count_label)
        # keep the label current on refresh too
        if kind == "Grid":
            self._grid_count_label = count_label
        else:
            self._level_count_label = count_label

        return content

    def _update_count_label(self, kind, label):
        rows = self.grid_rows if kind == "Grid" else self.level_rows
        label.Text = "{0} {1}(s)".format(len(rows), kind)

    def _build_footer(self):
        border = Border()
        border.Background = theme.brush(theme.CLR_FOOTER)
        border.Padding = Thickness(16, 8, 16, 8)

        grid = WpfGrid()
        grid.ColumnDefinitions.Add(_col_def("star"))
        grid.ColumnDefinitions.Add(_col_def("auto"))

        try:
            view_name = self.view.Name
        except Exception:
            view_name = "Active View"
        sig = _text(
            u"Datum Control v{0}  |  {1}  |  {2}".format(
                __version__, view_name, doc.Title),
            size=10, color=theme.CLR_MUTED)
        sig.VerticalAlignment = VerticalAlignment.Center

        close_btn = _btn("Close", theme.CLR_CARD, theme.CLR_TEXT, height=30)
        close_btn.BorderBrush = theme.brush(theme.CLR_BORDER)
        close_btn.BorderThickness = Thickness(1)
        close_btn.Click += lambda s, e: self.Close()

        WpfGrid.SetColumn(sig, 0)
        WpfGrid.SetColumn(close_btn, 1)
        grid.Children.Add(sig)
        grid.Children.Add(close_btn)

        border.Child = grid
        return border

    # ---------------------------------------------------------------
    # Handlers
    # ---------------------------------------------------------------
    def _make_select_handler(self, kind, checked):
        def handler(sender, args):
            rows = self.grid_rows if kind == "Grid" else self.level_rows
            for r in rows:
                r.selected = checked
                r.apply_selection_style()
        return handler

    def _make_refresh_handler(self, kind):
        def handler(sender, args):
            self._load_data(kind)
            label = self._grid_count_label if kind == "Grid" else self._level_count_label
            self._update_count_label(kind, label)
            status = self.grid_status_line if kind == "Grid" else self.level_status_line
            status.Text = "Refreshed from {0}.".format(
                "current selection" if self._has_selection(kind) else "active view")
        return handler

    def _has_selection(self, kind):
        of_class = Grid if kind == "Grid" else Level
        try:
            sel_ids = list(uidoc.Selection.GetElementIds())
        except Exception:
            sel_ids = []
        for eid in sel_ids:
            e = doc.GetElement(eid)
            if e is not None and isinstance(e, of_class):
                return True
        return False

    def _make_bubble_handler(self, kind, side, visible):
        def handler(sender, args):
            rows = self.grid_rows if kind == "Grid" else self.level_rows
            elements = self._selected_elements(rows)
            status_line = self.grid_status_line if kind == "Grid" else self.level_status_line
            if not elements:
                status_line.Text = "No elements selected."
                return
            t = Transaction(doc, "pyNBT - Datum Control - Set Bubble")
            t.Start()
            try:
                ok, skipped = set_bubble_for_elements(self.view, elements, side, visible)
                t.Commit()
            except Exception as ex:
                if t.HasStarted():
                    t.RollBack()
                forms.alert("Error: {0}".format(str(ex)), title="Datum Control")
                return
            action = "Show" if visible else "Hide"
            status_line.Text = u"{0} {1}: {2} updated, {3} skipped (no {1} end).".format(
                action, side, len(ok), len(skipped))
        return handler


# =========================================================================
# Entry point
# =========================================================================

if doc.ActiveView is None:
    forms.alert("No active view.", title="Datum Control")
else:
    window = DatumControlWindow()
    window.ShowDialog()
