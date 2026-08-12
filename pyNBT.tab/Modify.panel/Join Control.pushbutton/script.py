# -*- coding: utf-8 -*-
"""pyNBT - Join Control

Batch Disallow Join / Allow Join for Structural Framing (beams/braces),
Structural Columns, and Walls.

Scope: Active View (all supported elements visible in the current view)
       or Current Selection (supported elements already picked in Revit).

pyNBT Core Tools phase.
"""

import os
import sys

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System.Collections.Generic import List
from System.Windows import (
    Window, WindowStartupLocation, ResizeMode, Thickness,
    HorizontalAlignment, VerticalAlignment, FontWeights, TextWrapping,
    GridLength, GridUnitType
)
from System.Windows.Controls import (
    Grid, RowDefinition, ColumnDefinition, StackPanel, Orientation,
    TextBlock, Border, Button, RadioButton
)

from pyrevit import revit, forms, script

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ElementMulticategoryFilter,
    BuiltInCategory,
    Category,
    Transaction,
    Wall,
    WallUtils,
    FamilyInstance,
)
from Autodesk.Revit.DB.Structure import StructuralFramingUtils


# ---------------------------------------------------------------------------
# Locate pyNBT shared lib (lib/pyNBT package) regardless of nesting depth,
# so this tool keeps working even if the extension folder gets restructured.
# ---------------------------------------------------------------------------
def _find_extension_lib():
    path = os.path.dirname(__file__)
    for _ in range(8):
        if path.lower().endswith('.extension'):
            lib_path = os.path.join(path, 'lib')
            return lib_path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None


_LIB_PATH = _find_extension_lib()
if _LIB_PATH and _LIB_PATH not in sys.path:
    sys.path.append(_LIB_PATH)

from pyNBT.compat import eid_int  # noqa: E402
from pyNBT import theme  # noqa: E402


TOOL_NAME = "Join Control"
TOOL_VERSION = "1.0"

TARGET_BICS = (
    BuiltInCategory.OST_StructuralFraming,
    BuiltInCategory.OST_StructuralColumns,
    BuiltInCategory.OST_Walls,
)
FRAMING_COLUMN_BICS = (
    BuiltInCategory.OST_StructuralFraming,
    BuiltInCategory.OST_StructuralColumns,
)


# ---------------------------------------------------------------------------
# Standalone logic (no UI references) - pure Revit API work
# ---------------------------------------------------------------------------
def _target_category_ids(doc, bics):
    ids = set()
    for bic in bics:
        cat = Category.GetCategory(doc, bic)
        if cat is not None:
            ids.add(eid_int(cat.Id))
    return ids


def collect_active_view_elements(doc, view):
    """All Structural Framing / Structural Column / Wall elements visible
    in the given view (model elements only, not element types)."""
    bic_list = List[BuiltInCategory](list(TARGET_BICS))
    mcf = ElementMulticategoryFilter(bic_list)
    return list(
        FilteredElementCollector(doc, view.Id)
        .WherePasses(mcf)
        .WhereElementIsNotElementType()
        .ToElements()
    )


def collect_selection_elements(doc, uidoc):
    """Elements from the current selection that are Structural Framing,
    Structural Columns, or Walls. Anything else in the selection is ignored."""
    target_ids = _target_category_ids(doc, TARGET_BICS)
    result = []
    for eid in uidoc.Selection.GetElementIds():
        el = doc.GetElement(eid)
        if el is None or el.Category is None:
            continue
        if eid_int(el.Category.Id) in target_ids:
            result.append(el)
    return result


def apply_join_state(doc, elements, disallow):
    """Set Disallow Join (disallow=True) or Allow Join (disallow=False) on
    every element in `elements`.

    - Wall: WallUtils.*JoinAtEnd on both ends (0, 1).
    - Structural Framing / Structural Column (FamilyInstance):
      StructuralFramingUtils.*JoinAtEnd on both ends (0, 1). Some instances
      (e.g. no analytical model, in-place families) may not support this on
      one or both ends - those ends are skipped silently, the element itself
      is only counted as skipped if NEITHER end could be set.

    Returns (changed_count, skipped_count, skip_log) where skip_log is a
    list of short strings for the pyRevit output console (no popup).
    """
    framing_column_ids = _target_category_ids(doc, FRAMING_COLUMN_BICS)

    changed = 0
    skipped = 0
    skip_log = []

    for el in elements:
        try:
            if isinstance(el, Wall):
                for end in (0, 1):
                    allowed = WallUtils.IsWallJoinAllowedAtEnd(el, end)
                    if disallow and allowed:
                        WallUtils.DisallowWallJoinAtEnd(el, end)
                    elif (not disallow) and (not allowed):
                        WallUtils.AllowWallJoinAtEnd(el, end)
                changed += 1

            elif isinstance(el, FamilyInstance) and el.Category is not None \
                    and eid_int(el.Category.Id) in framing_column_ids:
                any_end_ok = False
                for end in (0, 1):
                    try:
                        allowed = StructuralFramingUtils.IsJoinAllowedAtEnd(el, end)
                        if disallow and allowed:
                            StructuralFramingUtils.DisallowJoinAtEnd(el, end)
                        elif (not disallow) and (not allowed):
                            StructuralFramingUtils.AllowJoinAtEnd(el, end)
                        any_end_ok = True
                    except Exception:
                        # this end has no analytical model / not applicable
                        continue
                if any_end_ok:
                    changed += 1
                else:
                    skipped += 1
                    skip_log.append(
                        "Id {}: no joinable end available (no analytical model)".format(eid_int(el.Id))
                    )
            else:
                skipped += 1
                skip_log.append("Id {}: unsupported element (not Framing/Column/Wall)".format(eid_int(el.Id)))

        except Exception as ex:
            skipped += 1
            skip_log.append("Id {}: {}".format(eid_int(el.Id), str(ex)))

    return changed, skipped, skip_log


