# -*- coding: utf-8 -*-
"""pyNBT - Align Tag

Rotate ONE tag (Floor / Structural Framing (beam) / Structural Column /
Architectural Column / Wall) to run parallel to a reference LINE Trung
defines by clicking two points, so the tag reads tilted in sync with
whatever direction Trung wants instead of staying at the default
horizontal angle.

Single-shot tool: pick 1 tag, click 2 points, the tag rotates
immediately, and the tool ends right there - no loop asking for the next
pair. To align another tag, just click the ribbon button again.

Workflow (v4.1):
    1. Run the tool. Pick ONE tag - Floor, Beam (Structural Framing),
       Column (Structural or Architectural), or Wall tag.
    2. Click the FIRST point of the reference line.
    3. Click the SECOND point of the reference line. The two points can
       be anywhere - free clicks in space, or snapped (Revit's normal
       object-snap behavior applies) onto endpoints/intersections/
       gridlines/geometry in the host model OR a linked file, since
       picking POINTS instead of an element sidesteps all the old
       "which element, which link" questions entirely.
    4. The tag rotates immediately, parallel to the line from point 1 to
       point 2, and the tool finishes right away. Press Esc at any pick
       step to cancel without changing anything.

Design notes:
    v1.0-v3.0 all worked by picking a REFERENCE ELEMENT (beam / column /
    grid / wall / slab) and reading that element's own placement data to
    get a direction. That history (edge-picking vs element-picking,
    Grid.Curve vs Location.Curve vs HandOrientation, linked-model
    Tab/ObjectType.LinkedElement handling, the v1.9-v3.0 saga of getting
    the ROTATION MATH right) is kept below for the record, but v4.0
    replaces the entire "pick a reference element" step - see the v4.0
    note.
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
    storage for next time.
    v1.12/v1.13: the Extensible Storage field itself needed two more
    fixes before it would actually work (unit/Spec errors on a Double
    field). v1.13 sidestepped that by storing the angle as a plain Int64
    of MILLIDEGREES instead - integer fields need no unit Spec at all.
    v1.14: added DEBUG_TIMING - prints a per-step timing breakdown to the
    pyRevit output window so a test run tells us exactly which step is
    slow instead of guessing.
    v2.0: broadened the tool beyond Floor Tags to also accept Beam
    (Structural Framing), Column, and Wall tags, and renamed the tool
    from "Align Floor Tag" to "Align Tag" to match. Only the tag-picking
    filter needed to change (FloorTagFilter -> TagFilter with 5 allow-
    listed categories) - the rotation math and reference-reading code
    were never Floor-specific.
    v2.1: v2.0 threw an AttributeError on load - the Structural Column
    Tags category enum was misspelled as BuiltInCategory.
    OST_StructColumnTags; the real name is OST_StructuralColumnTags.
    v2.2: Trung's DEBUG_TIMING data showed Transaction.Commit taking
    0.59s of a 0.64s total - confirmed the delay is Revit's own
    document/view regeneration, not this script, and that 0.64s is
    reasonable. Trung also asked for the timing output window to stop
    popping up on every run, so DEBUG_TIMING defaults to False.
    v3.0: Trung found tags he had rotated BY HAND (outside the tool, at
    any point before or after a previous align) came out wrong when the
    tool ran on them - the Extensible Storage memory from v1.11 only
    knew about rotations the TOOL itself had applied, so a hand-rotated
    tag's remembered angle didn't match its true angle. Fixed by no
    longer tracking or reading any prior angle at all: every run now
    force-resets the tag to a guaranteed 0-degree baseline first
    (TagOrientation -> Horizontal -> Regenerate -> AnyModelDirection ->
    Regenerate), then rotates directly to the absolute target. The
    Extensible Storage mechanism was removed entirely as no longer
    needed.
    v4.0: Trung asked to change how the REFERENCE DIRECTION is defined.
    Instead of picking a beam / column / grid / wall / slab and reading
    its placement data, Trung now clicks 2 points directly in the view
    and the tool treats the line between them as the reference direction
    to align the tag to - literally "click 2 points, that's my line".
    This is a deliberate simplification, not just a new option:
        - It removes the entire "which element, which category, does it
          have a Location.Curve or HandOrientation or Sketch.Profile"
          branch (get_reference_direction_deg, _floor_direction, the
          Grid-specific branch) - a line between 2 points needs none of
          that.
        - It removes the entire linked-model / Tab / ObjectType.
          LinkedElement handling (pick_reference, the RevitLinkInstance
          checks) - PickPoint() picks a raw 3D point in the ACTIVE VIEW,
          using Revit's normal object-snap behavior, and snaps onto
          geometry from the host model or a linked file exactly the same
          way, with no special-casing needed at all.
        - It is strictly more flexible: Trung can snap onto any 2 points
          he wants (2 grid intersections, 2 beam endpoints, a corner and
          a midpoint, or just eyeball a direction) instead of being
          limited to "the direction of one existing element".
    The rotation math in align_tag_to_angle() (the v3.0 force-reset-then-
    rotate design) is UNCHANGED - it never cared how angle_deg was
    determined, only what to do with it once known. Only the input side
    changed: pick_line_points() (2x uidoc.Selection.PickPoint calls) and
    get_line_direction_deg() (angle of p2-p1, same 0-360 / 90-270 flip
    convention as before) replace pick_reference() and
    get_reference_direction_deg(). The TagFilter/category-picking step
    (which TAG to align) is unchanged.
    v4.1: Trung tested v4.0 and reported total silence - no error
    popup, no traceback, and the tag did not visibly rotate at all,
    even though the tool ran to completion cleanly. Since v3.0's force-
    reset-then-rotate design had never actually been confirmed working
    by Trung before he asked to pivot to 2-point picking, this pointed
    at a previously-undiscovered bug in align_tag_to_angle() itself, not
    the new point-picking code. Working theory: calling doc.Regenerate()
    right after switching TagOrientation to AnyModelDirection but BEFORE
    RotateElement let Revit "check-point" the tag's internal rotation
    bookkeeping at 0, so the later RotateElement transform never made it
    into that bookkeeping and got silently discarded when Commit() did
    its own implicit regenerate. Fixed by moving the regenerate to run
    AFTER RotateElement instead of before it - see the updated design
    note inside align_tag_to_angle() for the exact new step order. This
    is an unverified hypothesis fix; if Trung still sees no rotation
    after this, the next step is a different diagnostic, not another
    guess at step ordering.
    v4.2: Trung reported a NEW/recurring problem after v4.1 - the FIRST
    align on a tag works correctly, but re-aligning the SAME tag a
    SECOND time (to a different reference line) comes out wrong / not
    parallel - UNLESS he first manually rotates the tag back to 0 by
    hand in Revit's own UI, after which the tool works correctly again.
    This is the same class of bug v1.9-v3.0 kept fighting, resurfacing
    inside v4.1's own force-reset step. Working theory: the reset step
    (TagOrientation -> Horizontal -> Regenerate -> AnyModelDirection)
    was happening INSIDE the same Transaction as the rotate step, and a
    mid-transaction Regenerate() does not reliably clear whatever
    internal "last custom rotation" bookkeeping Revit keeps for an
    IndependentTag - flipping back to AnyModelDirection can silently
    restore that old remembered angle instead of staying at 0, even
    though Regenerate() ran in between. Trung's manual workaround is the
    key clue: a real rotate-back-to-0 committed through Revit's own UI
    (a genuine standalone Transaction.Commit(), not just a Regenerate)
    is what actually clears it. Fixed by giving the reset step its own
    REAL Transaction with a REAL Commit (see _reset_tag_orientation()),
    separate from the rotate step's Transaction - both wrapped in one
    TransactionGroup in align_one() so Trung's Undo history still shows
    this as a single action, even though 2 Transactions are committed
    under the hood. Still a hypothesis fix (no live Revit available to
    verify against directly) - if the second-align case still fails
    after this, the next step is checking whether TagOrientation itself
    is even the right lever, vs. some other tag-rotation API.
    v5.0: Trung tested v4.2 and it STILL failed the second-align case -
    and this time gave us a real number to diagnose with: the rotation
    actually needed was only about 8 degrees, but the tool applied
    something like 300+ degrees instead. That number is the smoking
    gun. It means the "reset to a 0-degree baseline" premise itself was
    NEVER true, in ANY version (v3.0's in-transaction Regenerate, v4.1's
    reordered Regenerate, v4.2's real separate Commit) - the tag's real
    underlying rotation was still sitting at whatever the FIRST align
    had set it to, and RotateElement kept adding the new absolute target
    on top of that old angle as if it were a delta from 0, overshooting
    by roughly the old angle's amount. Every "reset" attempt so far
    tried to force TagOrientation through Horizontal and back, on the
    theory that this would zero out some internal Revit bookkeeping -
    that theory is now considered WRONG, not just unverified.
    v5.0 abandons "force a 0-degree baseline" entirely and switches to
    exactly what Trung suggested: read the tag's ACTUAL current
    rotation, and rotate by the DIFFERENCE to the target (shortest way
    round), the same way you'd manually correct an angle - via
    tag.Location.Rotation, which Revit updates in-place whenever
    RotateElement runs (this also naturally handles hand-rotated tags,
    the original v3.0 case, with no special-casing needed - whatever
    Location.Rotation reports IS the tag's true current angle,
    regardless of who rotated it last). v1.10/v1.11 tried this exact
    approach once before and Trung's testing then showed the same
    repeated-align bug - but that attempt ran alongside Extensible
    Storage and a conditional Regenerate() that were later found to be
    part of the problem, so it was never a clean test of
    Location.Rotation by itself. This time it runs alone, with a new
    opt-in DEBUG_ANGLE flag (default False, same silent-by-default rule
    as DEBUG_TIMING) that prints the exact current/target/delta numbers
    read on each run - if this still misfires, flipping that flag on
    for one test run gives real data instead of another guess.
    v6.0: Trung ran v5.0 and got a clean, immediate error instead of a
    wrong rotation: "Could not read this tag's current rotation angle
    (Location.Rotation was not available)", on Revit/pyRevit
    6.4.0.26100+0515, on a Floor Tag. That answers the open question
    from v5.0's own note for good: tag.Location.Rotation is NOT usable
    for an IndependentTag in this environment at all (not merely
    "unreliable" as v1.11 guessed - it is simply not exposed), so there
    is no live Revit property this tool can read to find a tag's true
    current rotation angle. Every approach that depended on reading
    Revit's own state back (TagOrientation toggling in v3.0-v4.2,
    Location.Rotation in v5.0) has now failed for a concrete, confirmed
    reason - not just "still buggy, keep guessing at the mechanism".
    v6.0 goes back to the ONE mechanism from this tool's whole history
    that never actually failed on repeated TOOL-ONLY use: pyNBT tracks
    the angle IT last applied, itself, via Extensible Storage (the same
    Int64-millidegrees design from v1.13, which had no unit/Spec issues)
    - not to "cheat" by remembering the true current angle, but because
    there is no other way left to know it. On each run: read the stored
    angle (0.0 if this tag was never aligned by the tool before, which
    matches a fresh tag's real 0-degree state), compute the shortest
    delta to the new target, RotateElement by that delta, then store the
    new absolute angle for next time. New schema/GUID for v6.0 so any
    leftover v1.11-v2.2 era entities (from before v3.0 ripped this
    mechanism out) are simply ignored, not reused.
    Known, accepted trade-off (same one that motivated v3.0's rewrite in
    the first place): if Trung rotates a tag BY HAND between two tool
    runs, the stored angle goes stale and the next tool run will be
    wrong again, exactly like pre-v3.0. There is no known way to avoid
    this without a live-readable rotation property, which v5.0 just
    proved does not exist here. If hand-rotation-between-runs turns out
    to matter in practice, the next idea is a lightweight "resync"
    command Trung runs once by hand after any manual rotation, rather
    than trying to auto-detect it.
    v7.0: Trung tested v6.0 on a Structural Column Tag and sent Properties
    palette screenshots that turned out to solve this whole saga. Before
    running the tool, the tag's own Properties palette showed a field
    literally called "Angle" = 350.00 degrees (only visible once
    Orientation = Model). After running the tool, Angle read 68.00
    degrees - but Trung measured the tag actually needed 78 degrees to
    be parallel with his reference line. Working out the arithmetic:
    (350 + 78) mod 360 = 68 - which proves two things at once. First,
    RotateElement's effect maps onto this "Angle" parameter in a
    perfectly direct, additive, absolute way (rotate by +78 truly adds
    78 to Angle, no hidden conversion). Second, this tool's own angle
    MATH (get_line_direction_deg, from the 2 picked points) was already
    computing the CORRECT target the whole time - 78 degrees, matching
    Trung's own manual measurement exactly. The only thing that was ever
    wrong was v6.0's assumed starting point: brand new to v6.0's storage
    (never aligned by THIS SCHEMA before), so it assumed the tag's
    current angle was 0 - but the tag's REAL current angle was 350 (set
    by something before v6.0 ever ran on it), so the correctly-computed
    +78 delta landed at the wrong absolute result (68 instead of 78).
    This is the same "wrong assumed baseline" failure mode that broke
    v3.0 through v6.0, in a new shape.
    v7.0's fix removes the need for ANY baseline or delta at all: since
    "Angle" is a real, directly settable Revit Parameter (found via
    tag.LookupParameter("Angle"), the exact field Trung sees on screen),
    just SET it straight to the absolute target angle computed from the
    2 picked points. No RotateElement, no relative delta, no remembering
    or reading any "current" angle, no Extensible Storage. This also
    means hand-rotated tags are no longer a special case at all (the
    v3.0 problem this whole saga started from) - whatever the tag's
    current Angle is, Set() simply overwrites it with the correct
    absolute value, unconditionally, every run.
    v8.0: Trung tested v7.0 on a SECOND tag, in a DIFFERENT view ("A102 -
    LAND FACILITIES - ROOF PL..." instead of the "CONCRETE BODY PLAN..."
    view the first, confirmed-correct 78-degree case was in) - and it
    came out wrong again (Angle ended up 348.74 degrees, visibly not
    parallel to the reference line he drew). This is a NEW root cause,
    not a repeat of the old "wrong current angle" family of bugs - v7.0
    no longer reads or assumes any "current" angle at all, it only
    computes a target from the 2 picked points and writes it directly.
    So the bug has to be in the computed TARGET itself, and the fact
    that it worked in one view but not another points at the VIEW,
    specifically: uidoc.Selection.PickPoint() returns points in true
    project (world) XYZ coordinates, the same in every view - but the
    tag's "Angle" parameter is very likely measured relative to the
    VIEW's own on-screen "horizontal" (its RightDirection), not world
    X. get_line_direction_deg() was computing everything in world XY,
    with no correction for the view's own rotation relative to the
    project. In the first (working) view, the view's RightDirection
    happens to line up with world +X (an unrotated plan view), so world
    angle and view-relative angle were the same number by coincidence -
    hiding the bug. The second view (a roof plan) is evidently rotated
    relative to the project, exposing it.
    v8.0 fix: before writing to the tag's Angle parameter, convert the
    picked line's world-frame direction into the angle relative to the
    TAG'S OWN VIEW (via tag.OwnerViewId, not just "whatever the active
    view happens to be", to be exact regardless of how the tool is
    invoked) by subtracting that view's RightDirection angle (in world
    XY) from the picked line's world angle. The "avoid upside-down tag
    text" 180-degree flip now happens AFTER this correction, since it
    needs to reflect how the text will actually look ON SCREEN in that
    view, not its raw project-coordinate direction. This is a new,
    reasoned hypothesis (matches both data points collected so far: 0
    correction needed where it worked, correction evidently needed where
    it didn't) but still unverified against live Revit - see the
    confirmation checklist in the project doc for what to test, including
    ruling out imprecise point-picking as a secondary factor.
"""

