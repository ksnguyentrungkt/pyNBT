# -*- coding: utf-8 -*-
"""Wall Top Elevation - pyNBT

Computes each wall's Top Elevation (Base Level elevation + Base Offset +
Unconnected Height, in meters) and writes it into a project parameter named
"Elevation at Top" on the Wall category. Ported from a Dynamo graph
("Get Wall Elevation at Top 2.dyn") into a native pyRevit tool.

v1.2 - rolled back from the v1.3 modeless/singleton/Pick-Walls experiment
at Trung's request (that upgrade needs more work before it's stable). This
version is modal (ShowDialog) again and only supports "All walls in the
active view".

v1.4 - Pick Walls added back in, on its own this time (still modal, no
ExternalEvent/modeless plumbing - that upgrade is still deferred). Fixes
applied vs the earlier attempt: the selection filter class now correctly
inherits directly from Autodesk.Revit.UI.Selection.ISelectionFilter (a
plain "class Foo(object)" silently fails at the IronPython/.NET interop
boundary), and only OperationCanceledException is caught around
PickObjects - not a broad except that could swallow real errors.

v1.4.1 - fixed a hard Revit crash (not just a script error) that happened
on Apply when "Elevation at Top" already existed as a Length-type
parameter. Root cause: removing the old binding and inserting the new
Number-type one back-to-back inside ONE Transaction/Regenerate cycle is
unstable. Fix: the removal now happens in its OWN, separately committed
Transaction (unbind_length_parameter_if_needed()) before the main
create/write Transaction starts. (Did NOT fix the crash Trung was
actually hitting - see v1.5.)

v1.5 - Trung confirmed "Calculate (Active View)" never crashed (even
Apply'd twice), only the Pick Walls flow did - isolating the real cause
to Hide()/PickObjects()/Show() nesting Revit's picking modal loop inside
this window's still-active ShowDialog() modal loop. Fixed by never
picking while any pyNBT window is open at all: _on_pick_walls() now just
Closes the window and sets a flag; the __main__ loop does the actual
PickObjects() with no window open, then re-opens a fresh window
preloaded with the picked walls. Also tried force-rounding PARAM_NAME's
display to 3 decimals via FormatOptions even for a Number parameter -
this did NOT actually fix the '32.250000' trailing-zeros issue Trung was
still seeing (see v1.6).

v1.6 - gave up on FormatOptions overrides entirely (this is the second
time they failed to reliably control what Revit displays, after also
failing for the earlier Length/mm issue) and switched PARAM_NAME to a
Text parameter instead. Text has no unit and no numeric rounding at
all - whatever string pyNBT writes ("32.250") is EXACTLY what Revit
shows, forever, on every Revit version, regardless of Project Units.
unbind_wrong_type_if_needed() (renamed from
unbind_length_parameter_if_needed) now auto-converts ANY non-Text
binding (Length OR Number) the same way. Also: Apply now closes the tool
window automatically after showing the results popup, since Trung
doesn't need to keep it open once the update is done.

v1.6.1 - fixed 'ExternalDefinitions' object has no attribute 'Erase' on
Trung's pyRevit/Revit build - that method doesn't exist there, so
_get_or_create_definition() now removes an old, wrong-type entry from
pyNBT's OWN shared parameter file by directly rewriting its plain-text
TSV content (_remove_shared_param_line()) instead of calling a Revit API
method that isn't available. Also stopped showing a second, confusing
"0 walls updated" summary popup on top of a hard-error alert - the
window now also stays open (doesn't auto-close) after a hard error, so
Trung can just retry without reopening the tool.

v1.6.2 - fixed "Name is already present in the associated shared
parameter definitions" right after the v1.6.1 fix. Root cause:
re-assigning Application.SharedParametersFilename to the SAME path it
was already set to is a no-op on this build - it does NOT force Revit to
re-read the file from disk, so OpenSharedParameterFile() kept returning
a stale, cached copy that still had the old (just-deleted-on-disk) entry
- Create() then failed with a duplicate-name error. Fixed by toggling
the property through an empty value first (guarantees Revit detects an
actual change and reloads), and added a post-reload check that raises a
clear error (suggesting a full Revit restart) if the old entry is
somehow STILL visible, instead of silently retrying Create() into the
same failure. (This STILL didn't work reliably - see v1.6.3.)

v1.6.3 - after 3 different automated approaches for auto-removing a
leftover non-Text shared parameter entry each failed on Trung's
Revit/pyRevit build in a row (a missing 'Erase' API, then a file-reload
caching issue, then the text-editing not matching this build's actual
file format), gave up on automating that removal entirely at Trung's
request. _get_or_create_definition() is back to simple: create the Text
Definition fresh if it's not there; if a non-Text entry with the same
name already exists, raise ONE clear message telling Trung to delete it
himself via Manage > Shared Parameters (a normal, fully-supported Revit
UI action) - a one-time manual step per leftover parameter, after which
every future run just works with no special-casing needed.

Author: pyNBT (Nguyen Bao Trung)
"""

__title__ = "Wall Top\nElevation"
__author__ = "pyNBT"
__doc__ = (
    "Calculates the Top Elevation of all walls in the active view and "
    "writes the result into the 'Elevation at Top' project parameter, "
    "in meters (3 decimal places)."
)

import os
import sys

