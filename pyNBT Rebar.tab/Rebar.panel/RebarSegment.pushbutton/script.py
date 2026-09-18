# -*- coding: utf-8 -*-
"""Rebar Segment

Drapes "top layer" rebar across a warped/twisted host face (e.g. a ramp
transition slab) that Revit's normal flat-plane Rebar Set/layout cannot
follow.

How to use:
  1. Pick a RebarBarType, Spacing (mm) and Cover (mm).
  2. Click "Pick Top Face & 4 Points" -- pick the host's TOP face once,
     then click the SHORT edge nearest each of its 4 corners, in order:
     A, b, c, d (A-b = the first bar, c-d = the last bar; the bars in
     between are distributed along the A->c and b->d edges, following the
     face's real curved surface, not a flat plane through the 4 points).
     Hovering near an edge makes Revit highlight the WHOLE edge -- a much
     clearer visual cue than a point snap, and it works even on a plain
     curved wall with no Adaptive Points at all. When the face is a simple
     4-edge quad (a ramp/wall top face normally is), the click then snaps
     straight onto that corner's real vertex -- no precise clicking
     needed. A colored crosshair marker is dropped on each locked corner
     (A=red, b=green, c=blue, d=orange) so you can see exactly which point
     was used; the markers stay on screen (through Create Rebar too) until
     you restart Step 1 or click Close, so you can keep checking A/b/c/d
     while troubleshooting a run's result.
  3. Choose a LAYOUT MODE:
       - "Rail-to-rail" -- every bar spans rail A->c to rail b->d, evenly
         spaced along the rail (the original behaviour). Nothing more to
         pick -- go straight to "Create Rebar".
       - "Parallel (clip at boundary)" -- draw a Model Line or Detail Line
         first (Revit's own Line tool, in a plan view) along the direction
         you want the bars to run, then click "Pick Direction Line"
         (Step 2, shown only in this mode) and pick it. Every bar runs
         PARALLEL to that line, spaced at a true perpendicular distance,
         clipped wherever it runs outside the top face. "Edge Distance"
         sets back the first/last row from the A-b / c-d ends. A bar
         clipped shorter than "Min Bar Length" is skipped.
  4. Click "Create Rebar". Cover is measured to the bar's OUTER surface --
     the tool automatically adds the selected Bar Type's own radius on
     top of the Cover value you enter, and is pushed in from BOTH the top
     face and whichever of the quad's 4 edges (A-b, b-d, d-c, c-A) each
     bar endpoint actually sits on/near -- computed straight from the 4
     picked corner points, no side face pick needed any more (v1.2.0).

V1 scope (locked with NBT, see project doc
"rebar-segment-warped-face-tool.md"): every bar is a single straight
Curve -- no hook is auto-generated yet (add one later via Edit Sketch, or
wait for a future version). The host face is assumed to be a ruled
surface in the A->c / b->d direction; each rail point is snapped exactly
onto the face via Face.Project, so the bars hug the surface closely even
where that assumption isn't perfectly exact. Each bar's own sketch plane
is computed from ITS OWN local direction + the face's local normal (not
one shared plane for the whole area), so a hook added by hand later bends
in the correct local direction. A fresh Partition value (not colliding
with anything already in the model) is auto-assigned to the whole batch.

pyNBT Rebar.tab / Rebar.panel / RebarSegment.pushbutton
"""

__title__ = "Rebar\nSegment"
__author__ = "pyNBT"
__doc__ = (
    "Drape top-layer rebar across a warped/twisted host face: pick a top "
    "face + 4 corner points (A, b, c, d), the bars follow the real curved "
    "surface instead of a flat plane through those 4 points."
)

# Mandatory for every pyNBT tool that uses a modeless window: without this,
# pyRevit recycles the IronPython engine right after Show() returns, and
# every module-level import (System.Windows.*, logic, ...) disappears the
# moment a button handler or ExternalEvent callback runs later. See project
# doc "pynbt-modeless-tool-pattern.md".
__persistentengine__ = True

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))    # ...\RebarSegment.pushbutton
PANEL_DIR = os.path.dirname(SCRIPT_DIR)                     # ...\Rebar.panel
TAB_DIR = os.path.dirname(PANEL_DIR)                        # ...\pyNBT Rebar.tab
EXTENSION_DIR = os.path.dirname(TAB_DIR)                    # ...\pyNBT.extension
LIB_DIR = os.path.join(EXTENSION_DIR, "lib")

if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

import System
from System.Windows import (
    Window, WindowStartupLocation, Thickness, GridLength, GridUnitType,
    HorizontalAlignment, VerticalAlignment, FontWeights, TextWrapping,
    ResizeMode, MessageBox, MessageBoxButton, MessageBoxImage,
)
from System.Windows.Controls import (
    Grid, RowDefinition, ColumnDefinition, Border, StackPanel, TextBlock,
    TextBox, Button, ComboBox, ComboBoxItem, ScrollViewer, ScrollBarVisibility,
    ToolTip, ToolTipService,
)

from Autodesk.Revit.DB import Transaction, LocationCurve, Line
from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyNBT.theme import (
    CLR_HEADER, CLR_HEADER_TEXT, CLR_HEADER_SUB, CLR_ACCENT, CLR_BG,
    CLR_CARD, CLR_BORDER, CLR_FOOTER, CLR_TEXT, CLR_MUTED, CLR_APPLY,
    CLR_APPLY_TEXT, CLR_ERROR, CLR_SUCCESS, brush,
)

