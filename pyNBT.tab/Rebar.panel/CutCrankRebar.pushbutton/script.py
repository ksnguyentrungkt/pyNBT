# -*- coding: utf-8 -*-
"""Crank Rebar (formerly "Cut & Crank Rebar" -- shortened in v1.6)

Select a single straight Rebar, click a position on it (projected onto the
original centerline) to mark **P2** -- the end of the crank/diagonal and the
start of the lap segment -- and the tool splits the bar into 2 bars:

  Bar A: original start (keeps the ORIGINAL start hook, if any) -> straight,
         unchanged, on the original elevation, up to P1 (computed by going
         back N x diameter from the clicked P2) -> crank up/down by exactly
         1x the bar diameter via a diagonal (P1 -> P2) -> a straight lap
         segment (length entered in mm), ending at a free/connecting end.
  Bar B: starts at the SAME axial position as P2 but on the ORIGINAL
         elevation (not the shifted one) -- so Bar B's straight run overlaps
         Bar A's lap segment axially, offset by exactly 1x diameter: the
         correct cranked-bar lap-splice detail -> straight -> ends at the
         point that makes Bar A's straight+diagonal run + Bar B's straight
         run equal the ORIGINAL bar's length -> keeps the ORIGINAL end hook,
         if any.

v1.4 design confirmed directly with NBT (see project doc
"cut-crank-rebar-tool.md", section "v1.3 -> v1.4" -- corrects two v1.3
mistakes: (1) the click now marks P2, not P1; (2) Bar B runs on the
original elevation, not the shifted one).

pyNBT.tab / Rebar.panel / CutCrankRebar.pushbutton
"""

__title__ = "Crank\nRebar"
__author__ = "pyNBT"
__doc__ = (
    "Cut a straight rebar at a picked point and crank the continuing "
    "segment up/down by 1x diameter, with a diagonal (N x D) transition "
    "and a manual lap-splice length."
)

# Mandatory for every pyNBT tool that uses a modeless window: without this,
# pyRevit recycles the IronPython engine right after Show() returns, and every
# module-level import (forms, script, System.Windows.*, ...) disappears the
# moment a button handler or ExternalEvent callback runs later.
# See project doc "pynbt-modeless-tool-pattern.md".
__persistentengine__ = True

# --- Startup timing diagnostic (temporary, v1.4.2) ---------------------------
# NBT still sees ~3s of "spinning" on launch even after removing the heavy
# pyrevit.forms import in v1.4.1. `time` is a built-in C module (effectively
# free to import), so timing from HERE (the very first line pyRevit executes
# in this script) to the moment the window is fully built isolates whether
# the 3s is spent INSIDE this script (imports, UI construction) or OUTSIDE it
# (pyRevit/Revit's own engine boot before/after running the script -- not
# something fixable from in here). The measured number is shown in the
# window's own status bar on open (see CutCrankWindow.__init__) so NBT can
# read it directly without needing pyRevit's output console.
import time
_perf_t0 = time.time()

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
    ResizeMode, MessageBox, MessageBoxButton, MessageBoxImage, MessageBoxResult,
)
from System.Windows.Controls import (
    Grid, RowDefinition, ColumnDefinition, Border, StackPanel, TextBlock,
    TextBox, Button, RadioButton, Orientation, ComboBox, ScrollViewer,
    ScrollBarVisibility, ToolTip, ToolTipService, Canvas,
)
from System.Windows.Media import Color, SolidColorBrush, DoubleCollection
from System.Windows.Shapes import Line as WpfLine, Ellipse

from Autodesk.Revit.DB import XYZ, Line, Transaction
from Autodesk.Revit.DB.Structure import Rebar
from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent
from Autodesk.Revit.UI.Selection import ObjectSnapTypes, ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

# NOTE: deliberately NOT importing `pyrevit.forms` here -- it is the single
# biggest contributor to this tool's slow startup (several seconds): forms.py
# pulls in a large chunk of pyRevit's own WPF resource dictionaries/themes on
# first import. This tool only ever used it for one alert box, which native
# WPF's own System.Windows.MessageBox covers just as well. See project doc
# "cut-crank-rebar-tool.md", section "v1.4 -> v1.4.1 (startup speed)".

import rebar_crank_logic as logic

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

TOOL_TITLE = "Crank Rebar"
TOOL_VERSION = "v1.7.3"

# ---------------------------------------------------------------------------
# pyNBT theme (Navy + Gray/White/Black) -- see references/dqt-patterns.md
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
CLR_OK = Color.FromRgb(22, 163, 74)
CLR_WARN = Color.FromRgb(217, 119, 6)
CLR_ERROR = Color.FromRgb(220, 38, 38)
CLR_BAR_A = Color.FromRgb(30, 41, 59)     # navy -- v1.6 schematic preview
CLR_BAR_B = Color.FromRgb(100, 116, 139)  # slate gray -- v1.6 schematic preview


def _brush(color):
    return SolidColorBrush(color)


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


def _labeled_input(label_text):
    """Returns (stack_panel, textbox)."""
    panel = StackPanel()
    panel.Margin = Thickness(0, 10, 0, 0)
    panel.Children.Add(_text(label_text, size=12, color=CLR_MUTED, bold=True))
    box = TextBox()
    box.FontSize = 14
    box.Padding = Thickness(6)
    box.Margin = Thickness(0, 4, 0, 0)
    panel.Children.Add(box)
    return panel, box


def _mode_radio_pair(label_a, label_b, group_name, default_first=True):
    """v1.5 -- a small horizontal RadioButton pair used for the Lap/Crank
    input-mode toggles. Returns (row_panel, rb_a, rb_b); rb_a is the label
    shown FIRST (left-to-right). `default_first` picks which one starts
    checked -- True checks rb_a (most toggles), False checks rb_b (v1.6:
    used for Lap length, where "x D" is now listed first to match Crank
    slope's layout, but "mm" stays the default-checked mode)."""
    row = StackPanel()
    row.Orientation = Orientation.Horizontal
    row.Margin = Thickness(0, 4, 0, 0)
    rb_a = RadioButton()
    rb_a.Content = label_a
    rb_a.GroupName = group_name
    rb_a.IsChecked = default_first
    rb_a.Margin = Thickness(0, 0, 16, 0)
    rb_b = RadioButton()
    rb_b.Content = label_b
    rb_b.GroupName = group_name
    rb_b.IsChecked = not default_first
    row.Children.Add(rb_a)
    row.Children.Add(rb_b)
    return row, rb_a, rb_b


def _fmt_num(value):
    """v1.5 -- format a float for re-populating a TextBox from a saved
    preset: whole numbers show with no decimals, otherwise trim trailing
    zeros."""
    if value == int(value):
        return str(int(value))
    text = "{:.3f}".format(value).rstrip("0").rstrip(".")
    return text if text else "0"


def _help_icon(tooltip_text, diameter=18):
    """v1.6 -- a small circular "?" indicator with a 0.3s-delay hover
    tooltip, replacing the old always-visible header subtitle (usage
    description). Matches the pattern already used in other pyNBT tools
    (e.g. Renumber Rebar: "chu thich dai dung icon '?' hover 0.3s") for
    long captions, instead of permanently taking up header space."""
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
    tip_content.MaxWidth = 280
    tip = ToolTip()
    tip.Content = tip_content
    border.ToolTip = tip
    ToolTipService.SetInitialShowDelay(border, 300)
    ToolTipService.SetShowDuration(border, 30000)
    return border


# ---------------------------------------------------------------------------
# v1.6 -- small helpers for drawing the schematic preview diagram (Canvas).
# Replaces the old wall-of-text Preview panel: NBT felt it wasn't visual/
# intuitive enough to actually catch a geometry mistake at a glance (the
# same class of mistake that slipped through in v1.3 -> v1.4). This is a
# SCHEMATIC, not to scale vertically (the crank offset is exaggerated a
# fixed number of pixels so it's visible regardless of actual bar
# diameter) -- horizontal spacing IS proportional to actual mm along the
# bar's axis.
# ---------------------------------------------------------------------------

def _wpf_line(x1, y1, x2, y2, color, thickness=3, dashed=False):
    ln = WpfLine()
    ln.X1 = x1
    ln.Y1 = y1
    ln.X2 = x2
    ln.Y2 = y2
    ln.Stroke = _brush(color)
    ln.StrokeThickness = thickness
    if dashed:
        dashes = DoubleCollection()
        dashes.Add(4)
        dashes.Add(3)
        ln.StrokeDashArray = dashes
    return ln