import clr
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("WindowsBase")
clr.AddReference("System.Windows.Forms")

from System.Windows import (
    Window, WindowStartupLocation, Thickness, HorizontalAlignment,
    VerticalAlignment, GridLength, GridUnitType, FontWeight, FontWeights,
    TextWrapping, SizeToContent, CornerRadius
)
from System.Windows.Controls import (
    Grid, RowDefinition, ColumnDefinition, Border, StackPanel, TextBlock,
    Button, DataGrid, DataGridTextColumn, DataGridLength,
    Orientation, ScrollBarVisibility, DataGridHeadersVisibility,
    DataGridSelectionMode, DataGridGridLinesVisibility
)
from System.Windows.Data import Binding
from System.Windows.Media import Color, SolidColorBrush, FontFamily
from System.Collections.ObjectModel import ObservableCollection
from System import Object

from Autodesk.Revit.DB import (
    Transaction, BuiltInCategory, BuiltInParameter, Level,
    ExternalDefinitionCreationOptions, CategorySet, StorageType
)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import revit, forms, script

# make the extension's lib/ folder importable (lib/pyNBT/ package lives
# there - shared by every pyNBT tool, see pyNBT.compat and pyNBT.theme)
_EXT_LIB = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib")
)
if _EXT_LIB not in sys.path:
    sys.path.append(_EXT_LIB)

from pyNBT.compat import (  # noqa: E402
    eid_int, internal_to_m, m_to_internal, get_number_spec, get_text_spec,
    get_dimensions_param_group, param_is_length, find_binding_categories_for_name,
    get_group_label, definition_is_length, definition_is_text,
    build_meters_format_options, build_number_format_options,
    force_definition_format,
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

TOOL_NAME = "Wall Top Elevation"
TOOL_VERSION = "1.6.3"
PARAM_NAME = "Elevation at Top"
SHARED_PARAM_GROUP_NAME = "pyNBT Parameters"


def _brush(color):
    return theme.brush(color)


# ---------------------------------------------------------------------------
# Standalone logic - no UI references below this line
# ---------------------------------------------------------------------------

def get_walls_in_active_view():
    """Collect every Wall instance visible in the active view."""
    from Autodesk.Revit.DB import FilteredElementCollector
    view = doc.ActiveView
    collector = (
        FilteredElementCollector(doc, view.Id)
        .OfCategory(BuiltInCategory.OST_Walls)
        .WhereElementIsNotElementType()
    )
    return list(collector)


class _WallSelectionFilter(ISelectionFilter):
    """Restricts interactive picking to Wall elements only.

    IMPORTANT: this class MUST inherit directly from
    Autodesk.Revit.UI.Selection.ISelectionFilter (not a plain 'object'
    subclass) - that was the root cause of an earlier bug where
    PickObjects() silently returned nothing: a plain-object filter fails
    at the IronPython/.NET interop boundary instead of raising a visible
    error.
    """

    def AllowElement(self, element):
        try:
            walls_cat_id = eid_int(
                doc.Settings.Categories.get_Item(BuiltInCategory.OST_Walls).Id
            )
            return (
                element.Category is not None
                and eid_int(element.Category.Id) == walls_cat_id
            )
        except Exception:
            return False

    def AllowReference(self, reference, point):
        return False


def pick_walls_interactively():
    """Let Trung click walls in the model, Finish to confirm. Returns the
    list of picked Wall elements, or [] if he cancelled (Escape) or
    finished without picking anything.

    IMPORTANT: this must be called with NO pyNBT WPF window open at all
    (not even Hidden). An earlier version called this with the tool
    window merely Hide()-d, which nests Revit's own PickObjects() modal
    loop inside the WPF window's still-active ShowDialog() modal loop -
    that combination caused a genuine Revit fatal-error crash (not just a
    script exception) as soon as a Transaction ran afterwards. The
    __main__ loop at the bottom of this file now fully Closes the window
    before calling this function, and only opens a fresh window again
    afterwards - so there is never more than one modal loop active at
    once, which is the pattern Revit's API actually supports safely.

    Only OperationCanceledException is caught around PickObjects() - NOT
    a broad 'except Exception' - so any real error stays visible instead
    of being silently swallowed as "nothing picked".
    """
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            _WallSelectionFilter(),
            "Select wall(s), then click Finish (or press Escape to cancel)",
        )
        return [doc.GetElement(r.ElementId) for r in refs]
    except OperationCanceledException:
        return []


