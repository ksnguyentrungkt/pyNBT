# -*- coding: utf-8 -*-
"""pyNBT - Quick Color

Apply a fast graphic override color (Surface Pattern + Cut Pattern, solid
fill) to selected elements. This is a VIEW-SPECIFIC override, it never
touches the element's real Material.

Input:  current selection if any, otherwise the tool prompts PickObjects.
Logic:  pick a color from the palette (click = select, does not apply yet),
        then Apply. Surface Foreground + Cut Foreground pattern are set to
        the project's "Solid fill" drafting pattern with that color, via
        View.SetElementOverrides. A second pair of actions can do the same
        across every view in the project (to spot the element anywhere) or
        clear it everywhere.
Output: direct view-specific override(s) (Transaction on the view(s)).
"""

import os
import sys

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("WindowsBase")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from Autodesk.Revit.DB import (
    Transaction,
    FilteredElementCollector,
    FillPatternElement,
    FillPatternTarget,
    OverrideGraphicSettings,
    Color as RevitColor,
    ElementId,
    View,
    WorksharingUtils,
    CheckoutStatus,
)
from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

from System.Windows import (
    Window, WindowStartupLocation, Thickness, HorizontalAlignment,
    VerticalAlignment, GridLength, GridUnitType, FontWeights, TextWrapping,
    SizeToContent, ResizeMode,
)
from System.Windows.Controls import (
    Grid, RowDefinition, ColumnDefinition, Border, TextBlock, Button,
    StackPanel, Orientation,
)
from System.Windows.Controls.Primitives import UniformGrid
from System.Windows.Media import SolidColorBrush, Color as MediaColor, Brushes
from System.Windows.Forms import ColorDialog, DialogResult
from System.Drawing import Color as DrawColor

from pyrevit import revit, forms

doc = revit.doc
uidoc = revit.uidoc

# --- make the shared pyNBT lib importable ----------------------------------
_LIB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "lib",
)
if _LIB_DIR not in sys.path:
    sys.path.append(_LIB_DIR)

from pyNBT import theme

TOOL_NAME = "Quick Color"
TOOL_VERSION = "2.1"

# Preset palette shown in the window (name, (r, g, b)) - 15 presets, the
# 16th tile in the 4x4 grid is the "Custom color..." tile.
PRESET_COLORS = [
    ("Red", (237, 28, 36)),
    ("Orange", (255, 127, 39)),
    ("Yellow", (255, 201, 14)),
    ("Green", (34, 177, 76)),
    ("Teal", (0, 168, 120)),
    ("Cyan", (0, 162, 232)),
    ("Blue", (63, 72, 204)),
    ("Purple", (128, 0, 255)),
    ("Magenta", (236, 0, 140)),
    ("Pink", (255, 174, 201)),
    ("Brown", (185, 122, 87)),
    ("Gray", (128, 128, 128)),
    ("Dark Gray", (64, 64, 64)),
    ("Black", (0, 0, 0)),
    ("White", (255, 255, 255)),
]


# ---------------------------------------------------------------------------
# Standalone Revit logic (no UI references)
# ---------------------------------------------------------------------------

def get_solid_fill_pattern_id(document):
    """Find the drafting 'Solid fill' pattern element id in the document.
    Returns ElementId.InvalidElementId if none is found."""
    collector = FilteredElementCollector(document).OfClass(FillPatternElement)
    for fpe in collector:
        try:
            pattern = fpe.GetFillPattern()
        except Exception:
            continue
        if pattern is not None and pattern.IsSolidFill \
                and pattern.Target == FillPatternTarget.Drafting:
            return fpe.Id
    return ElementId.InvalidElementId


def _owner_if_locked(document, element_id):
    """Return the owner's username if element_id is checked out by
    someone else in a workshared model, or None if it's free to edit
    (including on a non-workshared model)."""
    try:
        if not document.IsWorkshared:
            return None
        status = WorksharingUtils.GetCheckoutStatus(document, element_id)
        if status != CheckoutStatus.OwnedByOtherUser:
            return None
        try:
            info = WorksharingUtils.GetWorksharingTooltipInfo(document, element_id)
            return info.Owner if info and info.Owner else "another user"
        except Exception:
            return "another user"
    except Exception:
        # If ownership can't be determined, don't block on it - let the
        # normal try/except around SetElementOverrides handle it.
        return None


