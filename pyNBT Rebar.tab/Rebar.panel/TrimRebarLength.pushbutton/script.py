# -*- coding: utf-8 -*-
"""Trim Rebar to Length

Select 1 or more Rebar elements in Revit first (pre-select, like the Rebar
Start-End Direction tool), then run this tool. Type the desired REMAINING
length (measured along the whole centerline, including any bends) and pick
which end to KEEP -- Top or Bottom, judged by the current view's on-screen
orientation at the moment Apply is clicked (same screen-relative mechanism
as Left/Right in the Crank Rebar tool, applied to Up/Down here).

The tool deletes the excess from the opposite end and keeps the remaining
shape byte-for-byte identical up to the cut point (same segments, same
hook at the kept end). The hook at the cut end (if any) is removed, since
that end is now a fresh open cut.

Only single bars (Layout = Single, NumberOfBarPositions == 1) are supported
in this first version -- a Rebar SET/array is skipped with a clear message.
The bar's shape must be planar (same requirement Revit itself has for
Rebar.CreateFromCurves).
"""

__title__ = 'Trim\nRebar'
__author__ = 'NBT'
__persistentengine__ = True

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PANEL_DIR = os.path.dirname(SCRIPT_DIR)
TAB_DIR = os.path.dirname(PANEL_DIR)
EXTENSION_DIR = os.path.dirname(TAB_DIR)
LIB_DIR = os.path.join(EXTENSION_DIR, 'lib')

if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

import rebar_trim_logic as logic

from System.Windows import (
    Window, WindowStartupLocation, SizeToContent, Thickness,
    HorizontalAlignment, VerticalAlignment, FontWeight, FontWeights,
    MessageBox, MessageBoxButton, MessageBoxImage, GridLength, GridUnitType,
    TextWrapping,
)
from System.Windows.Controls import (
    Grid, RowDefinition, ColumnDefinition, StackPanel, Orientation,
    Border, TextBlock, TextBox, RadioButton, Button,
    ScrollViewer, ScrollBarVisibility,
)
from System.Windows.Media import Color, SolidColorBrush, Brushes, FontFamily
from System.Windows.Input import Cursors

from Autodesk.Revit.DB import Transaction
from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent
from Autodesk.Revit.Exceptions import OperationCanceledException

# Shared pyNBT palette -- imported AFTER the clr.AddReference('Presentation...')
# calls above, since pyNBT.theme itself does `from System.Windows.Media import
# ...` at module load time (see project doc "pynbt-theme-import-order-bug.md":
# those WPF assemblies must already be loaded before that import runs).
from pyNBT.theme import (
    CLR_HEADER, CLR_HEADER_TEXT, CLR_HEADER_SUB, CLR_ACCENT,
    CLR_APPLY, CLR_APPLY_TEXT, CLR_BG, CLR_CARD, CLR_BORDER, CLR_FOOTER,
    CLR_TEXT, CLR_MUTED, CLR_ERROR, CLR_SUCCESS, brush,
)

TOOL_NAME = 'Trim Rebar to Length'
TOOL_VERSION = 'v1.0'


def _gridlen(spec):
    if spec == 'Auto':
        return GridLength.Auto
    elif spec == '*':
        return GridLength(1.0, GridUnitType.Star)
    else:
        return GridLength(float(spec))


def _row(grid, height):
    grid.RowDefinitions.Add(RowDefinition(Height=_gridlen(height)))


def _col(grid, width):
    grid.ColumnDefinitions.Add(ColumnDefinition(Width=_gridlen(width)))


def _text(txt, size=12, color=CLR_TEXT, bold=False, wrap=False):
    tb = TextBlock()
    tb.Text = txt
    tb.FontSize = size
    tb.Foreground = brush(color)
    if bold:
        tb.FontWeight = FontWeights.Bold
    if wrap:
        tb.TextWrapping = TextWrapping.Wrap
    return tb