__title__ = "Align\nTag"
__doc__ = ("Pick a Floor / Beam / Column / Wall tag, then click 2 points "
           "to define the reference line to align it to; the tag rotates "
           "immediately and the tool finishes right away.")

import os
import sys
import math
import time

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, BuiltInCategory, BuiltInParameter, IndependentTag,
    TagOrientation,
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
TOOL_VERSION = "8.0"

# v7.0: the exact parameter name Trung sees in the Properties palette
# (confirmed from his own screenshot) once a tag's Orientation = Model -
# this IS the tag's rotation angle, directly settable, no RotateElement
# or baseline tracking needed at all. Kept as a couple of BuiltInParameter
# fallbacks too, in case the display name ever differs - see
# _get_angle_parameter().
_ANGLE_PARAM_NAME = "Angle"
_ANGLE_PARAM_BIP_FALLBACKS = [
    "TAG_ROTATION_ANGLE",
    "LEADER_TAG_ROTATION_ANGLE",
    "TAG_ANGLE",
]

# v1.14 added this timing breakdown to find slow steps; v2.2's real
# numbers showed the tool itself is fast (Revit's own regenerate on
# commit is where the time goes), so this now defaults to off - flip
# back to True only to investigate a genuine new slowness report.
DEBUG_TIMING = False

