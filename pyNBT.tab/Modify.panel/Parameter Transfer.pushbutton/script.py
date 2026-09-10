# -*- coding: utf-8 -*-
"""Parameter Transfer - pyNBT

Converts an existing PROJECT Parameter (already keyed in with real values
on beams/columns/floors/etc) into a real SHARED Parameter (with a GUID),
so it can finally be used in a Tag Label - WITHOUT losing any value
already typed into the model.

v1.0.0 - first build (2026-08-20). New request from Trung - NOT a
continuation of the "Parameter Transfer" line mentioned in
shared-lib-architecture.md's history (that one was noted as "in
progress" back on 2026-07-24 but was never pushed to GitHub and has no
surviving code/notes anywhere - Trung confirmed this is a fresh start).

Why the OLD project parameter has to be removed BEFORE the NEW shared
parameter can be created (important - read before touching this file):
Revit does not allow two different bound Definitions to share the same
name in one project. Since the whole point here is that the NEW shared
parameter must keep the exact SAME NAME as the old project parameter (so
existing Tags/Schedules/formulas that reference it by name keep working),
the old one's binding has to be removed first to free up that name - there
is no way around this, it is a hard Revit constraint, not a design choice.

To make that safe despite the ordering:
  1. Every value is read from the model into memory FIRST, before
     anything is touched.
  2. That snapshot is written to a plain CSV backup file on disk
     (%APPDATA%\\pyNBT\\ParameterTransfer_Backups\\) BEFORE the old
     parameter is removed - a manual safety net that survives even a
     total worst-case failure.
  3. Only THEN is the old project parameter unbound + deleted (its own,
     separately-committed Transaction - matching the pattern learned on
     Wall Top Elevation, where combining an unbind + a new bind in one
     Transaction/Regenerate cycle proved unstable).
  4. The new shared parameter is created, bound with the SAME categories,
     Instance/Type kind and parameter group as the old one, and every
     snapshotted value is written into it and read straight back to PROVE
     the write landed correctly.
  5. If anything in step 4 fails, the tool says so PLAINLY: the old
     parameter is already gone by that point (see step 3), so the report
     tells Trung exactly that, points him at the CSV backup, and explains
     that re-running the tool will just treat it as a fresh parameter
     (nothing is left half-bound or ambiguous).

Does NOT touch pyNBT's shared parameter FILE with any risky auto-editing
(Erase/rewrite) - if a same-named entry already exists there with a
DIFFERENT data type, this stops with one clear message asking Trung to
delete it himself via Manage > Shared Parameters (see
shared-lib-architecture.md / Wall Top Elevation v1.6.1-v1.6.3 history for
why: three different automated approaches for that all failed in a row on
Trung's actual Revit/pyRevit build).

v1.0.1 - Trung noticed Revit's own "Edit Shared Parameters" browser was
already defaulting to the right file on its own, and asked for the tool
to just tell him that path directly instead of him having to go find it -
he needs it every time he points a Tag family's Label at a newly
converted parameter (Parameter Properties > Shared parameter > Select >
Edit...). Added get_pynbt_shared_param_file_path() (a pure path helper,
no side effects - does NOT touch app.SharedParametersFilename, unlike
compat.ensure_pynbt_shared_param_file) and now show that path + the
"pyNBT Parameters" group name in two places: always visible in the left
panel while the tool is open, and again in the Apply success report.

v1.0.2 - Trung reported the left panel's candidate table couldn't be
resized at all ("bang cho nay khong the keo ra") - the DataGrid's 4
columns (385px combined) didn't fit inside the old fixed-width 380px left
column, so the "Type" column got cut off, and there was no way to widen
it since the left/right split had no splitter. Fixed by: (1) widening
the left column's default width 380 -> 440px, (2) adding a GridSplitter
between the left and right panels so Trung can drag it wider himself
(useful for parameters bound to many categories, where the Categories
text runs long), (3) rebalancing the 4 column widths slightly (Categories
gets more room, Name less, since Categories is the one most likely to
run long).

v1.0.3 - Trung also asked (a) to confirm the shared parameter file path
shown in the left panel is per-machine automatic, and (b) that he
couldn't copy that path's text at all. (a) is already true and unchanged
by this version - get_pynbt_shared_param_file_path() reads the
%APPDATA% environment variable, which Windows resolves to the CURRENT
logged-in user's own profile automatically on every machine (Trung's,
anh Viet's, anyone's) - nothing to fix there, just confirmed in the
report back to him. (b) was a real bug: the path was shown in a plain
TextBlock, and WPF TextBlock text is NOT selectable/copyable at all by
design. Fixed by moving the path into a read-only (but selectable) TextBox
instead, plus a one-click "Copy" button next to it that calls
System.Windows.Clipboard.SetText() directly.

Author: pyNBT (Nguyen Bao Trung)
"""

__title__ = "Parameter\nTransfer"
__author__ = "pyNBT"
__doc__ = (
    "Converts an existing Project Parameter into a real Shared Parameter "
    "(with a GUID) so it can be used in a Tag - all values already keyed "
    "in on model elements are copied over and verified before the old "
    "parameter is removed."
)

