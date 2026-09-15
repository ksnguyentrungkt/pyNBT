# -*- coding: utf-8 -*-
"""pyNBT - Select By Family

Groups the CURRENT SELECTION (elements Trung already picked/box-selected
in Revit before running this tool) by their Family Name, shows a count
per Family, and lets Trung tick one or more Family names to re-select
only those elements in Revit.

Each Family row can be expanded (small arrow toggle on the left) to
reveal its Types underneath, each with its own checkbox and count -
this lets Trung target a specific Type inside a Family instead of the
whole Family when a Family has many different Types mixed together.

The Family checkbox and its Type checkboxes stay in sync both ways:
- Ticking the Family checkbox ticks every Type underneath it.
- Unticking it unticks every Type underneath it.
- Ticking/unticking individual Types updates the Family checkbox to
  fully-ticked, empty, or a third "partial" state (dash) when only
  some of its Types are ticked - so Trung can see at a glance whether
  a whole Family or only part of it is selected, without having to
  expand every row.

Requires an existing selection when the tool is launched - if nothing is
selected, it alerts and exits instead of opening an empty window.

Runs as a MODELESS window (Show(), not ShowDialog()) so Trung can keep
working in Revit without closing this tool first. Because a modeless
pyRevit window's button clicks fire outside Revit's normal API context,
every Revit API call triggered from a button (Select, Refresh, Show in
View) is routed through an IExternalEventHandler / ExternalEvent pair -
this is the standard, required pattern for modeless Revit add-in UIs
(mirrors pyNBT's existing Select Partition tool).
"""

# Tell pyRevit to keep this script's engine (and therefore all the
# module-level imports below - forms, script, List, ElementId, ...)
# alive for as long as the window stays open. Without this, pyRevit
# recycles the engine right after the initial run finishes (i.e. right
# after window.Show()), so anything triggered later - a button click, an
# ExternalEvent.Execute callback - would find every top-level import
# gone ("name 'forms' is not defined", "name 'output' is not defined",
# etc.). Required for every pyNBT tool that uses a modeless window.
__persistentengine__ = True

__title__ = 'Select\nBy Family'
__author__ = 'NBT'

import traceback
from collections import OrderedDict

from pyrevit import forms, revit, script

from System.Collections.Generic import List
from System.Windows import (
    FontStyles,
    FontWeights,
    GridLength,
    GridUnitType,
    HorizontalAlignment,
    TextTrimming,
    Thickness,
    VerticalAlignment,
    Visibility,
)
from System.Windows.Controls import (
    Border,
    Button,
    CheckBox,
    ColumnDefinition,
    Grid,
    StackPanel,
    TextBlock,
)
from System.Windows.Input import Cursors
from System.Windows.Media import Color, SolidColorBrush

from Autodesk.Revit.DB import Element, ElementId
from Autodesk.Revit.UI import ExternalEvent, IExternalEventHandler

doc = revit.doc
uidoc = revit.uidoc

TOOL_NAME = 'Select By Family'
NO_FAMILY_LABEL = '(No Family)'
NO_TYPE_LABEL = '(No Type)'
EXPAND_COLLAPSED = u'\u25B8'  # small right-pointing triangle
EXPAND_EXPANDED = u'\u25BE'  # small down-pointing triangle

# pyNBT theme brushes (Navy + Gray/White/Black palette) - same values as
# lib/pyNBT/theme.py, inlined here to match Select Partition's existing
# pattern of not depending on the shared lib for a handful of brushes.
BRUSH_MUTED = SolidColorBrush(Color.FromRgb(120, 120, 120))
BRUSH_GRAY_TEXT = SolidColorBrush(Color.FromRgb(75, 85, 99))
BRUSH_ROW_BORDER = SolidColorBrush(Color.FromRgb(228, 231, 236))
BRUSH_TRANSPARENT = SolidColorBrush(Color.FromArgb(0, 0, 0, 0))
BRUSH_TYPE_BG = SolidColorBrush(Color.FromRgb(250, 251, 252))
BRUSH_NAVY = SolidColorBrush(Color.FromRgb(30, 41, 59))


# ---------------------------------------------------------------------------
# Standalone Revit-logic functions (no UI references - safe to unit test /
# reuse from other pyNBT tools later)
# ---------------------------------------------------------------------------