# v5.0: prints diagnostic angle numbers on every run, so if the angle
# logic ever misfires again we get real data instead of another blind
# guess. Same silent-by-default rule as DEBUG_TIMING - flip to True only
# to diagnose a fresh report of wrong-angle results.
DEBUG_ANGLE = False


def _log_timing(label, elapsed_seconds):
    if DEBUG_TIMING:
        print("  [pyNBT timing] {}: {:.2f}s".format(label, elapsed_seconds))


# ---------------------------------------------------------------------------
# Standalone Revit-logic functions (no UI references - see dqt-patterns.md #2)
# ---------------------------------------------------------------------------

# v2.0: tag categories this tool is allowed to pick and rotate. Both column
# tag categories are included (Structural Column Tags AND Architectural
# Column Tags) since a project can carry either or both.
# v2.1: build this defensively, one name at a time, instead of a single
# expression - v2.0 crashed the WHOLE tool at load time (AttributeError)
# because ONE misspelled enum name (OST_StructColumnTags, should have been
# OST_StructuralColumnTags) blew up the whole set() literal. Looking each
# name up individually means a future Revit version dropping/renaming one
# tag category degrades to "that one category isn't pickable" instead of
# "the tool doesn't run at all".
_ALIGNABLE_TAG_CATEGORY_NAMES = [
    "OST_FloorTags",
    "OST_StructuralFramingTags",   # beam tags
    "OST_StructuralColumnTags",    # structural column tags
    "OST_ColumnTags",              # architectural column tags
    "OST_WallTags",
]
_ALIGNABLE_TAG_CATEGORIES = set()
for _cat_name in _ALIGNABLE_TAG_CATEGORY_NAMES:
    _bic = getattr(BuiltInCategory, _cat_name, None)
    if _bic is not None:
        _ALIGNABLE_TAG_CATEGORIES.add(int(_bic))


