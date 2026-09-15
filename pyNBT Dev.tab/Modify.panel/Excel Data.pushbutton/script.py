# -*- coding: utf-8 -*-
"""Excel Data

Exports a Schedule to an Excel file and imports an edited Excel file back
to update the right elements' parameters in the model. Merges two separate
tools NBT used to have (Export Schedule to Excel / Import Mark from Excel)
into a single window, switched between Export/Import with a toggle in the
toolbar.

Export: pick any Schedule (no longer limited to Columns like the old
tool), export every visible field of the schedule to Excel, plus 3 columns
_ElementId / _UniqueId / _ScheduleName used to match rows back on Import.
Each row's values are read DIRECTLY from the element itself (not by
zipping row positions against Revit's GetTableData() table) - this is the
key correctness fix over the old tool: element<->row correlation no
longer depends on FilteredElementCollector's enumeration order matching
the schedule's displayed row order, which the Revit API never guarantees
once a schedule has its own sort/group/filter rules.

Import: reads the edited Excel file, matches each row back to its element
via _UniqueId (preferred) or _ElementId, then for EVERY remaining column
(not just Mark like the old tool) looks up a parameter with the same name
on the element (LookupParameter, with a Type fallback) and compares old
vs new value. Shows a preview table classified as Will update / No change
/ Error before writing anything to the model (with a "preview only"
checkbox plus a Yes/No confirmation - keeping the same safety-first spirit
as the original two tools).

Known limitation: ElementId-typed parameters (Level, Type, Phase...) can
only be written through Excel if Revit's SetValueString() can parse the
text entered (e.g. an existing Level/Type name) - reported as a clear
Error rather than failing silently.
"""
from __future__ import unicode_literals

import os
import re
import sys

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')
clr.AddReference('System.Xaml')
clr.AddReference('System.Data')

import System
from System.Data import DataTable
from System.Windows import Visibility
from System.Windows.Media import Brushes

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))   # ...\{Panel}.panel\Excel Data.pushbutton
PANEL_DIR = os.path.dirname(SCRIPT_DIR)                    # ...\{Panel}.panel
TAB_DIR = os.path.dirname(PANEL_DIR)                       # ...\pyNBT.tab (or pyNBT Dev.tab)
EXTENSION_DIR = os.path.dirname(TAB_DIR)                   # ...\pyNBT.extension
LIB_DIR = os.path.join(EXTENSION_DIR, 'lib')

if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyNBT.compat import eid_int, make_eid
from pyNBT import theme
from pyNBT.excel_io import read_xlsx, write_xlsx


TOOL_NAME = 'Excel Data'
__title__ = 'Excel\nData'
__author__ = 'NBT'
__persistentengine__ = True

doc = revit.doc

RESERVED_COLUMNS = ('_ElementId', '_UniqueId', '_ScheduleName')


# ---------------------------------------------------------------------------
# Standalone logic - pure Revit API + excel_io, no WPF/UI references here
# ---------------------------------------------------------------------------

def get_schedules(document):
    return [
        s for s in DB.FilteredElementCollector(document).OfClass(DB.ViewSchedule)
        if not s.IsTemplate
    ]


def get_schedule_category_name(document, schedule):
    category_id = schedule.Definition.CategoryId
    category = DB.Category.GetCategory(document, category_id)
    return category.Name if category else 'Multiple categories / unknown'


def make_unique_headers(headers):
    used = {}
    result = []
    for header in headers:
        clean_header = header or 'Column'
        if clean_header not in used:
            used[clean_header] = 1
            result.append(clean_header)
            continue
        used[clean_header] += 1
        result.append('{0} {1}'.format(clean_header, used[clean_header]))
    return result


def get_schedule_field_names(schedule):
    definition = schedule.Definition
    field_names = []
    field_params = []

    for index in range(definition.GetFieldCount()):
        field = definition.GetField(index)
        name = field.GetName()
        if not name:
            continue
        field_names.append(name)
        field_params.append((name, field.ParameterId))

    return field_names, field_params