def compute_top_elevation(wall):
    """Compute a single wall's top elevation in meters.

    Returns a dict: {
        'wall': wall, 'level_name': str, 'value': float or None,
        'status': 'ok' or 'skip', 'reason': str
    }
    Logic (ported 1:1 from the Dynamo graph):
        top = BaseLevel.Elevation + Wall.Base Offset + Wall.Unconnected Height
    Note: internal Revit units are decimal feet, not mm, so the mm/1000 step
    from Dynamo is replaced here by a direct internal-units -> meters
    conversion (compat.internal_to_m); the resulting number is the same
    real-world elevation in meters, measured from the Internal Origin
    (cos 0.00 of the model).
    """
    result = {
        "wall": wall, "level_name": "", "value": None,
        "status": "skip", "reason": "",
    }

    base_constraint = wall.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT)
    if base_constraint is None or base_constraint.AsElementId() is None:
        result["reason"] = "No Base Constraint parameter"
        return result

    level = doc.GetElement(base_constraint.AsElementId())
    if not isinstance(level, Level):
        result["reason"] = "Base Constraint level not found (deleted?)"
        return result
    result["level_name"] = level.Name

    base_offset_param = wall.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET)
    height_param = wall.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM)

    if base_offset_param is None or height_param is None:
        result["reason"] = "Missing Base Offset / Unconnected Height parameter"
        return result

    try:
        base_offset_ft = base_offset_param.AsDouble()
        height_ft = height_param.AsDouble()
        top_internal_ft = level.Elevation + base_offset_ft + height_ft
        value_m = round(internal_to_m(top_internal_ft), 3)
    except Exception as ex:
        result["reason"] = "Calculation error: {}".format(ex)
        return result

    result["status"] = "ok"
    result["value"] = value_m
    return result


def find_wall_bound_definition():
    """Return the Definition currently bound to the Walls category under
    PARAM_NAME, or None if PARAM_NAME isn't bound to Walls (yet)."""
    it = doc.ParameterBindings.ForwardIterator()
    it.Reset()
    walls_cat_id = eid_int(doc.Settings.Categories.get_Item(BuiltInCategory.OST_Walls).Id)
    while it.MoveNext():
        definition = it.Key
        if definition.Name == PARAM_NAME:
            binding = it.Current
            for cat in binding.Categories:
                if eid_int(cat.Id) == walls_cat_id:
                    return definition
    return None


def wall_parameter_is_bound():
    """Check whether PARAM_NAME is already bound to the Walls category."""
    return find_wall_bound_definition() is not None


def _ensure_shared_param_file():
    """Point the Revit application at a pyNBT-owned shared parameter file,
    creating it if needed. Returns the ORIGINAL path so callers can restore
    it afterwards and not disturb whatever shared parameter file Trung's
    project was already using."""
    original_path = app.SharedParametersFilename

    pynbt_dir = os.path.join(os.environ.get("APPDATA", ""), "pyNBT")
    if not os.path.exists(pynbt_dir):
        os.makedirs(pynbt_dir)
    spf_path = os.path.join(pynbt_dir, "pyNBT_SharedParameters.txt")

    if not os.path.exists(spf_path):
        with open(spf_path, "w") as f:
            f.write("# This is a Revit shared parameter file.\n")
            f.write("# Do not edit this file manually.\n")
            f.write("*META\tVERSION\tMINVERSION\n")
            f.write("META\t2\t1\n")
            f.write("*GROUP\tID\tNAME\n")
            f.write("*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\t"
                    "VISIBLE\tDESCRIPTION\tUSERMODIFIABLE\n")

    app.SharedParametersFilename = spf_path
    return original_path


def _get_or_create_definition():
    """Get (or create) the Text Definition for PARAM_NAME in pyNBT's own
    shared parameter file.

    Does NOT attempt to automatically remove/replace a leftover non-Text
    entry - three different automated approaches for that (a Revit API
    'Erase' call, then a file-reload trick, then a duplicate-name retry)
    each hit a different Revit/pyRevit-build-specific failure in a row.
    Instead, if a non-Text entry with this name already exists, this
    raises ONE clear, simple message telling Trung to delete it himself
    via Manage > Shared Parameters (a normal, fully-supported Revit UI
    action) - a one-time manual step, after which every future run just
    creates the Text version fresh with no special-casing needed at all.
    """
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

    definition = None
    for d in group.Definitions:
        if d.Name == PARAM_NAME:
            definition = d
            break

    if definition is not None and not definition_is_text(definition):
        raise Exception(
            "'{}' already exists in pyNBT's shared parameter file as a "
            "non-Text parameter. Please delete it ONE TIME via: Manage "
            "tab > Shared Parameters > Parameter group '{}' > select "
            "'{}' > Delete > OK - then run Apply again. pyNBT will then "
            "create it fresh as Text and this won't come up "
            "again.".format(PARAM_NAME, SHARED_PARAM_GROUP_NAME, PARAM_NAME)
        )

    if definition is None:
        options = ExternalDefinitionCreationOptions(PARAM_NAME, get_text_spec())
        options.UserModifiable = True
        definition = group.Definitions.Create(options)
    return definition