class TagFilter(ISelectionFilter):
    """Only allow picking tags this tool can align: Floor, Structural
    Framing (beam), Structural Column, (Architectural) Column, and Wall
    tags."""

    def AllowElement(self, element):
        try:
            cat = element.Category
            return cat is not None and eid_int(cat.Id) in _ALIGNABLE_TAG_CATEGORIES
        except Exception:
            return False

    def AllowReference(self, reference, position):
        return True


def _safe_tag_text(tag):
    try:
        txt = tag.TagText
        return txt if txt else "(no text)"
    except Exception:
        return "Tag {}".format(eid_int(tag.Id))


def pick_line_points(uidoc):
    """Pick 2 points in the active view that define the reference line to
    align the tag to (v4.0). Uses Revit's normal PickPoint object-snap
    behavior - the points can be free clicks in space, or snapped onto
    endpoints / intersections / gridlines / any geometry, in the host
    model OR a linked file, with no special handling needed either way
    (unlike the old element-picking flow, which needed a whole separate
    branch just for linked elements). Returns (p1, p2) as XYZ, or None if
    the user cancelled (Esc) at either point.
    """
    try:
        p1 = uidoc.Selection.PickPoint(
            "Click the FIRST point of the reference line")
    except OperationCanceledException:
        return None
    try:
        p2 = uidoc.Selection.PickPoint(
            "Click the SECOND point of the reference line")
    except OperationCanceledException:
        return None
    return p1, p2