def get_family_name(document, element):
    """Return the Family name of an element's type (works for both
    component families - FamilyInstance - and system families like
    Basic Wall, via the generic ElementType.FamilyName property), or
    None if the element has no type or no Family name."""
    try:
        type_id = element.GetTypeId()
        if type_id is None or type_id == ElementId.InvalidElementId:
            return None
        el_type = document.GetElement(type_id)
        if el_type is None:
            return None
        name = getattr(el_type, 'FamilyName', None)
        if name and name.strip():
            return name.strip()
        return None
    except Exception:
        return None


def get_type_name(document, element):
    """Return the Type name of an element (e.g. 'UB305x165x40' inside
    Family 'UB-Universal Beam'), or None if it has no resolvable type
    name. Reads via Element.Name.GetValue(...) rather than the plain
    .Name attribute - required for ElementType objects in this
    IronPython engine (direct attribute read throws for some types)."""
    try:
        type_id = element.GetTypeId()
        if type_id is None or type_id == ElementId.InvalidElementId:
            return None
        el_type = document.GetElement(type_id)
        if el_type is None:
            return None
        try:
            name = Element.Name.GetValue(el_type)
        except Exception:
            name = getattr(el_type, 'Name', None)
        if name and name.strip():
            return name.strip()
        return None
    except Exception:
        return None


def collect_family_groups(document, element_ids):
    """Group the given ElementIds by Family name, then by Type name
    within each Family.

    Returns a nested OrderedDict:
        family_name -> OrderedDict(type_name -> list[ElementId])
    Families are sorted A-Z with '(No Family)' always last; within each
    Family, Types are sorted A-Z with '(No Type)' always last.
    """
    raw = {}
    for eid in element_ids:
        try:
            el = document.GetElement(eid)
            if el is None:
                continue
            fam_name = get_family_name(document, el)
            fam_key = fam_name if fam_name else NO_FAMILY_LABEL
            type_name = get_type_name(document, el)
            type_key = type_name if type_name else NO_TYPE_LABEL
            raw.setdefault(fam_key, {}).setdefault(type_key, []).append(eid)
        except Exception:
            continue

    fam_keys = sorted(k for k in raw if k != NO_FAMILY_LABEL)
    if NO_FAMILY_LABEL in raw:
        fam_keys.append(NO_FAMILY_LABEL)

    ordered = OrderedDict()
    for fam_key in fam_keys:
        type_map = raw[fam_key]
        type_keys = sorted(k for k in type_map if k != NO_TYPE_LABEL)
        if NO_TYPE_LABEL in type_map:
            type_keys.append(NO_TYPE_LABEL)
        ordered_types = OrderedDict()
        for type_key in type_keys:
            ordered_types[type_key] = type_map[type_key]
        ordered[fam_key] = ordered_types
    return ordered


# ---------------------------------------------------------------------------
# External event plumbing - required so the modeless window can still call
# the Revit API safely (Select / Refresh / Show in View), without blocking
# Revit and without forcing the window to close first.
# ---------------------------------------------------------------------------