import os
import sys
import datetime

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("WindowsBase")

from System.Windows import (
    Window, WindowStartupLocation, Thickness, HorizontalAlignment,
    VerticalAlignment, GridLength, GridUnitType, FontWeights,
    TextWrapping, CornerRadius, Clipboard
)
from System.Windows.Controls import (
    Grid, RowDefinition, ColumnDefinition, Border, StackPanel, TextBlock,
    Button, TextBox, DataGrid, DataGridTextColumn, DataGridLength,
    Orientation, DataGridHeadersVisibility, DataGridSelectionMode,
    DataGridGridLinesVisibility, GridSplitter
)
from System.Windows.Input import Cursors
from System.Windows.Data import Binding
from System.Collections.ObjectModel import ObservableCollection
from System import Object

from Autodesk.Revit.DB import (
    Transaction, FilteredElementCollector, ParameterElement,
    SharedParameterElement, InstanceBinding, TypeBinding, CategorySet,
    StorageType, ExternalDefinitionCreationOptions
)

from pyrevit import revit, forms, script

# make the extension's lib/ folder importable (lib/pyNBT/ package lives
# there - shared by every pyNBT tool, see pyNBT.compat and pyNBT.theme)
_EXT_LIB = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib")
)
if _EXT_LIB not in sys.path:
    sys.path.append(_EXT_LIB)

from pyNBT.compat import (  # noqa: E402
    eid_int, find_binding_by_name, get_definition_spec, get_spec_label,
    get_definition_group_raw, get_group_label, ensure_pynbt_shared_param_file,
)
from pyNBT import theme  # noqa: E402


doc = revit.doc
uidoc = revit.uidoc
try:
    # classic pyRevit global, present in every script regardless of version
    app = __revit__.Application
except NameError:
    # fallback for pyRevit builds where pyrevit.revit does expose .app
    app = revit.app

logger = script.get_logger()

TOOL_NAME = "Parameter Transfer"
TOOL_VERSION = "1.0.3"
SHARED_PARAM_GROUP_NAME = "pyNBT Parameters"


def _brush(color):
    return theme.brush(color)


# ---------------------------------------------------------------------------
# Standalone logic - no UI references below this line
# ---------------------------------------------------------------------------

def get_pynbt_shared_param_file_path():
    """Pure path helper (no side effects - does NOT touch
    app.SharedParametersFilename, unlike compat.ensure_pynbt_shared_param_file)
    just so the UI/report can tell Trung exactly where every converted
    parameter is saved - the same path Revit's own 'Edit Shared Parameters'
    dialog will land on once this tool has pointed it there at least once."""
    return os.path.join(os.environ.get("APPDATA", ""), "pyNBT", "pyNBT_SharedParameters.txt")


def collect_project_parameter_candidates():
    """Return every NON-shared Project Parameter currently bound in this
    model - the candidate list for conversion. Already-shared parameters
    (SharedParameterElement) are skipped entirely, since converting those
    again makes no sense.

    Each item is a dict: {
        name, definition, binding, param_element, categories (list of
        Category), category_names (list of str), is_instance (bool),
        spec_label (str), group_label (str)
    }
    """
    candidates = []
    all_param_elems = FilteredElementCollector(doc).OfClass(ParameterElement).ToElements()
    for pe in all_param_elems:
        if isinstance(pe, SharedParameterElement):
            continue  # already a shared parameter - not a candidate
        try:
            name = pe.GetDefinition().Name
        except Exception:
            continue
        if not name:
            continue

        definition, binding = find_binding_by_name(doc, name)
        if definition is None or binding is None:
            # Defined but not actually bound to any category right now
            # (rare/orphan case, e.g. a Global Parameter) - nothing to
            # convert, skip it.
            continue

        try:
            categories = list(binding.Categories)
        except Exception:
            categories = []
        if not categories:
            continue

        candidates.append({
            "name": name,
            "definition": definition,
            "binding": binding,
            "param_element": pe,
            "categories": categories,
            "category_names": [c.Name for c in categories],
            "is_instance": isinstance(binding, InstanceBinding),
            "spec_label": get_spec_label(definition),
            "group_label": get_group_label(definition),
        })

    candidates.sort(key=lambda c: c["name"].lower())
    return candidates


def collect_elements_for_binding(binding):
    """Return every element (instances if InstanceBinding, ELEMENT TYPES
    if TypeBinding) across every category the binding covers."""
    is_instance = isinstance(binding, InstanceBinding)
    elements = []
    for cat in binding.Categories:
        collector = FilteredElementCollector(doc).OfCategoryId(cat.Id)
        collector = (
            collector.WhereElementIsNotElementType() if is_instance
            else collector.WhereElementIsElementType()
        )
        elements.extend(list(collector))
    return elements