def get_line_direction_deg(p1, p2):
    """Return (angle_deg, None) or (None, error_message) for the RAW
    project (world) XY direction of the line from p1 to p2, 0-360 range
    (v8.0: no upside-down flip here anymore - that now happens AFTER
    correcting for the view's own rotation, in view_relative_angle_deg(),
    since the flip needs to reflect how the text looks ON SCREEN, not
    its raw project-coordinate direction).
    """
    dx, dy = p2.X - p1.X, p2.Y - p1.Y
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return None, ("Those two points are the same point (or too close "
                       "together) - pick two points that are clearly "
                       "apart to define a direction.")
    angle_deg = math.degrees(math.atan2(dy, dx)) % 360.0
    return angle_deg, None


def _get_view_rotation_deg(view):
    """Return the angle (project/world XY frame, degrees) of view's own
    RightDirection - i.e. how far this view's on-screen "horizontal" is
    rotated relative to the project's true X axis (v8.0). 0 means the
    view is unrotated (its right side points along project +X, e.g. a
    plan view aligned to Project North); any other value means content
    on screen is rotated relative to raw project coordinates by that
    much - which is what caught pyNBT out on Trung's second test view.
    """
    right = view.RightDirection
    return math.degrees(math.atan2(right.Y, right.X)) % 360.0


