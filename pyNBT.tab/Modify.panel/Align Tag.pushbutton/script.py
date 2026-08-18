# -*- coding: utf-8 -*-
"""pyNBT - Align Tag

Rotate ONE tag (Floor / Structural Framing (beam) / Structural Column /
Architectural Column / Wall) to run parallel to a reference beam / column
/ grid / wall / slab, so the tag reads tilted in sync with the structure
instead of staying at the default horizontal angle.

Single-shot tool: pick 1 tag, pick 1 reference element, the tag rotates
immediately, and the tool ends right there - no loop asking for the next
pair. To align another tag, just click the ribbon button again.

Workflow (v2.0):
    1. Run the tool. Pick ONE tag - Floor, Beam (Structural Framing),
       Column (Structural or Architectural), or Wall tag.
    2. Click directly on ONE reference beam / column / grid / wall / slab
       (the WHOLE element, not a specific edge) - works the same whether
       it's in the host model or inside a linked RVT (e.g. a Structural
       discipline link, no Tab needed).
    3. The tag rotates immediately and the tool finishes right away.
       Press Esc at either pick step to cancel without changing anything.

Design notes:
    v1.3 switched from edge-picking (ObjectType.Edge) to element-picking
    (ObjectType.Element) because Structural Framing (beams) commonly
    display as a symbolic line at Coarse Detail Level instead of solid
    geometry, so there was often no real "Edge" to pick at all, even for
    elements in the host model. Reading the element's own placement data
    (Location curve, HandOrientation, sketch profile) instead of its view
    geometry fixed that, and is also easier to click.
    v1.4 added explicit Grid support (Grid has no Location curve the way
    beams/walls do - it exposes its line via Grid.Curve instead) and a
    clearer error when the pick resolved to the link as a whole.
    v1.5: testing showed pressing Tab during an ObjectType.Element pick
    does NOT drill into a link's individual elements through the API the
    way it does in normal interactive selection - it always resolves to
    the RevitLinkInstance. The fix is to detect that case and immediately
    re-pick using ObjectType.LinkedElement, Revit's dedicated mode for
    picking one specific element inside a link (no Tab needed there).
    v1.6 added multi-tag selection, v1.7 split it into a second ribbon
    button, v1.8 folded it back into one tool behind an opt-in tick
    screen. Trung then asked to drop multi-select entirely and keep only
    the single-pick flow, which he found simpler and nicer to use - so
    v1.9 removes the tick screen and multi-select code path completely.
    v1.9 bug fix attempt (found by Trung re-running the tool on an
    already aligned tag): ElementTransformUtils.RotateElement always
    rotates BY a RELATIVE amount from the tag's CURRENT rotation - it
    does not set an absolute angle. Tried toggling TagOrientation back to
    Horizontal and then to AnyModelDirection every time before rotating,
    hoping that would force the rotation back to a known 0 baseline.
    v1.9 also removed the pick-another-pair loop per Trung's request (the
    tool felt slow to "finish" while it kept re-prompting) - the tool now
    does exactly one Tag + one reference and ends immediately.
    v1.10: Trung reported the v1.9 toggle trick did NOT actually fix the
    repeated-align bug, and that rotating felt slow. Attempted fix: read
    the tag's current rotation back from tag.Location.Rotation, compute
    the difference to the target angle, and rotate by only that
    difference, plus a conditional Regenerate() the first time a tag was
    switched into free rotation.
    v1.11: Trung tested v1.10 and reported the SAME bug still happened
    (re-aligning an already-aligned tag to a new reference still came out
    skewed, not parallel), and that rotation still took about 6 seconds.
    Root cause: tag.Location.Rotation is not a trustworthy read-back for
    an IndependentTag in AnyModelDirection orientation - it does not
    reliably reflect the angle actually applied by a prior
    RotateElement call, so the "current angle" v1.10 computed from it
    was wrong from the start, and the conditional Regenerate() was
    likely the source of the slowness. Fixed per Trung's suggestion
    ("bring the old angle back to 0, then add the new angle") by no
    longer asking Revit for the tag's rotation at all: pyNBT now stores
    the last angle IT applied directly on the tag itself, via Revit
    Extensible Storage (a small schema with one Double field). Each run
    reads that stored angle (0.0 if the tag has never been aligned by
    this tool before), rotates by exactly (new angle - stored angle)
    normalized to the shortest turn, then writes the new angle back to
    storage for next time. This always lands on the intended absolute
    angle regardless of what Revit's own rotation properties report, and
    drops the Regenerate() call entirely, which should also make the
    tool noticeably faster.
    v1.12/v1.13: the Extensible Storage field itself needed two more
    fixes before it would actually work. v1.11 stored the angle as a
    Double field with no unit Spec set, and Revit refused to Finish()
    the schema at all ("Units are required for field AngleDeg"). v1.12
    tried declaring that Double field as SpecTypeId.Number (a supposedly
    unitless numeric spec) - Revit accepted that at Finish() time but
    then rejected it as an incompatible spec the moment the field was
    actually written/read ("The unit unitTypeId is not compatible with
    the field description"). Rather than keep guessing at which Spec
    Revit will accept for a Double, v1.13 sidesteps the whole unit/spec
    system: the field is now a plain Int64 storing MILLIDEGREES (angle *
    1000, rounded) instead of a Double storing degrees. Integer fields
    carry no physical unit and need no Spec declared at all, so this
    class of error cannot happen again. (Also switched to a fresh schema
    guid/name for v1.13, since the old guid may have a broken
    Double-based schema already registered from the v1.11/v1.12 test
    runs.)
    v1.14: v1.13 fixed the skew bug, but Trung reported the rotate step
    still felt very slow. Added DEBUG_TIMING: prints a per-step timing
    breakdown to the pyRevit output window (no popup, no behavior
    change) so a test run tells us exactly which step is slow instead of
    guessing. Still on while Trung's timing report is pending.
    v2.0: Trung confirmed the rotation logic itself now works correctly
    (v1.13 fixed the repeat-align skew for good), then asked to broaden
    the tool beyond Floor Tags: it should also accept Beam (Structural
    Framing), Column, and Wall tags. Renamed the tool from "Align Floor
    Tag" to "Align Tag" to match the wider scope (folder, title, tooltip
    all updated - this is why it ships as a fresh pushbutton delivery
    rather than a patch). The rotation math, linked-model handling, and
    Extensible Storage angle memory needed ZERO changes - none of that
    code was ever Floor-specific, it already worked generically on any
    IndependentTag. The only real change is FloorTagFilter -> TagFilter,
    which now allow-lists 5 tag categories instead of 1: Floor Tags,
    Structural Framing Tags (beam tags), Structural Column Tags,
    (Architectural) Column Tags, and Wall Tags. Both column tag
    categories are included since a given model can carry either or
    both. The Extensible Storage schema guid/name is unchanged from
    v1.13 (still internally named "AlignFloorTagAngleV2" - that name is
    never shown to Trung, so there is no need to touch it, and Extensible
    Storage schemas cannot be renamed in place once Finish()'d anyway).
"""