def _wpf_dot(x, y, color, radius=3.5):
    e = Ellipse()
    e.Width = radius * 2
    e.Height = radius * 2
    e.Fill = _brush(color)
    Canvas.SetLeft(e, x - radius)
    Canvas.SetTop(e, y - radius)
    return e


def _canvas_text(text, x, y, color=CLR_TEXT, size=10, bold=False, center_x=False):
    tb = _text(text, size=size, color=color, bold=bold, wrap=False)
    # Measure so labels can be centered on their point/segment instead of
    # always hanging off to the right, which gets cramped at this scale.
    tb.Measure(System.Windows.Size(System.Double.PositiveInfinity, System.Double.PositiveInfinity))
    left = (x - tb.DesiredSize.Width / 2.0) if center_x else x
    Canvas.SetLeft(tb, left)
    Canvas.SetTop(tb, y)
    return tb


class RebarOnlyFilter(ISelectionFilter):
    """Restricts interactive PickObject to Rebar elements only."""

    def AllowElement(self, elem):
        return isinstance(elem, Rebar)

    def AllowReference(self, reference, position):
        return True


# ---------------------------------------------------------------------------
# Generic ExternalEvent wrapper -- every Revit API call made after the window
# is shown (modeless) must run through this, not directly from a button
# click handler. See "pynbt-modeless-tool-pattern.md".
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
        return "pyNBT - Crank Rebar - generic handler"


_handler = _GenericHandler()
_event = ExternalEvent.Create(_handler)


def run_on_revit(action):
    _handler.action = action
    _event.Raise()


# ---------------------------------------------------------------------------
# v1.5 -- lightweight "Save Setup As" name prompt. A custom modal Window
# instead of pyrevit.forms (heavy, removed in v1.4.1) or
# Microsoft.VisualBasic.Interaction.InputBox (a new assembly reference) --
# keeps startup light and dependency-free.
# ---------------------------------------------------------------------------