import rebar_segment_logic as logic

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

TOOL_TITLE = "Rebar Segment"
TOOL_VERSION = "v1.2.0"

# v1.2.0 -- NBT's FIRST real test of the v1.0.7 rail-cover fix, on a real
# warped ramp/pier structure, came back "0 Errors, 120 Warnings: Rebar is
# placed completely outside of its host" for EVERY bar created. Root
# cause: cover was pushed "inward" by picking a SIDE FACE and projecting
# the point onto it (Face.Project) to read that face's local normal --
# fragile on a twisted/warped host, where the picked face may not extend
# cleanly under every point being offset, and a failed projection's old
# nudge-toward-an-anchor fallback could land on a wildly wrong part of
# that face (or effectively nowhere useful) instead of failing safely.
# NBT's fix, applied to BOTH layout modes: drop side-face picking
# ENTIRELY -- Step 2 (side faces) no longer exists in either mode. Cover
# "inward" is now computed purely from the 4 corner points (A, b, c, d)
# already picked in Step 1 plus the TOP face's own local normal (the one
# face read that HAS been reliable since v1.0.6) -- see
# logic.edge_inward_offset. Rail-to-rail mode now needs nothing beyond
# Step 1 before Create Rebar; Parallel mode needs Step 1 + the direction
# line pick (renumbered from "Step 3" to "Step 2", since there's no longer
# a Step 2 side-face pick in between). Also: the corner-crosshair markers
# are no longer auto-deleted right after a successful Create Rebar (only
# on Close or restarting Step 1) -- NBT was troubleshooting a bad result
# and the markers had already vanished by the time he looked, which he
# read (reasonably) as a separate bug.

# v1.1.1 -- NBT tried "parallel" Layout Mode and asked for 2
# simplifications:
#   1) Step 3 (direction) is now a Revit reference LINE NBT draws himself
#      first (Model Line / Detail Line, Revit's own Line tool -- full
#      snap/dimension precision, in a plan view), then picks here --
#      replacing the old "click 2 approximate points on the 3D face"
#      approach.
#   2) Step 2 only needs 2 side face picks in Parallel mode (not 4) --
#      applied to EVERY bar's own 2 endpoints, same 2 faces every time
#      (see logic.build_rebar_segment_batch_parallel). The 2 END faces
#      (A-b, c-d) are dropped -- a new "Edge Distance" number replaces
#      them for positioning the first/last row.

# v1.1.0 -- NBT asked for a 2nd LAYOUT MODE, picked per run via the new
# LAYOUT MODE combo:
#   "rail"     -- the original v1.0.x behaviour, unchanged. Every bar spans
#                 rail 1 (A->c) to rail 2 (b->d), evenly distributed along
#                 the rail's real arc length. Every bar always touches
#                 both rails (never short), but bars are NOT evenly spaced
#                 from each other in true perpendicular distance when the
#                 region tapers.
#   "parallel" -- new. NBT picks a direction (Step 3, 2 points anywhere on
#                 the top face); every bar is forced parallel to that
#                 direction and spaced at a TRUE perpendicular distance
#                 (the real code-required spacing), each one clipped
#                 wherever it runs outside the picked top face's own
#                 boundary. A bar clipped down to less than "Min Bar
#                 Length" is dropped instead of creating an unusable stub.
# See logic.build_rebar_segment_batch_parallel for the geometry.

# v1.0.7 -- NBT's live testing on a real curved wall found 2 more bugs:
#   1) Only the FIRST/LAST bar ever got a side-face cover offset. But
#      EVERY bar's 2 endpoints ride along rail A->c / rail b->d (see
#      compute_bar_positions), and those 2 rails ARE the host's real side
#      edges -- so every bar needs cover pushed in from a side face along
#      its own rail too, not just the 2 boundary bars. Step 2 now picks 4
#      side faces instead of 2: the 2 existing END faces (A-b end, c-d
#      end -- unchanged, first/last bar only) PLUS 2 new RAIL faces (along
#      A->c, along b->d -- applied to every single bar).
#   2) The Cover value NBT enters was being used as-is as the push-in
#      distance, which places the bar's CENTERLINE that distance from the
#      face -- but concrete cover is measured to the bar's OUTER surface.
#      logic.build_rebar_segment_batch now adds the selected Bar Type's
#      own radius on top of the Cover value automatically (see
#      logic.get_bar_radius_ft).

# v1.0.1/v1.0.2/v1.0.3 history -- corner picking used to go through
# PickPoint (a raw click in empty space), which only shows Revit's little
# snap glyph when the cursor happens to land on a snap-type target
# (endpoint, adaptive point, etc.) -- a plain curved wall's top face has
# NONE of those, so NBT saw no visual feedback at all while aiming, and
# after v1.0.3 switched to nearest-corner matching there was still nothing
# ON SCREEN while hovering to show what would be picked ("khong thay duoc
# hut vao vat the, rat kho").
#
# v1.0.5 -- replaced PickPoint entirely with PickObject(Edge, ...). Picking
# an EDGE (rather than a bare point) gets Revit's own strong pre-highlight:
# the whole edge lights up in the selection color as the cursor approaches
# it, which is a far more visible "hut vao vat the" cue than any point-snap
# glyph, and it works identically whether the host has Adaptive Points or
# not. NBT now clicks the short end-edge nearest the corner he means; we
# read exactly where he clicked via Reference.GlobalPoint (same confirmed
# property already used for the top-face pick, see face_ref.GlobalPoint
# below) and feed it into the same pop_nearest_corner matching used since
# v1.0.3. A per-pick ISelectionFilter (_SameElementFilter) restricts the
# highlight/pick to the SAME host element the top face was picked from, so
# Revit itself refuses to highlight some other nearby element's edge.