def split_editable_elements(document, element_ids):
    """Split element ids into (editable_ids, locked_map) where locked_map
    maps element_id -> owner name for elements owned by someone else."""
    editable = []
    locked = {}
    for eid in element_ids:
        owner = _owner_if_locked(document, eid)
        if owner is None:
            editable.append(eid)
        else:
            locked[eid] = owner
    return editable, locked


def split_editable_views(document, views):
    """Same as split_editable_elements, but for View elements."""
    editable = []
    locked = {}
    for v in views:
        owner = _owner_if_locked(document, v.Id)
        if owner is None:
            editable.append(v)
        else:
            locked[v.Id] = owner
    return editable, locked


def _format_locked_message(locked_map, label):
    if not locked_map:
        return ""
    owners = sorted(set(locked_map.values()))
    return " Skipped {} {} owned by: {}.".format(len(locked_map), label, ", ".join(owners))



def build_color_override(rgb, solid_fill_id):
    """Build an OverrideGraphicSettings that colors Surface + Cut
    foreground patterns solid with the given (r, g, b) tuple."""
    r, g, b = rgb
    revit_color = RevitColor(r, g, b)
    ogs = OverrideGraphicSettings()
    if solid_fill_id != ElementId.InvalidElementId:
        ogs.SetSurfaceForegroundPatternId(solid_fill_id)
        ogs.SetCutForegroundPatternId(solid_fill_id)
    ogs.SetSurfaceForegroundPatternColor(revit_color)
    ogs.SetSurfaceForegroundPatternVisible(True)
    ogs.SetCutForegroundPatternColor(revit_color)
    ogs.SetCutForegroundPatternVisible(True)
    return ogs


def apply_color_to_elements(document, view, element_ids, rgb, solid_fill_id):
    """Apply the color override to every editable element id, in one
    Transaction, in a single view. Elements owned by another user are
    skipped up front (never touched, so Revit's ownership dialog never
    fires). Returns a dict: applied, failed, locked (id -> owner)."""
    editable_ids, locked = split_editable_elements(document, element_ids)
    ogs = build_color_override(rgb, solid_fill_id)
    applied = 0
    failed = 0
    if editable_ids:
        t = Transaction(document, "pyNBT - {} - Apply".format(TOOL_NAME))
        t.Start()
        try:
            for eid in editable_ids:
                try:
                    view.SetElementOverrides(eid, ogs)
                    applied += 1
                except Exception:
                    failed += 1
            t.Commit()
        except Exception as ex:
            if t.HasStarted():
                t.RollBack()
            forms.alert("Error applying color: {}".format(str(ex)), title=TOOL_NAME)
            return {"applied": 0, "failed": len(editable_ids), "locked": locked}
    return {"applied": applied, "failed": failed, "locked": locked}


def reset_elements(document, view, element_ids):
    """Clear graphic overrides (back to default) for every editable
    element id, in a single view. Elements owned by another user are
    skipped up front. Returns a dict: applied, failed, locked."""
    editable_ids, locked = split_editable_elements(document, element_ids)
    default_ogs = OverrideGraphicSettings()
    applied = 0
    failed = 0
    if editable_ids:
        t = Transaction(document, "pyNBT - {} - Reset".format(TOOL_NAME))
        t.Start()
        try:
            for eid in editable_ids:
                try:
                    view.SetElementOverrides(eid, default_ogs)
                    applied += 1
                except Exception:
                    failed += 1
            t.Commit()
        except Exception as ex:
            if t.HasStarted():
                t.RollBack()
            forms.alert("Error resetting color: {}".format(str(ex)), title=TOOL_NAME)
            return {"applied": 0, "failed": len(editable_ids), "locked": locked}
    return {"applied": applied, "failed": failed, "locked": locked}