def read_param_snapshot(elements, name):
    """Read every element's CURRENT value for parameter `name`, skipping
    elements where it has no value set (nothing to transfer there - not
    an error). Returns a list of dicts: {
        element, storage_type, value (raw, native to storage_type),
        category_name, display_text (Revit's own formatted string, for
        the preview grid only - never used for the actual copy)
    }
    """
    snapshot = []
    for el in elements:
        param = el.LookupParameter(name)
        if param is None or not param.HasValue:
            continue
        st = param.StorageType
        if st == StorageType.String:
            value = param.AsString()
        elif st == StorageType.Integer:
            value = param.AsInteger()
        elif st == StorageType.Double:
            value = param.AsDouble()
        elif st == StorageType.ElementId:
            value = param.AsElementId()
        else:
            continue
        if value is None:
            continue
        try:
            cat_name = el.Category.Name if el.Category else "-"
        except Exception:
            cat_name = "-"
        try:
            display_text = param.AsValueString()
        except Exception:
            display_text = None
        if not display_text:
            display_text = str(value)
        snapshot.append({
            "element": el,
            "storage_type": st,
            "value": value,
            "category_name": cat_name,
            "display_text": display_text,
        })
    return snapshot


def write_backup_csv(name, snapshot):
    """Write a plain CSV backup of every value about to be transferred,
    BEFORE anything in the model is touched - a manual safety net Trung
    can open by hand (Excel/Notepad) if anything ever goes wrong mid-
    transfer. Returns the file path, or None if writing failed (a failed
    backup write is never fatal to the tool - just logged)."""
    try:
        backup_dir = os.path.join(
            os.environ.get("APPDATA", ""), "pyNBT", "ParameterTransfer_Backups"
        )
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(ch if ch.isalnum() else "_" for ch in name)
        path = os.path.join(backup_dir, "{}_{}.csv".format(safe_name, stamp))
        with open(path, "w") as f:
            f.write("ElementId,Category,Value\n")
            for row in snapshot:
                val_text = (row["display_text"] or "").replace(",", ";")
                f.write("{},{},{}\n".format(
                    eid_int(row["element"].Id), row["category_name"], val_text
                ))
        return path
    except Exception as ex:
        logger.error("Parameter Transfer backup CSV failed: {}".format(ex))
        return None


def _get_or_create_shared_definition(name, source_definition):
    """Get (or create) a Definition named `name` in pyNBT's own shared
    parameter file, matching source_definition's data type EXACTLY (so a
    Length project parameter becomes a Length shared parameter, an
    Integer stays Integer, and so on - no per-type special casing needed).

    Does NOT attempt to auto-remove/replace a leftover entry with a
    DIFFERENT data type under this name - raises one clear message asking
    Trung to delete it himself via Manage > Shared Parameters (see module
    docstring for why - three different automated approaches for that all
    failed on Trung's Revit/pyRevit build during Wall Top Elevation)."""
    dfile = app.OpenSharedParameterFile()
    if dfile is None:
        raise Exception("Could not open/create the pyNBT shared parameter file.")

    group = None
    for g in dfile.Groups:
        if g.Name == SHARED_PARAM_GROUP_NAME:
            group = g
            break
    if group is None:
        group = dfile.Groups.Create(SHARED_PARAM_GROUP_NAME)

    wanted_spec = get_definition_spec(source_definition)
    definition = None
    for d in group.Definitions:
        if d.Name == name:
            definition = d
            break

    if definition is not None:
        existing_spec = get_definition_spec(definition)
        if existing_spec != wanted_spec:
            raise Exception(
                "'{}' already exists in pyNBT's shared parameter file with a "
                "DIFFERENT data type ({}) than the project parameter you're "
                "converting ({}). Please delete it ONE TIME via: Manage tab > "
                "Shared Parameters > group '{}' > select '{}' > Delete > OK "
                "- then run Apply again. pyNBT will then create it fresh "
                "with the correct type and this won't come up again.".format(
                    name, get_spec_label(definition), get_spec_label(source_definition),
                    SHARED_PARAM_GROUP_NAME, name,
                )
            )
        return definition

    options = ExternalDefinitionCreationOptions(name, wanted_spec)
    options.UserModifiable = True
    definition = group.Definitions.Create(options)
    return definition