# ---------------------------------------------------------------------------
# UI class - only orchestrates: reads UI state, calls logic functions above
# ---------------------------------------------------------------------------
class JoinControlWindow(Window):

    def __init__(self, uidoc, doc, active_view):
        self.uidoc = uidoc
        self.doc = doc
        self.active_view = active_view

        self._view_elements = collect_active_view_elements(doc, active_view)
        self._selection_elements = collect_selection_elements(doc, uidoc)

        self.Title = "pyNBT - {}".format(TOOL_NAME)
        self.Width = 430
        self.Height = 430
        self.ResizeMode = ResizeMode.NoResize
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.Background = theme.brush(theme.CLR_BG)

        root = Grid()
        root.RowDefinitions.Add(RowDefinition(Height=GridLength(64)))
        root.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Star)))
        root.RowDefinitions.Add(RowDefinition(Height=GridLength(64)))

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

    # -- UI building blocks --------------------------------------------
    def _build_header(self):
        border = Border()
        border.Background = theme.brush(theme.CLR_HEADER)

        grid = Grid()
        grid.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(1, GridUnitType.Star)))
        grid.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength.Auto))

        text_stack = StackPanel()
        text_stack.Orientation = Orientation.Vertical
        text_stack.VerticalAlignment = VerticalAlignment.Center
        text_stack.Margin = Thickness(16, 0, 0, 0)

        title = TextBlock()
        title.Text = TOOL_NAME
        title.FontSize = 16
        title.FontWeight = FontWeights.Bold
        title.Foreground = theme.brush(theme.CLR_HEADER_TEXT)

        subtitle = TextBlock()
        subtitle.Text = "Disallow / Allow Join - Framing, Columns, Walls"
        subtitle.FontSize = 11
        subtitle.Foreground = theme.brush(theme.CLR_HEADER_SUB)

        text_stack.Children.Add(title)
        text_stack.Children.Add(subtitle)

        badge = TextBlock()
        badge.Text = "pyNBT v{}".format(TOOL_VERSION)
        badge.FontSize = 10
        badge.Foreground = theme.brush(theme.CLR_HEADER_SUB)
        badge.VerticalAlignment = VerticalAlignment.Center
        badge.Margin = Thickness(0, 0, 16, 0)

        Grid.SetColumn(text_stack, 0)
        Grid.SetColumn(badge, 1)
        grid.Children.Add(text_stack)
        grid.Children.Add(badge)

        border.Child = grid
        return border

    def _build_content(self):
        outer = Border()
        outer.Background = theme.brush(theme.CLR_CARD)
        outer.BorderBrush = theme.brush(theme.CLR_BORDER)
        outer.BorderThickness = Thickness(1)
        outer.Margin = Thickness(16)
        outer.Padding = Thickness(16)

        stack = StackPanel()
        stack.Orientation = Orientation.Vertical

        # -- Scope group --
        scope_label = self._section_label("Scope")
        stack.Children.Add(scope_label)

        view_count = len(self._view_elements)
        sel_count = len(self._selection_elements)

        self.rb_scope_view = RadioButton()
        self.rb_scope_view.GroupName = "scope"
        self.rb_scope_view.Content = "Active View ({} elements)".format(view_count)
        self.rb_scope_view.Margin = Thickness(0, 4, 0, 4)
        self.rb_scope_view.FontSize = 12

        self.rb_scope_selection = RadioButton()
        self.rb_scope_selection.GroupName = "scope"
        self.rb_scope_selection.Content = "Current Selection ({} elements)".format(sel_count)
        self.rb_scope_selection.Margin = Thickness(0, 4, 0, 4)
        self.rb_scope_selection.FontSize = 12

        # default: Selection if something supported is already selected, else Active View
        if sel_count > 0:
            self.rb_scope_selection.IsChecked = True
        else:
            self.rb_scope_view.IsChecked = True

        stack.Children.Add(self.rb_scope_view)
        stack.Children.Add(self.rb_scope_selection)

        spacer = TextBlock()
        spacer.Height = 12
        stack.Children.Add(spacer)

        # -- Action group --
        action_label = self._section_label("Action")
        stack.Children.Add(action_label)

        self.rb_action_disallow = RadioButton()
        self.rb_action_disallow.GroupName = "action"
        self.rb_action_disallow.Content = "Disallow Join"
        self.rb_action_disallow.Margin = Thickness(0, 4, 0, 4)
        self.rb_action_disallow.FontSize = 12
        self.rb_action_disallow.IsChecked = True

        self.rb_action_allow = RadioButton()
        self.rb_action_allow.GroupName = "action"
        self.rb_action_allow.Content = "Allow Join"
        self.rb_action_allow.Margin = Thickness(0, 4, 0, 4)
        self.rb_action_allow.FontSize = 12

        stack.Children.Add(self.rb_action_disallow)
        stack.Children.Add(self.rb_action_allow)

        spacer2 = TextBlock()
        spacer2.Height = 16
        stack.Children.Add(spacer2)

        info = TextBlock()
        info.Text = "Applies to: Structural Framing, Structural Columns, Walls."
        info.FontSize = 10
        info.Foreground = theme.brush(theme.CLR_MUTED)
        info.TextWrapping = TextWrapping.Wrap
        stack.Children.Add(info)

        outer.Child = stack
        return outer

    def _section_label(self, text):
        label = TextBlock()
        label.Text = text
        label.FontSize = 12
        label.FontWeight = FontWeights.Bold
        label.Foreground = theme.brush(theme.CLR_TEXT)
        label.Margin = Thickness(0, 0, 0, 4)
        return label

    def _build_footer(self):
        border = Border()
        border.Background = theme.brush(theme.CLR_FOOTER)

        grid = Grid()
        grid.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(1, GridUnitType.Star)))
        grid.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength.Auto))

        sig = TextBlock()
        sig.Text = "{} v{} | {}".format(TOOL_NAME, TOOL_VERSION, self.doc.Title)
        sig.FontSize = 9
        sig.Foreground = theme.brush(theme.CLR_MUTED)
        sig.VerticalAlignment = VerticalAlignment.Center
        sig.Margin = Thickness(16, 0, 0, 0)

        btn_stack = StackPanel()
        btn_stack.Orientation = Orientation.Horizontal
        btn_stack.Margin = Thickness(0, 0, 16, 0)

        close_btn = self._make_button("Close", theme.CLR_CARD, theme.CLR_TEXT, is_primary=False)
        close_btn.Click += self.on_close
        close_btn.Margin = Thickness(0, 0, 8, 0)

        run_btn = self._make_button("Run", theme.CLR_APPLY, theme.CLR_APPLY_TEXT, is_primary=True)
        run_btn.Click += self.on_run

        btn_stack.Children.Add(close_btn)
        btn_stack.Children.Add(run_btn)

        Grid.SetColumn(sig, 0)
        Grid.SetColumn(btn_stack, 1)
        grid.Children.Add(sig)
        grid.Children.Add(btn_stack)

        border.Child = grid
        return border

    def _make_button(self, text, bg_color, fg_color, is_primary):
        btn = Button()
        btn.Content = text
        btn.Background = theme.brush(bg_color)
        btn.Foreground = theme.brush(fg_color)
        btn.BorderBrush = theme.brush(theme.CLR_BORDER)
        btn.Padding = Thickness(18, 6, 18, 6)
        btn.FontWeight = FontWeights.Bold if is_primary else FontWeights.Normal
        return btn

    # -- Event handlers ---------------------------------------------------
    def on_close(self, sender, args):
        self.Close()

    def on_run(self, sender, args):
        use_selection = bool(self.rb_scope_selection.IsChecked)
        disallow = bool(self.rb_action_disallow.IsChecked)

        elements = self._selection_elements if use_selection else self._view_elements

        if not elements:
            forms.alert(
                "No Structural Framing, Structural Column, or Wall elements found in the chosen scope.",
                title=TOOL_NAME,
            )
            return

        t = Transaction(self.doc, "pyNBT - {} ({})".format(TOOL_NAME, "Disallow Join" if disallow else "Allow Join"))
        t.Start()
        try:
            changed, skipped, skip_log = apply_join_state(self.doc, elements, disallow)
            t.Commit()
        except Exception as ex:
            if t.HasStarted():
                t.RollBack()
            forms.alert("Error: {}".format(str(ex)), title=TOOL_NAME)
            return

        if skip_log:
            out = script.get_output()
            out.print_md("**[pyNBT] {} - {} skipped:**".format(TOOL_NAME, skipped))
            for line in skip_log:
                out.print_md("- {}".format(line))

        self.Close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    uidoc = revit.uidoc
    doc = revit.doc
    active_view = doc.ActiveView

    if active_view is None:
        forms.alert("No active view.", title=TOOL_NAME)
        return

    window = JoinControlWindow(uidoc, doc, active_view)
    window.ShowDialog()


if __name__ == '__main__':
    main()
