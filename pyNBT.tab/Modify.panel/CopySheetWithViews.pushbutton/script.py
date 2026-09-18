# -*- coding: utf-8 -*-
"""pyNBT - Copy Sheet + Views

Duplicates one or more Sheets together with every View placed on them, in
one click - instead of Trung having to create a new Sheet, then manually
Duplicate each View on the old Sheet one by one and re-place it.

For each ticked source Sheet:
  - A new Sheet is created (same title block type), with a Sheet Number
    Trung types in himself (Revit requires every Sheet Number to be
    unique, so this is validated before anything runs) and a Sheet Name
    that defaults to "<original name> - COPY" (editable).
  - Every normal View placed on the source Sheet (Floor Plan, Section,
    Elevation, Drafting, 3D, ...) is duplicated - "With Detailing" (keeps
    annotations/dimensions/tags) or plain "Duplicate" (geometry only),
    whichever radio Trung picks - renamed "<original> - COPY" (auto
    incremented to "- COPY 2", "- COPY 3", ... if that name is already
    taken), and placed on the new Sheet at the same spot as the original.
  - Legend and Schedule views are DIFFERENT on purpose: Revit already
    lets the same Legend/Schedule be placed on many Sheets at once without
    duplicating it, so this tool always reuses the original Legend/
    Schedule on the new Sheet instead of creating a redundant copy
    (confirmed with Trung - this is not a toggle).

Runs as a normal blocking dialog (ShowDialog) - all Revit API calls
happen from the same script call frame that opened the window, so no
ExternalEvent/modeless machinery is needed here (unlike Select Partition,
which must stay open while Trung keeps working in Revit).

Known limitations (v1.0):
  - Sheet-level parameters (Issue Date, Approved By, Revision, ...) are
    NOT copied to the new Sheet - only its Number/Name and its Views.
  - Revision clouds/tags placed directly on the Sheet are not copied.
  - If a single View fails to duplicate (locked view template, corrupt
    view, etc.) that View is skipped and reported - it does not stop the
    rest of that Sheet or the batch.
"""

__title__ = 'Copy Sheet\n+ Views'
__author__ = 'NBT'

TOOL_NAME = 'Copy Sheet + Views'

import os
import sys

# The WPF assemblies are NOT loaded automatically just by importing
# pyrevit.forms - they must be referenced explicitly, before ANY
# System.Windows.* import (including pyNBT.theme, which itself imports
# System.Windows.Media). Skipping this fails at import time with
# "ImportException: No module named Windows" (same pattern already used
# by Excel Data.pushbutton/script.py - mirrored here for consistency).
import clr
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')
clr.AddReference('System.Xaml')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))   # ...\CopySheetWithViews.pushbutton
PANEL_DIR = os.path.dirname(SCRIPT_DIR)                    # ...\Modify.panel
TAB_DIR = os.path.dirname(PANEL_DIR)                        # ...\pyNBT Dev.tab
EXTENSION_DIR = os.path.dirname(TAB_DIR)                    # ...\pyNBT.extension
LIB_DIR = os.path.join(EXTENSION_DIR, 'lib')

if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyrevit import forms, revit, script

from pyNBT.compat import eid_int
from pyNBT.theme import CLR_MUTED, CLR_TEXT, brush

from Autodesk.Revit.DB import (
    BuiltInCategory,
    ElementId,
    FilteredElementCollector,
    ScheduleSheetInstance,
    Transaction,
    TransactionGroup,
    ViewDuplicateOption,
    ViewSheet,
    Viewport,
    ViewType,
)

from System.Windows import (
    FontStyles,
    GridLength,
    GridUnitType,
    HorizontalAlignment,
    TextTrimming,
    TextWrapping,
    Thickness,
    VerticalAlignment,
    Visibility,
)
from System.Windows.Controls import Border, CheckBox, ColumnDefinition, Grid, TextBlock, TextBox
from System.Windows.Input import Cursors
from System.Windows.Media import Color, SolidColorBrush

doc = revit.doc
uidoc = revit.uidoc

NEW_VIEW_SUFFIX = ' - COPY'
NEW_SHEET_NAME_SUFFIX = ' - COPY'

BRUSH_MUTED = brush(CLR_MUTED)
BRUSH_TEXT = brush(CLR_TEXT)
BRUSH_ROW_BORDER = SolidColorBrush(Color.FromRgb(228, 231, 236))
BRUSH_TRANSPARENT = SolidColorBrush(Color.FromArgb(0, 0, 0, 0))


# ---------------------------------------------------------------------------
# Standalone Revit-logic functions (no UI references)
# ---------------------------------------------------------------------------