# v1.0.4 -- a plain curved wall (or any host with no Adaptive Points /
# Reference Points) gives NBT no visual snap glyph to aim at while picking,
# and once picked there was nothing on screen confirming which corner the
# tool actually locked onto (NBT: "khong biet la diem nao, khong sang len
# rat kho de biet"). Each of the 4 corner picks now drops a small colored
# crosshair (a temporary DirectShape, cleaned up automatically) right on
# the locked corner so it visibly "lights up" -- a distinct color per
# label so A/b/c/d are never confused with each other.
MARKER_COLORS = {
    "A": (230, 30, 30),    # red
    "b": (30, 160, 60),    # green
    "c": (30, 110, 230),   # blue
    "d": (240, 140, 20),   # orange
}
MARKER_HALF_SIZE_FT = logic.mm_to_ft(150.0)


def _brush(color):
    return brush(color)


def _row(height=None):
    r = RowDefinition()
    if height is not None:
        r.Height = height
    return r


def _col(width=None):
    c = ColumnDefinition()
    if width is not None:
        c.Width = width
    return c


def _text(txt, size=13, color=CLR_TEXT, bold=False, wrap=True):
    tb = TextBlock()
    tb.Text = txt
    tb.FontSize = size
    tb.Foreground = _brush(color)
    if bold:
        tb.FontWeight = FontWeights.Bold
    if wrap:
        tb.TextWrapping = TextWrapping.Wrap
    return tb


def _btn(text, bg, fg, height=34):
    b = Button()
    b.Content = text
    b.Background = _brush(bg)
    b.Foreground = _brush(fg)
    b.Height = height
    b.Margin = Thickness(0, 6, 0, 0)
    b.BorderThickness = Thickness(0)
    b.FontWeight = FontWeights.Bold
    b.Cursor = System.Windows.Input.Cursors.Hand
    return b


def _labeled_input(label_text, default_text=""):
    """Returns (stack_panel, textbox)."""
    panel = StackPanel()
    panel.Margin = Thickness(0, 10, 0, 0)
    panel.Children.Add(_text(label_text, size=12, color=CLR_MUTED, bold=True))
    box = TextBox()
    box.FontSize = 14
    box.Padding = Thickness(6)
    box.Margin = Thickness(0, 4, 0, 0)
    box.Text = default_text
    panel.Children.Add(box)
    return panel, box


def _help_icon(tooltip_text, diameter=18):
    border = Border()
    border.Width = diameter
    border.Height = diameter
    border.CornerRadius = System.Windows.CornerRadius(diameter / 2.0)
    border.Background = _brush(CLR_HEADER_SUB)
    border.Cursor = System.Windows.Input.Cursors.Help

    mark = TextBlock()
    mark.Text = "?"
    mark.FontSize = 11
    mark.FontWeight = FontWeights.Bold
    mark.Foreground = _brush(CLR_HEADER)
    mark.HorizontalAlignment = HorizontalAlignment.Center
    mark.VerticalAlignment = VerticalAlignment.Center
    border.Child = mark

    tip_content = _text(tooltip_text, size=12, color=CLR_TEXT, wrap=True)
    tip_content.MaxWidth = 300
    tip = ToolTip()
    tip.Content = tip_content
    border.ToolTip = tip
    ToolTipService.SetInitialShowDelay(border, 300)
    ToolTipService.SetShowDuration(border, 30000)
    return border


# ---------------------------------------------------------------------------
# Generic ExternalEvent wrapper -- every Revit API call made after the
# window is shown (modeless) must run through this. See
# "pynbt-modeless-tool-pattern.md".
# ---------------------------------------------------------------------------

class _GenericHandler(IExternalEventHandler):
    def __init__(self):
        self.action = None

    def Execute(self, uiapp):
        if self.action is None:
            return
        action = self.action
        self.action = None
        try:
            action()
        except Exception as ex:
            try:
                MessageBox.Show(
                    "Unexpected error: {}".format(str(ex)), TOOL_TITLE,
                    MessageBoxButton.OK, MessageBoxImage.Error,
                )
            except Exception:
                pass

    def GetName(self):
        return "pyNBT - Rebar Segment - generic handler"


_handler = _GenericHandler()
_event = ExternalEvent.Create(_handler)


def run_on_revit(action):
    _handler.action = action
    _event.Raise()


class _SameElementFilter(ISelectionFilter):
    """Restricts an Edge/Face pick to ONE host element's own geometry --
    used while picking the 4 corner edges (v1.0.5) so Revit itself refuses
    to pre-highlight or accept an edge belonging to some other nearby
    element, keeping every corner pick on the same top face NBT already
    picked in Step 1."""

    def __init__(self, element_id):
        self._element_id = element_id

    def AllowElement(self, element):
        try:
            return logic.eid_int(element.Id) == logic.eid_int(self._element_id)
        except Exception:
            return False

    def AllowReference(self, reference, position):
        return True


