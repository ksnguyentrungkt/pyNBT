# -*- coding: utf-8 -*-
"""pyNBT - Select By Family

Groups the CURRENT SELECTION (elements Trung already picked/box-selected
in Revit before running this tool) by their Family Name, shows a count
per Family, and lets Trung tick one or more Family names to re-select
only those elements in Revit.

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
    GridLength,
    GridUnitType,
    HorizontalAlignment,
    TextTrimming,
    Thickness,
    VerticalAlignment,
    Visibility,
)
from System.Windows.Controls import Border, CheckBox, ColumnDefinition, Grid, TextBlock
from System.Windows.Input import Cursors
from System.Windows.Media import Color, SolidColorBrush

from Autodesk.Revit.DB import ElementId
from Autodesk.Revit.UI import ExternalEvent, IExternalEventHandler

doc = revit.doc
uidoc = revit.uidoc

TOOL_NAME = 'Select By Family'
NO_FAMILY_LABEL = '(No Family)'

# pyNBT theme brushes (Navy + Gray/White/Black palette) - same values as
# lib/pyNBT/theme.py, inlined here to match Select Partition's existing
# pattern of not depending on the shared lib for a handful of brushes.
BRUSH_MUTED = SolidColorBrush(Color.FromRgb(120, 120, 120))
BRUSH_GRAY_TEXT = SolidColorBrush(Color.FromRgb(75, 85, 99))
BRUSH_ROW_BORDER = SolidColorBrush(Color.FromRgb(228, 231, 236))
BRUSH_TRANSPARENT = SolidColorBrush(Color.FromArgb(0, 0, 0, 0))


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


def collect_family_groups(document, element_ids):
    """Group the given ElementIds by Family name.

    Returns an OrderedDict: family_name -> list[ElementId], sorted A-Z,
    with '(No Family)' always last.
    """
    groups = {}
    for eid in element_ids:
        try:
            el = document.GetElement(eid)
            if el is None:
                continue
            name = get_family_name(document, el)
            key = name if name else NO_FAMILY_LABEL
            groups.setdefault(key, []).append(eid)
        except Exception:
            continue

    ordered = OrderedDict()
    for key in sorted(k for k in groups if k != NO_FAMILY_LABEL):
        ordered[key] = groups[key]
    if NO_FAMILY_LABEL in groups:
        ordered[NO_FAMILY_LABEL] = groups[NO_FAMILY_LABEL]
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

        self.checkboxes = {}
        self.row_borders = {}
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
        self.checkboxes = {}
        self.row_borders = {}
        for name, ids in self.groups.items():
            row = self.make_row(name, len(ids))
            self.panelList.Children.Add(row)
        self.update_status()

    def make_row(self, name, count):
        border = Border()
        border.Padding = Thickness(20, 9, 20, 9)
        border.BorderBrush = BRUSH_ROW_BORDER
        border.BorderThickness = Thickness(0, 0, 0, 1)
        border.Background = BRUSH_TRANSPARENT
        border.Cursor = Cursors.Hand

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
        chk.Tag = name
        chk.Checked += self.on_row_check_changed
        chk.Unchecked += self.on_row_check_changed
        Grid.SetColumn(chk, 0)

        txt_name = TextBlock()
        txt_name.Text = name
        txt_name.FontSize = 13
        txt_name.VerticalAlignment = VerticalAlignment.Center
        txt_name.TextTrimming = TextTrimming.CharacterEllipsis
        if name == NO_FAMILY_LABEL:
            txt_name.FontStyle = FontStyles.Italic
            txt_name.Foreground = BRUSH_MUTED
        Grid.SetColumn(txt_name, 1)

        txt_count = TextBlock()
        txt_count.Text = str(count)
        txt_count.FontSize = 12.5
        txt_count.HorizontalAlignment = HorizontalAlignment.Right
        txt_count.VerticalAlignment = VerticalAlignment.Center
        txt_count.Foreground = BRUSH_GRAY_TEXT
        Grid.SetColumn(txt_count, 2)

        grid.Children.Add(chk)
        grid.Children.Add(txt_name)
        grid.Children.Add(txt_count)
        border.Child = grid

        self.checkboxes[name] = chk
        self.row_borders[name] = border
        return border

    # -- helpers ---------------------------------------------------------

    def get_checked_ids(self):
        ids = []
        for name, chk in self.checkboxes.items():
            if chk.IsChecked:
                ids.extend(self.groups.get(name, []))
        return ids

    def get_checked_names(self):
        return [name for name, chk in self.checkboxes.items() if chk.IsChecked]

    def set_status_idle(self):
        total = sum(len(ids) for ids in self.groups.values())
        self.txtStatus.Text = "Ready - {} element(s) in selection, 0 checked".format(total)

    def update_status(self):
        names = self.get_checked_names()
        if not names:
            self.set_status_idle()
            return
        total = sum(len(self.groups.get(n, [])) for n in names)
        self.txtStatus.Text = "{} family(ies) checked - {} elements ready to select".format(
            len(names), total
        )

    # -- event handlers (wired from ui.xaml Click / TextChanged attrs) ---

    def on_row_check_changed(self, sender, args):
        self.update_status()

    def OnSearchChanged(self, sender, args):
        term = (self.txtSearch.Text or "").strip().lower()
        for name, border in self.row_borders.items():
            visible = term in name.lower()
            border.Visibility = Visibility.Visible if visible else Visibility.Collapsed

    def OnSelectAll(self, sender, args):
        term = (self.txtSearch.Text or "").strip().lower()
        for name, chk in self.checkboxes.items():
            if term in name.lower():
                chk.IsChecked = True
        self.update_status()

    def OnSelectNone(self, sender, args):
        for chk in self.checkboxes.values():
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
                "Please tick at least one Family first.",
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
                        "None of the {} element(s) in the ticked Family(ies) "
                        "could be found in the current model (stale list - "
                        "try Refresh).".format(len(ids)),
                    )
                    return
                ui_document.Selection.SetElementIds(id_list)
                skipped = len(ids) - id_list.Count
                msg = "Selected: {} elements across {} family(ies)".format(
                    id_list.Count, len(win.get_checked_names())
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
                "Please tick at least one Family first.",
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