def collect_sheets(document):
    """Return every real (non-placeholder) Sheet in the model, sorted by
    Sheet Number, as a list of ViewSheet elements."""
    sheets = []
    collector = FilteredElementCollector(document).OfClass(ViewSheet).WhereElementIsNotElementType()
    for sheet in collector:
        try:
            if getattr(sheet, 'IsPlaceholder', False):
                continue
        except Exception:
            pass
        sheets.append(sheet)
    sheets.sort(key=lambda s: (s.SheetNumber or ''))
    return sheets


def count_sheet_content(document, sheet_id):
    """Return (view_count, schedule_count) placed on a Sheet."""
    try:
        view_count = FilteredElementCollector(document, sheet_id).OfClass(Viewport).GetElementCount()
    except Exception:
        view_count = 0
    try:
        sched_count = FilteredElementCollector(document, sheet_id).OfClass(ScheduleSheetInstance).GetElementCount()
    except Exception:
        sched_count = 0
    return view_count, sched_count


def get_titleblock_type_id(document, sheet):
    """Return the ElementId of the title block TYPE placed on `sheet`, or
    ElementId.InvalidElementId if the sheet has no title block (Revit
    accepts InvalidElementId to create a blank sheet with no title
    block)."""
    try:
        titleblock = (
            FilteredElementCollector(document, sheet.Id)
            .OfCategory(BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsNotElementType()
            .FirstElement()
        )
        if titleblock is not None:
            return titleblock.GetTypeId()
    except Exception:
        pass
    return ElementId.InvalidElementId


def unique_view_name(document, base_name, taken_names):
    """Return a View name that does not collide with any existing View
    name in the model or any name already used earlier in this same
    batch run (`taken_names`, updated in place as names are handed out).
    "<base> - COPY", then "<base> - COPY 2", "<base> - COPY 3", ..."""
    candidate = base_name
    if candidate not in taken_names:
        taken_names.add(candidate)
        return candidate
    n = 2
    while True:
        candidate = '{} {}'.format(base_name, n)
        if candidate not in taken_names:
            taken_names.add(candidate)
            return candidate
        n += 1


def collect_all_view_names(document):
    """All View names currently in the model (any view type, including
    sheets, schedules, legends) - used as the starting pool for
    unique_view_name() so a freshly duplicated view never collides with
    something that already exists."""
    names = set()
    try:
        from Autodesk.Revit.DB import View
        for v in FilteredElementCollector(document).OfClass(View):
            try:
                if not v.IsTemplate:
                    names.add(v.Name)
            except Exception:
                continue
    except Exception:
        pass
    return names


def duplicate_one_sheet(document, src_sheet, new_number, new_name, duplicate_option, taken_view_names):
    """Create one new Sheet that mirrors `src_sheet`: same title block,
    every normal View duplicated (per `duplicate_option`) and placed at
    the same spot, Legends/Schedules reused as-is. Returns
    (new_sheet, skipped_messages)."""
    skipped = []

    titleblock_type_id = get_titleblock_type_id(document, src_sheet)
    new_sheet = ViewSheet.Create(document, titleblock_type_id)
    new_sheet.SheetNumber = new_number
    new_sheet.Name = new_name

    viewports = list(FilteredElementCollector(document, src_sheet.Id).OfClass(Viewport))
    for vp in viewports:
        try:
            src_view = document.GetElement(vp.ViewId)
            if src_view is None:
                continue
            center = vp.GetBoxCenter()

            if src_view.ViewType == ViewType.Legend:
                # Legends can be placed on many sheets at once - reuse the
                # same view instead of duplicating it.
                new_view_id = src_view.Id
            else:
                try:
                    new_view_id = src_view.Duplicate(duplicate_option)
                except Exception as dup_ex:
                    skipped.append('View "{}" could not be duplicated: {}'.format(src_view.Name, dup_ex))
                    continue
                new_view = document.GetElement(new_view_id)
                try:
                    new_view.Name = unique_view_name(document, src_view.Name + NEW_VIEW_SUFFIX, taken_view_names)
                except Exception:
                    pass

            new_vp = Viewport.Create(document, new_sheet.Id, new_view_id, center)
            try:
                new_vp.ChangeTypeId(vp.GetTypeId())
            except Exception:
                pass
        except Exception as vp_ex:
            skipped.append('A view on the source sheet could not be placed: {}'.format(vp_ex))
            continue

    schedules = list(FilteredElementCollector(document, src_sheet.Id).OfClass(ScheduleSheetInstance))
    for ssi in schedules:
        try:
            if getattr(ssi, 'IsTitleblockRevisionSchedule', False):
                # Baked into the title block family itself - the new title
                # block already shows this automatically, don't re-create it.
                continue
            ScheduleSheetInstance.Create(document, new_sheet.Id, ssi.ScheduleId, ssi.Point)
        except Exception as ssi_ex:
            skipped.append('A schedule on the source sheet could not be placed: {}'.format(ssi_ex))
            continue

    return new_sheet, skipped


def run_copy_batch(document, plan, duplicate_option):
    """Run duplicate_one_sheet() for every (sheet, new_number, new_name)
    in `plan`, each inside its own Transaction so one failing Sheet does
    not roll back Sheets that already succeeded. Returns a list of result
    dicts, one per plan entry, in the same order."""
    results = []
    taken_view_names = collect_all_view_names(document)

    tg = TransactionGroup(document, 'pyNBT - Copy Sheet + Views')
    tg.Start()
    any_ok = False
    for src_sheet, new_number, new_name in plan:
        label = '{} - {}'.format(src_sheet.SheetNumber, src_sheet.Name)
        t = Transaction(document, 'pyNBT - Copy Sheet + Views - {}'.format(new_number))
        t.Start()
        try:
            new_sheet, skipped = duplicate_one_sheet(
                document, src_sheet, new_number, new_name, duplicate_option, taken_view_names
            )
            t.Commit()
            any_ok = True
            results.append({
                'status': 'ok',
                'label': label,
                'new_number': new_number,
                'new_name': new_name,
                'skipped': skipped,
            })
        except Exception as ex:
            if t.HasStarted():
                t.RollBack()
            results.append({
                'status': 'error',
                'label': label,
                'new_number': new_number,
                'message': str(ex),
            })
    if any_ok:
        tg.Assimilate()
    else:
        tg.RollBack()
    return results


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class CopySheetWithViewsWindow(forms.WPFWindow):
    def __init__(self, xaml_file):
        forms.WPFWindow.__init__(self, xaml_file)

        self.all_sheets = []          # list[ViewSheet], sorted
        self.sheet_by_key = {}        # int -> ViewSheet
        self.content_counts = {}      # int -> (views, schedules)
        self.sheet_checkboxes = {}    # int -> CheckBox
        self.sheet_rows = {}          # int -> Border (for search filter)
        self.pending_new_number = {}  # int -> str
        self.pending_new_name = {}    # int -> str

        self.load_sheets()
        self.set_status_idle()

    # -- data / row building ---------------------------------------------

    def load_sheets(self):
        self.all_sheets = collect_sheets(doc)
        self.sheet_by_key = {}
        self.content_counts = {}
        for sheet in self.all_sheets:
            key = eid_int(sheet.Id)
            self.sheet_by_key[key] = sheet
            self.content_counts[key] = count_sheet_content(doc, sheet.Id)
        self.rebuild_sheet_rows()

    def rebuild_sheet_rows(self):
        self.panelSheetList.Children.Clear()
        self.sheet_checkboxes = {}
        self.sheet_rows = {}
        if not self.all_sheets:
            empty = TextBlock()
            empty.Text = 'No sheets found in this model.'
            empty.FontSize = 12.5
            empty.Foreground = BRUSH_MUTED
            empty.Margin = Thickness(20, 14, 20, 14)
            self.panelSheetList.Children.Add(empty)
            return
        for sheet in self.all_sheets:
            row = self.make_sheet_row(sheet)
            self.panelSheetList.Children.Add(row)

    def make_sheet_row(self, sheet):
        key = eid_int(sheet.Id)
        views, scheds = self.content_counts.get(key, (0, 0))

        border = Border()
        border.Padding = Thickness(20, 9, 20, 9)
        border.BorderBrush = BRUSH_ROW_BORDER
        border.BorderThickness = Thickness(0, 0, 0, 1)
        border.Background = BRUSH_TRANSPARENT
        border.Cursor = Cursors.Hand

        grid = Grid()
        col0 = ColumnDefinition()
        col0.Width = GridLength(30)
        col1 = ColumnDefinition()
        col1.Width = GridLength(1, GridUnitType.Star)
        col2 = ColumnDefinition()
        col2.Width = GridLength(55)
        grid.ColumnDefinitions.Add(col0)
        grid.ColumnDefinitions.Add(col1)
        grid.ColumnDefinitions.Add(col2)

        chk = CheckBox()
        chk.VerticalAlignment = VerticalAlignment.Center
        chk.Tag = key
        chk.Checked += self.on_sheet_check_changed
        chk.Unchecked += self.on_sheet_check_changed
        Grid.SetColumn(chk, 0)

        txt_name = TextBlock()
        txt_name.Text = '{} - {}'.format(sheet.SheetNumber, sheet.Name)
        txt_name.FontSize = 12.5
        txt_name.VerticalAlignment = VerticalAlignment.Center
        txt_name.TextTrimming = TextTrimming.CharacterEllipsis
        Grid.SetColumn(txt_name, 1)

        txt_count = TextBlock()
        txt_count.Text = str(views + scheds)
        txt_count.FontSize = 12
        txt_count.HorizontalAlignment = HorizontalAlignment.Right
        txt_count.VerticalAlignment = VerticalAlignment.Center
        txt_count.Foreground = BRUSH_MUTED
        Grid.SetColumn(txt_count, 2)

        grid.Children.Add(chk)
        grid.Children.Add(txt_name)
        grid.Children.Add(txt_count)
        border.Child = grid

        self.sheet_checkboxes[key] = chk
        self.sheet_rows[key] = border
        return border

    def rebuild_pending_rows(self):
        checked_keys = [k for k, chk in self.sheet_checkboxes.items() if chk.IsChecked]
        checked_keys_set = set(checked_keys)
        ordered = [eid_int(s.Id) for s in self.all_sheets if eid_int(s.Id) in checked_keys_set]

        self.panelPendingRows.Children.Clear()
        if not ordered:
            empty = TextBlock()
            empty.Text = 'Tick sheets on the left to add them here.'
            empty.FontSize = 12
            empty.FontStyle = FontStyles.Italic
            empty.Foreground = BRUSH_MUTED
            empty.TextWrapping = TextWrapping.Wrap
            empty.Margin = Thickness(4, 10, 4, 10)
            self.panelPendingRows.Children.Add(empty)
        else:
            for key in ordered:
                row = self.make_pending_row(key)
                self.panelPendingRows.Children.Add(row)

        self.txtPendingHeader.Text = 'Sheets to copy ({})'.format(len(ordered))
        self.update_status()

    def make_pending_row(self, key):
        sheet = self.sheet_by_key[key]

        if key not in self.pending_new_name:
            self.pending_new_name[key] = sheet.Name + NEW_SHEET_NAME_SUFFIX
        if key not in self.pending_new_number:
            self.pending_new_number[key] = ''

        border = Border()
        border.Padding = Thickness(0, 8, 0, 8)
        border.BorderBrush = BRUSH_ROW_BORDER
        border.BorderThickness = Thickness(0, 0, 0, 1)

        grid = Grid()
        col0 = ColumnDefinition()
        col0.Width = GridLength(1, GridUnitType.Star)
        col1 = ColumnDefinition()
        col1.Width = GridLength(130)
        col2 = ColumnDefinition()
        col2.Width = GridLength(200)
        grid.ColumnDefinitions.Add(col0)
        grid.ColumnDefinitions.Add(col1)
        grid.ColumnDefinitions.Add(col2)

        txt_label = TextBlock()
        txt_label.Text = '{} - {}'.format(sheet.SheetNumber, sheet.Name)
        txt_label.FontSize = 12
        txt_label.Foreground = BRUSH_TEXT
        txt_label.VerticalAlignment = VerticalAlignment.Center
        txt_label.TextTrimming = TextTrimming.CharacterEllipsis
        txt_label.Margin = Thickness(0, 0, 8, 0)
        Grid.SetColumn(txt_label, 0)

        box_number = TextBox()
        box_number.Text = self.pending_new_number[key]
        box_number.Tag = key
        box_number.FontSize = 12
        box_number.Padding = Thickness(6, 4, 6, 4)
        box_number.Margin = Thickness(0, 0, 8, 0)
        box_number.TextChanged += self.on_number_changed
        Grid.SetColumn(box_number, 1)

        box_name = TextBox()
        box_name.Text = self.pending_new_name[key]
        box_name.Tag = key
        box_name.FontSize = 12
        box_name.Padding = Thickness(6, 4, 6, 4)
        box_name.TextChanged += self.on_name_changed
        Grid.SetColumn(box_name, 2)

        grid.Children.Add(txt_label)
        grid.Children.Add(box_number)
        grid.Children.Add(box_name)
        border.Child = grid
        return border

    # -- helpers -----------------------------------------------------------

    def get_checked_keys(self):
        checked_keys_set = set(k for k, chk in self.sheet_checkboxes.items() if chk.IsChecked)
        return [eid_int(s.Id) for s in self.all_sheets if eid_int(s.Id) in checked_keys_set]

    def set_status_idle(self):
        self.txtStatus.Text = 'Ready - tick sheets on the left to begin'

    def update_status(self):
        keys = self.get_checked_keys()
        if not keys:
            self.set_status_idle()
            return
        self.txtStatus.Text = '{} sheet(s) ticked - fill in New Sheet Number for each before copying'.format(len(keys))

    def report_error(self, title, ex):
        self.txtStatus.Text = 'Error - see popup for details'
        forms.alert('{}\n\n{}'.format(title, ex), title=TOOL_NAME)

    # -- event handlers (wired from ui.xaml Click / TextChanged / Checked) -

    def on_sheet_check_changed(self, sender, args):
        self.rebuild_pending_rows()

    def on_number_changed(self, sender, args):
        self.pending_new_number[sender.Tag] = sender.Text

    def on_name_changed(self, sender, args):
        self.pending_new_name[sender.Tag] = sender.Text

    def OnSearchChanged(self, sender, args):
        term = (self.txtSearch.Text or '').strip().lower()
        for key, border in self.sheet_rows.items():
            sheet = self.sheet_by_key[key]
            label = '{} {}'.format(sheet.SheetNumber, sheet.Name).lower()
            border.Visibility = Visibility.Visible if term in label else Visibility.Collapsed

    def OnSelectAll(self, sender, args):
        term = (self.txtSearch.Text or '').strip().lower()
        for key, chk in self.sheet_checkboxes.items():
            sheet = self.sheet_by_key[key]
            label = '{} {}'.format(sheet.SheetNumber, sheet.Name).lower()
            if term in label:
                chk.IsChecked = True
        self.rebuild_pending_rows()

    def OnSelectNone(self, sender, args):
        for chk in self.sheet_checkboxes.values():
            chk.IsChecked = False
        self.rebuild_pending_rows()

    def OnCancel(self, sender, args):
        self.Close()

    def OnCopy(self, sender, args):
        try:
            keys = self.get_checked_keys()
            if not keys:
                forms.alert('Please tick at least one sheet first.', title=TOOL_NAME)
                return

            existing_numbers = set(s.SheetNumber for s in self.all_sheets)
            used_in_batch = set()
            errors = []
            plan = []
            for key in keys:
                sheet = self.sheet_by_key[key]
                label = '{} - {}'.format(sheet.SheetNumber, sheet.Name)
                new_number = (self.pending_new_number.get(key) or '').strip()
                new_name = (self.pending_new_name.get(key) or '').strip() or (sheet.Name + NEW_SHEET_NAME_SUFFIX)

                if not new_number:
                    errors.append('{}: New Sheet Number is required'.format(label))
                    continue
                if new_number in existing_numbers:
                    errors.append('{}: Sheet Number "{}" already exists in the model'.format(label, new_number))
                    continue
                if new_number in used_in_batch:
                    errors.append('{}: Sheet Number "{}" is used twice in this batch'.format(label, new_number))
                    continue
                used_in_batch.add(new_number)
                plan.append((sheet, new_number, new_name))

            if errors:
                forms.alert(
                    'Please fix the following before copying:\n\n' + '\n'.join(errors),
                    title=TOOL_NAME,
                )
                return

            if not forms.alert(
                'Copy {} sheet(s) now?'.format(len(plan)),
                title=TOOL_NAME, yes=True, no=True,
            ):
                return

            duplicate_option = (
                ViewDuplicateOption.WithDetailing
                if self.radWithDetailing.IsChecked
                else ViewDuplicateOption.Duplicate
            )

            self.txtStatus.Text = 'Copying...'
            results = run_copy_batch(doc, plan, duplicate_option)
            self.show_summary(results)

            self.pending_new_number = {}
            self.pending_new_name = {}
            self.load_sheets()
        except Exception as ex:
            self.report_error('Copy failed', ex)

    def show_summary(self, results):
        ok_results = [r for r in results if r['status'] == 'ok']
        err_results = [r for r in results if r['status'] == 'error']

        lines = ['{} of {} sheet(s) copied successfully.'.format(len(ok_results), len(results)), '']
        for r in ok_results:
            line = 'OK   {}  ->  {}'.format(r['label'], r['new_number'])
            lines.append(line)
            for s in r['skipped']:
                lines.append('       - skipped: {}'.format(s))
        for r in err_results:
            lines.append('FAILED   {} (wanted {}): {}'.format(r['label'], r['new_number'], r['message']))

        self.txtStatus.Text = '{} of {} sheet(s) copied successfully'.format(len(ok_results), len(results))
        forms.alert('\n'.join(lines), title=TOOL_NAME)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

window = CopySheetWithViewsWindow('ui.xaml')
window.ShowDialog()