class _LineElementFilter(ISelectionFilter):
    """v1.1.1 -- restricts Step 3's direction pick (Parallel Layout Mode
    only) to an element with a straight LocationCurve -- a Model Line or
    Detail Line NBT draws himself first with Revit's own Line tool (full
    snap/dimension precision, in a plan view), instead of clicking 2
    approximate points on the (possibly curved) 3D top face."""

    def AllowElement(self, element):
        try:
            loc = element.Location
            return (
                loc is not None
                and isinstance(loc, LocationCurve)
                and isinstance(loc.Curve, Line)
            )
        except Exception:
            return False

    def AllowReference(self, reference, position):
        return True


class RebarSegmentWindow(Window):
    def __init__(self):
        self.top_face = None
        self.top_host = None
        self.pt_a = None
        self.pt_b = None
        self.pt_c = None
        self.pt_d = None
        self.dir_p1 = None
        self.dir_p2 = None
        self._corner_markers = []

        self._build_ui()

        self.Title = "pyNBT - {}".format(TOOL_TITLE)
        self.Width = 460
        self.Height = 700
        self.MinWidth = 420
        self.MinHeight = 560
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.ResizeMode = ResizeMode.CanResize
        self.Background = _brush(CLR_BG)

        self._set_status("Ready.", CLR_MUTED)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = Grid()
        root.RowDefinitions.Add(_row(GridLength.Auto))
        root.RowDefinitions.Add(_row(GridLength(1, GridUnitType.Star)))
        root.RowDefinitions.Add(_row(GridLength.Auto))

        header = self._build_header()
        Grid.SetRow(header, 0)
        root.Children.Add(header)

        content = self._build_content()
        Grid.SetRow(content, 1)
        root.Children.Add(content)

        footer = self._build_footer()
        Grid.SetRow(footer, 2)
        root.Children.Add(footer)

        self.Content = root

    def _build_header(self):
        border = Border()
        border.Background = _brush(CLR_HEADER)
        border.Padding = Thickness(18, 14, 18, 14)

        grid = Grid()
        grid.ColumnDefinitions.Add(_col(GridLength(1, GridUnitType.Star)))
        grid.ColumnDefinitions.Add(_col(GridLength.Auto))

        left = StackPanel()
        left.Orientation = System.Windows.Controls.Orientation.Horizontal
        left.VerticalAlignment = VerticalAlignment.Center
        title = _text(TOOL_TITLE, size=18, color=CLR_HEADER_TEXT, bold=True, wrap=False)
        left.Children.Add(title)
        help_icon = _help_icon(
            "1) Pick Bar Type / Spacing / Cover.\n"
            "2) 'Pick Top Face & 4 Points': pick the host's top face, then "
            "click the SHORT edge nearest each of its 4 corners, in order "
            "A -> b -> c -> d (A-b = first bar, c-d = last bar). The edge "
            "highlights as you hover near it -- click ROUGHLY near the "
            "corner, the tool snaps to that corner's real vertex for you, "
            "and drops a colored crosshair there so you can see it locked "
            "(A=red, b=green, c=blue, d=orange) -- the crosshairs stay on "
            "screen through Create Rebar, until you restart Step 1 or "
            "click Close.\n"
            "3) Choose LAYOUT MODE: 'Rail-to-rail' (original -- every bar "
            "spans rail A->c to rail b->d) needs nothing more, go "
            "straight to Create Rebar. 'Parallel (clip at boundary)' "
            "needs Step 2: draw a Model/Detail Line first for direction, "
            "then pick it. Every bar runs parallel to that line at a "
            "TRUE perpendicular spacing and gets clipped at the top "
            "face's boundary; 'Edge Distance' sets back the first/last "
            "row from the A-b / c-d ends; a bar clipped shorter than Min "
            "Bar Length is skipped.\n"
            "4) 'Create Rebar' -- Cover is measured to the bar's OUTER "
            "surface (the tool adds the Bar Type's own radius on top of "
            "the Cover value automatically) and pushed in from BOTH the "
            "top face and whichever of the quad's 4 edges (A-b, b-d, "
            "d-c, c-A) each bar endpoint sits on/near -- computed "
            "straight from the 4 picked corner points, no side face pick "
            "needed."
        )
        help_icon.Margin = Thickness(8, 3, 0, 0)
        left.Children.Add(help_icon)
        Grid.SetColumn(left, 0)
        grid.Children.Add(left)

        badge = _text(TOOL_VERSION, size=11, color=CLR_HEADER_SUB, wrap=False)
        badge.VerticalAlignment = VerticalAlignment.Bottom
        Grid.SetColumn(badge, 1)
        grid.Children.Add(badge)

        border.Child = grid
        return border

    def _card(self):
        b = Border()
        b.Background = _brush(CLR_CARD)
        b.BorderBrush = _brush(CLR_BORDER)
        b.BorderThickness = Thickness(1)
        b.CornerRadius = System.Windows.CornerRadius(6)
        b.Padding = Thickness(14)
        return b

    def _build_content(self):
        card = self._card()
        card.Margin = Thickness(16)
        panel = StackPanel()

        panel.Children.Add(_text("PARAMETERS", size=11, color=CLR_MUTED, bold=True))

        self.cmb_bar_type = ComboBox()
        self.cmb_bar_type.FontSize = 13
        self.cmb_bar_type.Padding = Thickness(6)
        self.cmb_bar_type.Margin = Thickness(0, 6, 0, 0)
        for bt in logic.get_bar_types(doc):
            item = ComboBoxItem()
            item.Content = logic.get_element_name(bt)
            item.Tag = bt
            self.cmb_bar_type.Items.Add(item)
        if self.cmb_bar_type.Items.Count > 0:
            self.cmb_bar_type.SelectedIndex = 0
        bar_type_panel = StackPanel()
        bar_type_panel.Children.Add(_text("BAR TYPE", size=12, color=CLR_MUTED, bold=True))
        bar_type_panel.Children.Add(self.cmb_bar_type)
        panel.Children.Add(bar_type_panel)

        spacing_row, self.tb_spacing = _labeled_input("SPACING (mm)", "200")
        panel.Children.Add(spacing_row)

        cover_row, self.tb_cover = _labeled_input("COVER (mm)", "40")
        panel.Children.Add(cover_row)

        # v1.1.0 -- Layout Mode: "rail" (original) vs "parallel" (new).
        self.cmb_layout_mode = ComboBox()
        self.cmb_layout_mode.FontSize = 13
        self.cmb_layout_mode.Padding = Thickness(6)
        self.cmb_layout_mode.Margin = Thickness(0, 6, 0, 0)
        item_rail = ComboBoxItem()
        item_rail.Content = "Rail-to-rail (bam theo 2 canh rail)"
        item_rail.Tag = "rail"
        self.cmb_layout_mode.Items.Add(item_rail)
        item_parallel = ComboBoxItem()
        item_parallel.Content = "Parallel (song song, cat theo bien)"
        item_parallel.Tag = "parallel"
        self.cmb_layout_mode.Items.Add(item_parallel)
        self.cmb_layout_mode.SelectedIndex = 0
        self.cmb_layout_mode.SelectionChanged += self.on_layout_mode_changed
        layout_mode_panel = StackPanel()
        layout_mode_panel.Margin = Thickness(0, 10, 0, 0)
        layout_mode_panel.Children.Add(_text("LAYOUT MODE", size=12, color=CLR_MUTED, bold=True))
        layout_mode_panel.Children.Add(self.cmb_layout_mode)
        panel.Children.Add(layout_mode_panel)

        min_len_row, self.tb_min_len = _labeled_input("MIN BAR LENGTH (mm) -- Parallel mode only", "50")
        panel.Children.Add(min_len_row)

        edge_setback_row, self.tb_edge_setback = _labeled_input(
            "EDGE DISTANCE FROM A-b / c-d (mm) -- Parallel mode only", "0"
        )
        panel.Children.Add(edge_setback_row)

        panel.Children.Add(self._separator_labeled("STEP 1 - TOP FACE + 4 POINTS"))
        self.tb_pick1_status = _text(
            "Not picked yet.", size=12, color=CLR_MUTED,
        )
        self.tb_pick1_status.Margin = Thickness(0, 6, 0, 0)
        panel.Children.Add(self.tb_pick1_status)

        btn_pick1 = _btn("Pick Top Face & 4 Points (A -> b -> c -> d)", CLR_ACCENT, CLR_HEADER_TEXT)
        btn_pick1.Click += self.on_pick1_click
        panel.Children.Add(btn_pick1)

        # v1.2.0 -- Step 2 only matters in "parallel" Layout Mode; hidden
        # (Visibility.Collapsed) whenever "rail" mode is selected, see
        # on_layout_mode_changed. (There is no side-face pick step any
        # more in either mode -- see logic.edge_inward_offset.)
        self.step3_panel = StackPanel()
        self.step3_panel.Children.Add(self._separator_labeled("STEP 2 - DIRECTION LINE (PARALLEL MODE ONLY)"))
        step3_hint = _text(
            "Draw a Model Line or Detail Line first (Revit's own Line "
            "tool, in a plan view) along the direction you want the bars "
            "to run, then pick it below.",
            size=11, color=CLR_MUTED,
        )
        self.step3_panel.Children.Add(step3_hint)
        self.tb_pick3_status = _text(
            "Not picked yet.", size=12, color=CLR_MUTED,
        )
        self.tb_pick3_status.Margin = Thickness(0, 6, 0, 0)
        self.step3_panel.Children.Add(self.tb_pick3_status)

        btn_pick3 = _btn("Pick Direction Line", CLR_ACCENT, CLR_HEADER_TEXT)
        btn_pick3.Click += self.on_pick3_click
        self.step3_panel.Children.Add(btn_pick3)

        self.step3_panel.Visibility = System.Windows.Visibility.Collapsed
        panel.Children.Add(self.step3_panel)

        scroll = ScrollViewer()
        scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        scroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
        scroll.Content = panel
        card.Child = scroll
        return card

    def _separator_labeled(self, label_text):
        wrap = StackPanel()
        wrap.Children.Add(self._separator())
        lbl = _text(label_text, size=11, color=CLR_MUTED, bold=True)
        lbl.Margin = Thickness(0, 8, 0, 0)
        wrap.Children.Add(lbl)
        return wrap

    def _separator(self):
        sep = Border()
        sep.Height = 1
        sep.Background = _brush(CLR_BORDER)
        sep.Margin = Thickness(0, 16, 0, 0)
        return sep

    def _build_footer(self):
        border = Border()
        border.Background = _brush(CLR_FOOTER)
        border.Padding = Thickness(16, 10, 16, 10)
        border.BorderBrush = _brush(CLR_BORDER)
        border.BorderThickness = Thickness(0, 1, 0, 0)

        outer = StackPanel()

        self.tb_status = _text("Ready.", size=11, color=CLR_MUTED, wrap=True)
        outer.Children.Add(self.tb_status)

        row = Grid()
        row.Margin = Thickness(0, 8, 0, 0)
        row.ColumnDefinitions.Add(_col(GridLength(1, GridUnitType.Star)))
        row.ColumnDefinitions.Add(_col(GridLength.Auto))
        row.ColumnDefinitions.Add(_col(GridLength.Auto))

        sig = _text(
            "{} {} | {}".format(TOOL_TITLE, TOOL_VERSION, doc.Title),
            size=10, color=CLR_MUTED, wrap=False,
        )
        sig.VerticalAlignment = VerticalAlignment.Center
        Grid.SetColumn(sig, 0)
        row.Children.Add(sig)

        btn_close = _btn("Close", CLR_CARD, CLR_ACCENT, height=32)
        btn_close.Width = 90
        btn_close.BorderThickness = Thickness(1)
        btn_close.BorderBrush = _brush(CLR_BORDER)
        btn_close.Margin = Thickness(0, 0, 8, 0)
        btn_close.Click += self.on_close_click
        Grid.SetColumn(btn_close, 1)
        row.Children.Add(btn_close)

        btn_create = _btn("Create Rebar", CLR_APPLY, CLR_APPLY_TEXT, height=32)
        btn_create.Width = 120
        btn_create.Margin = Thickness(0)
        btn_create.Click += self.on_create_click
        Grid.SetColumn(btn_create, 2)
        row.Children.Add(btn_create)
        self.btn_create = btn_create

        outer.Children.Add(row)
        border.Child = outer
        return border

    # ------------------------------------------------------------------
    # Small UI helpers
    # ------------------------------------------------------------------
    def _set_status(self, text, color=CLR_MUTED):
        self.tb_status.Text = text
        self.tb_status.Foreground = _brush(color)

    def _bring_to_front(self):
        """Re-focus this modeless window over the Revit main window --
        needed after any interactive pick (PickObject/PickPoint) sends
        focus to Revit's canvas."""
        try:
            self.Topmost = True
            self.Topmost = False
            self.Activate()
        except Exception:
            pass

    def _parse_positive_float(self, text, field_name):
        try:
            value = float(text.strip().replace(",", "."))
        except Exception:
            raise ValueError("{} must be a number.".format(field_name))
        if value <= 0:
            raise ValueError("{} must be greater than 0.".format(field_name))
        return value

    def _parse_nonnegative_float(self, text, field_name):
        """Like _parse_positive_float but allows 0 -- used for Edge
        Distance (v1.1.1), where 0 is a normal, common choice (no setback
        at all, first/last row right at the A-b / c-d edge)."""
        try:
            value = float(text.strip().replace(",", "."))
        except Exception:
            raise ValueError("{} must be a number.".format(field_name))
        if value < 0:
            raise ValueError("{} must be 0 or greater.".format(field_name))
        return value

    def on_close_click(self, sender, args):
        run_on_revit(self.do_close)

    def do_close(self):
        try:
            self._clear_corner_markers()
        except Exception:
            pass
        self.Close()

    # ------------------------------------------------------------------
    # Button handlers -- just hand off to run_on_revit (ExternalEvent)
    # ------------------------------------------------------------------
    def on_pick1_click(self, sender, args):
        run_on_revit(self.do_pick_geometry)

    def on_pick3_click(self, sender, args):
        run_on_revit(self.do_pick_direction)

    def on_layout_mode_changed(self, sender, args):
        """Step 2 (Pick Direction Line) only matters in "parallel" Layout
        Mode; show/hide it so "rail" mode (unchanged default behaviour)
        doesn't present an irrelevant extra step. v1.2.0 -- there is no
        longer a side-face pick step in either mode (see
        logic.edge_inward_offset), so nothing else needs resetting here
        any more."""
        item = self.cmb_layout_mode.SelectedItem
        mode = item.Tag if item is not None else "rail"
        self.step3_panel.Visibility = (
            System.Windows.Visibility.Visible if mode == "parallel"
            else System.Windows.Visibility.Collapsed
        )

    def on_create_click(self, sender, args):
        run_on_revit(self.do_create)

    # ------------------------------------------------------------------
    # Actions run inside Revit's API context (via ExternalEvent)
    # ------------------------------------------------------------------
    def _clear_corner_markers(self):
        """Delete every temporary corner-crosshair marker created so far
        (see MARKER_COLORS). Safe to call even when none exist."""
        if not self._corner_markers:
            return
        ids = self._corner_markers
        self._corner_markers = []
        t = Transaction(doc, "pyNBT - Rebar Segment - clear markers")
        t.Start()
        try:
            logic.delete_elements_safe(doc, ids)
            t.Commit()
        except Exception:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()

    def _add_corner_marker(self, point, label):
        """Drop a small colored crosshair at `point` so NBT can SEE which
        corner the tool just locked onto for `label` (A/b/c/d). Never lets
        a marker failure abort the pick flow -- worst case, NBT just
        doesn't get the visual confirmation for that one point."""
        color = MARKER_COLORS.get(label, (255, 255, 255))
        t = Transaction(doc, "pyNBT - Rebar Segment - corner marker")
        t.Start()
        try:
            marker_id = logic.create_corner_marker(doc, point, MARKER_HALF_SIZE_FT, color)
            t.Commit()
            self._corner_markers.append(marker_id)
        except Exception:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()

    def do_pick_geometry(self):
        try:
            self._do_pick_geometry_inner()
        finally:
            self._bring_to_front()

    def _do_pick_geometry_inner(self):
        try:
            face_ref = uidoc.Selection.PickObject(
                ObjectType.Face, "Pick the TOP face to spread rebar on (host face)"
            )
        except OperationCanceledException:
            self._set_status("Pick cancelled.", CLR_MUTED)
            return
        except Exception as ex:
            self._set_status("Pick top face failed: {}".format(str(ex)), CLR_ERROR)
            return

        host_el = doc.GetElement(face_ref.ElementId)
        try:
            face = host_el.GetGeometryObjectFromReference(face_ref)
        except Exception as ex:
            self._set_status("Could not read the picked face: {}".format(str(ex)), CLR_ERROR)
            return

        # v1.0.4 -- clear any markers left over from a previous attempt
        # (e.g. NBT re-clicked "Pick Top Face & 4 Points" to restart)
        # before dropping new ones for this attempt.
        self._clear_corner_markers()

        # v1.0.2 -- the exact point Revit registered when picking the face
        # itself (guaranteed to already be ON the face). Used as the
        # "nudge toward" anchor when one of the 4 corner points sits right
        # at the face's trim boundary (see project_point_to_face_tolerant).
        face_anchor = face_ref.GlobalPoint

        # v1.0.3 -- read the face's own 4 corner vertices straight from its
        # boundary geometry (see get_face_corner_vertices). When the face is
        # a simple 4-edge quad (the normal shape of a ramp/wall top face),
        # each of the 4 picks below snaps onto one of these REAL vertices
        # instead of depending on Face.Project accepting a manually-clicked
        # point -- this is what actually fixes faces with no snappable
        # point elements at all (e.g. a plain curved wall's top face, which
        # has no Adaptive Points to snap to). NBT only needs to click
        # roughly near the corner he means. Falls back to the old
        # tolerant-projection approach when the face isn't a clean quad.
        remaining_corners = logic.get_face_corner_vertices(face)

        # v1.0.5 -- restrict the edge picks below to this SAME host element,
        # so Revit itself refuses to pre-highlight/pick an edge on any other
        # nearby element.
        same_el_filter = _SameElementFilter(host_el.Id)

        points = []
        labels = ["A", "b", "c", "d"]
        for label in labels:
            try:
                edge_ref = uidoc.Selection.PickObject(
                    ObjectType.Edge, same_el_filter,
                    "Click the SHORT edge near corner '{}' -- it will "
                    "highlight as you hover over it".format(label),
                )
            except OperationCanceledException:
                self._set_status(
                    "Point pick cancelled at '{}'. Click 'Pick Top Face & "
                    "4 Points' again to restart from the top face.".format(label),
                    CLR_MUTED,
                )
                return
            except Exception as ex:
                self._set_status(
                    "Pick point '{}' failed: {}".format(label, str(ex)), CLR_ERROR
                )
                return

            # Reference.GlobalPoint -- the exact point Revit registered for
            # this edge pick (same confirmed property already used for
            # face_anchor above). Guaranteed to sit ON the edge, i.e. on the
            # face's own boundary.
            raw_pt = edge_ref.GlobalPoint
            if remaining_corners:
                snapped = logic.pop_nearest_corner(raw_pt, remaining_corners)
            else:
                snapped = logic.project_point_to_face_tolerant(face, raw_pt, face_anchor)
            if snapped is None:
                self._set_status(
                    "Point '{}' did not land on the picked top face -- click "
                    "closer to that face's surface, and restart from 'Pick "
                    "Top Face & 4 Points'.".format(label),
                    CLR_ERROR,
                )
                return
            points.append(snapped)

            # v1.0.4 -- visible confirmation right after THIS point locks,
            # not just a single summary once all 4 are done.
            self._add_corner_marker(snapped, label)
            self.tb_pick1_status.Text = "'{}' locked ({}/4).".format(label, len(points))
            self.tb_pick1_status.Foreground = _brush(CLR_MUTED)

        self.top_face = face
        self.top_host = host_el
        self.pt_a, self.pt_b, self.pt_c, self.pt_d = points
        self.tb_pick1_status.Text = "OK - top face + A, b, c, d picked."
        self.tb_pick1_status.Foreground = _brush(CLR_SUCCESS)
        mode_item = self.cmb_layout_mode.SelectedItem
        layout_mode = mode_item.Tag if mode_item is not None else "rail"
        if layout_mode == "parallel":
            next_step_msg = "Now pick the Direction Line (Step 2)."
        else:
            next_step_msg = "Click 'Create Rebar' when ready."
        self._set_status(
            "Top face + 4 points picked (see the colored crosshairs: A=red, "
            "b=green, c=blue, d=orange). {}".format(next_step_msg),
            CLR_MUTED,
        )

    def do_pick_direction(self):
        try:
            self._do_pick_direction_inner()
        finally:
            self._bring_to_front()

    def _do_pick_direction_inner(self):
        """v1.1.1 -- "parallel" Layout Mode only. NBT draws a reference
        line himself FIRST (a Model Line or Detail Line, using Revit's own
        Line tool in a plan view -- full snap/dimension precision), then
        picks that line here to set the direction every bar will run
        parallel to. Replaces the old v1.1.0 approach of clicking 2
        approximate points on the (possibly curved) 3D top face."""
        if self.top_face is None:
            self._set_status(
                "Pick the top face + 4 points first (Step 1).", CLR_ERROR
            )
            return

        try:
            ref = uidoc.Selection.PickObject(
                ObjectType.Element, _LineElementFilter(),
                "Pick the reference line for bar direction (draw a Model "
                "Line or Detail Line first if you haven't)"
            )
        except OperationCanceledException:
            self._set_status("Direction line pick cancelled.", CLR_MUTED)
            return
        except Exception as ex:
            self._set_status("Pick direction line failed: {}".format(str(ex)), CLR_ERROR)
            return

        try:
            line_el = doc.GetElement(ref.ElementId)
            curve = line_el.Location.Curve
            self.dir_p1 = curve.GetEndPoint(0)
            self.dir_p2 = curve.GetEndPoint(1)
        except Exception as ex:
            self._set_status("Could not read the picked line: {}".format(str(ex)), CLR_ERROR)
            return

        self.tb_pick3_status.Text = "OK - direction line picked."
        self.tb_pick3_status.Foreground = _brush(CLR_SUCCESS)
        self._set_status("Direction picked. Click 'Create Rebar' when ready.", CLR_MUTED)

    def do_create(self):
        try:
            self._do_create_inner()
        finally:
            self._bring_to_front()

    def _do_create_inner(self):
        if self.top_face is None:
            self._set_status("Pick the top face + 4 points first (Step 1).", CLR_ERROR)
            return

        bar_type_item = self.cmb_bar_type.SelectedItem
        if bar_type_item is None:
            self._set_status("Select a Bar Type.", CLR_ERROR)
            return
        bar_type = bar_type_item.Tag

        mode_item = self.cmb_layout_mode.SelectedItem
        layout_mode = mode_item.Tag if mode_item is not None else "rail"

        # v1.2.0 -- no side faces to validate any more in either mode; the
        # only extra requirement for "parallel" mode is the direction line
        # (checked further below, same as before).

        try:
            spacing_mm = self._parse_positive_float(self.tb_spacing.Text, "Spacing")
            cover_mm = self._parse_positive_float(self.tb_cover.Text, "Cover")
            min_len_ft = None
            edge_setback_ft = 0.0
            if layout_mode == "parallel":
                min_len_mm = self._parse_positive_float(self.tb_min_len.Text, "Min Bar Length")
                min_len_ft = logic.mm_to_ft(min_len_mm)
                edge_setback_mm = self._parse_nonnegative_float(self.tb_edge_setback.Text, "Edge Distance")
                edge_setback_ft = logic.mm_to_ft(edge_setback_mm)
        except ValueError as ve:
            self._set_status(str(ve), CLR_ERROR)
            return

        spacing_ft = logic.mm_to_ft(spacing_mm)
        cover_ft = logic.mm_to_ft(cover_mm)

        if layout_mode == "parallel" and (self.dir_p1 is None or self.dir_p2 is None):
            self._set_status(
                "Pick the Direction Line first (Step 3) -- required in Parallel mode.",
                CLR_ERROR,
            )
            return

        skipped_short = 0

        t = Transaction(doc, "pyNBT - Rebar Segment")
        t.Start()
        try:
            if layout_mode == "parallel":
                created, partition_value, errors, skipped_short = logic.build_rebar_segment_batch_parallel(
                    doc, self.top_face,
                    self.top_host,
                    self.pt_a, self.pt_b, self.pt_c, self.pt_d,
                    self.dir_p1, self.dir_p2,
                    bar_type, spacing_ft, cover_ft, min_len_ft, edge_setback_ft,
                )
            else:
                created, partition_value, errors = logic.build_rebar_segment_batch(
                    doc, self.top_face,
                    self.top_host,
                    self.pt_a, self.pt_b, self.pt_c, self.pt_d,
                    bar_type, spacing_ft, cover_ft,
                )
            t.Commit()
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            self._set_status("Create failed: {}".format(str(ex)), CLR_ERROR)
            return

        skipped_note = " {} row(s) skipped (too short).".format(skipped_short) if skipped_short else ""
        if errors:
            msg = "Created {} bar(s), Partition = {}.{} {} position(s) failed:\n{}".format(
                len(created), partition_value, skipped_note, len(errors),
                "\n".join("  #{}: {}".format(i, m) for i, m in errors),
            )
            self._set_status(msg, CLR_ERROR if not created else CLR_MUTED)
        else:
            self._set_status(
                "Created {} bar(s). Partition = {}.{}".format(len(created), partition_value, skipped_note),
                CLR_SUCCESS,
            )

        # v1.2.0 -- no longer auto-cleared here even on success: NBT was
        # troubleshooting a run with bad results (bars placed outside the
        # host) and the corner crosshairs had already been deleted by the
        # time he looked, since that run raised no Python exceptions (the
        # bad placement only showed up as a Revit warning dialog after
        # Create, not an `errors` entry). The markers now stay up through
        # Create Rebar regardless of outcome; they're still cleared at the
        # start of the next Step 1 pick (see _do_pick_geometry_inner) or
        # by clicking Close.


RebarSegmentWindow().Show()