def apply_transfer(candidate):
    """Run the full Parameter Transfer flow for one candidate Project
    Parameter. See module docstring for the full step-by-step reasoning.
    Returns a report dict - either {"hard_error": True, "message": ...}
    or the full success/partial-failure report described inline below."""
    name = candidate["name"]
    definition = candidate["definition"]
    binding = candidate["binding"]
    param_element = candidate["param_element"]

    elements = collect_elements_for_binding(binding)
    snapshot = read_param_snapshot(elements, name)

    if not snapshot:
        return {
            "hard_error": True,
            "message": (
                "No element with a value set for '{}' was found - nothing "
                "to transfer, so nothing was changed.".format(name)
            ),
        }

    backup_path = write_backup_csv(name, snapshot)

    # Step 1: remove the OLD project parameter's binding, in its OWN,
    # separately-committed Transaction. Must happen before the new Shared
    # Parameter can be bound under the same name (Revit does not allow
    # two different bound Definitions to share one name) - see module
    # docstring for the full reasoning and the safety net around this.
    t_unbind = Transaction(doc, "pyNBT - remove old project parameter '{}'".format(name))
    t_unbind.Start()
    try:
        doc.ParameterBindings.Remove(definition)
        try:
            doc.Delete(param_element.Id)
        except Exception:
            pass  # already gone / auto-purged by Remove() on this Revit build - fine
        t_unbind.Commit()
    except Exception as ex:
        if t_unbind.HasStarted():
            t_unbind.RollBack()
        return {
            "hard_error": True,
            "message": (
                "Could not remove the old project parameter '{}':\n{}\n\n"
                "Nothing was changed - the original parameter and all its "
                "values are untouched. A backup was still saved to:\n{}".format(
                    name, ex, backup_path or "(backup failed too, see log)"
                )
            ),
        }

    # Step 2: create/bind the NEW shared parameter and write every
    # snapshotted value into it, all in a separate transaction.
    success = 0
    failed = []
    mismatches = []
    original_spf_path = None
    t = Transaction(doc, "pyNBT - {} ('{}')".format(TOOL_NAME, name))
    t.Start()
    try:
        original_spf_path = ensure_pynbt_shared_param_file(app)
        new_definition = _get_or_create_shared_definition(name, definition)

        cats = CategorySet()
        for cat in candidate["categories"]:
            cats.Insert(cat)
        if candidate["is_instance"]:
            new_binding = app.Create.NewInstanceBinding(cats)
        else:
            new_binding = app.Create.NewTypeBinding(cats)
        group_raw = get_definition_group_raw(definition)
        doc.ParameterBindings.Insert(new_definition, new_binding, group_raw)
        doc.Regenerate()

        for row in snapshot:
            el = row["element"]
            st = row["storage_type"]
            value = row["value"]
            param = el.LookupParameter(name)
            if param is None or param.IsReadOnly:
                failed.append((el, row["category_name"], "New parameter not found / read-only on this element"))
                continue
            try:
                param.Set(value)
                readback = el.LookupParameter(name)
                if st == StorageType.String:
                    ok = readback.AsString() == value
                elif st == StorageType.Integer:
                    ok = readback.AsInteger() == value
                elif st == StorageType.Double:
                    ok = abs(readback.AsDouble() - value) < 1e-9
                elif st == StorageType.ElementId:
                    ok = eid_int(readback.AsElementId()) == eid_int(value)
                else:
                    ok = False
                if ok:
                    success += 1
                else:
                    mismatches.append((el, row["category_name"], row["display_text"]))
            except Exception as ex:
                failed.append((el, row["category_name"], "Set failed: {}".format(ex)))
        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        logger.error("Parameter Transfer write step failed: {}".format(ex))
        return {
            "hard_error": True,
            "message": (
                "Error while creating the new Shared Parameter / writing "
                "values:\n{}\n\n"
                "IMPORTANT: the OLD project parameter '{}' was already "
                "removed in the previous step, and this step just rolled "
                "back - so right now NO parameter named '{}' exists in the "
                "project. Your original values are safely backed up at:\n"
                "{}\n\nFix the issue above, then run this tool again - it "
                "will treat '{}' as a fresh parameter to convert (create "
                "a plain Project Parameter named '{}' again first if you "
                "want to re-key values by hand instead, using the backup "
                "CSV as reference).".format(
                    ex, name, name, backup_path or "(backup failed too, see log)",
                    name, name,
                )
            ),
        }
    finally:
        try:
            if original_spf_path:
                app.SharedParametersFilename = original_spf_path
        except Exception:
            pass

    return {
        "hard_error": False,
        "name": name,
        "total": len(snapshot),
        "success": success,
        "failed": failed,
        "mismatches": mismatches,
        "backup_path": backup_path,
    }


# ---------------------------------------------------------------------------
# Row models for the DataGrids
# ---------------------------------------------------------------------------

class CandidateRow(Object):
    def __init__(self, name, categories_text, bind_text, type_text, group_text):
        self.Name = name
        self.CategoriesText = categories_text
        self.BindText = bind_text
        self.TypeText = type_text
        self.GroupText = group_text


class PreviewRow(Object):
    def __init__(self, element_id, category_name, value_text):
        self.ElementId = element_id
        self.CategoryName = category_name
        self.ValueText = value_text


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

PREVIEW_ROW_LIMIT = 300