def _btn(text, bg, fg, width=120, height=34):
    b = Button()
    b.Content = text
    b.Width = width
    b.Height = height
    b.Background = brush(bg)
    b.Foreground = brush(fg)
    b.BorderThickness = Thickness(0)
    b.FontSize = 12
    b.Cursor = Cursors.Hand
    b.Margin = Thickness(0, 0, 8, 0)
    return b


class _GenericHandler(IExternalEventHandler):
    def __init__(self):
        self.callback = None

    def SetCallback(self, callback):
        self.callback = callback

    def Execute(self, uiapp):
        if self.callback is None:
            return
        try:
            self.callback(uiapp)
        except Exception as ex:
            try:
                MessageBox.Show(
                    'Unexpected error: {}'.format(str(ex)),
                    TOOL_NAME, MessageBoxButton.OK, MessageBoxImage.Error
                )
            except Exception:
                pass

    def GetName(self):
        return 'pyNBT Trim Rebar to Length External Event Handler'


class TrimRebarWindow(Window):
    def __init__(self, uiapp):
        self.uiapp = uiapp
        self.uidoc = uiapp.ActiveUIDocument
        self.doc = self.uidoc.Document

        self._handler = _GenericHandler()
        self._event = ExternalEvent.Create(self._handler)

        self.Title = TOOL_NAME
        self.Width = 460
        self.Height = 460
        self.MinWidth = 420
        self.MinHeight = 420
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.Background = brush(CLR_BG)

        self._build_ui()
        self._refresh_selection_display()

    # -- ExternalEvent helper ------------------------------------------------
    def run_on_revit(self, callback):
        self._handler.SetCallback(callback)
        self._event.Raise()

    def _bring_to_front(self):
        self.Topmost = True
        self.Topmost = False
        self.Activate()

    def _set_status(self, msg, color=CLR_MUTED):
        self.tb_status.Text = msg
        self.tb_status.Foreground = brush(color)

    # -- UI construction ------------------------------------------------------
    def _build_ui(self):
        root = Grid()
        _row(root, 'Auto')
        _row(root, '*')
        _row(root, 'Auto')
        self.Content = root

        root.Children.Add(self._build_header())
        Grid.SetRow(root.Children[root.Children.Count - 1], 0)

        body = self._build_body()
        root.Children.Add(body)
        Grid.SetRow(body, 1)

        footer = self._build_footer()
        root.Children.Add(footer)
        Grid.SetRow(footer, 2)

    def _build_header(self):
        header = Border()
        header.Background = brush(CLR_HEADER)
        header.Padding = Thickness(16, 12, 16, 12)
        grid = Grid()
        _col(grid, '*')
        _col(grid, 'Auto')

        title_panel = StackPanel()
        title_panel.Children.Add(_text(TOOL_NAME, size=15, color=CLR_HEADER_TEXT, bold=True))
        title_panel.Children.Add(_text(
            'Shortens selected bars, keeping the shape intact',
            size=11, color=CLR_HEADER_SUB
        ))
        grid.Children.Add(title_panel)
        Grid.SetColumn(title_panel, 0)

        badge = Border()
        badge.Background = brush(Color.FromRgb(51, 65, 85))
        badge.Padding = Thickness(8, 3, 8, 3)
        badge.Child = _text(TOOL_VERSION, size=11, color=CLR_HEADER_SUB)
        grid.Children.Add(badge)
        Grid.SetColumn(badge, 1)

        header.Child = grid
        return header

    def _card(self):
        b = Border()
        b.Background = brush(CLR_CARD)
        b.BorderBrush = brush(CLR_BORDER)
        b.BorderThickness = Thickness(1)
        b.Padding = Thickness(14)
        b.Margin = Thickness(16, 12, 16, 0)
        return b

    def _build_body(self):
        outer = StackPanel()
        outer.Orientation = Orientation.Vertical

        # SELECTED BARS card
        sel_card = self._card()
        sel_panel = StackPanel()
        sel_panel.Children.Add(_text('SELECTED BARS', size=11, color=CLR_MUTED, bold=True))
        self.tb_selection = _text('No Rebar selected yet.', size=13)
        self.tb_selection.Margin = Thickness(0, 6, 0, 8)
        sel_panel.Children.Add(self.tb_selection)
        btn_refresh = _btn('Refresh Selection', CLR_CARD, CLR_ACCENT, width=160)
        btn_refresh.BorderBrush = brush(CLR_BORDER)
        btn_refresh.BorderThickness = Thickness(1)
        btn_refresh.Click += self.on_refresh_click
        sel_panel.Children.Add(btn_refresh)
        sel_card.Child = sel_panel
        outer.Children.Add(sel_card)

        # PARAMETERS card
        param_card = self._card()
        param_panel = StackPanel()
        param_panel.Children.Add(_text('PARAMETERS', size=11, color=CLR_MUTED, bold=True))

        len_row = StackPanel()
        len_row.Orientation = Orientation.Horizontal
        len_row.Margin = Thickness(0, 8, 0, 4)
        len_row.Children.Add(_text('Desired remaining length (mm): ', size=12))
        self.tb_length = TextBox()
        self.tb_length.Width = 100
        self.tb_length.Height = 26
        len_row.Children.Add(self.tb_length)
        param_panel.Children.Add(len_row)

        param_panel.Children.Add(_text(
            'Total length kept along the centerline, including any bends.',
            size=10, color=CLR_MUTED, wrap=True
        ))

        dir_label = _text('Keep which end:', size=12, bold=True)
        dir_label.Margin = Thickness(0, 12, 0, 4)
        param_panel.Children.Add(dir_label)

        self.rb_keep_top = RadioButton()
        self.rb_keep_top.Content = 'Keep TOP end (cut off the bottom)'
        self.rb_keep_top.GroupName = 'keepend'
        self.rb_keep_top.IsChecked = True
        self.rb_keep_top.Margin = Thickness(0, 2, 0, 2)
        param_panel.Children.Add(self.rb_keep_top)

        self.rb_keep_bottom = RadioButton()
        self.rb_keep_bottom.Content = 'Keep BOTTOM end (cut off the top)'
        self.rb_keep_bottom.GroupName = 'keepend'
        self.rb_keep_bottom.Margin = Thickness(0, 2, 0, 2)
        param_panel.Children.Add(self.rb_keep_bottom)

        param_panel.Children.Add(_text(
            'Top/Bottom = on-screen orientation of the active view when you '
            'click Apply (re-checked every time), not the raw draw direction.',
            size=10, color=CLR_MUTED, wrap=True
        ))

        param_card.Child = param_panel
        outer.Children.Add(param_card)

        scroll = ScrollViewer()
        scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        scroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
        scroll.Content = outer
        return scroll

    def _build_footer(self):
        footer = Border()
        footer.Background = brush(CLR_FOOTER)
        footer.Padding = Thickness(16, 10, 16, 10)
        grid = Grid()
        _col(grid, '*')
        _col(grid, 'Auto')

        self.tb_status = _text('Ready.', size=11, color=CLR_MUTED)
        self.tb_status.VerticalAlignment = VerticalAlignment.Center
        grid.Children.Add(self.tb_status)
        Grid.SetColumn(self.tb_status, 0)

        btn_panel = StackPanel()
        btn_panel.Orientation = Orientation.Horizontal

        btn_close = _btn('Close', CLR_CARD, CLR_ACCENT, width=90)
        btn_close.BorderBrush = brush(CLR_BORDER)
        btn_close.BorderThickness = Thickness(1)
        btn_close.Click += self.on_close_click
        btn_panel.Children.Add(btn_close)

        btn_apply = _btn('Apply', CLR_APPLY, CLR_APPLY_TEXT, width=110)
        btn_apply.Margin = Thickness(0)
        btn_apply.Click += self.on_apply_click
        btn_panel.Children.Add(btn_apply)

        grid.Children.Add(btn_panel)
        Grid.SetColumn(btn_panel, 1)

        footer.Child = grid
        return footer

    # -- Handlers ---------------------------------------------------------
    def on_refresh_click(self, sender, args):
        self.run_on_revit(self._do_refresh_selection)

    def _do_refresh_selection(self, uiapp):
        self._refresh_selection_display()
        self._bring_to_front()

    def _refresh_selection_display(self):
        try:
            bars = logic.get_selected_rebars(self.uidoc, self.doc)
        except Exception:
            bars = []
        if not bars:
            self.tb_selection.Text = (
                'No Rebar selected. Select bar(s) in Revit, then click Refresh Selection.'
            )
        else:
            self.tb_selection.Text = '{} Rebar bar(s) selected.'.format(len(bars))

    def on_close_click(self, sender, args):
        self.Close()

    def on_apply_click(self, sender, args):
        raw = self.tb_length.Text.strip() if self.tb_length.Text else ''
        try:
            length_mm = float(raw.replace(',', '.'))
            if length_mm <= 0:
                raise ValueError()
        except Exception:
            self._set_status('Enter a valid desired length in mm (> 0).', CLR_ERROR)
            return
        keep_top = bool(self.rb_keep_top.IsChecked)
        self._pending_length_mm = length_mm
        self._pending_keep_top = keep_top
        self.run_on_revit(self._do_apply)

    def _do_apply(self, uiapp):
        try:
            bars = logic.get_selected_rebars(self.uidoc, self.doc)
            if not bars:
                self._set_status('No Rebar selected. Select bar(s) in Revit first.', CLR_ERROR)
                self._bring_to_front()
                return

            view = self.doc.ActiveView
            view_up = logic.get_view_up_direction(view)
            if view_up is None:
                self._set_status(
                    'Cannot read the active view orientation (try a Plan/Section/'
                    'Elevation/3D view).', CLR_ERROR
                )
                self._bring_to_front()
                return

            desired_ft = logic.mm_to_ft(self._pending_length_mm)
            keep_top = self._pending_keep_top

            ok_count = 0
            fail_msgs = []

            t = Transaction(self.doc, 'pyNBT - Trim Rebar to Length')
            t.Start()
            try:
                for bar in bars:
                    success, msg = logic.process_one_rebar(
                        self.doc, bar, keep_top, desired_ft, view_up
                    )
                    if success:
                        ok_count += 1
                    else:
                        fail_msgs.append(msg)
                t.Commit()
            except Exception as ex:
                if t.HasStarted():
                    t.RollBack()
                self._set_status('Transaction failed: {}'.format(str(ex)), CLR_ERROR)
                self._bring_to_front()
                return

            total = len(bars)
            if ok_count == total:
                self._set_status(
                    'Trimmed {} of {} bar(s) successfully.'.format(ok_count, total),
                    CLR_SUCCESS
                )
            else:
                # Show a MessageBox too (not just the status line) so a
                # failed/skipped bar is impossible to miss -- the original
                # bar is safe either way (rebar_trim_logic.py rolls back
                # the delete+recreate per bar on failure).
                if ok_count == 0:
                    summary = 'All {} bar(s) failed. Nothing was changed.'.format(total)
                else:
                    summary = 'Trimmed {} of {} bar(s). {} skipped (left untouched).'.format(
                        ok_count, total, total - ok_count
                    )
                detail = '\n'.join(fail_msgs[:5])
                self._set_status(summary, CLR_ERROR)
                MessageBox.Show(
                    summary + '\n\n' + detail, TOOL_NAME,
                    MessageBoxButton.OK, MessageBoxImage.Warning
                )

            self._refresh_selection_display()
            self._bring_to_front()
        except OperationCanceledException:
            pass
        except Exception as ex:
            self._set_status('Unexpected error: {}'.format(str(ex)), CLR_ERROR)
            self._bring_to_front()


def _main():
    uiapp = __revit__
    window = TrimRebarWindow(uiapp)
    window.Show()


_main()