class _NamePromptDialog(Window):
    def __init__(self, owner, title, label, initial_text=""):
        self.result_text = None

        root = StackPanel()
        root.Margin = Thickness(16)
        root.Children.Add(_text(label, size=12, color=CLR_TEXT))

        self._input = TextBox()
        self._input.FontSize = 14
        self._input.Padding = Thickness(6)
        self._input.Margin = Thickness(0, 8, 0, 14)
        self._input.Text = initial_text
        root.Children.Add(self._input)

        btn_row = StackPanel()
        btn_row.Orientation = Orientation.Horizontal
        btn_row.HorizontalAlignment = HorizontalAlignment.Right

        btn_cancel = _btn("Cancel", CLR_CARD, CLR_ACCENT, height=30)
        btn_cancel.Width = 80
        btn_cancel.BorderThickness = Thickness(1)
        btn_cancel.BorderBrush = _brush(CLR_BORDER)
        btn_cancel.Margin = Thickness(0, 0, 8, 0)
        btn_cancel.Click += self._on_cancel
        btn_row.Children.Add(btn_cancel)

        btn_ok = _btn("OK", CLR_ACCENT, CLR_HEADER_TEXT, height=30)
        btn_ok.Width = 80
        btn_ok.Click += self._on_ok
        btn_row.Children.Add(btn_ok)

        root.Children.Add(btn_row)

        self.Content = root
        self.Title = title
        self.Width = 340
        self.SizeToContent = System.Windows.SizeToContent.Height
        self.WindowStartupLocation = WindowStartupLocation.CenterOwner
        self.Owner = owner
        self.ResizeMode = ResizeMode.NoResize
        self.Background = _brush(CLR_BG)

    def _on_ok(self, sender, args):
        self.result_text = self._input.Text
        self.DialogResult = True

    def _on_cancel(self, sender, args):
        self.DialogResult = False


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class CutCrankWindow(Window):
    def __init__(self):
        # State captured from the currently-selected bar
        self.rebar = None
        self.bar_type = None
        self.host = None
        self.line = None
        self.bar_dir = None
        self.diameter_ft = None

        # State captured from Pick Point
        self.pick_point = None
        self.dist_from_start_ft = None
        self.dist_from_end_ft = None
        self.total_len_ft = None
        # v1.7 -- the active view's RightDirection at the moment of picking,
        # used to resolve the Left/Right crank-side choice (see
        # logic.resolve_crank_toward_start).
        self.view_right_dir = None

        # v1.5 -- Setup (preset) state, loaded from this project's
        # Extensible Storage (doc.ProjectInformation). This is a read-only
        # Revit API call, but it's safe to make it directly here (not via
        # run_on_revit) because __init__ runs in the original pyRevit API
        # context, before Show() -- the same reason module-level code like
        # `doc = __revit__.ActiveUIDocument.Document` above works directly.
        self.presets = []
        self.default_preset_name = ""
        self.active_preset_name = None
        try:
            self.default_preset_name, self.presets = logic.load_presets(doc)
        except Exception:
            self.default_preset_name, self.presets = "", []

        self._build_ui()

        self._reload_preset_dropdown(select_name=self.default_preset_name or None)
        self._update_preset_note()
        if self.default_preset_name:
            default_preset = logic.find_preset(self.presets, self.default_preset_name)
            if default_preset is not None:
                self.active_preset_name = self.default_preset_name
                self._apply_preset_to_fields(default_preset)

        self.Title = "pyNBT - {}".format(TOOL_TITLE)
        # v1.6 -- was 640x620/560x560: too short to show the Crank direction
        # radio + "Select Bar & Pick Point" button without manually resizing
        # (NBT: "phan Crank direction va select va pick bi an, phai keo
        # rong ra ms thay"). Bumped so everything fits by default; the left
        # panel is also wrapped in a ScrollViewer now as a safety net for
        # any future additions (see _build_left_panel).
        self.Width = 700
        self.Height = 760
        self.MinWidth = 640
        self.MinHeight = 680
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.ResizeMode = ResizeMode.CanResize
        self.Background = _brush(CLR_BG)

        # Startup timing diagnostic (temporary, v1.4.2) -- see the comment
        # next to `_perf_t0` near the top of this file. This covers
        # "pyRevit handed control to this script" -> "window object fully
        # built", NOT the Show()/first-paint step, and NOT whatever pyRevit
        # itself does before running the script -- read together with how
        # long the on-screen spin actually feels, this tells us whether the
        # remaining ~3s is inside this script or outside it.
        self._set_status(
            "Ready. (script init: {:.2f}s)".format(time.time() - _perf_t0),
            CLR_MUTED,
        )

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
        left.Orientation = Orientation.Horizontal
        left.VerticalAlignment = VerticalAlignment.Center
        title = _text(TOOL_TITLE, size=18, color=CLR_HEADER_TEXT, bold=True, wrap=False)
        left.Children.Add(title)
        # v1.6 -- the old always-visible usage description is now a "?"
        # icon with a 0.3s hover tooltip (NBT: "phan mo ta cach dung chuyen
        # thanh dau cham hoi 0.3s luc cham vao, giong cac tool da tung xu ly").
        help_icon = _help_icon(
            "Select a straight rebar, then click a position on it to crank "
            "it. The tool splits the bar into 2 overlapping bars (Bar A + "
            "Bar B, a lap splice), offset by exactly 1x the bar diameter. "
            "Use the Setup section to save and reuse Lap/Crank presets."
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

    def _build_content(self):
        grid = Grid()
        grid.Margin = Thickness(16)
        grid.ColumnDefinitions.Add(_col(GridLength(300)))
        grid.ColumnDefinitions.Add(_col(GridLength(16)))
        grid.ColumnDefinitions.Add(_col(GridLength(1, GridUnitType.Star)))

        left = self._build_left_panel()
        Grid.SetColumn(left, 0)
        grid.Children.Add(left)

        right = self._build_right_panel()
        Grid.SetColumn(right, 2)
        grid.Children.Add(right)

        return grid

    def _card(self):
        b = Border()
        b.Background = _brush(CLR_CARD)
        b.BorderBrush = _brush(CLR_BORDER)
        b.BorderThickness = Thickness(1)
        b.CornerRadius = System.Windows.CornerRadius(6)
        b.Padding = Thickness(14)
        return b

    def _separator(self):
        sep = Border()
        sep.Height = 1
        sep.Background = _brush(CLR_BORDER)
        sep.Margin = Thickness(0, 14, 0, 0)
        return sep

    def _build_left_panel(self):
        card = self._card()
        panel = StackPanel()

        panel.Children.Add(_text("SELECTED BAR", size=11, color=CLR_MUTED, bold=True))
        self.tb_bar_info = _text(
            "No bar selected yet. Fill in the parameters below, then click "
            "'Select Bar & Pick Point'.",
            size=12, color=CLR_TEXT,
        )
        self.tb_bar_info.Margin = Thickness(0, 4, 0, 0)
        panel.Children.Add(self.tb_bar_info)

        panel.Children.Add(self._separator())

        # --- v1.5: SETUP (saved preset) -----------------------------------
        panel.Children.Add(_text("SETUP (SAVED PRESET)", size=11, color=CLR_MUTED, bold=True))

        self.cmb_preset = ComboBox()
        self.cmb_preset.FontSize = 13
        self.cmb_preset.Padding = Thickness(6)
        self.cmb_preset.Margin = Thickness(0, 6, 0, 0)
        panel.Children.Add(self.cmb_preset)

        preset_btn_row = StackPanel()
        preset_btn_row.Orientation = Orientation.Horizontal
        preset_btn_row.Margin = Thickness(0, 6, 0, 0)

        btn_save_preset = _btn("Save as...", CLR_CARD, CLR_ACCENT, height=28)
        btn_save_preset.Width = 88
        btn_save_preset.FontSize = 11
        btn_save_preset.BorderThickness = Thickness(1)
        btn_save_preset.BorderBrush = _brush(CLR_BORDER)
        btn_save_preset.Margin = Thickness(0, 0, 4, 0)
        btn_save_preset.FontWeight = FontWeights.Normal
        btn_save_preset.Click += self.on_save_preset_click
        preset_btn_row.Children.Add(btn_save_preset)

        btn_set_default = _btn("Set default", CLR_CARD, CLR_ACCENT, height=28)
        btn_set_default.Width = 88
        btn_set_default.FontSize = 11
        btn_set_default.BorderThickness = Thickness(1)
        btn_set_default.BorderBrush = _brush(CLR_BORDER)
        btn_set_default.Margin = Thickness(0, 0, 4, 0)
        btn_set_default.FontWeight = FontWeights.Normal
        btn_set_default.Click += self.on_set_default_click
        preset_btn_row.Children.Add(btn_set_default)

        # v1.6 -- NBT: Setups could only be added, never removed.
        # v1.7.3 -- was rendering 6px lower than the other 2 buttons in this
        # same row: unlike btn_save_preset/btn_set_default, this one never
        # overrode `_btn()`'s default Margin (0, 6, 0, 0) -- the top-margin-6
        # pushed it down out of line (NBT: "Delete ... k dong cung hang voi
        # save as va set default"). Also switched from CLR_ERROR (red) to
        # CLR_ACCENT to match the other two -- NBT asked for a consistent
        # color instead of red.
        btn_delete_preset = _btn("Delete", CLR_CARD, CLR_ACCENT, height=28)
        btn_delete_preset.Width = 88
        btn_delete_preset.FontSize = 11
        btn_delete_preset.BorderThickness = Thickness(1)
        btn_delete_preset.BorderBrush = _brush(CLR_BORDER)
        btn_delete_preset.Margin = Thickness(0, 0, 0, 0)
        btn_delete_preset.FontWeight = FontWeights.Normal
        btn_delete_preset.Click += self.on_delete_preset_click
        preset_btn_row.Children.Add(btn_delete_preset)

        panel.Children.Add(preset_btn_row)

        self.tb_preset_note = _text(
            "Setups are saved inside this Revit project.",
            size=10, color=CLR_MUTED,
        )
        self.tb_preset_note.Margin = Thickness(0, 6, 0, 0)
        panel.Children.Add(self.tb_preset_note)

        panel.Children.Add(self._separator())

        panel.Children.Add(_text("PARAMETERS", size=11, color=CLR_MUTED, bold=True))
        wrap_params = StackPanel()
        wrap_params.Margin = Thickness(0, 10, 0, 0)

        # --- Lap length: xD or mm (v1.6: xD listed first, matching Crank
        # slope's layout below -- "mm" stays the default-checked mode) -----
        lap_label_panel = StackPanel()
        lap_label_panel.Children.Add(_text("Lap length", size=12, color=CLR_MUTED, bold=True))
        lap_mode_row, self.rb_lap_xd, self.rb_lap_mm = _mode_radio_pair(
            "x D (rounds up to 10mm)", "mm", "lap_mode", default_first=False
        )
        lap_label_panel.Children.Add(lap_mode_row)
        wrap_params.Children.Add(lap_label_panel)

        self.tb_lap = TextBox()
        self.tb_lap.FontSize = 14
        self.tb_lap.Padding = Thickness(6)
        self.tb_lap.Margin = Thickness(0, 4, 0, 0)
        wrap_params.Children.Add(self.tb_lap)

        self.tb_lap_computed = _text("", size=10, color=CLR_MUTED)
        self.tb_lap_computed.Margin = Thickness(0, 2, 0, 0)
        wrap_params.Children.Add(self.tb_lap_computed)

        # --- Crank slope: xD or mm -----------------------------------------
        n_label_panel = StackPanel()
        n_label_panel.Margin = Thickness(0, 12, 0, 0)
        n_label_panel.Children.Add(_text("Crank slope (horizontal)", size=12, color=CLR_MUTED, bold=True))
        crank_mode_row, self.rb_crank_xd, self.rb_crank_mm = _mode_radio_pair("x D", "mm", "crank_mode")
        n_label_panel.Children.Add(crank_mode_row)
        wrap_params.Children.Add(n_label_panel)

        self.tb_n = TextBox()
        self.tb_n.FontSize = 14
        self.tb_n.Padding = Thickness(6)
        self.tb_n.Margin = Thickness(0, 4, 0, 0)
        self.tb_n.Text = "12"
        wrap_params.Children.Add(self.tb_n)

        self.tb_crank_computed = _text("", size=10, color=CLR_MUTED)
        self.tb_crank_computed.Margin = Thickness(0, 2, 0, 0)
        wrap_params.Children.Add(self.tb_crank_computed)

        dir_panel = StackPanel()
        dir_panel.Margin = Thickness(0, 12, 0, 0)
        dir_panel.Children.Add(_text("Crank direction (vertical)", size=12, color=CLR_MUTED, bold=True))
        dir_row = StackPanel()
        dir_row.Orientation = Orientation.Horizontal
        dir_row.Margin = Thickness(0, 4, 0, 0)
        self.rb_up = RadioButton()
        self.rb_up.Content = "Up"
        self.rb_up.GroupName = "crank_dir"
        self.rb_up.IsChecked = True
        self.rb_up.Margin = Thickness(0, 0, 20, 0)
        self.rb_down = RadioButton()
        self.rb_down.Content = "Down"
        self.rb_down.GroupName = "crank_dir"
        dir_row.Children.Add(self.rb_up)
        dir_row.Children.Add(self.rb_down)
        dir_panel.Children.Add(dir_row)
        wrap_params.Children.Add(dir_panel)

        # v1.7 -- Crank side (Left/Right): which side of the pick point, AS
        # SEEN ON SCREEN in the view active when the point was picked, gets
        # the crank. Added because the tool used to always crank toward the
        # ORIGINAL bar's start point, which lands on a different visual side
        # for different bars depending on which way each one happened to be
        # drawn (see logic.resolve_crank_toward_start's docstring). Both
        # this and Up/Down are now part of a Setup, per NBT's explicit
        # request -- reversing the earlier v1.5 decision to keep direction
        # out of Setups.
        side_panel = StackPanel()
        side_panel.Margin = Thickness(0, 12, 0, 0)
        side_panel.Children.Add(_text("Crank side (as seen on screen)", size=12, color=CLR_MUTED, bold=True))
        side_row = StackPanel()
        side_row.Orientation = Orientation.Horizontal
        side_row.Margin = Thickness(0, 4, 0, 0)
        self.rb_crank_left = RadioButton()
        self.rb_crank_left.Content = "Left"
        self.rb_crank_left.GroupName = "crank_side"
        self.rb_crank_left.IsChecked = True
        self.rb_crank_left.Margin = Thickness(0, 0, 20, 0)
        self.rb_crank_right = RadioButton()
        self.rb_crank_right.Content = "Right"
        self.rb_crank_right.GroupName = "crank_side"
        side_row.Children.Add(self.rb_crank_left)
        side_row.Children.Add(self.rb_crank_right)
        side_panel.Children.Add(side_row)
        wrap_params.Children.Add(side_panel)

        panel.Children.Add(wrap_params)

        # v1.6.1 -- convert the textbox's NUMBER when the mode radio is
        # flipped, so the real mm quantity stays the same across the switch
        # (fixes: leaving the "12" x D default in place and switching to mm
        # silently reinterpreted it as 12mm -- a much too steep crank. See
        # _convert_mode_value / NBT bug report 2026-08-22). WPF only raises
        # Checked when a RadioButton newly becomes checked, so each handler
        # fires exactly on the transition INTO that mode -- safe to assume
        # the previous mode was the other one.
        self.rb_crank_mm.Checked += self.on_crank_mode_to_mm
        self.rb_crank_xd.Checked += self.on_crank_mode_to_xd
        self.rb_lap_mm.Checked += self.on_lap_mode_to_mm
        self.rb_lap_xd.Checked += self.on_lap_mode_to_xd

        # Wire up live "computed value" refresh + preview refresh whenever
        # any Lap/Crank input changes (mode radios or the raw values).
        self.rb_lap_mm.Checked += self.on_params_changed
        self.rb_lap_xd.Checked += self.on_params_changed
        self.rb_crank_xd.Checked += self.on_params_changed
        self.rb_crank_mm.Checked += self.on_params_changed
        self.tb_lap.TextChanged += self.on_params_changed
        self.tb_n.TextChanged += self.on_params_changed
        # v1.7 -- Up/Down and Left/Right now affect the schematic directly
        # (and are part of a Setup), so refresh the preview when they change
        # too (previously these two didn't auto-refresh).
        self.rb_up.Checked += self.on_params_changed
        self.rb_down.Checked += self.on_params_changed
        self.rb_crank_left.Checked += self.on_params_changed
        self.rb_crank_right.Checked += self.on_params_changed

        btn_pick = _btn("Select Bar & Pick Point", CLR_ACCENT, CLR_HEADER_TEXT)
        btn_pick.Margin = Thickness(0, 18, 0, 0)
        btn_pick.Click += self.on_pick_click
        panel.Children.Add(btn_pick)
        self.btn_pick = btn_pick

        # v1.6 -- wrap in a ScrollViewer so nothing is ever cut off
        # regardless of window height (matches the fix already used in the
        # Renumber Rebar tool: "trai (ScrollViewer, khong bi cat)") -- a
        # safety net on top of the taller default window size below.
        scroll = ScrollViewer()
        scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        scroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
        scroll.Content = panel
        card.Child = scroll
        return card

    def _build_right_panel(self):
        card = self._card()
        panel = StackPanel()

        panel.Children.Add(_text("PREVIEW (SCHEMATIC, NOT TO SCALE)", size=11, color=CLR_MUTED, bold=True))

        # v1.6 -- the old Preview was a long wall of text describing every
        # point/length; NBT felt it wasn't visual/intuitive enough. This is
        # now a small line-diagram (Canvas) showing Bar A / Bar B's actual
        # shape (start-P1-P2-P3, P2B-P4) at a glance, PLUS a short 2-3 line
        # numeric summary underneath for the exact figures -- see
        # _draw_schematic() / _clear_schematic().
        self.preview_canvas = Canvas()
        self.preview_canvas.Height = 190
        self.preview_canvas.Margin = Thickness(0, 8, 0, 8)
        self.preview_canvas.ClipToBounds = True
        panel.Children.Add(self.preview_canvas)

        self.tb_preview_summary = _text(
            "Pick a point on the bar to see the schematic here.",
            size=12, color=CLR_MUTED,
        )
        panel.Children.Add(self.tb_preview_summary)

        card.Child = panel
        self._clear_schematic()
        return card

    def _build_footer(self):
        border = Border()
        border.Background = _brush(CLR_FOOTER)
        border.Padding = Thickness(16, 10, 16, 10)
        border.BorderBrush = _brush(CLR_BORDER)
        border.BorderThickness = Thickness(0, 1, 0, 0)

        grid = Grid()
        grid.ColumnDefinitions.Add(_col(GridLength(1, GridUnitType.Star)))
        grid.ColumnDefinitions.Add(_col(GridLength.Auto))
        grid.ColumnDefinitions.Add(_col(GridLength.Auto))
        grid.ColumnDefinitions.Add(_col(GridLength.Auto))

        self.tb_status = _text("Ready.", size=11, color=CLR_MUTED, wrap=True)
        self.tb_status.VerticalAlignment = VerticalAlignment.Center
        Grid.SetColumn(self.tb_status, 0)
        grid.Children.Add(self.tb_status)

        sig = _text(
            "{} {} | {}".format(TOOL_TITLE, TOOL_VERSION, doc.Title),
            size=10, color=CLR_MUTED, wrap=False,
        )
        sig.VerticalAlignment = VerticalAlignment.Center
        sig.Margin = Thickness(0, 0, 12, 0)
        Grid.SetColumn(sig, 1)
        grid.Children.Add(sig)

        btn_close = _btn("Close", CLR_CARD, CLR_ACCENT, height=32)
        btn_close.Width = 90
        btn_close.BorderThickness = Thickness(1)
        btn_close.BorderBrush = _brush(CLR_BORDER)
        btn_close.Margin = Thickness(0, 0, 8, 0)
        btn_close.Click += self.on_close_click
        Grid.SetColumn(btn_close, 2)
        grid.Children.Add(btn_close)

        btn_apply = _btn("Apply", CLR_ACCENT, CLR_HEADER_TEXT, height=32)
        btn_apply.Width = 110
        btn_apply.IsEnabled = False
        btn_apply.Click += self.on_apply_click
        Grid.SetColumn(btn_apply, 3)
        grid.Children.Add(btn_apply)
        self.btn_apply = btn_apply

        border.Child = grid
        return border

    # ------------------------------------------------------------------
    # Small UI helpers
    # ------------------------------------------------------------------
    def _set_bar_info_text(self, text):
        self.tb_bar_info.Text = text

    def _set_status(self, text, color=CLR_MUTED):
        self.tb_status.Text = text
        self.tb_status.Foreground = _brush(color)

    def _clear_schematic(self, message=None, color=CLR_MUTED):
        """v1.6 -- clear the schematic Canvas and show a plain status line
        instead (used before a pick exists, and for invalid-parameter /
        geometry-error states, where there's nothing valid to draw)."""
        self.preview_canvas.Children.Clear()
        self.tb_preview_summary.Text = message or "Pick a point on the bar to see the schematic here."
        self.tb_preview_summary.Foreground = _brush(color)

    def _draw_schematic(
        self, bar_a_anchor_mm, p1_mm, p2_mm, p3_mm, p2b_mm, p4_mm, total_mm, up,
        bar_a_anchor_label="Start", bar_b_anchor_label="End",
        screen_left_is_start=True,
    ):
        """v1.6 -- draw the Bar A / Bar B line-diagram. Horizontal spacing is
        proportional to actual mm along the bar's axis; the vertical crank
        offset is a fixed number of pixels for visibility (NOT to scale --
        an actual 1xD offset of a few mm would be invisible at this size).

        v1.7 -- `bar_a_anchor_mm` (where Bar A's straight run starts, and
        where it keeps an original hook) is no longer assumed to be 0 --
        when NBT's Left/Right choice puts the crank toward the ORIGINAL
        END instead of the start, the whole picture mirrors and Bar A
        anchors at `total_mm`. `bar_a_anchor_label`/`bar_b_anchor_label`
        say which original end ("Start"/"End") each piece actually keeps,
        so the diagram/legend stay correct either way -- see
        _refresh_preview's caller code for how these are computed.

        v1.7.2 -- all the mm positions above are axial distances from the
        bar's TRUE start (0..total_mm), independent of screen orientation.
        This diagram used to always draw mm=0 (true Start) at the LEFT edge
        of the picture and mm=total_mm (true End) at the right -- but NBT
        reported (2026-08-22) that after v1.7.1 fixed the real 3D result to
        correctly match his Left/Right choice, the small preview picture
        itself now sometimes shows the mirror image of what actually
        happens, because a bar's true Start isn't always the one that's
        really on-screen-left (same root cause as the original "crank
        alternates sides" bug -- see resolve_crank_toward_start). When
        `screen_left_is_start` is False (true End is really the one on
        screen-left, per logic.start_is_screen_left), X() flips the axis so
        the picture's left/right matches the real Revit view instead of
        always following the bar's arbitrary internal Start/End labels."""
        canvas = self.preview_canvas
        canvas.Children.Clear()

        width, y_mid, y_off = 300.0, 95.0, 26.0
        left_m, right_m = 26.0, 22.0
        span_mm = max(total_mm, 1.0)
        scale = (width - left_m - right_m) / span_mm

        def X(mm):
            shown_mm = mm if screen_left_is_start else (total_mm - mm)
            return left_m + shown_mm * scale

        y1 = y_mid
        y2 = (y_mid - y_off) if up else (y_mid + y_off)

        # Faint full-length baseline (phuong 1) for reference -- always the
        # TRUE 0..total_mm span, regardless of which side got cranked.
        canvas.Children.Add(_wpf_line(X(0.0), y1, X(total_mm), y1, CLR_BORDER, thickness=1, dashed=True))

        # Bar A: anchor -> P1 (phuong 1) -> P2 (phuong 2) -> P3 (phuong 2).
        canvas.Children.Add(_wpf_line(X(bar_a_anchor_mm), y1, X(p1_mm), y1, CLR_BAR_A, thickness=4))
        canvas.Children.Add(_wpf_line(X(p1_mm), y1, X(p2_mm), y2, CLR_BAR_A, thickness=4))
        canvas.Children.Add(_wpf_line(X(p2_mm), y2, X(p3_mm), y2, CLR_BAR_A, thickness=4))

        # Bar B: P2B -> P4, entirely on phuong 1.
        canvas.Children.Add(_wpf_line(X(p2b_mm), y1, X(p4_mm), y1, CLR_BAR_B, thickness=4))

        # Hook glyphs (small perpendicular ticks) at the two original ends.
        canvas.Children.Add(_wpf_line(X(bar_a_anchor_mm), y1 - 8, X(bar_a_anchor_mm), y1 + 8, CLR_BAR_A, thickness=3))
        canvas.Children.Add(_wpf_line(X(p4_mm), y1 - 8, X(p4_mm), y1 + 8, CLR_BAR_B, thickness=3))

        points = [
            (bar_a_anchor_mm, y1, bar_a_anchor_label),
            (p1_mm, y1, "P1"),
            (p2_mm, y2, "P2"),
            (p3_mm, y2, "P3"),
            (p2b_mm, y1, "P2B"),
            (p4_mm, y1, bar_b_anchor_label),
        ]
        for mm, y, label in points:
            canvas.Children.Add(_wpf_dot(X(mm), y, CLR_TEXT))
            label_y = (y - 20) if y <= y_mid else (y + 8)
            canvas.Children.Add(_canvas_text(label, X(mm), label_y, size=10, bold=True, center_x=True))

        legend_y = 160.0
        canvas.Children.Add(_wpf_line(left_m, legend_y, left_m + 20, legend_y, CLR_BAR_A, thickness=4))
        canvas.Children.Add(_canvas_text(
            "Bar A ({} hook)".format(bar_a_anchor_label.lower()),
            left_m + 26, legend_y - 7, size=10, color=CLR_MUTED,
        ))
        canvas.Children.Add(_wpf_line(left_m + 150, legend_y, left_m + 170, legend_y, CLR_BAR_B, thickness=4))
        canvas.Children.Add(_canvas_text(
            "Bar B ({} hook)".format(bar_b_anchor_label.lower()),
            left_m + 176, legend_y - 7, size=10, color=CLR_MUTED,
        ))

    def _bring_to_front(self):
        """Re-focus this modeless window over the Revit main window. Needed
        after any interactive pick (PickObject/PickPoint) sends focus to
        Revit's canvas -- without this the tool window is left hidden behind
        Revit once the pick completes. Safe to call from do_select_and_pick /
        do_apply: both run via ExternalEvent on Revit's main UI thread, the
        same thread this window itself runs on (no cross-thread marshaling
        needed -- confirmed by _set_status() already touching WPF controls
        directly from those same methods)."""
        try:
            self.Topmost = True
            self.Topmost = False
            self.Activate()
        except Exception:
            pass

    def _reset_pick_state(self):
        self.pick_point = None
        self.dist_from_start_ft = None
        self.dist_from_end_ft = None
        self.total_len_ft = None
        self.view_right_dir = None
        self.btn_apply.IsEnabled = False
        self._clear_schematic()

    @staticmethod
    def _parse_float(text):
        if text is None:
            raise ValueError("Value is empty.")
        cleaned = text.strip().replace(",", ".")
        if not cleaned:
            raise ValueError("Value is empty.")
        return float(cleaned)

    # ------------------------------------------------------------------
    # v1.5 -- Setup (preset) helpers (WPF thread; preset persistence itself
    # is queued onto Revit's API context via run_on_revit(), same as every
    # other model-touching action).
    # ------------------------------------------------------------------
    def _update_computed_labels(self):
        if self.diameter_ft is None:
            self.tb_lap_computed.Text = ""
            self.tb_crank_computed.Text = ""
            return
        try:
            lap_mode = logic.LAP_MODE_XD if bool(self.rb_lap_xd.IsChecked) else logic.LAP_MODE_MM
            lap_val = self._parse_float(self.tb_lap.Text)
            lap_ft = logic.compute_lap_len_ft(lap_mode, lap_val, self.diameter_ft)
            self.tb_lap_computed.Text = "= {:.0f} mm".format(logic.ft_to_mm(lap_ft))
        except Exception:
            self.tb_lap_computed.Text = ""
        try:
            crank_mode = logic.CRANK_MODE_MM if bool(self.rb_crank_mm.IsChecked) else logic.CRANK_MODE_XD
            crank_val = self._parse_float(self.tb_n.Text)
            horiz_ft = logic.compute_crank_horiz_ft(crank_mode, crank_val, self.diameter_ft)
            self.tb_crank_computed.Text = "= {:.0f} mm horizontal".format(logic.ft_to_mm(horiz_ft))
        except Exception:
            self.tb_crank_computed.Text = ""

    def on_params_changed(self, sender, args):
        self._update_computed_labels()
        if self.pick_point is not None:
            run_on_revit(self._refresh_preview)

    # ------------------------------------------------------------------
    # v1.6.1 -- mode-switch value conversion (bug fix, see NBT report
    # 2026-08-22: "crank slope o mm bi loi"). Keeps the NUMBER meaningful
    # across an xD <-> mm toggle instead of silently reinterpreting the same
    # digits under a different unit. Needs self.diameter_ft, so this is a
    # no-op until a bar has actually been selected -- fine, since the bug
    # only bites once Apply/Preview actually runs the numbers.
    # ------------------------------------------------------------------
    def _convert_mode_value(self, textbox, to_mm):
        if self.diameter_ft is None:
            return
        try:
            old_val = self._parse_float(textbox.Text)
        except Exception:
            return
        d_mm = logic.ft_to_mm(self.diameter_ft)
        if d_mm <= 0:
            return
        new_val = (old_val * d_mm) if to_mm else (old_val / d_mm)
        textbox.Text = _fmt_num(new_val)

    def on_crank_mode_to_mm(self, sender, args):
        self._convert_mode_value(self.tb_n, to_mm=True)

    def on_crank_mode_to_xd(self, sender, args):
        self._convert_mode_value(self.tb_n, to_mm=False)

    def on_lap_mode_to_mm(self, sender, args):
        self._convert_mode_value(self.tb_lap, to_mm=True)

    def on_lap_mode_to_xd(self, sender, args):
        self._convert_mode_value(self.tb_lap, to_mm=False)

    def _apply_preset_to_fields(self, preset):
        if preset["lap_mode"] == logic.LAP_MODE_XD:
            self.rb_lap_xd.IsChecked = True
        else:
            self.rb_lap_mm.IsChecked = True
        self.tb_lap.Text = _fmt_num(preset["lap_value"])
        if preset["crank_mode"] == logic.CRANK_MODE_MM:
            self.rb_crank_mm.IsChecked = True
        else:
            self.rb_crank_xd.IsChecked = True
        self.tb_n.Text = _fmt_num(preset["crank_value"])
        # v1.7 -- Up/Down and Left/Right are now part of a Setup too.
        # .get(...) defaults keep older (v1.5/v1.6) saved presets loadable.
        if preset.get("direction_up", True):
            self.rb_up.IsChecked = True
        else:
            self.rb_down.IsChecked = True
        if preset.get("crank_side", logic.CRANK_SIDE_LEFT) == logic.CRANK_SIDE_RIGHT:
            self.rb_crank_right.IsChecked = True
        else:
            self.rb_crank_left.IsChecked = True
        self._update_computed_labels()

    def on_preset_selected(self, sender, args):
        idx = self.cmb_preset.SelectedIndex
        if idx < 0 or idx >= self.cmb_preset.Items.Count:
            return
        name = self.cmb_preset.Items[idx]
        preset = logic.find_preset(self.presets, name)
        if preset is None:
            return
        self.active_preset_name = name
        self._apply_preset_to_fields(preset)
        if self.pick_point is not None:
            run_on_revit(self._refresh_preview)

    def _reload_preset_dropdown(self, select_name=None):
        self.cmb_preset.SelectionChanged -= self.on_preset_selected
        self.cmb_preset.Items.Clear()
        for p in self.presets:
            self.cmb_preset.Items.Add(p["name"])
        self.cmb_preset.SelectionChanged += self.on_preset_selected
        target_index = -1
        if select_name:
            for i in range(self.cmb_preset.Items.Count):
                if self.cmb_preset.Items[i] == select_name:
                    target_index = i
                    break
        self.cmb_preset.SelectedIndex = target_index

    def _update_preset_note(self):
        if self.default_preset_name:
            self.tb_preset_note.Text = "Default for this project: '{}'".format(
                self.default_preset_name
            )
        else:
            self.tb_preset_note.Text = "Setups are saved inside this Revit project."

    def on_save_preset_click(self, sender, args):
        dialog = _NamePromptDialog(
            self, "Save Setup As", "Setup name:", self.active_preset_name or ""
        )
        ok = dialog.ShowDialog()
        if not ok:
            return
        raw_name = dialog.result_text
        try:
            lap_mode = logic.LAP_MODE_XD if bool(self.rb_lap_xd.IsChecked) else logic.LAP_MODE_MM
            lap_val = self._parse_float(self.tb_lap.Text)
            crank_mode = logic.CRANK_MODE_MM if bool(self.rb_crank_mm.IsChecked) else logic.CRANK_MODE_XD
            crank_val = self._parse_float(self.tb_n.Text)
            name = logic.sanitize_preset_name(raw_name)
        except ValueError as ve:
            self._set_status(str(ve), CLR_ERROR)
            return
        # v1.7 -- Up/Down and Left/Right are saved into the Setup too.
        crank_side = logic.CRANK_SIDE_LEFT if bool(self.rb_crank_left.IsChecked) else logic.CRANK_SIDE_RIGHT
        up = bool(self.rb_up.IsChecked)
        run_on_revit(
            lambda: self.do_save_preset(
                name, lap_mode, lap_val, crank_mode, crank_val, crank_side, up
            )
        )

    def do_save_preset(
        self, name, lap_mode, lap_val, crank_mode, crank_val,
        crank_side=logic.CRANK_SIDE_LEFT, direction_up=True,
    ):
        is_new = logic.find_preset(self.presets, name) is None
        updated = logic.upsert_preset(
            self.presets, name, lap_mode, lap_val, crank_mode, crank_val,
            crank_side=crank_side, direction_up=direction_up,
        )
        t = Transaction(doc, "pyNBT - Save Crank Rebar setup")
        t.Start()
        try:
            logic.save_presets(doc, self.default_preset_name, updated)
            t.Commit()
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            self._set_status("Could not save setup: {}".format(str(ex)), CLR_ERROR)
            return
        self.presets = updated
        self.active_preset_name = name
        self._reload_preset_dropdown(select_name=name)
        self._set_status(
            "Setup '{}' saved{}.".format(name, "" if is_new else " (updated)"),
            CLR_OK,
        )

    def on_set_default_click(self, sender, args):
        name = self.active_preset_name
        if not name:
            self._set_status("Select or save a setup first.", CLR_ERROR)
            return
        run_on_revit(lambda: self.do_set_default(name))

    def do_set_default(self, name):
        t = Transaction(doc, "pyNBT - Set default Crank Rebar setup")
        t.Start()
        try:
            logic.save_presets(doc, name, self.presets)
            t.Commit()
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            self._set_status("Could not set default: {}".format(str(ex)), CLR_ERROR)
            return
        self.default_preset_name = name
        self._update_preset_note()
        self._set_status("'{}' set as default setup for this project.".format(name), CLR_OK)

    def on_delete_preset_click(self, sender, args):
        idx = self.cmb_preset.SelectedIndex
        if idx < 0 or idx >= self.cmb_preset.Items.Count:
            self._set_status("Select a setup to delete first.", CLR_ERROR)
            return
        name = self.cmb_preset.Items[idx]
        result = MessageBox.Show(
            "Delete setup '{}'? This cannot be undone.".format(name),
            TOOL_TITLE, MessageBoxButton.YesNo, MessageBoxImage.Warning,
        )
        if result != MessageBoxResult.Yes:
            return
        run_on_revit(lambda: self.do_delete_preset(name))

    def do_delete_preset(self, name):
        updated = logic.remove_preset(self.presets, name)
        # If the Setup being deleted was pinned as default, clear that too
        # (persisted together in the same save_presets() call/Transaction).
        new_default = "" if self.default_preset_name == name else self.default_preset_name
        t = Transaction(doc, "pyNBT - Delete Crank Rebar setup")
        t.Start()
        try:
            logic.save_presets(doc, new_default, updated)
            t.Commit()
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            self._set_status("Could not delete setup: {}".format(str(ex)), CLR_ERROR)
            return
        self.presets = updated
        self.default_preset_name = new_default
        if self.active_preset_name == name:
            self.active_preset_name = None
        self._reload_preset_dropdown(select_name=self.active_preset_name)
        self._update_preset_note()
        self._set_status("Setup '{}' deleted.".format(name), CLR_OK)

    # ------------------------------------------------------------------
    # Button handlers (WPF thread) -- only ever queue work onto Revit's
    # API context via run_on_revit(); never touch the Revit API directly
    # here.
    # ------------------------------------------------------------------
    def on_pick_click(self, sender, args):
        run_on_revit(self.do_select_and_pick)

    def on_apply_click(self, sender, args):
        run_on_revit(self.do_apply)

    def on_close_click(self, sender, args):
        self.Close()

    # ------------------------------------------------------------------
    # Actions run inside Revit's API context (via ExternalEvent)
    # ------------------------------------------------------------------
    def _pick_point_with_workplane_fallback(self, prompt):
        """uidoc.Selection.PickPoint() can fail with "No work plane set in
        current view" in Section/Elevation/3D views that have no active
        SketchPlane -- a known Revit API quirk (see logic.ensure_work_plane).
        Try normally first; only set a work plane (aligned to the selected
        bar's own vertical plane) and retry once if that specific error
        happens, so plan views (which already have a work plane) are
        unaffected."""
        try:
            return uidoc.Selection.PickPoint(ObjectSnapTypes.Nearest, prompt)
        except OperationCanceledException:
            raise
        except Exception as ex:
            if "work plane" not in str(ex).lower():
                raise
            norm = self.bar_dir.CrossProduct(XYZ.BasisZ).Normalize()
            origin = self.line.GetEndPoint(0)
            t = Transaction(doc, "pyNBT - Set work plane for pick")
            t.Start()
            try:
                logic.ensure_work_plane(doc, doc.ActiveView, origin, norm)
                t.Commit()
            except Exception:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise
            return uidoc.Selection.PickPoint(ObjectSnapTypes.Nearest, prompt)

    def _load_bar(self, el):
        """Validate `el` and, if OK, populate self.rebar/bar_type/host/line/
        bar_dir/diameter_ft and the SELECTED BAR info text. Returns True on
        success, False on failure (bar info / status already updated to
        explain why)."""
        self.rebar = None
        try:
            if not isinstance(el, Rebar):
                raise ValueError(
                    "The selected element is not a Rebar. Select a rebar bar."
                )

            line = logic.get_single_centerline(el)
            start = line.GetEndPoint(0)
            end = line.GetEndPoint(1)
            vec = end - start
            length_ft = vec.GetLength()
            if length_ft < logic.mm_to_ft(10.0):
                raise ValueError("The bar is too short to cut/crank.")
            direction = vec.Normalize()
            if abs(direction.Z) > 0.01:
                raise ValueError(
                    "This tool only supports horizontal bars in v1 "
                    "(sloped or vertical bars are not supported yet)."
                )

            bar_type = logic.get_bar_type(el, doc)
            diameter_ft = logic.get_diameter_ft(el, doc)
            if diameter_ft is None:
                raise ValueError(
                    "Could not read the bar diameter (REBAR_BAR_DIAMETER "
                    "parameter on the RebarBarType)."
                )

            host_id = el.GetHostId()
            host = doc.GetElement(host_id) if logic.is_valid_eid(host_id) else None
            if host is None:
                raise ValueError("The bar has no valid host element.")

            self.rebar = el
            self.bar_type = bar_type
            self.host = host
            self.line = line
            self.bar_dir = direction
            self.diameter_ft = diameter_ft

            d_mm = logic.ft_to_mm(diameter_ft)
            len_mm = logic.ft_to_mm(length_ft)
            type_name = logic.get_element_name(bar_type)
            self._set_bar_info_text(
                "Type: {}\nDiameter: {:.0f} mm\nLength: {:.0f} mm".format(
                    type_name, d_mm, len_mm
                )
            )
            return True
        except ValueError as ve:
            self._set_bar_info_text(str(ve))
            self._set_status(str(ve), CLR_ERROR)
            return False
        except Exception as ex:
            self._set_bar_info_text("Error reading the selected bar.")
            self._set_status("Error: {}".format(str(ex)), CLR_ERROR)
            return False

    def do_select_and_pick(self):
        """Combined flow: pick the bar interactively, then pick the cut
        point on it -- does not depend on Revit's ambient pre-selection, so
        the tool works the same whether or not something was selected
        before the button was clicked.

        Wrapped in try/finally so the window is always re-focused (see
        _bring_to_front) once this returns, whatever the outcome -- picking
        sends focus to Revit's canvas, and NBT wants the tool window pulled
        back to front afterwards instead of staying hidden behind Revit."""
        self._reset_pick_state()
        try:
            self._do_select_and_pick_inner()
        finally:
            self._bring_to_front()

    def _do_select_and_pick_inner(self):
        try:
            ref = uidoc.Selection.PickObject(
                ObjectType.Element, RebarOnlyFilter(),
                "Select the rebar bar to cut/crank",
            )
        except OperationCanceledException:
            self._set_status("Selection cancelled.", CLR_MUTED)
            return
        except Exception as ex:
            self._set_status("Selection failed: {}".format(str(ex)), CLR_ERROR)
            return

        el = doc.GetElement(ref.ElementId)
        if not self._load_bar(el):
            return

        self._set_status(
            "Bar loaded. Now click the position on the bar to cut/crank.",
            CLR_MUTED,
        )

        try:
            raw_pt = self._pick_point_with_workplane_fallback(
                "Click the position on the bar to cut/crank"
            )
        except OperationCanceledException:
            self._set_status(
                "Point pick cancelled. Bar is still loaded -- click "
                "'Select Bar & Pick Point' again to pick the cut point.",
                CLR_MUTED,
            )
            return
        except Exception as ex:
            self._set_status("Pick failed: {}".format(str(ex)), CLR_ERROR)
            return

        try:
            pt_on_line, dist_start_ft, dist_end_ft, total_ft = (
                logic.project_point_on_line(self.line, raw_pt)
            )
        except ValueError as ve:
            self._set_status(str(ve), CLR_ERROR)
            return

        self.pick_point = pt_on_line
        self.dist_from_start_ft = dist_start_ft
        self.dist_from_end_ft = dist_end_ft
        self.total_len_ft = total_ft
        # v1.7 -- capture the CURRENT view's screen-right direction now, at
        # the moment of picking, so Left/Right always matches what NBT saw
        # on screen when he clicked (not whatever view happens to be active
        # later when he hits Apply).
        self.view_right_dir = logic.get_view_right_direction(doc.ActiveView)
        self._refresh_preview()

    def _resolve_crank_reference(self, crank_side):
        """v1.7 -- returns (toward_start, ref_start_pt, ref_bar_dir,
        ref_dist_to_click_ft) for `crank_side` ('left'/'right'), resolved
        against self.view_right_dir (captured at pick time). See
        logic.resolve_crank_toward_start's docstring for the reasoning.
        Raises ValueError (safe to show directly to NBT) if Left/Right can't
        be resolved in the view the point was picked from."""
        toward_start = logic.resolve_crank_toward_start(
            crank_side, self.bar_dir, self.view_right_dir
        )
        if toward_start:
            return (
                True, self.line.GetEndPoint(0), self.bar_dir,
                self.dist_from_start_ft,
            )
        neg_dir = XYZ(-self.bar_dir.X, -self.bar_dir.Y, -self.bar_dir.Z)
        return (False, self.line.GetEndPoint(1), neg_dir, self.dist_from_end_ft)

    def _refresh_preview(self):
        if self.pick_point is None:
            return
        try:
            lap_mode = logic.LAP_MODE_XD if bool(self.rb_lap_xd.IsChecked) else logic.LAP_MODE_MM
            lap_val = self._parse_float(self.tb_lap.Text)
            crank_mode = logic.CRANK_MODE_MM if bool(self.rb_crank_mm.IsChecked) else logic.CRANK_MODE_XD
            crank_val = self._parse_float(self.tb_n.Text)
            if lap_val <= 0 or crank_val <= 0:
                raise ValueError("Lap length and Crank slope must be positive numbers.")
            lap_ft = logic.compute_lap_len_ft(lap_mode, lap_val, self.diameter_ft)
            horiz_ft = logic.compute_crank_horiz_ft(crank_mode, crank_val, self.diameter_ft)
        except ValueError as ve:
            self.btn_apply.IsEnabled = False
            self._clear_schematic(
                "Point picked, but parameters are invalid: {}".format(str(ve)),
                CLR_ERROR,
            )
            return

        up = bool(self.rb_up.IsChecked)
        crank_side = logic.CRANK_SIDE_LEFT if bool(self.rb_crank_left.IsChecked) else logic.CRANK_SIDE_RIGHT
        try:
            toward_start, ref_start_pt, ref_bar_dir, ref_dist_ft = (
                self._resolve_crank_reference(crank_side)
            )
            bend_dia_ft = logic.get_standard_bend_diameter_ft(self.bar_type)
            geo = logic.compute_full_geometry(
                ref_start_pt, self.pick_point, ref_bar_dir,
                self.diameter_ft, horiz_ft, lap_ft, up,
                ref_dist_ft, self.total_len_ft,
                bend_diameter_ft=bend_dia_ft,
            )
        except ValueError as ve:
            self.btn_apply.IsEnabled = False
            self._clear_schematic(str(ve), CLR_ERROR)
            return

        d_mm = logic.ft_to_mm(self.diameter_ft)
        p2_from_start_mm = logic.ft_to_mm(self.dist_from_start_ft)
        horiz_mm = logic.ft_to_mm(geo["horiz_ft"])
        diag_mm = logic.ft_to_mm(geo["diagonal_len_ft"])
        bar_a_straight_mm = logic.ft_to_mm(geo["straight_a_len_ft"])
        bar_b_len_mm = logic.ft_to_mm(geo["b_len_ft"])
        lap_mm = logic.ft_to_mm(geo["lap_len_ft"])
        total_mm = logic.ft_to_mm(self.total_len_ft)

        lap_source = (
            "{:g} x D, rounded up to nearest 10mm".format(lap_val)
            if lap_mode == logic.LAP_MODE_XD else "entered directly"
        )
        crank_source = (
            "entered directly"
            if crank_mode == logic.CRANK_MODE_MM else "{:g} x D".format(crank_val)
        )

        # v1.6 -- draw the schematic using axial mm distances from the bar's
        # TRUE start (always 0..total_mm, regardless of crank_side). P2 is
        # always the click point's own true-axial position. v1.7 -- when
        # the crank is built toward the ORIGINAL END instead (crank_side
        # picked the other screen side than this bar's start->end happens
        # to point), the whole picture mirrors: Bar A's straight run anchors
        # at total_mm (not 0) and P1/P3/P4 move the other way -- see
        # _resolve_crank_reference's docstring for the underlying algebra.
        p2_mm = p2_from_start_mm
        p2b_mm = p2_mm
        if toward_start:
            bar_a_anchor_mm = 0.0
            p1_mm = bar_a_straight_mm
            p3_mm = p2_mm + lap_mm
            p4_mm = p2b_mm + bar_b_len_mm
            anchor_labels = ("Start", "End")
        else:
            bar_a_anchor_mm = total_mm
            p1_mm = total_mm - bar_a_straight_mm
            p3_mm = p2_mm - lap_mm
            p4_mm = p2b_mm - bar_b_len_mm
            anchor_labels = ("End", "Start")
        screen_left_is_start = logic.start_is_screen_left(
            self.bar_dir, self.view_right_dir
        )
        self._draw_schematic(
            bar_a_anchor_mm, p1_mm, p2_mm, p3_mm, p2b_mm, p4_mm, total_mm, up,
            anchor_labels[0], anchor_labels[1],
            screen_left_is_start=screen_left_is_start,
        )

        summary = (
            "Bar A: {:.0f}mm straight + crank ({}, {:.0f}mm horiz) + "
            "{:.0f}mm lap ({})\n"
            "Bar B: {:.0f}mm straight, offset 1xD ({:.0f}mm) from Bar A's "
            "lap -- Direction: {}, Side: {}"
        ).format(
            bar_a_straight_mm, crank_source, horiz_mm,
            lap_mm, lap_source,
            bar_b_len_mm, d_mm,
            "Up" if up else "Down",
            "Left" if crank_side == logic.CRANK_SIDE_LEFT else "Right",
        )
        self.tb_preview_summary.Text = summary
        self.tb_preview_summary.Foreground = _brush(CLR_TEXT)
        self.btn_apply.IsEnabled = True
        self._set_status("Preview ready. Click Apply to commit.", CLR_MUTED)

    def do_apply(self):
        if self.rebar is None or self.pick_point is None:
            self._set_status(
                "Missing data: click 'Select Bar & Pick Point' first.",
                CLR_ERROR,
            )
            return
        try:
            lap_mode = logic.LAP_MODE_XD if bool(self.rb_lap_xd.IsChecked) else logic.LAP_MODE_MM
            lap_val = self._parse_float(self.tb_lap.Text)
            crank_mode = logic.CRANK_MODE_MM if bool(self.rb_crank_mm.IsChecked) else logic.CRANK_MODE_XD
            crank_val = self._parse_float(self.tb_n.Text)
            if lap_val <= 0 or crank_val <= 0:
                raise ValueError("Lap length and Crank slope must be positive numbers.")
            lap_ft = logic.compute_lap_len_ft(lap_mode, lap_val, self.diameter_ft)
            horiz_ft = logic.compute_crank_horiz_ft(crank_mode, crank_val, self.diameter_ft)
            up = bool(self.rb_up.IsChecked)
            crank_side = logic.CRANK_SIDE_LEFT if bool(self.rb_crank_left.IsChecked) else logic.CRANK_SIDE_RIGHT
            toward_start, ref_start_pt, ref_bar_dir, ref_dist_ft = (
                self._resolve_crank_reference(crank_side)
            )
            bend_dia_ft = logic.get_standard_bend_diameter_ft(self.bar_type)
            geo = logic.compute_full_geometry(
                ref_start_pt, self.pick_point, ref_bar_dir, self.diameter_ft,
                horiz_ft, lap_ft, up, ref_dist_ft, self.total_len_ft,
                bend_diameter_ft=bend_dia_ft,
            )
        except ValueError as ve:
            self._set_status(str(ve), CLR_ERROR)
            return
        except Exception as ex:
            self._set_status("Calculation error: {}".format(str(ex)), CLR_ERROR)
            return

        # v1.5 -- if a Setup is currently active, auto-save the just-applied
        # Lap/Crank values back into it (NBT: "an apply thi no se duoc luu tu
        # dong lai"). This does NOT change which Setup is pinned as default
        # (Set default is a separate, explicit action). Computed here (before
        # the Transaction below) so it can be committed atomically together
        # with the bar creation -- both roll back together on failure.
        # v1.7 -- crank_side/up are now saved into the Setup too (NBT
        # explicitly reversed the earlier v1.5 decision to keep direction
        # out of Setups).
        updated_presets = None
        if self.active_preset_name:
            updated_presets = logic.upsert_preset(
                self.presets, self.active_preset_name,
                lap_mode, lap_val, crank_mode, crank_val,
                crank_side=crank_side, direction_up=up,
            )

        norm = self.bar_dir.CrossProduct(XYZ.BasisZ).Normalize()

        rebar = self.rebar
        bar_type = self.bar_type
        host = self.host

        # Read the original bar's TWO hooks BEFORE deleting it (v1.3/v1.4):
        # index 0 = the hook at line.GetEndPoint(0) (the TRUE original
        # start), index 1 = the hook at line.GetEndPoint(1) (the TRUE
        # original end). P1, P2, P2B and P3 (the lap/connecting interface
        # between A and B) never get a hook -- those are new interfaces, not
        # original bar ends.
        hook0_type, hook0_orient = logic.get_end_hook(doc, rebar, 0)
        hook1_type, hook1_orient = logic.get_end_hook(doc, rebar, 1)
        # v1.7.3 -- see logic.flip_hook_orientation's docstring: this tool's
        # own `norm` (computed a few lines below from self.bar_dir) isn't
        # guaranteed to match the original bar's own plane normal, which
        # flips the physical bend side of a preserved hook (NBT: hook set to
        # face down came out facing up on both preserved ends). Correcting
        # here, right after reading, so every use of hook0_orient/hook1_orient
        # below already has the right value.
        hook0_orient = logic.flip_hook_orientation(hook0_orient)
        hook1_orient = logic.flip_hook_orientation(hook1_orient)

        # v1.7 -- Bar A (the piece with the diagonal+lap, created starting
        # at ref_start_pt) keeps whichever original hook sits at
        # ref_start_pt -- hook0 when cranking toward the true start (the
        # v1.3-v1.6 default), hook1 when NBT's Left/Right choice put the
        # crank toward the true end instead. Bar B (the plain straight
        # piece, ending at geo["P4"]) keeps whichever hook is left over.
        if toward_start:
            a_hook_type, a_hook_orient = hook0_type, hook0_orient
            b_hook_type, b_hook_orient = hook1_type, hook1_orient
        else:
            a_hook_type, a_hook_orient = hook1_type, hook1_orient
            b_hook_type, b_hook_orient = hook0_type, hook0_orient

        t = Transaction(doc, "pyNBT - Crank Rebar")
        t.Start()
        try:
            bar_a, bar_b = logic.create_cut_crank_bars(
                doc, rebar, bar_type, host, norm,
                ref_start_pt, geo["P1"], geo["P2"], geo["P2B"], geo["P3"], geo["P4"],
                a_hook_type, a_hook_orient,
                b_hook_type, b_hook_orient,
            )
            if updated_presets is not None:
                logic.save_presets(doc, self.default_preset_name, updated_presets)
            t.Commit()
            if updated_presets is not None:
                self.presets = updated_presets

            success_msg = (
                "Success. Bar A id={}, Bar B id={}. Ready for the next bar."
            ).format(logic.eid_int(bar_a.Id), logic.eid_int(bar_b.Id))

            # v1.6 -- NBT reversed the earlier v1.4 request: Apply should no
            # longer close the window, just bring it back to front over
            # Revit. The original bar (self.rebar) was just deleted, so its
            # cached state is stale -- clear it and reset the pick state
            # (same as a fresh launch) so NBT can immediately select the
            # next bar, with the Setup/parameters still filled in.
            self.rebar = None
            self.bar_type = None
            self.host = None
            self.line = None
            self.bar_dir = None
            self.diameter_ft = None
            self._reset_pick_state()
            self._set_bar_info_text(
                "No bar selected yet. Fill in the parameters below, then "
                "click 'Select Bar & Pick Point'."
            )
            self._update_computed_labels()
            self._set_status(success_msg, CLR_OK)
            self._bring_to_front()
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            self._set_status("Apply failed: {}".format(str(ex)), CLR_ERROR)


CutCrankWindow().Show()