def unbind_wrong_type_if_needed():
    """If PARAM_NAME is currently bound to Walls as anything OTHER than
    Text (e.g. a leftover Length parameter from the old Dynamo graph, or a
    Number parameter from an earlier pyNBT version), and ONLY bound to
    Walls, remove that binding in its OWN, separate, immediately-committed
    Transaction - deliberately NOT sharing a transaction with the later
    create/rebind step.

    Why Text specifically: a Text parameter has no unit and no numeric
    rounding at all, so whatever string pyNBT writes is EXACTLY what
    Revit displays - always, on every Revit version, regardless of the
    project's Project Units. This ends the whole class of "displayed
    value doesn't match what was computed" bugs (the x1000 mm/m mismatch,
    then the '32.250000' trailing-zeros issue) for good, instead of
    chasing them one Revit-version-specific FormatOptions quirk at a time.

    Why a separate transaction: removing an old binding and inserting a
    new one for the SAME parameter name back-to-back inside a single
    Transaction/Regenerate cycle proved unstable (crashed Revit outright
    for Trung, not just a Python error) - splitting it into two fully
    committed transactions gives Revit's parameter binding manager a
    clean regen cycle to settle in between, the same way doing this by
    hand in Manage > Project Parameters would be two separate UI actions.

    Returns True if a binding was actually removed, False if there was
    nothing to remove (already Text, or not bound yet).
    Raises a clear, friendly Exception if PARAM_NAME is bound to OTHER
    categories too (not just Walls) - auto-removing that could affect
    those other categories, so this stops and asks instead of guessing.
    """
    existing_definition = find_wall_bound_definition()
    if existing_definition is None or definition_is_text(existing_definition):
        return False

    bound_categories = find_binding_categories_for_name(doc, PARAM_NAME) or []
    if bound_categories != ["Walls"]:
        raise Exception(
            "'{}' is bound to: {} (not just Walls) and isn't a Text "
            "parameter. Auto-converting it could affect those other "
            "categories too, so pyNBT is stopping here instead of "
            "guessing - please adjust it manually via Manage > Project "
            "Parameters.".format(PARAM_NAME, ", ".join(bound_categories))
        )

    t_unbind = Transaction(doc, "pyNBT - remove old '{}' binding".format(PARAM_NAME))
    t_unbind.Start()
    try:
        doc.ParameterBindings.Remove(existing_definition)
        t_unbind.Commit()
    except Exception:
        if t_unbind.HasStarted():
            t_unbind.RollBack()
        raise
    return True


def ensure_wall_parameter_exists():
    """Make sure PARAM_NAME is bound to Walls via a pyNBT-owned shared
    parameter file, and return a status string describing what happened:
      'existing' - already bound (as Text, normally - or as a leftover
                   Length/Number type that unbind_wrong_type_if_needed()
                   declined to touch because it's shared with other
                   categories too), nothing changed here
      'created'  - didn't exist yet (or was just unbound by
                   unbind_wrong_type_if_needed()), created fresh as Text

    Must be called inside an open Transaction (a DIFFERENT transaction
    than whatever called unbind_wrong_type_if_needed(), if that was
    needed first - see apply_results()).
    Raises a clear, friendly Exception instead of letting this collide
    silently/confusingly if someone else already created a same-named
    parameter bound to OTHER categories.
    """
    existing_definition = find_wall_bound_definition()
    if existing_definition is not None:
        # Already bound to Walls - either Text (normal case) or a
        # leftover Length/Number type unbind_wrong_type_if_needed() left
        # alone because it's shared with other categories too. Either
        # way, nothing more to safely do here.
        return "existing"
    else:
        existing_categories = find_binding_categories_for_name(doc, PARAM_NAME)
        if existing_categories is not None:
            # A parameter named PARAM_NAME already exists in the project,
            # just not bound to Walls. It almost certainly has a different
            # GUID than pyNBT's own shared parameter (someone else made it
            # independently), so trying to bind ours too would create a
            # confusing duplicate or get rejected by Revit as a name
            # clash. Stop here with a clear message instead of guessing.
            raise Exception(
                "A parameter named '{}' already exists in this project, "
                "but it's bound to: {} (not Walls). Adding pyNBT's own "
                "parameter of the same name would create a name clash. "
                "Please either (a) add the 'Walls' category to that "
                "existing parameter's binding yourself via Manage > "
                "Project Parameters, or (b) rename it so pyNBT can create "
                "its own '{}' for Walls.".format(
                    PARAM_NAME, ", ".join(existing_categories), PARAM_NAME
                )
            )
        status = "created"

    original_spf_path = _ensure_shared_param_file()
    try:
        definition = _get_or_create_definition()
        categories = CategorySet()
        categories.Insert(doc.Settings.Categories.get_Item(BuiltInCategory.OST_Walls))
        instance_binding = app.Create.NewInstanceBinding(categories)
        doc.ParameterBindings.Insert(
            definition, instance_binding, get_dimensions_param_group()
        )
        doc.Regenerate()
    finally:
        # restore whatever shared parameter file the project was using
        # before this tool ran, so we don't disturb other work
        try:
            if original_spf_path:
                app.SharedParametersFilename = original_spf_path
        except Exception:
            pass

    if not wall_parameter_is_bound():
        raise Exception(
            "Parameter binding did not take effect - '{}' still not bound "
            "to the Walls category after Insert().".format(PARAM_NAME)
        )
    return status


def force_display_format():
    """FALLBACK ONLY - normally PARAM_NAME is Text (see
    unbind_wrong_type_if_needed) and needs no format override at all,
    since Text has no unit/rounding for Revit to reinterpret.

    This only does something in the rare edge case where an existing
    Length/Number parameter is bound to OTHER categories besides Walls
    too, so unbind_wrong_type_if_needed() declined to auto-convert it. In
    that case, force at least 3-decimal rounding (and meters, if Length)
    so the leftover parameter is as close to correct as possible without
    touching the other categories' binding.
    Safe to call every time Apply runs - it's a cheap, idempotent no-op
    for the normal Text case, or if this Revit version doesn't support
    per-parameter format overrides.
    Must be called inside an open Transaction.
    """
    definition = find_wall_bound_definition()
    if definition is None:
        return "no_definition"
    if definition_is_text(definition):
        return "not_applicable"
    if definition_is_length(definition):
        ok = force_definition_format(definition, build_meters_format_options())
    else:
        ok = force_definition_format(definition, build_number_format_options())
    return "applied" if ok else "failed"