__title__ = "Align\nTag"
__doc__ = ("Pick a Floor / Beam / Column / Wall tag, then click a "
           "reference beam / column / grid / wall / slab (host model or "
           "linked file); the tag rotates immediately and the tool "
           "finishes right away.")

import os
import sys
import math
import time

import System
import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, BuiltInCategory, IndependentTag, TagOrientation,
    Line, XYZ, ElementTransformUtils, ElementId, RevitLinkInstance, Grid,
)
from Autodesk.Revit.DB.ExtensibleStorage import (
    Schema, SchemaBuilder, Entity, AccessLevel,
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import forms, revit

# ---------------------------------------------------------------------------
# pyNBT shared lib (compat.py / theme.py) - see shared-lib-architecture.md
# theme is not needed here (no WPF window in this tool), only compat.
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(__file__)
LIB_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", "lib"))
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyNBT.compat import eid_int

doc = revit.doc
uidoc = revit.uidoc

TOOL_NAME = "Align Tag"
TOOL_VERSION = "2.0"

# v1.14: Trung reported the tool now aligns correctly but the rotate step
# still feels very slow. All the algorithmic work in this file (one
# TagOrientation check, one RotateElement call, two tiny Extensible
# Storage reads/writes) should be near-instant - if it is not, the real
# cost is almost certainly somewhere Revit itself controls (regenerating
# the document/view on Transaction.Commit, or - on a workshared central
# model - a network round trip to "borrow" the tag element before it can
# be edited). This flag prints a timing breakdown of every step to the
# pyRevit output window (no popup, does not change behavior) so we can
# see exactly which step is slow instead of guessing. Safe to leave on;
# set to False to silence it once the slow step is identified and fixed.
DEBUG_TIMING = True


def _log_timing(label, elapsed_seconds):
    if DEBUG_TIMING:
        print("  [pyNBT timing] {}: {:.2f}s".format(label, elapsed_seconds))

# Extensible Storage schema pyNBT uses to remember, ON THE TAG ITSELF, the
# last absolute angle this tool rotated it to. Fixed GUID - never change
# once tags in the field may already carry this schema's data.
# NOTE: this guid/name dates back to v1.13 (the v1.11/v1.12 guid used a
# Double field that Revit kept rejecting for unit/spec reasons - see the
# design notes above). The name still says "AlignFloorTagAngleV2" even
# after the v2.0 rename to "Align Tag" - that name is an internal storage
# label never shown to Trung, and Extensible Storage schemas cannot be
# renamed in place once Finish()'d, so it is left as-is on purpose.
_ANGLE_SCHEMA_GUID = System.Guid("7c3f2a9e-5d1b-4f7a-b8c6-1a9e4d2f6b53")
_ANGLE_SCHEMA_NAME = "pyNBT_AlignFloorTagAngleV2"
_ANGLE_FIELD_NAME = "AngleMilliDeg"


# ---------------------------------------------------------------------------
# Standalone Revit-logic functions (no UI references - see dqt-patterns.md #2)
# ---------------------------------------------------------------------------

# v2.0: tag categories this tool is allowed to pick and rotate. Both column
# tag categories are included (Structural Column Tags AND Architectural
# Column Tags) since a project can carry either or both.
_ALIGNABLE_TAG_CATEGORIES = set([
    int(BuiltInCategory.OST_FloorTags),
    int(BuiltInCategory.OST_StructuralFramingTags),
    int(BuiltInCategory.OST_StructColumnTags),
    int(BuiltInCategory.OST_ColumnTags),
    int(BuiltInCategory.OST_WallTags),
])


class TagFilter(ISelectionFilter):
    """Only allow picking tags this tool can align: Floor, Structural
    Framing (beam), Structural Column, (Architectural) Column, and Wall
    tags. Renamed from FloorTagFilter in v2.0 - see the v2.0 design note
    in the module docstring."""

    def AllowElement(self, element):
        try:
            cat = element.Category
            return cat is not None and eid_int(cat.Id) in _ALIGNABLE_TAG_CATEGORIES
        except Exception:
            return False

    def AllowReference(self, reference, position):
        return True


def _floor_direction(owner_doc, floor_el, transform):
    """Best-effort direction for a Floor: longest straight segment of its
    sketch profile. Returns an XYZ vector (untransformed callers must
    transform it) or None if unavailable. `transform` (if not None) is
    applied to the curve before computing the vector, matching the other
    direction helpers."""
    try:
        sketch_id = floor_el.SketchId
    except Exception:
        return None
    if sketch_id is None or sketch_id == ElementId.InvalidElementId:
        return None
    try:
        sketch = owner_doc.GetElement(sketch_id)
        profile = sketch.Profile
    except Exception:
        return None

    longest_curve = None
    longest_len = -1.0
    try:
        for curve_array in profile:
            for curve in curve_array:
                try:
                    length = curve.Length
                except Exception:
                    continue
                if length > longest_len:
                    longest_len = length
                    longest_curve = curve
    except Exception:
        return None

    if longest_curve is None:
        return None
    if transform is not None:
        longest_curve = longest_curve.CreateTransformed(transform)
    p0 = longest_curve.GetEndPoint(0)
    p1 = longest_curve.GetEndPoint(1)
    return p1 - p0


def get_reference_direction_deg(doc, ref):
    """Return (angle_deg, None) or (None, error_message) for the plan
    direction of a picked reference ELEMENT (beam / column / wall / slab).

    Reads the element's own placement data instead of picking a specific
    edge - see the v1.3 design note in the module docstring for why.
    Works for elements in the host document or inside a linked RVT.
    """
    linked_id = ref.LinkedElementId
    transform = None
    owner_doc = doc

    if linked_id is not None and linked_id != ElementId.InvalidElementId:
        link_instance = doc.GetElement(ref.ElementId)
        if not isinstance(link_instance, RevitLinkInstance):
            return None, "Could not resolve the linked element."
        link_doc = link_instance.GetLinkDocument()
        if link_doc is None:
            return None, ("The linked file appears to be unloaded. Reload "
                           "the link (Manage > Manage Links) and try again.")
        el = link_doc.GetElement(linked_id)
        transform = link_instance.GetTotalTransform()
        owner_doc = link_doc
    else:
        el = doc.GetElement(ref.ElementId)
        if isinstance(el, RevitLinkInstance):
            # Should not normally reach here - pick_reference() re-picks
            # with ObjectType.LinkedElement as soon as it sees a whole
            # link. Kept as a safety net in case that re-pick still
            # resolves to the link itself (e.g. link has no elements at
            # that point).
            return None, (
                "That is the linked file as a whole, not one element "
                "inside it. Try clicking directly on the beam / column / "
                "grid again.")

    if el is None:
        return None, "Could not find that element."

    direction = None

    if isinstance(el, Grid):
        try:
            curve = el.Curve
        except Exception:
            curve = None
        if curve is not None:
            if transform is not None:
                curve = curve.CreateTransformed(transform)
            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
            direction = p1 - p0
    else:
        location = getattr(el, "Location", None)
        curve = getattr(location, "Curve", None) if location is not None else None
        if curve is not None:
            if transform is not None:
                curve = curve.CreateTransformed(transform)
            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
            direction = p1 - p0
        else:
            hand = getattr(el, "HandOrientation", None)
            if hand is not None:
                direction = transform.OfVector(hand) if transform is not None else hand

    if direction is None:
        direction = _floor_direction(owner_doc, el, transform)

    if direction is None:
        return None, ("Could not find a usable direction on that element. "
                       "Pick a beam, column, grid, wall, brace, or slab "
                       "instead.")

    dx, dy = direction.X, direction.Y
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return None, ("That reference has no clear horizontal direction "
                       "(it runs vertically in plan). Pick a different "
                       "reference.")
    angle_deg = math.degrees(math.atan2(dy, dx)) % 360.0
    if 90.0 < angle_deg < 270.0:
        angle_deg = (angle_deg + 180.0) % 360.0
    return angle_deg, None


def _safe_tag_text(tag):
    try:
        txt = tag.TagText
        return txt if txt else "(no text)"
    except Exception:
        return "Tag {}".format(eid_int(tag.Id))


def _get_angle_schema():
    return Schema.Lookup(_ANGLE_SCHEMA_GUID)


def _get_or_create_angle_schema():
    """Create (once per Revit session) the Extensible Storage schema pyNBT
    uses to remember its own last-applied angle. The field is a plain
    Int64 storing MILLIDEGREES (angle * 1000, rounded) rather than a
    Double storing degrees - see the v1.13 design note in
    align_tag_to_angle() for why: Double fields require declaring a unit
    Spec, and every Spec pyNBT tried was rejected by Revit as
    incompatible. Int64/Int32/String/Boolean fields carry no physical
    unit and need no Spec at all, so this sidesteps the problem
    entirely."""
    schema = _get_angle_schema()
    if schema is not None:
        return schema
    builder = SchemaBuilder(_ANGLE_SCHEMA_GUID)
    builder.SetSchemaName(_ANGLE_SCHEMA_NAME)
    builder.SetReadAccessLevel(AccessLevel.Public)
    builder.SetWriteAccessLevel(AccessLevel.Public)
    builder.AddSimpleField(_ANGLE_FIELD_NAME, System.Int64)
    return builder.Finish()


def _get_stored_angle_deg(tag):
    """Return the angle (degrees) pyNBT last rotated this tag to, read
    from Extensible Storage on the tag itself - 0.0 if this tag has never
    been aligned by this tool before. Stored value is milli-degrees
    (Int64), converted back to degrees here."""
    schema = _get_angle_schema()
    if schema is None:
        return 0.0
    try:
        entity = tag.GetEntity(schema)
        if not entity.IsValid():
            return 0.0
        field = schema.GetField(_ANGLE_FIELD_NAME)
        milli_deg = entity.Get[System.Int64](field)
        return float(milli_deg) / 1000.0
    except Exception:
        return 0.0


def _store_angle_deg(tag, angle_deg):
    """Remember angle_deg on the tag itself (Extensible Storage) so the
    NEXT run can compute the correct delta instead of guessing. Stored as
    milli-degrees (Int64) - see _get_or_create_angle_schema()."""
    schema = _get_or_create_angle_schema()
    entity = Entity(schema)
    field = schema.GetField(_ANGLE_FIELD_NAME)
    milli_deg = System.Int64(int(round(angle_deg * 1000.0)))
    entity.Set[System.Int64](field, milli_deg)
    tag.SetEntity(entity)


def align_tag_to_angle(doc, tag, angle_deg):
    """Rotate a single IndependentTag to angle_deg (ABSOLUTE, degrees).
    Must run inside an already-open Transaction.

    ElementTransformUtils.RotateElement always rotates BY a relative
    amount from the tag's CURRENT rotation - there is no "set absolute
    angle" API for it, so every approach needs to know the tag's current
    angle to compute the right delta. Two earlier attempts to learn that
    current angle from Revit itself both proved unreliable in testing:
    v1.9 tried toggling TagOrientation off AnyModelDirection and back on
    to force a 0-rotation baseline (Revit does not reliably re-settle the
    rotation from just re-assigning the same property in one
    transaction); v1.10 tried reading the live value back via
    tag.Location.Rotation (also did not reflect the tag's actual applied
    rotation for IndependentTag - it kept reporting a stale value, which
    reproduced the exact same compounding bug and, since it was paired
    with a Regenerate() call to "settle" that value, also made every run
    noticeably slower on a large linked model).
    v1.11 stops trying to ask Revit for the current angle at all: pyNBT
    now remembers the angle IT last applied directly on the tag element
    via Extensible Storage (_get_stored_angle_deg / _store_angle_deg,
    0.0 if the tag was never aligned by this tool before), computes the
    delta from THAT, and updates the stored value after rotating. This
    does not depend on Revit's internal rotation bookkeeping for tags
    being readable at all, and removes the Regenerate() call entirely -
    should fix the repeat-align skew for good and be noticeably faster.
    v1.12/v1.13: the Extensible Storage field itself needed two more
    fixes before it would actually work. v1.11 stored the angle as a
    Double field with no unit Spec set, and Revit refused to Finish()
    the schema at all ("Units are required for field AngleDeg"). v1.12
    tried declaring that Double field as SpecTypeId.Number (a supposedly
    unitless numeric spec) - Revit accepted that at Finish() time but
    then rejected it as an incompatible spec the moment the field was
    actually written/read ("The unit unitTypeId is not compatible with
    the field description"). Rather than keep guessing at which Spec
    Revit will accept for a Double, v1.13 sidesteps the whole unit/spec
    system: the field is now a plain Int64 storing MILLIDEGREES (angle *
    1000, rounded) instead of a Double storing degrees. Integer fields
    carry no physical unit and need no Spec declared at all, so this
    class of error cannot happen again. (Also switched to a fresh schema
    guid/name for v1.13, since the old guid may have a broken
    Double-based schema already registered from the v1.11/v1.12 test
    runs.) None of this is Floor-specific - it works identically for
    Beam / Column / Wall tags added in v2.0, since it only ever touches
    generic IndependentTag members (TagOrientation, TagHeadPosition) plus
    pyNBT's own Extensible Storage data on the tag element.
    """
    t_orient = time.time()
    try:
        if tag.TagOrientation != TagOrientation.AnyModelDirection:
            tag.TagOrientation = TagOrientation.AnyModelDirection
    except AttributeError:
        raise Exception("Free tag rotation needs Revit 2022 or later.")
    _log_timing("Set TagOrientation (only costs time the FIRST time a "
                "given tag is switched to free rotation)",
                time.time() - t_orient)

    t_read = time.time()
    previous_deg = _get_stored_angle_deg(tag)
    _log_timing("Read stored angle (Extensible Storage)",
                time.time() - t_read)

    delta_rad = math.radians(angle_deg - previous_deg)
    # Normalize to the shortest turn so we never spin the long way round.
    delta_rad = (delta_rad + math.pi) % (2 * math.pi) - math.pi

    if abs(delta_rad) > 1e-9:
        t_rotate = time.time()
        head_pos = tag.TagHeadPosition
        axis = Line.CreateBound(head_pos, head_pos + XYZ.BasisZ)
        ElementTransformUtils.RotateElement(doc, tag.Id, axis, delta_rad)
        _log_timing("RotateElement", time.time() - t_rotate)

    t_write = time.time()
    _store_angle_deg(tag, angle_deg)
    _log_timing("Write stored angle (Extensible Storage)",
                time.time() - t_write)


def align_one(doc, tag, angle_deg):
    """Rotate tag to angle_deg in its own Transaction. Returns a list of
    error messages (empty on success)."""
    t = Transaction(doc, "pyNBT - {}".format(TOOL_NAME))
    t_start = time.time()
    t.Start()
    _log_timing("Transaction.Start", time.time() - t_start)
    try:
        align_tag_to_angle(doc, tag, angle_deg)
        t_commit = time.time()
        t.Commit()
        _log_timing("Transaction.Commit (this is where Revit regenerates "
                    "the document/view - on a workshared central model it "
                    "can also include a network round trip to borrow the "
                    "tag element)", time.time() - t_commit)
        return []
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        return ["Error aligning {}: {}".format(_safe_tag_text(tag), str(ex))]


def pick_reference(uidoc, doc):
    """Pick a reference beam / column / grid / wall / slab.

    Click works the same for host-model or linked elements: if the first
    click resolves to the link AS A WHOLE (RevitLinkInstance) - which is
    all a plain ObjectType.Element pick can ever return for something
    inside a link, Tab or not - immediately re-pick using
    ObjectType.LinkedElement, Revit's dedicated mode for picking one
    specific element inside a link. Returns a Reference, or None if the
    user cancelled (Esc) at either step.
    """
    try:
        ref_pick = uidoc.Selection.PickObject(
            ObjectType.Element,
            "Click a reference beam / column / grid / wall / slab "
            "(host model or linked file)")
    except OperationCanceledException:
        return None

    picked_el = doc.GetElement(ref_pick.ElementId)
    if isinstance(picked_el, RevitLinkInstance):
        try:
            ref_pick = uidoc.Selection.PickObject(
                ObjectType.LinkedElement,
                "Click the specific beam / column / grid inside the "
                "linked file")
        except OperationCanceledException:
            return None

    return ref_pick


# ---------------------------------------------------------------------------
# Entry point - single-shot: pick 1 Tag, pick 1 reference, rotate, done.
# No loop - run the tool again from the ribbon to align another tag.
# ---------------------------------------------------------------------------

def main():
    if DEBUG_TIMING:
        print("--- pyNBT {} v{} timing ---".format(TOOL_NAME, TOOL_VERSION))

    try:
        tag_ref = uidoc.Selection.PickObject(
            ObjectType.Element, TagFilter(),
            "Pick a Floor / Beam / Column / Wall tag to align")
    except OperationCanceledException:
        return

    tag = doc.GetElement(tag_ref)
    if not isinstance(tag, IndependentTag):
        forms.alert("Please pick a Floor, Beam, Column, or Wall tag.",
                    title=TOOL_NAME)
        return

    t_pick_ref = time.time()
    ref_pick = pick_reference(uidoc, doc)
    _log_timing("Pick reference element (includes YOUR OWN mouse time - "
                "not a useful number to judge tool speed by)",
                time.time() - t_pick_ref)
    if ref_pick is None:
        return

    t_dir = time.time()
    angle_deg, err = get_reference_direction_deg(doc, ref_pick)
    _log_timing("Compute reference direction", time.time() - t_dir)
    if angle_deg is None:
        forms.alert(err, title=TOOL_NAME)
        return

    t_align = time.time()
    errors = align_one(doc, tag, angle_deg)
    _log_timing("TOTAL align_one() (everything after your last click)",
                time.time() - t_align)
    if errors:
        forms.alert("\n".join(errors), title=TOOL_NAME)


main()