def view_relative_angle_deg(world_angle_deg, view):
    """Convert a project (world) XY direction into the angle pyNBT
    should write to a tag's Angle parameter for a given view (v8.0):
    subtract the view's own rotation (see _get_view_rotation_deg) so the
    result is relative to how the view actually displays on screen, then
    flip 180 degrees if needed (90-270 range) so the tag text never
    reads upside down ON SCREEN in that view.
    """
    view_deg = (world_angle_deg - _get_view_rotation_deg(view)) % 360.0
    if 90.0 < view_deg < 270.0:
        view_deg = (view_deg + 180.0) % 360.0
    return view_deg


def _get_angle_parameter(tag):
    """Find the tag's 'Angle' parameter - the exact field Trung sees and
    can edit directly in the Properties palette once Orientation = Model
    (v7.0). Tries the parameter's display name first (confirmed to exist
    from Trung's own screenshot), then a short list of BuiltInParameter
    fallbacks in case the display name ever differs on some tag category
    or a future Revit version. Returns None if nothing usable is found -
    caller must treat that as a hard error, not a silent guess.
    """
    param = tag.LookupParameter(_ANGLE_PARAM_NAME)
    if param is not None and not param.IsReadOnly:
        return param
    for name in _ANGLE_PARAM_BIP_FALLBACKS:
        bip = getattr(BuiltInParameter, name, None)
        if bip is None:
            continue
        try:
            param = tag.get_Parameter(bip)
        except Exception:
            param = None
        if param is not None and not param.IsReadOnly:
            return param
    return None