def get_parameter_by_id(element, parameter_id):
    if not parameter_id or parameter_id == DB.ElementId.InvalidElementId:
        return None

    if eid_int(parameter_id) < 0:
        built_in_param = DB.BuiltInParameter(eid_int(parameter_id))
        param = element.get_Parameter(built_in_param)
        if param:
            return param
        element_type = element.Document.GetElement(element.GetTypeId())
        if element_type:
            return element_type.get_Parameter(built_in_param)

    for param in element.Parameters:
        try:
            if eid_int(param.Id) == eid_int(parameter_id):
                return param
        except Exception:
            pass

    element_type = element.Document.GetElement(element.GetTypeId())
    if element_type:
        for param in element_type.Parameters:
            try:
                if eid_int(param.Id) == eid_int(parameter_id):
                    return param
            except Exception:
                pass

    return None


def get_parameter_by_name(element, parameter_name):
    param = element.LookupParameter(parameter_name)
    if param:
        return param
    element_type = element.Document.GetElement(element.GetTypeId())
    if element_type:
        return element_type.LookupParameter(parameter_name)
    return None


def get_param_text(param):
    if not param:
        return ''

    value = param.AsValueString()
    if value:
        return value

    storage_type = param.StorageType
    if storage_type == DB.StorageType.String:
        return param.AsString() or ''
    if storage_type == DB.StorageType.Integer:
        return str(param.AsInteger())
    if storage_type == DB.StorageType.Double:
        return str(round(param.AsDouble(), 6))
    if storage_type == DB.StorageType.ElementId:
        element_id = param.AsElementId()
        if element_id and eid_int(element_id) > 0:
            ref_element = param.Element.Document.GetElement(element_id)
            if ref_element:
                return ref_element.Name
        return str(eid_int(element_id))

    return ''


def get_field_value(element, field_name, parameter_id):
    param = get_parameter_by_id(element, parameter_id)
    if param:
        return get_param_text(param)
    return get_param_text(get_parameter_by_name(element, field_name))