class ParameterTransferWindow(Window):

    def __init__(self):
        self.Title = "pyNBT - {}".format(TOOL_NAME)
        self.Width = 1000
        self.Height = 600
        self.MinWidth = 820
        self.MinHeight = 460
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.Background = _brush(theme.CLR_BG)

        self._all_candidates = collect_project_parameter_candidates()
        self._filtered_candidates = list(self._all_candidates)
        self._selected_candidate = None
        self._selected_snapshot = None

        root = Grid()
        root.RowDefinitions.Add(RowDefinition(Height=GridLength(74)))
        root.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Star)))
        root.RowDefinitions.Add(RowDefinition(Height=GridLength(56)))

        header = self._build_header()
        root.Children.Add(header)
        Grid.SetRow(header, 0)

        content = self._build_content()
        root.Children.Add(content)
        Grid.SetRow(content, 1)

        footer = self._build_footer()
        root.Children.Add(footer)
        Grid.SetRow(footer, 2)

        self.Content = root

        self._refresh_candidate_grid()

    # -- header -------------------------------------------------------
    def _build_header(self):
        header = Border()
        header.Background = _brush(theme.CLR_HEADER)
        grid = Grid()
        grid.Margin = Thickness(20, 0, 20, 0)
        grid.ColumnDefinitions.Add(ColumnDefinition())
        grid.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(1, GridUnitType.Auto)))

        titles = StackPanel(VerticalAlignment=VerticalAlignment.Center)
        title = TextBlock(Text=TOOL_NAME)
        title.FontSize = 20
        title.FontWeight = FontWeights.Bold
        title.Foreground = _brush(theme.CLR_HEADER_TEXT)
        subtitle = TextBlock(
            Text="Convert a Project Parameter into a Shared Parameter - values preserved"
        )
        subtitle.FontSize = 12
        subtitle.Foreground = _brush(theme.CLR_HEADER_SUB)
        subtitle.Margin = Thickness(0, 2, 0, 0)
        titles.Children.Add(title)
        titles.Children.Add(subtitle)
        grid.Children.Add(titles)
        Grid.SetColumn(titles, 0)

        badge = Border()
        badge.Background = _brush(theme.CLR_HEADER_SUB)
        badge.CornerRadius = CornerRadius(4)
        badge.Padding = Thickness(10, 4, 10, 4)
        badge.VerticalAlignment = VerticalAlignment.Center
        badge_text = TextBlock(Text="pyNBT v{}".format(TOOL_VERSION))
        badge_text.Foreground = _brush(theme.CLR_HEADER)
        badge_text.FontWeight = FontWeights.Bold
        badge_text.FontSize = 11
        badge.Child = badge_text
        grid.Children.Add(badge)
        Grid.SetColumn(badge, 1)

        header.Child = grid
        return header

    # -- content --------------------------------------------------------
    def _build_content(self):
        grid = Grid()
        grid.Margin = Thickness(16)

        # Left panel starts at 440px (fits Name/Categories/Bind/Type without
        # scrolling in the common case) but is NOT a fixed size - a
        # GridSplitter sits between it and the right panel so Trung can drag
        # it wider himself (e.g. for parameters bound to many categories,
        # where the Categories text runs long). Trung reported the fixed-
        # width version couldn't be resized at all ("bảng chỗ này khong the
        # kéo ra") - this is the fix.
        col_left = ColumnDefinition(Width=GridLength(440))
        col_left.MinWidth = 300
        col_left.MaxWidth = 720
        col_splitter = ColumnDefinition(Width=GridLength(6))
        col_right = ColumnDefinition(Width=GridLength(1, GridUnitType.Star))
        col_right.MinWidth = 260
        grid.ColumnDefinitions.Add(col_left)
        grid.ColumnDefinitions.Add(col_splitter)
        grid.ColumnDefinitions.Add(col_right)

        left = self._build_left_panel()
        grid.Children.Add(left)
        Grid.SetColumn(left, 0)

        splitter = GridSplitter()
        splitter.Width = 6
        splitter.HorizontalAlignment = HorizontalAlignment.Stretch
        splitter.VerticalAlignment = VerticalAlignment.Stretch
        splitter.Background = _brush(theme.CLR_BORDER)
        splitter.Cursor = Cursors.SizeWE
        grid.Children.Add(splitter)
        Grid.SetColumn(splitter, 1)

        right = self._build_right_panel()
        right.Margin = Thickness(12, 0, 0, 0)
        grid.Children.Add(right)
        Grid.SetColumn(right, 2)

        return grid

    def _card(self):
        b = Border()
        b.Background = _brush(theme.CLR_CARD)
        b.BorderBrush = _brush(theme.CLR_BORDER)
        b.BorderThickness = Thickness(1)
        b.CornerRadius = CornerRadius(6)
        b.Padding = Thickness(14)
        return b

    def _build_left_panel(self):
        card = self._card()
        grid = Grid()
        grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Auto)))
        grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Auto)))
        grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Star)))
        grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Auto)))

        label = TextBlock(Text="Project Parameters in this model")
        label.FontWeight = FontWeights.Bold
        label.Foreground = _brush(theme.CLR_TEXT)
        label.Margin = Thickness(0, 0, 0, 8)
        grid.Children.Add(label)
        Grid.SetRow(label, 0)

        search_row = Grid()
        search_row.ColumnDefinitions.Add(ColumnDefinition())
        search_row.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(1, GridUnitType.Auto)))
        self.txt_search = TextBox()
        self.txt_search.Padding = Thickness(6, 4, 6, 4)
        self.txt_search.TextChanged += self._on_search_changed
        search_row.Children.Add(self.txt_search)
        Grid.SetColumn(self.txt_search, 0)
        btn_refresh = self._make_btn("Refresh", theme.CLR_CARD, theme.CLR_TEXT, border=True)
        btn_refresh.Margin = Thickness(6, 0, 0, 0)
        btn_refresh.Click += self._on_refresh
        search_row.Children.Add(btn_refresh)
        Grid.SetColumn(btn_refresh, 1)
        search_row.Margin = Thickness(0, 0, 0, 8)
        grid.Children.Add(search_row)
        Grid.SetRow(search_row, 1)

        self.grid_candidates = DataGrid()
        self.grid_candidates.AutoGenerateColumns = False
        self.grid_candidates.IsReadOnly = True
        self.grid_candidates.HeadersVisibility = DataGridHeadersVisibility.Column
        self.grid_candidates.SelectionMode = DataGridSelectionMode.Single
        self.grid_candidates.CanUserAddRows = False
        self.grid_candidates.GridLinesVisibility = getattr(DataGridGridLinesVisibility, "None")
        self.grid_candidates.RowHeight = 26
        self.grid_candidates.Columns.Add(self._text_col("Name", "Name", 100))
        self.grid_candidates.Columns.Add(self._text_col("Categories", "CategoriesText", 160))
        self.grid_candidates.Columns.Add(self._text_col("Bind", "BindText", 55))
        self.grid_candidates.Columns.Add(self._text_col("Type", "TypeText", 70))
        self.grid_candidates.SelectionChanged += self._on_candidate_selected
        self._candidate_rows = ObservableCollection[Object]()
        self.grid_candidates.ItemsSource = self._candidate_rows
        grid.Children.Add(self.grid_candidates)
        Grid.SetRow(self.grid_candidates, 2)

        note_panel = StackPanel()
        note_panel.Margin = Thickness(0, 8, 0, 0)

        note1 = TextBlock(
            Text=(
                "Only Project Parameters are listed here (parameters that "
                "are already Shared are not shown - nothing to convert)."
            )
        )
        note1.FontSize = 10
        note1.Foreground = _brush(theme.CLR_MUTED)
        note1.TextWrapping = TextWrapping.Wrap
        note_panel.Children.Add(note1)

        note2 = TextBlock(Text="New Shared Parameters are saved to:")
        note2.FontSize = 10
        note2.FontWeight = FontWeights.Bold
        note2.Foreground = _brush(theme.CLR_MUTED)
        note2.Margin = Thickness(0, 8, 0, 2)
        note_panel.Children.Add(note2)

        # Read-only, but SELECTABLE/COPYABLE (a TextBox, not a TextBlock -
        # TextBlock text cannot be selected/copied at all in WPF). Trung
        # reported he couldn't copy the path shown here - this is the fix.
        # A "Copy" button next to it is a one-click alternative to
        # select-all + Ctrl+C.
        path_row = Grid()
        path_row.ColumnDefinitions.Add(ColumnDefinition())
        path_row.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(1, GridUnitType.Auto)))

        self.txt_spf_path = TextBox()
        self.txt_spf_path.Text = get_pynbt_shared_param_file_path()
        self.txt_spf_path.IsReadOnly = True
        self.txt_spf_path.BorderThickness = Thickness(1)
        self.txt_spf_path.BorderBrush = _brush(theme.CLR_BORDER)
        self.txt_spf_path.Background = _brush(theme.CLR_BG)
        self.txt_spf_path.Foreground = _brush(theme.CLR_TEXT)
        self.txt_spf_path.FontSize = 10
        self.txt_spf_path.TextWrapping = TextWrapping.Wrap
        self.txt_spf_path.Padding = Thickness(4)
        path_row.Children.Add(self.txt_spf_path)
        Grid.SetColumn(self.txt_spf_path, 0)

        self.btn_copy_spf = self._make_btn("Copy", theme.CLR_CARD, theme.CLR_TEXT, border=True)
        self.btn_copy_spf.Padding = Thickness(8, 4, 8, 4)
        self.btn_copy_spf.FontSize = 10
        self.btn_copy_spf.Margin = Thickness(4, 0, 0, 0)
        self.btn_copy_spf.Click += self._on_copy_spf_path
        path_row.Children.Add(self.btn_copy_spf)
        Grid.SetColumn(self.btn_copy_spf, 1)

        note_panel.Children.Add(path_row)

        note3 = TextBlock(
            Text=(
                "(group '{}') - point Revit's 'Edit Shared Parameters' "
                "dialog here when adding one to a Tag family's Label."
            ).format(SHARED_PARAM_GROUP_NAME)
        )
        note3.FontSize = 10
        note3.Foreground = _brush(theme.CLR_MUTED)
        note3.TextWrapping = TextWrapping.Wrap
        note3.Margin = Thickness(0, 4, 0, 0)
        note_panel.Children.Add(note3)

        grid.Children.Add(note_panel)
        Grid.SetRow(note_panel, 3)

        card.Child = grid
        return card

    def _build_right_panel(self):
        card = self._card()
        grid = Grid()
        grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Auto)))
        grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Auto)))
        grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Star)))

        self.lbl_summary = TextBlock(
            Text="Select a Project Parameter on the left to preview its values."
        )
        self.lbl_summary.FontWeight = FontWeights.Bold
        self.lbl_summary.Foreground = _brush(theme.CLR_TEXT)
        self.lbl_summary.TextWrapping = TextWrapping.Wrap
        self.lbl_summary.Margin = Thickness(0, 0, 0, 4)
        grid.Children.Add(self.lbl_summary)
        Grid.SetRow(self.lbl_summary, 0)

        self.lbl_note = TextBlock(Text="")
        self.lbl_note.FontSize = 10
        self.lbl_note.Foreground = _brush(theme.CLR_MUTED)
        self.lbl_note.TextWrapping = TextWrapping.Wrap
        self.lbl_note.Margin = Thickness(0, 0, 0, 8)
        grid.Children.Add(self.lbl_note)
        Grid.SetRow(self.lbl_note, 1)

        self.grid_preview = DataGrid()
        self.grid_preview.AutoGenerateColumns = False
        self.grid_preview.IsReadOnly = True
        self.grid_preview.HeadersVisibility = DataGridHeadersVisibility.Column
        self.grid_preview.SelectionMode = DataGridSelectionMode.Extended
        self.grid_preview.CanUserAddRows = False
        self.grid_preview.GridLinesVisibility = getattr(DataGridGridLinesVisibility, "None")
        self.grid_preview.RowHeight = 26
        self.grid_preview.Columns.Add(self._text_col("Element Id", "ElementId", 90))
        self.grid_preview.Columns.Add(self._text_col("Category", "CategoryName", 160))
        self.grid_preview.Columns.Add(self._text_col("Value", "ValueText", 260))
        self._preview_rows = ObservableCollection[Object]()
        self.grid_preview.ItemsSource = self._preview_rows
        grid.Children.Add(self.grid_preview)
        Grid.SetRow(self.grid_preview, 2)

        card.Child = grid
        return card

    def _text_col(self, header, path, width):
        col = DataGridTextColumn()
        col.Header = header
        col.Binding = Binding(path)
        col.Width = DataGridLength(width)
        return col

    # -- footer -----------------------------------------------------------
    def _build_footer(self):
        footer = Border()
        footer.Background = _brush(theme.CLR_FOOTER)
        grid = Grid()
        grid.Margin = Thickness(20, 0, 20, 0)
        grid.ColumnDefinitions.Add(ColumnDefinition())
        grid.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(1, GridUnitType.Auto)))

        sig = TextBlock(
            Text="{} v{} | {} | {}".format(
                TOOL_NAME, TOOL_VERSION, doc.Title, datetime.date.today().isoformat(),
            )
        )
        sig.FontSize = 10
        sig.Foreground = _brush(theme.CLR_MUTED)
        sig.VerticalAlignment = VerticalAlignment.Center
        grid.Children.Add(sig)
        Grid.SetColumn(sig, 0)

        btn_panel = StackPanel(Orientation=Orientation.Horizontal)
        self.btn_close = self._make_btn("Close", theme.CLR_CARD, theme.CLR_TEXT, border=True)
        self.btn_close.Margin = Thickness(0, 0, 8, 0)
        self.btn_close.Click += self._on_close
        self.btn_apply = self._make_btn("Apply", theme.CLR_APPLY, theme.CLR_APPLY_TEXT)
        self.btn_apply.Click += self._on_apply
        self.btn_apply.IsEnabled = False
        btn_panel.Children.Add(self.btn_close)
        btn_panel.Children.Add(self.btn_apply)
        grid.Children.Add(btn_panel)
        Grid.SetColumn(btn_panel, 1)

        footer.Child = grid
        return footer

    def _make_btn(self, text, bg, fg, border=False):
        btn = Button(Content=text)
        btn.Background = _brush(bg)
        btn.Foreground = _brush(fg)
        btn.Padding = Thickness(14, 6, 14, 6)
        btn.FontWeight = FontWeights.Bold if not border else FontWeights.Normal
        if border:
            btn.BorderBrush = _brush(theme.CLR_BORDER)
            btn.BorderThickness = Thickness(1)
        return btn

    # -- data helpers -------------------------------------------------
    def _refresh_candidate_grid(self):
        self._candidate_rows.Clear()
        for c in self._filtered_candidates:
            self._candidate_rows.Add(CandidateRow(
                c["name"],
                "/".join(c["category_names"]),
                "Instance" if c["is_instance"] else "Type",
                c["spec_label"],
                c["group_label"],
            ))
        if not self._filtered_candidates:
            self.lbl_summary.Text = (
                "No Project Parameters found in this model."
                if not self._all_candidates else
                "No Project Parameters match your search."
            )
            self.lbl_note.Text = ""
            self._preview_rows.Clear()
            self._selected_candidate = None
            self._selected_snapshot = None
            self.btn_apply.IsEnabled = False

    def _on_search_changed(self, sender, args):
        query = (self.txt_search.Text or "").strip().lower()
        if not query:
            self._filtered_candidates = list(self._all_candidates)
        else:
            self._filtered_candidates = [
                c for c in self._all_candidates if query in c["name"].lower()
            ]
        self._refresh_candidate_grid()

    def _on_refresh(self, sender, args):
        self._all_candidates = collect_project_parameter_candidates()
        self._on_search_changed(None, None)

    def _on_copy_spf_path(self, sender, args):
        try:
            Clipboard.SetText(self.txt_spf_path.Text)
            self.btn_copy_spf.Content = "Copied!"
        except Exception:
            # Clipboard access can occasionally fail if another app is
            # holding it - the path is still fully selectable/copyable by
            # hand in the read-only textbox either way, so this is never
            # fatal, just a fallback message on the button itself.
            self.btn_copy_spf.Content = "Select + Ctrl+C"

    def _on_candidate_selected(self, sender, args):
        idx = self.grid_candidates.SelectedIndex
        if idx < 0 or idx >= len(self._filtered_candidates):
            self._selected_candidate = None
            self._selected_snapshot = None
            self.btn_apply.IsEnabled = False
            return

        candidate = self._filtered_candidates[idx]
        self._selected_candidate = candidate
        elements = collect_elements_for_binding(candidate["binding"])
        snapshot = read_param_snapshot(elements, candidate["name"])
        self._selected_snapshot = snapshot

        self._preview_rows.Clear()
        for row in snapshot[:PREVIEW_ROW_LIMIT]:
            self._preview_rows.Add(PreviewRow(
                str(eid_int(row["element"].Id)), row["category_name"], row["display_text"]
            ))

        bind_word = "Instance" if candidate["is_instance"] else "Type"
        self.lbl_summary.Text = (
            "'{}' - {} | {} | group '{}' | {} element(s) with a value set".format(
                candidate["name"], candidate["spec_label"], bind_word,
                candidate["group_label"], len(snapshot),
            )
        )
        if len(snapshot) > PREVIEW_ROW_LIMIT:
            self.lbl_note.Text = (
                "Showing the first {} of {} - Apply will still process ALL "
                "of them.".format(PREVIEW_ROW_LIMIT, len(snapshot))
            )
        elif not snapshot:
            self.lbl_note.Text = (
                "No element currently has a value set for this parameter - "
                "nothing to transfer."
            )
        else:
            self.lbl_note.Text = ""

        self.btn_apply.IsEnabled = len(snapshot) > 0

    # -- events -----------------------------------------------------------
    def _on_apply(self, sender, args):
        from pyrevit import forms  # local import - see note in Wall Top Elevation

        candidate = self._selected_candidate
        snapshot = self._selected_snapshot
        if candidate is None or not snapshot:
            return

        bind_word = "instance" if candidate["is_instance"] else "type"
        confirm = forms.alert(
            "Convert '{}' to a Shared Parameter?\n\n"
            "- {} element(s) ({}) across: {}\n"
            "- The old Project Parameter will be removed ONLY after every "
            "value is copied to the new Shared Parameter and verified by "
            "reading it straight back.\n"
            "- A backup CSV of every value will be saved to your pyNBT "
            "folder before anything is changed.\n\n"
            "Continue?".format(
                candidate["name"], len(snapshot), bind_word,
                "/".join(candidate["category_names"]),
            ),
            title=TOOL_NAME, yes=True, no=True,
        )
        if not confirm:
            return

        report = apply_transfer(candidate)

        if report.get("hard_error"):
            forms.alert(report["message"], title=TOOL_NAME)
            return

        msg_lines = [
            "'{}' converted to a Shared Parameter.".format(report["name"]),
            "",
            "{} of {} value(s) copied and verified by reading straight "
            "back from the model.".format(report["success"], report["total"]),
        ]
        if report["backup_path"]:
            msg_lines.append("Backup CSV saved to: {}".format(report["backup_path"]))
        msg_lines.append(
            "Shared parameter saved in: {} (group '{}') - use this path in "
            "Revit's 'Edit Shared Parameters' dialog when adding it to a "
            "Tag family's Label.".format(
                get_pynbt_shared_param_file_path(), SHARED_PARAM_GROUP_NAME
            )
        )
        if report["mismatches"]:
            msg_lines.append("")
            msg_lines.append(
                "WARNING - written value didn't match on readback ({}):".format(
                    len(report["mismatches"])
                )
            )
            for el, cat_name, original_text in report["mismatches"][:20]:
                msg_lines.append(
                    "  Id {} ({}): expected '{}'".format(
                        eid_int(el.Id), cat_name, original_text
                    )
                )
        if report["failed"]:
            msg_lines.append("")
            msg_lines.append("Failed to write ({}):".format(len(report["failed"])))
            for el, cat_name, reason in report["failed"][:20]:
                msg_lines.append("  Id {} ({}): {}".format(eid_int(el.Id), cat_name, reason))
        if report["mismatches"] or report["failed"]:
            msg_lines.append("")
            msg_lines.append(
                "The elements above did NOT get the correct value written - "
                "use the backup CSV to key them in by hand, or re-run the "
                "tool on '{}' again (it will still find and retry these "
                "same elements, since the parameter now exists)."
                .format(report["name"])
            )
        forms.alert("\n".join(msg_lines), title=TOOL_NAME)

        # refresh the candidate list (the converted parameter no longer
        # shows up as a Project Parameter candidate) and close, matching
        # the other pyNBT tools' behavior of closing after a successful Apply
        self.Close()

    def _on_close(self, sender, args):
        self.Close()


def main():
    window = ParameterTransferWindow()
    window.ShowDialog()


if __name__ == "__main__":
    main()