def align_tag_to_angle(doc, tag, angle_deg):
    """Set a single IndependentTag's rotation to angle_deg (ABSOLUTE,
    degrees). Must run inside an already-open Transaction.

    v1.9 through v6.0 all rotated the tag with
    ElementTransformUtils.RotateElement, which only rotates BY a relative
    amount from the tag's CURRENT rotation - there is no "set absolute
    angle" API on it. Every one of those versions had to somehow
    determine or remember what the tag's current angle already was
    before rotating: force it through a "guaranteed" 0-degree baseline
    (v3.0-v4.2, never actually worked - see those version notes), read it
    back live from Revit (v5.0's tag.Location.Rotation, confirmed simply
    unavailable), or track it in pyNBT's own Extensible Storage (v6.0,
    which works for repeat TOOL-ONLY use but is wrong the first time it
    ever runs on a tag that already had some other rotation - which is
    exactly what Trung's next test hit: a Structural Column Tag already
    sitting at 350 degrees, never touched by v6.0's storage before, so
    v6.0 assumed 0 and landed on the wrong absolute result).

    v7.0 removes the entire "current angle" problem instead of solving
    it: Trung's Properties palette screenshot showed a real, directly
    editable Revit parameter called "Angle" for tags with Orientation =
    Model - the exact field he can (and did) read the tag's angle from
    by eye. Setting THAT parameter directly to the absolute target,
    computed the same way as before (get_line_direction_deg on the 2
    picked points - confirmed correct: Trung's manual 78-degree
    measurement matched this tool's own math exactly, the earlier
    mismatch was purely v6.0's wrong starting-point assumption, not the
    angle calculation), needs no RotateElement, no relative delta, no
    remembering or reading any "current" state at all - every run simply
    overwrites Angle with the correct value, unconditionally. This also
    means a hand-rotated tag (the original v3.0 problem) is no longer a
    special case: whatever Angle currently reads, Set() replaces it.
    """
    t_switch = time.time()
    if tag.TagOrientation != TagOrientation.AnyModelDirection:
        try:
            tag.TagOrientation = TagOrientation.AnyModelDirection
        except AttributeError:
            raise Exception("Free tag rotation needs Revit 2022 or later.")
        doc.Regenerate()
    _log_timing("Ensure TagOrientation.AnyModelDirection (only switches - "
                "and only regenerates - if it wasn't already in that mode)",
                time.time() - t_switch)

    t_set = time.time()
    angle_param = _get_angle_parameter(tag)
    if angle_param is None:
        raise Exception(
            "Could not find an editable 'Angle' parameter on this tag "
            "(the same field visible in the Properties palette when "
            "Orientation = Model). Please tell pyNBT the tag category "
            "and Revit version so this can be investigated further.")
    if DEBUG_ANGLE:
        try:
            before_deg = math.degrees(angle_param.AsDouble()) % 360.0
            before_text = "{:.3f}".format(before_deg)
        except Exception:
            before_text = "?"
        print("  [pyNBT angle] before={} deg, target={:.3f} deg".format(
            before_text, angle_deg))
    angle_param.Set(math.radians(angle_deg))
    _log_timing("Set the 'Angle' parameter directly to the absolute "
                "target (view-corrected as of v8.0 - no RotateElement, "
                "no baseline tracking)", time.time() - t_set)


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


# ---------------------------------------------------------------------------
# Entry point - single-shot: pick 1 Tag, click 2 points, rotate, done.
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

    t_pick_pts = time.time()
    points = pick_line_points(uidoc)
    _log_timing("Pick 2 line points (includes YOUR OWN mouse time - not "
                "a useful number to judge tool speed by)",
                time.time() - t_pick_pts)
    if points is None:
        return
    p1, p2 = points

    world_angle_deg, err = get_line_direction_deg(p1, p2)
    if world_angle_deg is None:
        forms.alert(err, title=TOOL_NAME)
        return

    # v8.0: convert the picked line's raw project-XY direction into the
    # angle relative to the TAG'S OWN VIEW (not just "whatever view is
    # active" - using tag.OwnerViewId is exact regardless of how/where
    # the tool was invoked from) before writing it to the Angle
    # parameter - see view_relative_angle_deg()'s docstring for why.
    tag_view = doc.GetElement(tag.OwnerViewId)
    angle_deg = view_relative_angle_deg(world_angle_deg, tag_view)
    if DEBUG_ANGLE:
        print("  [pyNBT angle] world={:.3f} deg, view_rotation={:.3f} "
              "deg, view-corrected target={:.3f} deg".format(
                  world_angle_deg, _get_view_rotation_deg(tag_view),
                  angle_deg))

    t_align = time.time()
    errors = align_one(doc, tag, angle_deg)
    _log_timing("TOTAL align_one() (everything after your last click)",
                time.time() - t_align)
    if errors:
        forms.alert("\n".join(errors), title=TOOL_NAME)


main()