def build_export_rows(document, schedule):
    """Build export rows by reading each visible field DIRECTLY from each
    element, instead of from the schedule's own displayed table. This is
    the key correctness fix vs. the original tool: element<->row
    correlation no longer depends on FilteredElementCollector's
    enumeration order matching the schedule's displayed row order, which
    the Revit API never guarantees once a schedule has its own
    sort/group/filter rules.
    """
    field_names, field_params = get_schedule_field_names(schedule)

    elements = list(
        DB.FilteredElementCollector(document, schedule.Id)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    elements.sort(key=lambda el: eid_int(el.Id))

    headers = make_unique_headers(list(field_names) + list(RESERVED_COLUMNS))

    rows = []
    for element in elements:
        row = [get_field_value(element, name, parameter_id) for name, parameter_id in field_params]
        row.append(str(eid_int(element.Id)))
        row.append(element.UniqueId)
        row.append(schedule.Name)
        rows.append(row)

    return headers, rows


def get_element_from_row(document, row, element_id_index, unique_id_index):
    if unique_id_index is not None and unique_id_index < len(row):
        unique_id = (row[unique_id_index] or '').strip()
        if unique_id:
            element = document.GetElement(unique_id)
            if element:
                return element

    if element_id_index is not None and element_id_index < len(row):
        raw_value = (row[element_id_index] or '').strip()
        if raw_value:
            try:
                return document.GetElement(make_eid(int(float(raw_value))))
            except Exception:
                return None

    return None


def find_target_parameter(element, column_name):
    return get_parameter_by_name(element, column_name)


def compute_diff(document, headers, rows):
    """Read-only comparison pass: resolves each row to its element and
    diffs every non-reserved column against the element's current value.
    Never writes to the model - safe to call as many times as needed to
    refresh the preview."""

    eid_index = headers.index('_ElementId') if '_ElementId' in headers else None
    uid_index = headers.index('_UniqueId') if '_UniqueId' in headers else None

    if eid_index is None and uid_index is None:
        return [{
            'row': None, 'mark': '', 'parameter': '-', 'old': '-', 'new': '-',
            'status': 'error',
            'message': 'File is missing the _ElementId/_UniqueId column - cannot match rows to elements',
            'element': None,
        }]

    data_columns = [i for i, name in enumerate(headers) if name and name not in RESERVED_COLUMNS]
    display_index = data_columns[0] if data_columns else 0

    entries = []
    for row_number, row in enumerate(rows, start=2):
        element = get_element_from_row(document, row, eid_index, uid_index)
        display_value = row[display_index] if display_index < len(row) else ''

        if not element:
            entries.append({
                'row': row_number, 'mark': display_value or '(unknown)',
                'parameter': '-', 'old': '-', 'new': '-', 'status': 'error',
                'message': 'Element not found (ElementId/UniqueId does not match)',
                'element': None,
            })
            continue

        if not data_columns:
            entries.append({
                'row': row_number, 'mark': display_value, 'parameter': '-',
                'old': '-', 'new': '-', 'status': 'error',
                'message': 'No data column besides _ElementId/_UniqueId/_ScheduleName',
                'element': element,
            })
            continue

        for col_index in data_columns:
            column_name = headers[col_index]
            new_value = (row[col_index] or '').strip() if col_index < len(row) else ''
            param = find_target_parameter(element, column_name)

            if not param:
                entries.append({
                    'row': row_number, 'mark': display_value, 'parameter': column_name,
                    'old': '-', 'new': new_value, 'status': 'error',
                    'message': 'Parameter "{0}" not found on this element'.format(column_name),
                    'element': element,
                })
                continue

            if param.IsReadOnly:
                entries.append({
                    'row': row_number, 'mark': display_value, 'parameter': column_name,
                    'old': get_param_text(param), 'new': new_value, 'status': 'error',
                    'message': 'Parameter is locked (read-only)',
                    'element': element,
                })
                continue

            old_value = get_param_text(param)
            if old_value.strip() == new_value:
                entries.append({
                    'row': row_number, 'mark': display_value, 'parameter': column_name,
                    'old': old_value, 'new': new_value, 'status': 'unchanged',
                    'message': '', 'element': element,
                })
            else:
                entries.append({
                    'row': row_number, 'mark': display_value, 'parameter': column_name,
                    'old': old_value, 'new': new_value, 'status': 'update',
                    'message': '', 'element': element,
                })

    return entries


def set_param_value(param, text_value):
    if param.StorageType == DB.StorageType.String:
        param.Set(text_value)
        return

    # SetValueString parses text the same way the Revit UI would (handles
    # units for Length/Area/etc, matches names for Level/Type-like params)
    # - this is what makes writing back ANY parameter (not just Mark, a
    # plain String) safe without hand-rolling unit conversion per data type.
    if param.SetValueString(text_value):
        return

    if param.StorageType == DB.StorageType.Integer:
        param.Set(int(float(text_value)))
    elif param.StorageType == DB.StorageType.Double:
        param.Set(float(text_value))
    else:
        raise ValueError('Cannot write value "{0}" for this parameter type'.format(text_value))


def apply_updates(document, entries):
    to_write = [e for e in entries if e['status'] == 'update' and e['element'] is not None]
    updated = 0
    failed = 0

    t = DB.Transaction(document, 'pyNBT - Excel Data Import')
    t.Start()
    try:
        for entry in to_write:
            param = find_target_parameter(entry['element'], entry['parameter'])
            if not param or param.IsReadOnly:
                failed += 1
                continue
            try:
                set_param_value(param, entry['new'])
                updated += 1
            except Exception:
                failed += 1
        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        forms.alert('Error writing to the model: {0}'.format(str(ex)), title=TOOL_NAME)
        return 0, len(to_write)

    return updated, failed


# ---------------------------------------------------------------------------
# UI - orchestrates the functions above, never contains Revit API logic
# ---------------------------------------------------------------------------

class ExcelDataWindow(forms.WPFWindow):
    def __init__(self, xaml_source):
        forms.WPFWindow.__init__(self, xaml_source)

        self.schedules = sorted(get_schedules(doc), key=lambda s: s.Name)
        self.cmbSchedule.ItemsSource = [s.Name for s in self.schedules]
        self.btnExport.IsEnabled = bool(self.schedules)

        self._export_headers = []
        self._export_rows = []
        self._diff_entries = []
        self._diff_status_order = []
        self.import_path = None

        self.btnApply.IsEnabled = False

        if self.schedules:
            self.cmbSchedule.SelectedIndex = 0
        else:
            self.txtCategory.Text = '-'

        self._set_mode('export')

    # ---- mode switching ----

    def _set_mode(self, mode):
        self.mode = mode
        is_export = (mode == 'export')

        self.pageExportLeft.Visibility = Visibility.Visible if is_export else Visibility.Collapsed
        self.pageImportLeft.Visibility = Visibility.Collapsed if is_export else Visibility.Visible
        self.gridExportPreview.Visibility = Visibility.Visible if is_export else Visibility.Collapsed
        self.gridImportDiff.Visibility = Visibility.Collapsed if is_export else Visibility.Visible
        self.btnExport.Visibility = Visibility.Visible if is_export else Visibility.Collapsed
        self.btnApply.Visibility = Visibility.Collapsed if is_export else Visibility.Visible
        self.txtRightTitle.Text = 'Schedule preview' if is_export else 'Compare before writing'

        self.btnModeExport.Background = theme.brush(theme.CLR_HEADER) if is_export else Brushes.Transparent
        self.btnModeExport.Foreground = Brushes.White if is_export else theme.brush(theme.CLR_TEXT)
        self.btnModeImport.Background = Brushes.Transparent if is_export else theme.brush(theme.CLR_HEADER)
        self.btnModeImport.Foreground = theme.brush(theme.CLR_TEXT) if is_export else Brushes.White

        if is_export:
            self._load_export_preview()
        else:
            self._update_import_status(self._diff_entries)

    def OnModeExport(self, sender, args):
        self._set_mode('export')

    def OnModeImport(self, sender, args):
        self._set_mode('import')

    # ---- export ----

    def _current_schedule(self):
        name = self.cmbSchedule.SelectedItem
        for schedule in self.schedules:
            if schedule.Name == name:
                return schedule
        return None

    def _load_export_preview(self):
        schedule = self._current_schedule()
        if not schedule:
            self.txtCategory.Text = '-'
            self.gridExportPreview.ItemsSource = None
            self._update_export_status(0)
            return

        self.txtCategory.Text = get_schedule_category_name(doc, schedule)
        headers, rows = build_export_rows(doc, schedule)
        self._export_headers = headers
        self._export_rows = rows
        self._render_export_table(headers, rows)
        self._update_export_status(len(rows))

    def _render_export_table(self, headers, rows):
        table = DataTable()
        for header in headers:
            table.Columns.Add(header)
        for row in rows:
            table.Rows.Add(System.Array[System.Object](row))
        self.gridExportPreview.ItemsSource = table.DefaultView

    def _update_export_status(self, count):
        self.txtRightCount.Text = '{0} rows'.format(count)
        self.txtStatus.Text = '{0} rows ready to export'.format(count)

    def OnScheduleChanged(self, sender, args):
        self._load_export_preview()

    def OnRefreshPreview(self, sender, args):
        self._load_export_preview()

    def OnExportClick(self, sender, args):
        schedule = self._current_schedule()
        if not schedule or not self._export_rows:
            forms.alert('No data to export.', title=TOOL_NAME)
            return

        default_name = 'pyNBT_{0}.xlsx'.format(re.sub(r'[^A-Za-z0-9]+', '_', schedule.Name).strip('_'))
        save_path = forms.save_file(file_ext='xlsx', default_name=default_name)
        if not save_path:
            return

        write_xlsx(save_path, self._export_headers, self._export_rows, sheet_name=schedule.Name[:31])
        forms.alert('Excel file exported:\n{0}'.format(save_path), title=TOOL_NAME)

    # ---- import ----

    def OnPickImportFile(self, sender, args):
        path = forms.pick_file(file_ext='xlsx', title='Select the edited Excel file')
        if not path:
            return
        self.import_path = path
        self.txtImportFile.Text = os.path.basename(path)
        self._scan_import_file()

    def _scan_import_file(self):
        if not self.import_path:
            return
        headers, rows = read_xlsx(self.import_path)
        self._diff_entries = compute_diff(doc, headers, rows)
        self._render_matched_columns(headers)
        self._render_diff_table(self._diff_entries)
        self._update_import_status(self._diff_entries)

    def _render_matched_columns(self, headers):
        columns = [name for name in headers if name and name not in RESERVED_COLUMNS]
        self.txtMatchedColumns.Text = '\n'.join(columns) if columns else '(no data column)'

    def _render_diff_table(self, entries):
        table = DataTable()
        for column in ('Row', 'Element', 'Parameter', 'Old value', 'New value', 'Status'):
            table.Columns.Add(column)

        status_labels = {
            'update': 'Will update',
            'unchanged': 'No change',
        }

        self._diff_status_order = []
        for entry in entries:
            if entry['status'] == 'error':
                status_text = 'Error: {0}'.format(entry.get('message', ''))
            else:
                status_text = status_labels.get(entry['status'], entry['status'])

            table.Rows.Add(System.Array[System.Object]([
                entry['row'] if entry['row'] is not None else '-',
                entry['mark'],
                entry['parameter'],
                entry['old'],
                entry['new'],
                status_text,
            ]))
            self._diff_status_order.append(entry['status'])

        self.gridImportDiff.ItemsSource = table.DefaultView

    def OnDiffRowLoading(self, sender, e):
        try:
            index = e.Row.GetIndex()
            status = self._diff_status_order[index]
        except Exception:
            return
        if status == 'update':
            e.Row.Background = theme.brush(theme.CLR_SUCCESS_BG)
        elif status == 'error':
            e.Row.Background = theme.brush(theme.CLR_ERROR_BG)

    def _update_import_status(self, entries):
        total = len(entries)
        updates = len([e for e in entries if e['status'] == 'update'])
        unchanged = len([e for e in entries if e['status'] == 'unchanged'])
        errors = len([e for e in entries if e['status'] == 'error'])
        self.txtRightCount.Text = '{0} rows detected'.format(total)

        status_text = 'Will update: {0}   No change: {1}   Errors: {2}'.format(updates, unchanged, errors)
        if getattr(self, 'chkPreviewOnly', None) is not None and self.chkPreviewOnly.IsChecked:
            status_text += '   -   Uncheck "Preview only" on the left to enable Import & Update'
        self.txtStatus.Text = status_text

    def OnPreviewOnlyChanged(self, sender, args):
        self.btnApply.IsEnabled = not self.chkPreviewOnly.IsChecked
        if self.mode == 'import':
            self._update_import_status(self._diff_entries)

    def OnApplyClick(self, sender, args):
        if not self._diff_entries:
            forms.alert('No data to import yet. Choose an Excel file first.', title=TOOL_NAME)
            return

        to_update = [e for e in self._diff_entries if e['status'] == 'update']
        if not to_update:
            forms.alert('Nothing needs to be written to the model.', title=TOOL_NAME)
            return

        confirm = forms.alert(
            'This will update {0} parameter value(s) in the model.\nSave/backup the model before continuing.\nContinue?'.format(len(to_update)),
            title=TOOL_NAME, yes=True, no=True,
        )
        if not confirm:
            return

        updated, failed = apply_updates(doc, self._diff_entries)
        forms.alert('Import finished.\nUpdated: {0}\nFailed: {1}'.format(updated, failed), title=TOOL_NAME)
        self._scan_import_file()

    # ---- common ----

    def OnCloseClick(self, sender, args):
        self.Close()


xaml_path = os.path.join(SCRIPT_DIR, 'ui.xaml')
window = ExcelDataWindow(xaml_path)
window.show(modal=True)