def _set_elevation_value(param, value_m):
    """Write an elevation value (in meters) into `param`.

    Normal case: PARAM_NAME is a Text parameter (pyNBT's design - see
    unbind_wrong_type_if_needed for why). Text has no unit and no numeric
    rounding at all, so writing the pre-formatted string is EXACTLY what
    Revit will display, forever, on every Revit version, regardless of
    Project Units - this is what finally makes "whatever the preview
    shows gets forced into the parameter" unconditionally true.

    Fallback case: PARAM_NAME could still be a legacy Number/Length
    parameter if it's bound to OTHER categories besides Walls too (see
    unbind_wrong_type_if_needed) - handled the old numeric-safe way
    instead of failing outright.
    """
    if param.StorageType == StorageType.String:
        param.Set("{:.3f}".format(value_m))
    elif param.StorageType == StorageType.Double:
        if param_is_length(param):
            param.Set(m_to_internal(value_m))
        else:
            param.Set(value_m)
    else:
        raise Exception(
            "Parameter '{}' has an unsupported storage type ({}) - cannot "
            "write an elevation value into it.".format(PARAM_NAME, param.StorageType)
        )


def apply_results(walls):
    """Recompute every wall's Top Elevation FRESH (live, right now) and
    write it into PARAM_NAME. Deliberately does not trust whatever was
    shown by a previous Calculate click - if Trung edited a wall's height
    after clicking Calculate, Apply still writes the current, up-to-date
    value instead of a stale one.

    Also reads every value straight back from the wall right after writing
    it (in the same transaction) so the report can PROVE the write really
    landed, instead of just trusting that Set() didn't throw. And reports
    which parameter group (Dimensions / Data / ...) the reused-or-created
    parameter actually lives under, so a mismatch with an old/leftover
    parameter (e.g. from an earlier buggy version) is obvious immediately
    instead of looking like "nothing happened".

    Returns a dict:
      success        - int, walls successfully written AND verified by readback
      failed         - list of (wall, reason)
      skipped        - list of compute_top_elevation() results, status != 'ok'
      mismatches     - list of (wall, written, readback) where the readback
                       didn't match what was just written (should basically
                       never happen, but would be the smoking gun if it did)
      created_new    - True if this call had to create the parameter binding
      rebound        - True if an existing Length-type binding was removed
                       and replaced with a Number-type one (see
                       ensure_wall_parameter_exists) - the old GUID is
                       gone, so any Tag/Schedule built against it would
                       need to be re-pointed
      group_label    - human-readable group the parameter is bound under
                       right now (e.g. 'Dimensions', 'Data')
    """
    success = 0
    failed = []
    skipped = []
    mismatches = []

    # Step 1: if needed, remove an old Length-type binding in its OWN
    # committed transaction FIRST - see unbind_length_parameter_if_needed()
    # for why this must not share a transaction with the create/bind step
    # below (doing both together was found to crash Revit outright).
    try:
        rebound = unbind_wrong_type_if_needed()
    except Exception as ex:
        logger.error("Wall Top Elevation unbind step failed: {}".format(ex))
        forms.alert(
            "Error while removing the old '{}' parameter binding:\n{}".format(
                PARAM_NAME, ex
            ),
            title=TOOL_NAME,
        )
        return {
            "success": 0,
            "failed": [(w, "Unbind step failed") for w in walls],
            "skipped": [],
            "mismatches": [],
            "created_new": False,
            "rebound": False,
            "group_label": "-",
            "is_text_param": False,
            "format_status": "no_definition",
            "hard_error": True,
        }

    # Step 2: create/confirm the (now guaranteed Text-type, in the normal
    # case) binding and write values, all in a separate transaction.
    t = Transaction(doc, "pyNBT - {}".format(TOOL_NAME))
    t.Start()
    try:
        param_status = ensure_wall_parameter_exists()
        created_new = rebound or param_status == "created"
        format_status = force_display_format()
        for wall in walls:
            r = compute_top_elevation(wall)  # always live, never cached
            if r["status"] != "ok":
                skipped.append(r)
                continue
            param = wall.LookupParameter(PARAM_NAME)
            if param is None or param.IsReadOnly:
                failed.append((wall, "Parameter not found / read-only on this wall"))
                continue
            try:
                _set_elevation_value(param, r["value"])
                # read it straight back to PROVE the write landed, instead
                # of just trusting that Set() didn't raise
                readback = wall.LookupParameter(PARAM_NAME)
                if readback.StorageType == StorageType.String:
                    expected_str = "{:.3f}".format(r["value"])
                    readback_val = readback.AsString()
                    is_match = readback_val == expected_str
                else:
                    readback_val = (
                        internal_to_m(readback.AsDouble())
                        if param_is_length(readback) else readback.AsDouble()
                    )
                    is_match = abs(readback_val - r["value"]) <= 0.0005
                if is_match:
                    success += 1
                else:
                    mismatches.append((wall, r["value"], readback_val))
            except Exception as ex:
                failed.append((wall, "Set failed: {}".format(ex)))
        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        logger.error("Wall Top Elevation transaction failed: {}".format(ex))
        forms.alert(
            "Error while writing parameter values:\n{}".format(ex),
            title=TOOL_NAME,
        )
        return {
            "success": 0,
            "failed": [(w, "Transaction rolled back") for w in walls],
            "skipped": [],
            "mismatches": [],
            "created_new": False,
            "rebound": False,
            "group_label": "-",
            "is_text_param": False,
            "format_status": "no_definition",
            "hard_error": True,
        }

    definition = find_wall_bound_definition()
    group_label = get_group_label(definition) if definition is not None else "-"
    is_text_param = definition_is_text(definition) if definition is not None else False
    return {
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "mismatches": mismatches,
        "rebound": rebound,
        "created_new": created_new,
        "group_label": group_label,
        "is_text_param": is_text_param,
        "format_status": format_status,
        "hard_error": False,
    }