def get_all_overridable_views(document):
    """All non-template views in the project that accept element graphic
    overrides."""
    collector = FilteredElementCollector(document).OfClass(View)
    views = []
    for v in collector:
        try:
            if v.IsTemplate:
                continue
            if hasattr(v, "AreGraphicsOverridesAllowed") and not v.AreGraphicsOverridesAllowed():
                continue
            views.append(v)
        except Exception:
            continue
    return views


def apply_color_to_all_views(document, element_ids, rgb, solid_fill_id):
    """Apply the color override to the given elements in EVERY overridable
    view of the project, in one Transaction. Elements AND views owned by
    another user are skipped up front. Returns a dict: applied, failed,
    view_count, locked_elements, locked_views."""
    editable_ids, locked_elements = split_editable_elements(document, element_ids)
    all_views = get_all_overridable_views(document)
    editable_views, locked_views = split_editable_views(document, all_views)
    ogs = build_color_override(rgb, solid_fill_id)
    applied = 0
    failed = 0
    if editable_ids and editable_views:
        t = Transaction(document, "pyNBT - {} - Apply (All Views)".format(TOOL_NAME))
        t.Start()
        try:
            for v in editable_views:
                for eid in editable_ids:
                    try:
                        v.SetElementOverrides(eid, ogs)
                        applied += 1
                    except Exception:
                        failed += 1
            t.Commit()
        except Exception as ex:
            if t.HasStarted():
                t.RollBack()
            forms.alert("Error applying color to all views: {}".format(str(ex)), title=TOOL_NAME)
            return {
                "applied": 0, "failed": len(editable_ids) * len(editable_views),
                "view_count": len(editable_views),
                "locked_elements": locked_elements, "locked_views": locked_views,
            }
    return {
        "applied": applied, "failed": failed, "view_count": len(editable_views),
        "locked_elements": locked_elements, "locked_views": locked_views,
    }


def reset_color_in_all_views(document, element_ids):
    """Clear graphic overrides for the given elements in EVERY overridable
    view of the project, in one Transaction. Elements AND views owned by
    another user are skipped up front. Returns a dict: applied, failed,
    view_count, locked_elements, locked_views."""
    editable_ids, locked_elements = split_editable_elements(document, element_ids)
    all_views = get_all_overridable_views(document)
    editable_views, locked_views = split_editable_views(document, all_views)
    default_ogs = OverrideGraphicSettings()
    applied = 0
    failed = 0
    if editable_ids and editable_views:
        t = Transaction(document, "pyNBT - {} - Reset (All Views)".format(TOOL_NAME))
        t.Start()
        try:
            for v in editable_views:
                for eid in editable_ids:
                    try:
                        v.SetElementOverrides(eid, default_ogs)
                        applied += 1
                    except Exception:
                        failed += 1
            t.Commit()
        except Exception as ex:
            if t.HasStarted():
                t.RollBack()
            forms.alert("Error resetting color in all views: {}".format(str(ex)), title=TOOL_NAME)
            return {
                "applied": 0, "failed": len(editable_ids) * len(editable_views),
                "view_count": len(editable_views),
                "locked_elements": locked_elements, "locked_views": locked_views,
            }
    return {
        "applied": applied, "failed": failed, "view_count": len(editable_views),
        "locked_elements": locked_elements, "locked_views": locked_views,
    }


def get_current_selection_ids(ui_document):
    """Return the currently selected element ids, or an empty list."""
    try:
        return list(ui_document.Selection.GetElementIds())
    except Exception:
        return []


def pick_elements(ui_document):
    """Prompt the user to pick elements. Returns a list of ElementId, or
    None if the user cancelled the pick."""
    try:
        refs = ui_document.Selection.PickObjects(
            ObjectType.Element, "Select elements for {}".format(TOOL_NAME)
        )
    except OperationCanceledException:
        return None
    return [r.ElementId for r in refs]


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _rgb_brush(rgb):
    r, g, b = rgb
    return SolidColorBrush(MediaColor.FromRgb(r, g, b))