class GenericEventHandler(IExternalEventHandler):
    """Runs a single pending python callable inside a valid Revit API
    context, triggered from the modeless window via ExternalEvent.Raise().
    """

    def __init__(self):
        self.action = None

    def Execute(self, uiapp):
        try:
            if self.action:
                self.action(uiapp)
        except Exception as ex:
            print("pyNBT Select By Family - external event error: {}".format(ex))
        finally:
            self.action = None

    def GetName(self):
        return "pyNBT Select By Family - External Event Handler"


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class SelectByFamilyWindow(forms.WPFWindow):
    def __init__(self, xaml_file, initial_ids):
        forms.WPFWindow.__init__(self, xaml_file)

        self.handler = GenericEventHandler()
        self.ext_event = ExternalEvent.Create(self.handler)

        # name -> CheckBox (Family-level, tri-state)
        self.family_checkboxes = {}
        # (family_name, type_name) -> CheckBox (Type-level)
        self.type_checkboxes = {}
        # name -> Border (Family header row, for search visibility)
        self.family_borders = {}
        # name -> {type_name: Border} (Type rows, for search visibility)
        self.type_rows = {}
        # name -> StackPanel holding that Family's Type rows (collapsible)
        self.family_body_panels = {}
        # name -> Button (expand/collapse arrow, to flip its glyph)
        self.family_toggle_buttons = {}
        # name -> bool, current expand state (default collapsed)
        self.expanded_state = {}
        # guard to stop programmatic checkbox updates re-triggering
        # the Family<->Type sync handlers (would otherwise recurse)
        self._suspend_sync = False

        self.groups = OrderedDict()

        # Initial grouping runs synchronously - the script entry point
        # already executes inside a valid Revit API context, so no
        # ExternalEvent is needed here (only later, button-triggered
        # calls need it).
        self.load_groups(doc, initial_ids)

    # -- data / row building -------------------------------------------

    def load_groups(self, document, element_ids):
        self.groups = collect_family_groups(document, element_ids)
        self.rebuild_rows()

    def rebuild_rows(self):
        self.panelList.Children.Clear()
        self.family_checkboxes = {}
        self.type_checkboxes = {}
        self.family_borders = {}
        self.type_rows = {}
        self.family_body_panels = {}
        self.family_toggle_buttons = {}
        self.expanded_state = {}

        for family_name, type_map in self.groups.items():
            block = self.build_family_block(family_name, type_map)
            self.panelList.Children.Add(block)

        self.update_status()

    def build_family_block(self, family_name, type_map):
        outer = StackPanel()

        header = self.make_family_row(family_name, type_map)
        outer.Children.Add(header)

        body = StackPanel()
        body.Visibility = Visibility.Collapsed
        self.type_rows[family_name] = {}
        for type_name, ids in type_map.items():
            row = self.make_type_row(family_name, type_name, len(ids))
            body.Children.Add(row)
            self.type_rows[family_name][type_name] = row
        outer.Children.Add(body)

        self.family_body_panels[family_name] = body
        self.expanded_state[family_name] = False
        return outer

    def make_family_row(self, name, type_map):
        total = sum(len(ids) for ids in type_map.values())

        border = Border()
        border.Padding = Thickness(12, 9, 20, 9)
        border.BorderBrush = BRUSH_ROW_BORDER
        border.BorderThickness = Thickness(0, 0, 0, 1)
        border.Background = BRUSH_TRANSPARENT

        grid = Grid()
        col0 = ColumnDefinition()
        col0.Width = GridLength(24)
        col1 = ColumnDefinition()
        col1.Width = GridLength(34)
        col2 = ColumnDefinition()
        col2.Width = GridLength(1, GridUnitType.Star)
        col3 = ColumnDefinition()
        col3.Width = GridLength(64)
        grid.ColumnDefinitions.Add(col0)
        grid.ColumnDefinitions.Add(col1)
        grid.ColumnDefinitions.Add(col2)
        grid.ColumnDefinitions.Add(col3)

        toggle = Button()
        toggle.Content = EXPAND_COLLAPSED
        toggle.Tag = name
        toggle.Width = 22
        toggle.Height = 22
        toggle.Padding = Thickness(0)
        toggle.FontSize = 10
        toggle.Background = BRUSH_TRANSPARENT
        toggle.BorderThickness = Thickness(0)
        toggle.Foreground = BRUSH_GRAY_TEXT
        toggle.Cursor = Cursors.Hand
        toggle.HorizontalAlignment = HorizontalAlignment.Center
        toggle.VerticalAlignment = VerticalAlignment.Center
        toggle.Click += self.on_toggle_expand
        Grid.SetColumn(toggle, 0)

        chk = CheckBox()
        chk.VerticalAlignment = VerticalAlignment.Center
        chk.Tag = name
        chk.IsThreeState = True
        chk.Cursor = Cursors.Hand
        chk.Checked += self.on_family_checked
        chk.Unchecked += self.on_family_unchecked
        chk.Indeterminate += self.on_family_indeterminate
        Grid.SetColumn(chk, 1)

        txt_name = TextBlock()
        txt_name.Text = name
        txt_name.FontSize = 13
        txt_name.FontWeight = FontWeights.SemiBold
        txt_name.VerticalAlignment = VerticalAlignment.Center
        txt_name.TextTrimming = TextTrimming.CharacterEllipsis
        if name == NO_FAMILY_LABEL:
            txt_name.FontStyle = FontStyles.Italic
            txt_name.FontWeight = FontWeights.Normal
            txt_name.Foreground = BRUSH_MUTED
        Grid.SetColumn(txt_name, 2)

        txt_count = TextBlock()
        txt_count.Text = str(total)
        txt_count.FontSize = 12.5
        txt_count.HorizontalAlignment = HorizontalAlignment.Right
        txt_count.VerticalAlignment = VerticalAlignment.Center
        txt_count.Foreground = BRUSH_GRAY_TEXT
        Grid.SetColumn(txt_count, 3)

        grid.Children.Add(toggle)
        grid.Children.Add(chk)
        grid.Children.Add(txt_name)
        grid.Children.Add(txt_count)
        border.Child = grid

        self.family_checkboxes[name] = chk
        self.family_borders[name] = border
        self.family_toggle_buttons[name] = toggle
        return border

    def make_type_row(self, family_name, type_name, count):
        border = Border()
        border.Padding = Thickness(46, 7, 20, 7)
        border.BorderBrush = BRUSH_ROW_BORDER
        border.BorderThickness = Thickness(0, 0, 0, 1)
        border.Background = BRUSH_TYPE_BG

        grid = Grid()
        col0 = ColumnDefinition()
        col0.Width = GridLength(34)
        col1 = ColumnDefinition()
        col1.Width = GridLength(1, GridUnitType.Star)
        col2 = ColumnDefinition()
        col2.Width = GridLength(64)
        grid.ColumnDefinitions.Add(col0)
        grid.ColumnDefinitions.Add(col1)
        grid.ColumnDefinitions.Add(col2)

        chk = CheckBox()
        chk.VerticalAlignment = VerticalAlignment.Center
        chk.Tag = (family_name, type_name)
        chk.Cursor = Cursors.Hand
        chk.Checked += self.on_type_check_changed
        chk.Unchecked += self.on_type_check_changed
        Grid.SetColumn(chk, 0)

        txt_name = TextBlock()
        txt_name.Text = type_name
        txt_name.FontSize = 12
        txt_name.VerticalAlignment = VerticalAlignment.Center
        txt_name.TextTrimming = TextTrimming.CharacterEllipsis
        txt_name.Foreground = BRUSH_GRAY_TEXT
        if type_name == NO_TYPE_LABEL:
            txt_name.FontStyle = FontStyles.Italic
            txt_name.Foreground = BRUSH_MUTED
        Grid.SetColumn(txt_name, 1)

        txt_count = TextBlock()
        txt_count.Text = str(count)
        txt_count.FontSize = 11.5
        txt_count.HorizontalAlignment = HorizontalAlignment.Right
        txt_count.VerticalAlignment = VerticalAlignment.Center
        txt_count.Foreground = BRUSH_MUTED
        Grid.SetColumn(txt_count, 2)

        grid.Children.Add(chk)
        grid.Children.Add(txt_name)
        grid.Children.Add(txt_count)
        border.Child = grid

        self.type_checkboxes[(family_name, type_name)] = chk
        return border

    # -- expand / collapse ------------------------------------------------

    def on_toggle_expand(self, sender, args):
        name = sender.Tag
        self.set_expanded(name, not self.expanded_state.get(name, False))

    def set_expanded(self, name, expanded):
        self.expanded_state[name] = expanded
        self.family_body_panels[name].Visibility = (
            Visibility.Visible if expanded else Visibility.Collapsed
        )
        self.family_toggle_buttons[name].Content = (
            EXPAND_EXPANDED if expanded else EXPAND_COLLAPSED
        )

    # -- Family <-> Type checkbox sync ------------------------------------

    def set_all_types_checked(self, family_name, value):
        self._suspend_sync = True
        try:
            for (fam, _type_name), chk in self.type_checkboxes.items():
                if fam == family_name:
                    chk.IsChecked = value
        finally:
            self._suspend_sync = False

    def sync_family_state(self, family_name):
        type_chks = [
            chk for (fam, _t), chk in self.type_checkboxes.items()
            if fam == family_name
        ]
        if not type_chks:
            return
        checked_count = sum(1 for c in type_chks if c.IsChecked)
        total = len(type_chks)

        fam_chk = self.family_checkboxes.get(family_name)
        if fam_chk is None:
            return
        self._suspend_sync = True
        try:
            if checked_count == 0:
                fam_chk.IsChecked = False
            elif checked_count == total:
                fam_chk.IsChecked = True
            else:
                fam_chk.IsChecked = None
        finally:
            self._suspend_sync = False

    def on_family_checked(self, sender, args):
        if self._suspend_sync:
            return
        self.set_all_types_checked(sender.Tag, True)
        self.update_status()

    def on_family_unchecked(self, sender, args):
        if self._suspend_sync:
            return
        self.set_all_types_checked(sender.Tag, False)
        self.update_status()

    def on_family_indeterminate(self, sender, args):
        # IsThreeState lets us SHOW an indeterminate ("partial") state,
        # but Trung should never be able to CLICK his way into one -
        # WPF's default 3-state click cycle is
        # Unchecked -> Checked -> Indeterminate -> Unchecked, so a user
        # click that lands here is really a 3rd click that should just
        # complete the cycle back to fully unchecked. Programmatic
        # updates from sync_family_state() are guarded above and never
        # reach this branch.
        if self._suspend_sync:
            return
        name = sender.Tag
        self._suspend_sync = True
        try:
            sender.IsChecked = False
        finally:
            self._suspend_sync = False
        self.set_all_types_checked(name, False)
        self.update_status()

    def on_type_check_changed(self, sender, args):
        if self._suspend_sync:
            return
        family_name, _type_name = sender.Tag
        self.sync_family_state(family_name)
        self.update_status()

    # -- helpers ---------------------------------------------------------

    def get_checked_ids(self):
        ids = []
        for (fam, type_name), chk in self.type_checkboxes.items():
            if chk.IsChecked:
                ids.extend(self.groups.get(fam, {}).get(type_name, []))
        return ids

    def get_checked_family_names(self):
        names = set()
        for (fam, _type_name), chk in self.type_checkboxes.items():
            if chk.IsChecked:
                names.add(fam)
        return names

    def set_status_idle(self):
        total = sum(
            len(ids) for type_map in self.groups.values() for ids in type_map.values()
        )
        self.txtStatus.Text = "Ready - {} element(s) in selection, 0 checked".format(total)

    def update_status(self):
        names = self.get_checked_family_names()
        if not names:
            self.set_status_idle()
            return
        total = len(self.get_checked_ids())
        self.txtStatus.Text = "{} family(ies) checked - {} elements ready to select".format(
            len(names), total
        )

    # -- event handlers (wired from ui.xaml Click / TextChanged attrs) ---

    def OnSearchChanged(self, sender, args):
        term = (self.txtSearch.Text or "").strip().lower()

        for family_name, family_border in self.family_borders.items():
            type_map = self.type_rows.get(family_name, {})
            family_match = (not term) or (term in family_name.lower())
            matching_types = [
                t for t in type_map if term in t.lower()
            ] if term else list(type_map.keys())

            show_family = family_match or bool(matching_types)
            family_border.Visibility = (
                Visibility.Visible if show_family else Visibility.Collapsed
            )
            if not show_family:
                continue

            for type_name, row in type_map.items():
                show_type = family_match or (type_name in matching_types)
                row.Visibility = Visibility.Visible if show_type else Visibility.Collapsed

            # Auto-expand a Family when the match is only on one of its
            # Types (not on the Family name itself) so the hit is
            # actually visible without an extra click.
            if term and not family_match and matching_types:
                self.set_expanded(family_name, True)

    def OnSelectAll(self, sender, args):
        term = (self.txtSearch.Text or "").strip().lower()
        for family_name, fam_chk in self.family_checkboxes.items():
            type_names = list(self.groups.get(family_name, {}).keys())
            family_match = (not term) or (term in family_name.lower())
            if family_match:
                fam_chk.IsChecked = True
                continue
            for type_name in type_names:
                if term in type_name.lower():
                    self.type_checkboxes[(family_name, type_name)].IsChecked = True
        self.update_status()

    def OnSelectNone(self, sender, args):
        for chk in self.family_checkboxes.values():
            chk.IsChecked = False
        self.update_status()

    def report_error(self, title, ex):
        """Show the exception to Trung (alert) AND print the full
        traceback to the pyRevit output window, since silent console
        prints from inside an ExternalEvent are easy to miss.

        The output-window logging is best-effort and wrapped on its own
        so that if IT fails for any reason, we still always show the
        alert with the real, original error message - a secondary
        logging failure must never hide the primary error.
        """
        self.txtStatus.Text = "Error - see popup for details"
        try:
            script.get_output().print_md(
                "**{}**\n\n```\n{}\n```".format(title, traceback.format_exc())
            )
        except Exception:
            pass
        forms.alert("{}\n\n{}".format(title, ex), title=TOOL_NAME)

    @staticmethod
    def valid_ids(document, ids):
        """Filter out ElementIds that no longer resolve to a real element
        (stale/deleted) - Selection.SetElementIds throws for the WHOLE
        batch if even one id is invalid, so a single stale id would
        otherwise silently block selecting everything else."""
        good = List[ElementId]()
        for eid in ids:
            try:
                el = document.GetElement(eid)
                if el is not None:
                    good.Add(eid)
            except Exception:
                continue
        return good

    def OnRefresh(self, sender, args):
        self.txtStatus.Text = "Refreshing from current Revit selection..."
        win = self

        def do_refresh(uiapp):
            try:
                ui_document = uiapp.ActiveUIDocument
                current_ids = list(ui_document.Selection.GetElementIds())
                if not current_ids:
                    win.report_error(
                        "Refresh failed",
                        "Nothing is selected in Revit right now. Select "
                        "elements first, then click Refresh again.",
                    )
                    return
                win.load_groups(ui_document.Document, current_ids)
                win.txtStatus.Text = "Refreshed from current Revit selection"
            except Exception as ex:
                win.report_error("Refresh failed", ex)

        self.handler.action = do_refresh
        self.ext_event.Raise()

    def OnSelectInRevit(self, sender, args):
        ids = self.get_checked_ids()
        if not ids:
            forms.alert(
                "Please tick at least one Family or Type first.",
                title=TOOL_NAME,
            )
            return

        self.txtStatus.Text = "Selecting..."
        win = self

        def do_select(uiapp):
            try:
                ui_document = uiapp.ActiveUIDocument
                id_list = win.valid_ids(ui_document.Document, ids)
                if id_list.Count == 0:
                    win.report_error(
                        "Select failed",
                        "None of the {} element(s) in the ticked Family/Type(s) "
                        "could be found in the current model (stale list - "
                        "try Refresh).".format(len(ids)),
                    )
                    return
                ui_document.Selection.SetElementIds(id_list)
                skipped = len(ids) - id_list.Count
                msg = "Selected: {} elements across {} family(ies)".format(
                    id_list.Count, len(win.get_checked_family_names())
                )
                if skipped:
                    msg += " ({} stale id(s) skipped)".format(skipped)
                win.txtStatus.Text = msg
            except Exception as ex:
                win.report_error("Select failed", ex)

        self.handler.action = do_select
        self.ext_event.Raise()

    def OnShowInView(self, sender, args):
        ids = self.get_checked_ids()
        if not ids:
            forms.alert(
                "Please tick at least one Family or Type first.",
                title=TOOL_NAME,
            )
            return

        self.txtStatus.Text = "Showing in view..."
        win = self

        def do_show(uiapp):
            try:
                ui_document = uiapp.ActiveUIDocument
                id_list = win.valid_ids(ui_document.Document, ids)
                if id_list.Count == 0:
                    win.report_error(
                        "Show in View failed",
                        "None of the ticked element(s) could be found in the "
                        "current model (stale list - try Refresh).",
                    )
                    return
                ui_document.Selection.SetElementIds(id_list)
                ui_document.ShowElements(id_list)
                win.txtStatus.Text = "Showing {} elements in view".format(id_list.Count)
            except Exception as ex:
                win.report_error("Show in View failed", ex)

        self.handler.action = do_show
        self.ext_event.Raise()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

pre_selected_ids = list(uidoc.Selection.GetElementIds())

if not pre_selected_ids:
    forms.alert(
        "Select the elements you want to filter by Family first, then run "
        "this tool again.",
        title=TOOL_NAME,
    )
else:
    window = SelectByFamilyWindow("ui.xaml", pre_selected_ids)
    window.Show()