# ---------------------------------------------------------------------------
# Row model for the preview DataGrid
# ---------------------------------------------------------------------------

class ResultRow(Object):
    def __init__(self, wall_id, level_name, value_text, status_text):
        self.WallId = wall_id
        self.LevelName = level_name
        self.ValueText = value_text
        self.StatusText = status_text


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class WallTopElevationWindow(Window):

    def __init__(self, initial_walls=None):
        self.Title = "pyNBT - {}".format(TOOL_NAME)
        self.Width = 780
        self.Height = 560
        self.MinWidth = 640
        self.MinHeight = 420
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.Background = _brush(theme.CLR_BG)

        self._results = []  # last computed results (list of dicts) - preview only
        self._current_walls = []  # walls Apply will recompute + write fresh
        # set by _on_pick_walls; read by the __main__ loop after this
        # window closes, to decide whether to run PickObjects and re-open
        # a fresh window - see pick_walls_interactively() for why picking
        # never happens while a pyNBT window is still open (even hidden).
        self.pick_requested = False

        root = Grid()
        root.RowDefinitions.Add(RowDefinition(Height=GridLength(74)))
        root.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Star)))
        root.RowDefinitions.Add(RowDefinition(Height=GridLength(56)))

        root.Children.Add(self._build_header())
        Grid.SetRow(root.Children[root.Children.Count - 1], 0)

        content = self._build_content()
        root.Children.Add(content)
        Grid.SetRow(content, 1)

        footer = self._build_footer()
        root.Children.Add(footer)
        Grid.SetRow(footer, 2)

        self.Content = root

        if initial_walls:
            # re-opened right after a Pick Walls round-trip - show the
            # preview immediately instead of making Trung click Calculate
            # again for walls he already picked.
            self._current_walls = initial_walls
            self._results = [compute_top_elevation(w) for w in initial_walls]
            self._refresh_grid()

    # -- header -------------------------------------------------------
    def _build_header(self):
        header = Border()
        header.Background = _brush(theme.CLR_HEADER)
        grid = Grid()
        grid.Margin = Thickness(20, 0, 20, 0)
        col_l = ColumnDefinition()
        col_r = ColumnDefinition(Width=GridLength(1, GridUnitType.Auto))
        grid.ColumnDefinitions.Add(col_l)
        grid.ColumnDefinitions.Add(col_r)

        titles = StackPanel(VerticalAlignment=VerticalAlignment.Center)
        title = TextBlock(Text=TOOL_NAME)
        title.FontSize = 20
        title.FontWeight = FontWeights.Bold
        title.Foreground = _brush(theme.CLR_HEADER_TEXT)
        subtitle = TextBlock(
            Text="Calculate wall top elevation and write it to '{}'".format(PARAM_NAME)
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
        col_left = ColumnDefinition(Width=GridLength(230))
        col_right = ColumnDefinition(Width=GridLength(1, GridUnitType.Star))
        grid.ColumnDefinitions.Add(col_left)
        grid.ColumnDefinitions.Add(col_right)

        left = self._build_left_panel()
        left.Margin = Thickness(0, 0, 12, 0)
        grid.Children.Add(left)
        Grid.SetColumn(left, 0)

        right = self._build_right_panel()
        grid.Children.Add(right)
        Grid.SetColumn(right, 1)

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
        panel = StackPanel()

        label = TextBlock(Text="Source")
        label.FontWeight = FontWeights.Bold
        label.Foreground = _brush(theme.CLR_TEXT)
        label.Margin = Thickness(0, 0, 0, 8)
        panel.Children.Add(label)

        source_note = TextBlock(
            Text="Calculate all walls in the active view, or pick specific walls."
        )
        source_note.FontSize = 12
        source_note.Foreground = _brush(theme.CLR_TEXT)
        source_note.TextWrapping = TextWrapping.Wrap
        source_note.Margin = Thickness(0, 0, 0, 14)
        panel.Children.Add(source_note)

        self.btn_calc = self._make_btn("Calculate (Active View)", theme.CLR_ACCENT, theme.CLR_HEADER_TEXT)
        self.btn_calc.Click += self._on_calculate
        panel.Children.Add(self.btn_calc)

        self.btn_pick = self._make_btn(
            "Pick Walls...", theme.CLR_CARD, theme.CLR_TEXT, border=True
        )
        self.btn_pick.Margin = Thickness(0, 8, 0, 0)
        self.btn_pick.Click += self._on_pick_walls
        panel.Children.Add(self.btn_pick)

        note = TextBlock(
            Text=("Result = Base Level elevation + Base Offset + "
                  "Unconnected Height, in meters (3 decimals).")
        )
        note.FontSize = 10
        note.Foreground = _brush(theme.CLR_MUTED)
        note.TextWrapping = TextWrapping.Wrap
        note.Margin = Thickness(0, 14, 0, 0)
        panel.Children.Add(note)

        card.Child = panel
        return card

    def _build_right_panel(self):
        card = self._card()
        grid = Grid()
        grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Auto)))
        grid.RowDefinitions.Add(RowDefinition(Height=GridLength(1, GridUnitType.Star)))

        self.lbl_summary = TextBlock(Text="Preview will appear here after Calculate.")
        self.lbl_summary.FontWeight = FontWeights.Bold
        self.lbl_summary.Foreground = _brush(theme.CLR_TEXT)
        self.lbl_summary.Margin = Thickness(0, 0, 0, 8)
        grid.Children.Add(self.lbl_summary)
        Grid.SetRow(self.lbl_summary, 0)

        self.grid_results = DataGrid()
        self.grid_results.AutoGenerateColumns = False
        self.grid_results.IsReadOnly = True
        self.grid_results.HeadersVisibility = DataGridHeadersVisibility.Column
        self.grid_results.SelectionMode = DataGridSelectionMode.Extended
        self.grid_results.CanUserAddRows = False
        self.grid_results.GridLinesVisibility = getattr(
            DataGridGridLinesVisibility, "None"
        )
        self.grid_results.RowHeight = 26

        self.grid_results.Columns.Add(self._text_col("Wall Id", "WallId", 90))
        self.grid_results.Columns.Add(self._text_col("Base Level", "LevelName", 150))
        self.grid_results.Columns.Add(self._text_col("Top Elevation (m)", "ValueText", 130))
        self.grid_results.Columns.Add(self._text_col("Status", "StatusText", 260))

        self._rows = ObservableCollection[Object]()
        self.grid_results.ItemsSource = self._rows

        grid.Children.Add(self.grid_results)
        Grid.SetRow(self.grid_results, 1)

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
        col_l = ColumnDefinition()
        col_r = ColumnDefinition(Width=GridLength(1, GridUnitType.Auto))
        grid.ColumnDefinitions.Add(col_l)
        grid.ColumnDefinitions.Add(col_r)

        sig = TextBlock(
            Text="{} v{} | {} | {}".format(
                TOOL_NAME, TOOL_VERSION, doc.Title,
                __import__("datetime").date.today().isoformat(),
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
        self.btn_apply = self._make_btn("Apply", theme.CLR_APPLY, theme.CLR_HEADER_TEXT)
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

    # -- events -----------------------------------------------------------
    def _on_calculate(self, sender, args):
        # local import: on some IronPython/pyRevit builds the module-level
        # 'forms' global can go missing when a handler is invoked as a .NET
        # event callback - importing it fresh here sidesteps that entirely.
        from pyrevit import forms

        walls = get_walls_in_active_view()

        if not walls:
            forms.alert(
                "No walls found in the active view.",
                title=TOOL_NAME,
            )
            return

        self._current_walls = walls  # remembered for Apply, re-computed live
        self._results = [compute_top_elevation(w) for w in walls]
        self._refresh_grid()

    def _on_pick_walls(self, sender, args):
        # Deliberately does NOT call PickObjects() here. Doing the pick
        # while this window is merely Hide()-d nests Revit's own picking
        # modal loop inside this window's still-active ShowDialog() modal
        # loop, which was the actual cause of a real Revit fatal-error
        # crash (confirmed: "Calculate" mode never crashed, only the
        # Hide()/PickObjects()/Show() flow did). Instead: fully close this
        # window, and let the __main__ loop at the bottom of this file do
        # the picking with NO window open, then re-open a fresh window
        # with the results preloaded.
        self.pick_requested = True
        self.Close()

    def _refresh_grid(self):
        self._rows.Clear()
        ok_count = 0
        skip_count = 0
        for r in self._results:
            wall_id = str(eid_int(r["wall"].Id))
            if r["status"] == "ok":
                ok_count += 1
                self._rows.Add(ResultRow(
                    wall_id, r["level_name"], "{:.3f}".format(r["value"]), "OK"
                ))
            else:
                skip_count += 1
                self._rows.Add(ResultRow(
                    wall_id, r["level_name"] or "-", "-", "Skipped: {}".format(r["reason"])
                ))
        self.lbl_summary.Text = "{} wall(s): {} OK, {} skipped".format(
            len(self._results), ok_count, skip_count
        )
        self.btn_apply.IsEnabled = ok_count > 0

    def _on_apply(self, sender, args):
        from pyrevit import forms  # local import, see note in _on_calculate

        if not self._current_walls:
            return

        # Apply always recomputes live values inside apply_results() itself -
        # this is what makes a re-run reflect whatever the model looks like
        # right now, even if Trung changed a wall's height after the last
        # Calculate click.
        report = apply_results(self._current_walls)

        # refresh the preview grid so what's on screen matches what was
        # actually just written
        self._results = [compute_top_elevation(w) for w in self._current_walls]
        self._refresh_grid()

        if report.get("hard_error"):
            # apply_results() already showed its own alert with the real
            # error - don't pile a second, confusing "0 walls updated"
            # summary popup on top of it. Leave the window OPEN in this
            # case (not auto-closed) so Trung can see the preview and try
            # again without having to reopen the tool.
            return

        success = report["success"]
        failed = report["failed"]
        skipped = report["skipped"]
        mismatches = report["mismatches"]

        msg_lines = [
            "Updated '{}' on {} wall(s) - verified by reading the value "
            "straight back from the model.".format(PARAM_NAME, success)
        ]
        msg_lines.append("")
        if report["rebound"]:
            source_desc = "auto-converted from an old Length/Number parameter to Text"
        elif report["created_new"]:
            source_desc = "just created by pyNBT (Text)"
        else:
            source_desc = "reused existing parameter already in the project"
        msg_lines.append(
            "Parameter source: {} | currently grouped under: '{}'".format(
                source_desc, report["group_label"],
            )
        )
        if report["rebound"]:
            msg_lines.append(
                "  Note: '{}' used to be a Length or Number parameter "
                "(likely left over from the old Dynamo graph, or an "
                "earlier pyNBT version), which is why its displayed value "
                "could come out wrong depending on this project's Project "
                "Units or default rounding. pyNBT removed that old "
                "binding and rebuilt '{}' as a Text parameter, so the "
                "value shown here always matches the preview exactly, "
                "character for character, no matter what. If any Tag or "
                "Schedule referenced the OLD parameter, it will need to "
                "be re-pointed to the new one.".format(PARAM_NAME, PARAM_NAME)
            )
        if not report["is_text_param"]:
            # Rare fallback: PARAM_NAME is bound to other categories too,
            # so unbind_wrong_type_if_needed() left it as Length/Number.
            if report["format_status"] == "applied":
                msg_lines.append(
                    "  Note: '{}' is bound to other categories besides "
                    "Walls too, so pyNBT couldn't convert it to Text - its "
                    "display was instead force-rounded to 3 decimals "
                    "(and meters, if Length).".format(PARAM_NAME)
                )
            elif report["format_status"] == "failed":
                msg_lines.append(
                    "  WARNING: '{}' is bound to other categories besides "
                    "Walls too, so pyNBT couldn't convert it to Text, and "
                    "this Revit version/setup did NOT accept a format "
                    "override either - its displayed value may not match "
                    "the preview exactly (check Manage > Project Units).".format(
                        PARAM_NAME
                    )
                )
        if report["group_label"] not in ("Dimensions",):
            msg_lines.append(
                "  Note: this is NOT the 'Dimensions' group. If you expected "
                "to find it there, this is likely a same-named parameter "
                "created earlier (by an older tool version or someone else) "
                "that pyNBT is correctly reusing by name - check under "
                "'{}' in Properties, or delete it via Manage > Project "
                "Parameters so pyNBT recreates it under Dimensions.".format(
                    report["group_label"]
                )
            )
        if mismatches:
            msg_lines.append("")
            msg_lines.append(
                "WARNING - written value didn't match on readback ({}):".format(
                    len(mismatches)
                )
            )
            for wall, written, readback in mismatches[:20]:
                # readback is a float for the legacy Number/Length fallback
                # path, or a string for the normal Text path - IronPython
                # string types vary (str/unicode), so check for float
                # rather than assuming what "not a number" means.
                readback_text = (
                    "{:.3f}".format(readback)
                    if isinstance(readback, float) else readback
                )
                msg_lines.append(
                    "  Id {}: wrote {:.3f}, read back {}".format(
                        eid_int(wall.Id), written, readback_text
                    )
                )
        if failed:
            msg_lines.append("")
            msg_lines.append("Failed to write ({}):".format(len(failed)))
            for wall, reason in failed[:20]:
                msg_lines.append("  Id {}: {}".format(eid_int(wall.Id), reason))
        if skipped:
            msg_lines.append("")
            msg_lines.append("Skipped ({}):".format(len(skipped)))
            for r in skipped[:20]:
                msg_lines.append(
                    "  Id {}: {}".format(eid_int(r["wall"].Id), r["reason"])
                )
        forms.alert("\n".join(msg_lines), title=TOOL_NAME)

        # Trung asked for the tool to close itself once Apply is done -
        # no need to keep the window open after the update finished (the
        # popup above already confirmed what happened). pick_requested
        # stays False here, so the main() loop below just exits normally
        # instead of re-opening a window.
        self.Close()

    def _on_close(self, sender, args):
        self.Close()


def main():
    """Runs the tool window, and if the user used Pick Walls, fully closes
    it, runs PickObjects() with NO pyNBT window open at all, then re-opens
    a fresh window preloaded with the picked walls - looping for as many
    Pick Walls round-trips as needed. See pick_walls_interactively() and
    _on_pick_walls() for why this is structured this way (a real Revit
    crash, not just a script bug, when picking happened via Hide()/Show()
    on an already-open modal window instead)."""
    initial_walls = None
    while True:
        window = WallTopElevationWindow(initial_walls=initial_walls)
        window.ShowDialog()
        if not window.pick_requested:
            break  # closed normally (Close button, or the X) - done
        picked = pick_walls_interactively()
        initial_walls = picked if picked else None
        # loop back around and open a fresh window either way - with the
        # newly picked walls preloaded, or empty if cancelled/nothing picked


if __name__ == "__main__":
    main()