def _row(grid, height=None, star=False):
    rd = RowDefinition()
    if star:
        rd.Height = GridLength(1, GridUnitType.Star)
    elif height is not None:
        rd.Height = GridLength(height)
    else:
        rd.Height = GridLength(1, GridUnitType.Auto)
    grid.RowDefinitions.Add(rd)


def _make_button(text, bg_color, fg_color, height=36):
    btn = Button()
    btn.Content = text
    btn.Height = height
    btn.Background = theme.brush(bg_color)
    btn.Foreground = theme.brush(fg_color)
    btn.BorderThickness = Thickness(0)
    btn.FontWeight = FontWeights.SemiBold
    return btn


def _make_secondary_button(text, height=34):
    btn = Button()
    btn.Content = text
    btn.Height = height
    btn.Background = theme.brush(theme.CLR_CARD)
    btn.Foreground = theme.brush(theme.CLR_TEXT)
    btn.BorderBrush = theme.brush(theme.CLR_BORDER)
    btn.BorderThickness = Thickness(1)
    return btn


def _two_col_row(left, right, gap=8):
    grid = Grid()
    grid.Margin = Thickness(0, 6, 0, 0)
    c0 = ColumnDefinition()
    c0.Width = GridLength(1, GridUnitType.Star)
    c1 = ColumnDefinition()
    c1.Width = GridLength(gap)
    c2 = ColumnDefinition()
    c2.Width = GridLength(1, GridUnitType.Star)
    grid.ColumnDefinitions.Add(c0)
    grid.ColumnDefinitions.Add(c1)
    grid.ColumnDefinitions.Add(c2)
    Grid.SetColumn(left, 0)
    Grid.SetColumn(right, 2)
    grid.Children.Add(left)
    grid.Children.Add(right)
    return grid


def _section_label(text):
    tb = TextBlock()
    tb.Text = text
    tb.FontSize = 11
    tb.Foreground = theme.brush(theme.CLR_MUTED)
    tb.Margin = Thickness(0, 12, 0, 4)
    return tb


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

class QuickColorWindow(Window):
    def __init__(self, document, ui_document, view, element_ids, solid_fill_id):
        self.doc = document
        self.uidoc = ui_document
        self.view = view
        self.element_ids = element_ids
        self.solid_fill_id = solid_fill_id
        self.request_pick = False

        self.selected_rgb = None
        self.selected_name = None
        self.custom_rgb = None
        self._all_swatch_buttons = []
        self._custom_tile = None

        self.Title = "pyNBT - {}".format(TOOL_NAME)
        self.Width = 300
        self.SizeToContent = SizeToContent.Height
        self.ResizeMode = ResizeMode.NoResize
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.Background = theme.brush(theme.CLR_BG)

        root = Grid()
        _row(root)  # header
        _row(root)  # content
        _row(root)  # footer

        root.Children.Add(self._build_header())
        content = self._build_content()
        Grid.SetRow(content, 1)
        root.Children.Add(content)
        footer = self._build_footer()
        Grid.SetRow(footer, 2)
        root.Children.Add(footer)

        self.Content = root

    # -- sections ------------------------------------------------------

    def _build_header(self):
        header = Border()
        header.Background = theme.brush(theme.CLR_HEADER)
        header.Padding = Thickness(14, 10, 14, 10)

        stack = StackPanel()

        title = TextBlock()
        title.Text = TOOL_NAME
        title.FontSize = 16
        title.FontWeight = FontWeights.Bold
        title.Foreground = theme.brush(theme.CLR_HEADER_TEXT)
        stack.Children.Add(title)

        self.subtitle_tb = TextBlock()
        self.subtitle_tb.FontSize = 11
        self.subtitle_tb.Foreground = theme.brush(theme.CLR_HEADER_SUB)
        self.subtitle_tb.Margin = Thickness(0, 2, 0, 0)
        self._refresh_subtitle()
        stack.Children.Add(self.subtitle_tb)

        header.Child = stack
        Grid.SetRow(header, 0)
        return header

    def _build_content(self):
        content = Border()
        content.Background = theme.brush(theme.CLR_BG)
        content.Padding = Thickness(14, 10, 14, 4)

        stack = StackPanel()

        label = TextBlock()
        label.Text = "Colors (click to select, then Apply)"
        label.FontSize = 11
        label.Foreground = theme.brush(theme.CLR_MUTED)
        label.Margin = Thickness(0, 0, 0, 6)
        stack.Children.Add(label)

        swatch_grid = UniformGrid()
        swatch_grid.Columns = 4
        swatch_grid.Rows = 4
        for name, rgb in PRESET_COLORS:
            swatch_grid.Children.Add(self._build_swatch(name, rgb))
        swatch_grid.Children.Add(self._build_custom_tile())
        stack.Children.Add(swatch_grid)

        pick_btn = _make_secondary_button("Pick Elements...", height=30)
        pick_btn.Margin = Thickness(0, 10, 0, 0)
        pick_btn.Click += self.on_pick_elements_click
        stack.Children.Add(pick_btn)

        stack.Children.Add(_section_label("This view"))
        self.apply_btn = _make_button("Apply", theme.CLR_APPLY, theme.CLR_APPLY_TEXT)
        self.apply_btn.Click += self.on_apply_click
        self._set_enabled(self.apply_btn, False)
        self.reset_btn = _make_secondary_button("Reset")
        self.reset_btn.Click += self.on_reset_click
        stack.Children.Add(_two_col_row(self.apply_btn, self.reset_btn))

        stack.Children.Add(_section_label("All views (find the element anywhere)"))
        self.apply_all_btn = _make_button("Highlight All Views", theme.CLR_ACCENT, theme.CLR_HEADER_TEXT)
        self.apply_all_btn.Click += self.on_apply_all_views_click
        self._set_enabled(self.apply_all_btn, False)
        self.reset_all_btn = _make_secondary_button("Reset All Views")
        self.reset_all_btn.Click += self.on_reset_all_views_click
        stack.Children.Add(_two_col_row(self.apply_all_btn, self.reset_all_btn))

        content.Child = stack
        return content

    def _build_swatch(self, name, rgb):
        btn = Button()
        btn.Width = 54
        btn.Height = 38
        btn.Margin = Thickness(3)
        btn.Background = _rgb_brush(rgb)
        btn.BorderBrush = theme.brush(theme.CLR_BORDER)
        btn.BorderThickness = Thickness(1)
        btn.ToolTip = name
        btn.Content = None
        btn.Click += lambda s, e, c=rgb, n=name, b=btn: self._select_swatch(b, c, n)
        self._all_swatch_buttons.append(btn)
        return btn

    def _build_custom_tile(self):
        btn = Button()
        btn.Width = 54
        btn.Height = 38
        btn.Margin = Thickness(3)
        btn.Background = theme.brush(theme.CLR_CARD)
        btn.BorderBrush = theme.brush(theme.CLR_BORDER)
        btn.BorderThickness = Thickness(1)
        btn.ToolTip = "Custom color..."

        plus = TextBlock()
        plus.Text = "+"
        plus.FontSize = 18
        plus.FontWeight = FontWeights.Bold
        plus.Foreground = theme.brush(theme.CLR_MUTED)
        plus.HorizontalAlignment = HorizontalAlignment.Center
        plus.VerticalAlignment = VerticalAlignment.Center
        btn.Content = plus

        btn.Click += self.on_custom_swatch_click
        self._all_swatch_buttons.append(btn)
        self._custom_tile = btn
        return btn

    def _build_footer(self):
        footer = Border()
        footer.Background = theme.brush(theme.CLR_FOOTER)
        footer.Padding = Thickness(14, 8, 14, 8)

        grid = Grid()
        c0 = ColumnDefinition()
        c0.Width = GridLength(1, GridUnitType.Star)
        c1 = ColumnDefinition()
        c1.Width = GridLength(1, GridUnitType.Auto)
        grid.ColumnDefinitions.Add(c0)
        grid.ColumnDefinitions.Add(c1)

        self.status_tb = TextBlock()
        self.status_tb.Text = "Ready."
        self.status_tb.FontSize = 11
        self.status_tb.Foreground = theme.brush(theme.CLR_MUTED)
        self.status_tb.TextWrapping = TextWrapping.Wrap
        self.status_tb.VerticalAlignment = VerticalAlignment.Center
        Grid.SetColumn(self.status_tb, 0)
        grid.Children.Add(self.status_tb)

        close_btn = Button()
        close_btn.Content = "Close"
        close_btn.Width = 70
        close_btn.Height = 28
        close_btn.Background = theme.brush(theme.CLR_CARD)
        close_btn.Foreground = theme.brush(theme.CLR_TEXT)
        close_btn.BorderBrush = theme.brush(theme.CLR_BORDER)
        close_btn.BorderThickness = Thickness(1)
        close_btn.Click += self.on_close_click
        Grid.SetColumn(close_btn, 1)
        grid.Children.Add(close_btn)

        footer.Child = grid
        return footer

    # -- state helpers ---------------------------------------------------

    def _refresh_subtitle(self):
        count = len(self.element_ids)
        if count == 1:
            self.subtitle_tb.Text = "1 element selected"
        else:
            self.subtitle_tb.Text = "{} elements selected".format(count)

    def set_status(self, text):
        self.status_tb.Text = text

    def _set_enabled(self, button, enabled):
        button.IsEnabled = enabled
        button.Opacity = 1.0 if enabled else 0.45

    def _select_swatch(self, target_btn, rgb, name):
        for btn in self._all_swatch_buttons:
            btn.BorderBrush = theme.brush(theme.CLR_BORDER)
            btn.BorderThickness = Thickness(1)
        target_btn.BorderBrush = theme.brush(theme.CLR_ACCENT)
        target_btn.BorderThickness = Thickness(3)
        self.selected_rgb = rgb
        self.selected_name = name
        self._set_enabled(self.apply_btn, True)
        self._set_enabled(self.apply_all_btn, True)
        self.set_status("Selected {}.".format(name))

    # -- event handlers ----------------------------------------------------

    def on_custom_swatch_click(self, sender, args):
        dlg = ColorDialog()
        dlg.FullOpen = True
        if self.custom_rgb is not None:
            r, g, b = self.custom_rgb
            dlg.Color = DrawColor.FromArgb(r, g, b)
        if dlg.ShowDialog() == DialogResult.OK:
            c = dlg.Color
            rgb = (c.R, c.G, c.B)
            self.custom_rgb = rgb
            self._custom_tile.Background = _rgb_brush(rgb)
            self._custom_tile.Content = None
            self._custom_tile.ToolTip = "Custom: {}, {}, {}".format(*rgb)
            self._select_swatch(self._custom_tile, rgb, "custom color")

    def on_apply_click(self, sender, args):
        if self.selected_rgb is None:
            forms.alert("Pick a color first.", title=TOOL_NAME)
            return
        if not self.element_ids:
            forms.alert("No elements selected.", title=TOOL_NAME)
            return
        result = apply_color_to_elements(
            self.doc, self.view, self.element_ids, self.selected_rgb, self.solid_fill_id
        )
        msg = "Applied to {} element(s).".format(result["applied"])
        if result["failed"]:
            msg += " {} failed.".format(result["failed"])
        msg += _format_locked_message(result["locked"], "element(s)")
        if result["failed"] or result["locked"]:
            forms.alert(msg, title=TOOL_NAME)
        self.request_pick = False
        self.Close()

    def on_reset_click(self, sender, args):
        if not self.element_ids:
            forms.alert("No elements selected.", title=TOOL_NAME)
            return
        result = reset_elements(self.doc, self.view, self.element_ids)
        msg = "Reset {} element(s).".format(result["applied"])
        if result["failed"]:
            msg += " {} failed.".format(result["failed"])
        msg += _format_locked_message(result["locked"], "element(s)")
        if result["failed"] or result["locked"]:
            forms.alert(msg, title=TOOL_NAME)
        self.request_pick = False
        self.Close()

    def on_apply_all_views_click(self, sender, args):
        if self.selected_rgb is None:
            forms.alert("Pick a color first.", title=TOOL_NAME)
            return
        if not self.element_ids:
            forms.alert("No elements selected.", title=TOOL_NAME)
            return
        proceed = forms.alert(
            "Apply {} to the selected element(s) in ALL views of the "
            "project? This is useful to spot the element anywhere, but "
            "touches every view. Elements/views owned by another user "
            "right now will be skipped automatically.".format(self.selected_name),
            title=TOOL_NAME, yes=True, no=True,
        )
        if not proceed:
            return
        result = apply_color_to_all_views(
            self.doc, self.element_ids, self.selected_rgb, self.solid_fill_id
        )
        msg = "Applied in {} view(s) ({} element-view overrides).".format(
            result["view_count"], result["applied"]
        )
        if result["failed"]:
            msg += " {} skipped (not visible in that view).".format(result["failed"])
        msg += _format_locked_message(result["locked_elements"], "element(s)")
        msg += _format_locked_message(result["locked_views"], "view(s)")
        forms.alert(msg, title=TOOL_NAME)
        self.request_pick = False
        self.Close()

    def on_reset_all_views_click(self, sender, args):
        if not self.element_ids:
            forms.alert("No elements selected.", title=TOOL_NAME)
            return
        proceed = forms.alert(
            "Reset (clear) color override for the selected element(s) in "
            "ALL views of the project? Elements/views owned by another "
            "user right now will be skipped automatically.",
            title=TOOL_NAME, yes=True, no=True,
        )
        if not proceed:
            return
        result = reset_color_in_all_views(self.doc, self.element_ids)
        msg = "Reset in {} view(s) ({} element-view overrides).".format(
            result["view_count"], result["applied"]
        )
        if result["failed"]:
            msg += " {} skipped.".format(result["failed"])
        msg += _format_locked_message(result["locked_elements"], "element(s)")
        msg += _format_locked_message(result["locked_views"], "view(s)")
        forms.alert(msg, title=TOOL_NAME)
        self.request_pick = False
        self.Close()

    def on_pick_elements_click(self, sender, args):
        self.request_pick = True
        self.Close()

    def on_close_click(self, sender, args):
        self.request_pick = False
        self.Close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    view = doc.ActiveView
    if view is None:
        forms.alert("No active graphical view.", title=TOOL_NAME)
        return
    if hasattr(view, "AreGraphicsOverridesAllowed") and not view.AreGraphicsOverridesAllowed():
        forms.alert(
            "The active view ({}) does not support element graphic "
            "overrides.".format(view.Name),
            title=TOOL_NAME,
        )
        return

    element_ids = get_current_selection_ids(uidoc)
    if not element_ids:
        element_ids = pick_elements(uidoc)
        if element_ids is None:
            return  # user cancelled the pick
        if not element_ids:
            forms.alert("No elements selected.", title=TOOL_NAME)
            return

    solid_fill_id = get_solid_fill_pattern_id(doc)
    if solid_fill_id == ElementId.InvalidElementId:
        forms.alert(
            "Could not find a 'Solid fill' drafting pattern in this "
            "project. Color will still be applied, but the pattern may "
            "not render as a flat solid fill.",
            title=TOOL_NAME,
        )

    # Loop so "Pick Elements..." can fully close this window, run
    # PickObjects on its own (never nested inside a hidden modal window),
    # then reopen a fresh window - avoids the nested-modal crash pattern
    # already hit and fixed once before in Wall Top Elevation.
    while True:
        window = QuickColorWindow(doc, uidoc, view, element_ids, solid_fill_id)
        window.ShowDialog()

        if not window.request_pick:
            break

        new_ids = pick_elements(uidoc)
        if new_ids is None or not new_ids:
            element_ids = window.element_ids
            continue
        element_ids = new_ids


if __name__ == "__main__":
    main()
