# -*- coding: utf-8 -*-
"""Apply Master Bar Shape

Companion tool to Circular Rebar Array's Phase 3 ("Follow crosshead profile
above shaft" checkbox). See project doc claude/apply-master-bar-shape-tool.md.

WHY THIS TOOL EXISTS (2026-09-14): Circular Rebar Array's automatic
horizontal-slice-scan works well for the round shaft, but NBT's first real
crosshead test showed the auto-computed shape gets messy/self-crossing near
the top - the crosshead's cross-section is not a simple, evenly-bulging
shape, so a single radial ray cast per bar per height station can jump
around between adjacent slices once the boundary stops being "star-shaped"
from the true center. Rather than trying to make the automatic slicing
smarter, NBT chose the more reliable path: he hand-corrects ONE bar's shape
(using Revit's own Edit Sketch, the same tool he already knows) until it
visually matches the real concrete surface at the top, and this tool then
re-creates the OTHER bars in the array by scaling that same hand-verified
shape to each bar's own true radius at the top - the same way NBT works by
hand (draw one true cross-section carefully, then adapt it per angle).

How to use (v2.0.0):
1. Run Circular Rebar Array first (Phase 3, checkbox ON) as usual - this
   creates N straight-ish bars plus the N green center-reference lines.
2. Pick ONE bar you consider the most representative of the crosshead shape,
   open Edit Sketch on it, and manually correct its curve (drag/add points)
   until it matches the real surface you want, base to top. Finish Sketch.
   This is now the "master bar" - do NOT delete the other bars anymore
   (v1.x required deleting them first; v2.0.0 does not).
3. Run this tool. It walks you through 2 picks in Revit itself:
   a. First pick (multi-select, box-select is fine): select every bar in
      the array - the master bar AND all the other (still auto-generated,
      not-yet-correct) bars - plus, if you like, the host column/pier.
      Click Finish when done.
   b. Second pick (single click): click the ONE master bar among what you
      just selected.
4. The tool figures out the rest on its own - no more "how many bars"
   prompt. Every OTHER bar from the first pick is treated as a bar to
   replace: its own real position (read before it is deleted) gives its
   angle around the host, so there is no assumption of a perfectly even
   N-way split - each bar keeps whatever angle it is actually sitting at.
   For each one, the tool deletes the old (auto-generated, not-yet-correct)
   bar and creates a new one that keeps the master's exact vertical "bulge"
   pattern (height vs. how far it has moved out from its own base radius)
   but scaled to that bar's OWN true radius at the top (read once via a
   single horizontal-slice ray cast at the master's own top height). If a
   "Partition" parameter is set on the old bar, it is copied onto its
   replacement.
5. The master bar itself is left untouched.

Base radius, cover, and bar type are all read directly from the master bar
(its own type, and its own radius at its lowest point) rather than asked
again - one less place for a typo to cause a mismatch with the original
run.

v1.1.0 (2026-09-14): NBT's first real test used a Circular Rebar Array run
of 100 bars (not 8 - the "Number of bars" prompt must be the ORIGINAL total
count from that run, before deleting any bars down to the one master bar;
the default value used to say '8' which was itself a leftover from a
different, unrelated test and caused NBT to enter the wrong number once).
Result: "Created 99 replacement bars" with no errors, but nothing new was
visible in the model - the exact same silent-success-but-invisible symptom
Circular Rebar Array hit in its own v1.3.5. Added full diagnostics to the
result popup to find the real cause instead of guessing.

v1.2.0 (2026-09-14): the v1.1.0 diagnostics themselves had a bug - reading
GetCenterlineCurves() back immediately after creating each Rebar, still
inside the same open Transaction and before Revit had regenerated that
element's geometry, silently failed every time. Fixed by recording each new
bar's ElementId during the creation loop, calling doc.Regenerate() once
after the loop, then re-fetching every bar by id in a second pass.

v1.3.0 (2026-09-14): the fixed diagnostics answered the question -
Rebar.CreateFromCurves returned no element for ANY of the 99 bars, uniformly.
simplify_collinear_points() only removes points that are (near-)EXACTLY
collinear, which does nothing for a genuinely curved hand-edited shape (27
tessellated points for the master's corrected curve). Revit's Standard
rebar style can only auto-fit a curve chain with a small number of real
bends. Added douglas_peucker_indices(): true perpendicular-distance line
simplification applied to the master curve's (radius, height) profile once,
collapsing it to its real handful of bend points within
MASTER_SIMPLIFY_TOL_MM (25mm default) of the original shape.

v1.4.0 (2026-09-14): NBT re-tested with the point-count reduction actually
working (27 raw -> 11 simplified) and CreateFromCurves STILL returned None
for all 99 bars - ruling out "too many bends" as the sole cause. Attempted
fix: read the host directly off the master bar via
RebarHostData.GetRebarHostData(master_bar).GetHostElement() instead of
trusting a second selected element (this turned out to be based on a wrong
reading of the API - see v1.6.0).

v1.5.0 (2026-09-14): the v1.4.0 host auto-detection could not confirm a
host either, so v1.4.0 was never actually put to the test. Added a control
test (one trivial 2-point straight bar, created in a rolled-back
Transaction) to isolate whether the problem was bar_type/host/API mechanics
in general, or specific to the curved multi-point shapes.

v1.6.0 (2026-09-18): ROOT CAUSE FOUND for the host auto-detection always
failing. Verified directly against NBT's live Revit session
(send_code_to_revit, reflecting on the real RebarHostData class) rather
than guessing from API docs again: RebarHostData.GetRebarHostData(element)
takes a HOST element and reports the rebars placed IN it - the wrong
direction entirely for going from a Rebar to ITS host - and this Revit API
version does not even have a GetHostElement() method on RebarHostData at
all (v1.4.0's call would have raised an exception every time, silently
swallowed into "could not resolve a host"). The correct, much simpler API:
Rebar has its own direct GetHostId() method - confirmed live on NBT's
actual master bar (Id 1745583): GetHostId() correctly returned the real
host (Id 1660224, the crosshead pier FamilyInstance). Rewrote host
resolution around GetHostId() + doc.GetElement(host_id).

v1.6.0 FOLLOW-UP TEST (2026-09-18, before v2.0.0 was written): with the
host now correct, ran a second live control test - CreateFromCurves with
the master bar's OWN real 15-segment curve chain (7 arcs, unsimplified)
against the correct host: still FAILED (returned null), confirming the
"too many bends / needs straight-line simplification" hypothesis from
v1.3.0 was ALSO still real and independent of the host bug. A third live
test - the same shape but reduced via the existing Douglas-Peucker +
straight-line-only simplification down to 8 segments (and separately to 5
segments) - SUCCEEDED both times. Conclusion: v1.3.0's curve-simplification
logic and v1.6.0's host fix were both genuinely necessary; combined, with a
correct host AND simplified straight-line-only geometry, Rebar.CreateFromCurves
does work on NBT's real crosshead model. No corresponding code change was
needed here since v1.3.0's simplification pipeline (douglas_peucker_indices
-> simplify_collinear_points -> points_to_lines) was already exactly this;
it just never got a correct host to run against until v1.6.0.

v2.1.0 (2026-09-18): NBT's first real v2.0.0 test run FAILED all 49
replacement bars uniformly with "CreateFromCurves returned no element",
even with the v1.6.0 host fix in place - meaning the v1.6.0 follow-up
test's conclusion ("host fix + Douglas-Peucker simplification together
are sufficient") was WRONG, or at least incomplete. Root-caused this for
real by reproducing the exact failure live (send_code_to_revit against
NBT's actual model and one of the actual 49 failed bars, Id 1745555) and
then bisecting systematically rather than guessing:
  1. Recreating the master's OWN real curve, AT ITS OWN ORIGINAL POSITION
     (no scale, no rotation), reduced via Douglas-Peucker to 8 or even 5
     segments: SUCCEEDED (this is what the v1.6.0 follow-up test actually
     showed - it never tested a bar moved to a NEW angle/direction).
  2. Recreating that SAME Douglas-Peucker-reduced shape, ROTATED to a
     real target bar's own direction (scale=1.0, i.e. rotation only, no
     radius change) and even at the real computed scale (~0.636): FAILED
     - at 6, 7, 8, and 9 segments, every time.
  3. Recreating a target bar's OWN real shape, simplified independently,
     AT ITS OWN existing position: SUCCEEDED at 4 segments.
  4. The decisive test: taking the SAME master points but resampled to
     EVENLY-SPACED points along arc length (not Douglas-Peucker's
     deviation-based bend selection) and moving THAT to the rotated
     target position: SUCCEEDED at 9, 11, and 14 segments; only started
     failing at 16+ segments.
Conclusion: Douglas-Peucker simplification was never actually the fix -
it happened to produce a shape that worked when left at the master's own
original position (tests 1 and the v1.6.0 follow-up), but concentrating
the curve's total direction change into a handful of SHARP-angle bend
points (which is exactly what Douglas-Peucker is designed to do) is what
Rebar.CreateFromCurves (RebarStyle.Standard, createNewShape=True) cannot
reliably auto-fit once the shape is moved/rotated away from its original
placement - regardless of segment count (6 sharp segments failed; 14
gentle ones succeeded). There IS a separate, genuine segment-count
ceiling too, somewhere between 14 and 16 for this model/bar type.
FIXED: replaced douglas_peucker_indices()-based bend reduction with
resample_even_by_length() - resamples the master curve to N points
evenly spaced by actual arc length (gentle per-point angle change,
mimicking the original smooth curve rather than reducing it to sharp
corners), with N chosen from the bar's total length (one point per
~300mm, clamped to 6-15 points so segment count always stays safely
under the observed 16-segment ceiling). douglas_peucker_indices() itself
is kept in the file (still correct, general-purpose RDP) but is no
longer called by main() for this purpose.

v2.0.0 (2026-09-18): USABILITY OVERHAUL per NBT's request - the old flow
(delete N-1 bars by hand, select the 1 remaining master bar, type in the
original N) had 2 problems: it required a destructive manual deletion step
before the tool could even run, and the typed N was one more place for a
mistake (already bit NBT once in v1.1.0). New flow: NBT no longer deletes
anything by hand. He selects EVERY bar in the array (master + all the
still-auto-generated ones) in one pick, then clicks the master bar
specifically in a second pick. The tool no longer assumes an idealized,
perfectly-even N-way split of directions (bar_angles()/nearest_direction_index()
removed) - instead each non-master bar's OWN real base position (read
before it is deleted) gives its true angle, so the result is correct even
if the original array was not perfectly even. Old bars are deleted only
after their replacement is successfully created (so a failure leaves the
old bar in place rather than losing it). A "Partition" parameter, if
present on the old bar, is copied onto its replacement - this project's
established convention (see claude/select-by-partition-tool.md) for every
tool that recreates rebar. The heavy per-bar diagnostics and the
rolled-back "control test" from v1.5.0 were removed now that the root
cause is confirmed and fixed (see the v1.6.0 follow-up test note above) -
kept a compact summary + per-bar error list instead.

v2.2.0 (2026-09-18): v2.1.0's arc-length resampling fix was ALSO shown
insufficient by NBT's follow-up live test data - re-verifying the exact
v2.1.0 algorithm live against the same real failed bar (Id 1745555) at
targetCount=15 (14 segments) FAILED again, and a further live bisection
(counts 4 through 15, same bar) found only 3-4 segments reliably
succeeded - a much narrower window than the v2.1.0 test session had found,
and narrower than a from-scratch synthetic reproduction (pure rotation, and
independently radial-scale-only, of the master's own full ~24-segment
curve against the same real host) which instead succeeded at EVERY count
and scale tried. In other words: Rebar.CreateFromCurves's internal
Standard-style shape-fit validation is sensitive to more than just "segment
count" or "sharp vs. gentle bends" - some specific real bars in NBT's
crosshead hit a rejection that a clean synthetic rotation/scale test does
not reproduce, and chasing the exact geometric trigger further was not
converging. FIXED pragmatically instead of chasing the exact cause
further: replaced the single fixed-detail attempt with a per-bar
RETRY LADDER - try the fullest reasonable detail first (same
arc-length-resampled target count as v2.1.0, computed from that bar's own
scaled curve length), and on a None result step down through
CREATE_RETRY_LADDER (11, 9, 7, 5, 4, 3, 2 points) until one succeeds,
guaranteed to bottom out at a straight 2-point line (confirmed live, many
times across this whole debugging history, to always succeed regardless of
position/rotation/scale). The result popup now also notes which bars had
to fall back to a simplified shape, so NBT can see at a glance which ones
are worth a manual Edit Sketch touch-up afterwards. Scaling itself is now
done from the master's FULL, un-resampled curve (previously resampled once
up front to a single shared point count before scaling) so no detail is
thrown away before it is actually needed by a specific fallback attempt.

v2.3.0 (2026-09-18): NBT ran v2.2.0 for real through the pyRevit button on
Test V1.rvt - the result popup said "Created 49 of 49" (matching a live
send_code_to_revit re-run of the exact same pipeline against the same real
model beforehand), but NBT reported the rebar actually looked wrong in the
model afterward, with some bars missing entirely. Checked the model
directly after the run: the crosshead host that should have had 1 master +
49 replacements (50 Rebar elements total) only had 48, and the document's
total Rebar element count had dropped by exactly 2 versus before the run -
real elements had gone missing even though the tool's own counter said
49/49 succeeded. Root cause: Rebar.CreateFromCurves(RebarStyle.Standard,
createNewShape=True) can return a real, non-null Rebar element at the
moment it is called, but the actual "does this shape fit a valid Standard
rebar shape" validation happens later, during document regeneration -
and if it fails there, Revit can silently delete the element without
raising an exception back into the API call that created it. v2.2.0's
retry ladder only checked the immediate return value, so a bar that failed
this later, silent regeneration check was wrongly counted as a success.
FIXED: creation is now round-based across the whole batch of bars instead
of bar-by-bar. Each round tries the next untried candidate point count for
every unresolved bar (cheap - just the CreateFromCurves() call), then does
ONE doc.Regenerate() for the whole batch and checks doc.GetElement(id) on
every newly-created bar from this round. A bar that survives is accepted
(old bar deleted, Partition copied) right away; a bar that Revit silently
removed during that Regenerate() goes back into the pool and tries the
next, simpler point count on the following round - exactly like an
immediate None result from CreateFromCurves, just detected one step later.
This keeps the total number of full-document regenerations small (bounded
by the ladder's length, not the bar count) while actually confirming each
bar survived before calling it a success.

v2.3.1 (2026-09-18): NBT's first real run of v2.3.0 through the pyRevit
button failed immediately with "max() got an unexpected keyword argument
(default)" - IronPython 2.7's built-in max() does not support the
default= keyword, which is a Python 3.4+ addition; v2.3.0's
max_rounds calculation used it. Fixed by computing max_rounds from a plain
list instead (jobs is already known non-empty at that point, so no
default value was ever actually needed).

v2.4.0 (2026-09-18): NBT's next 2 real runs of v2.3.1 both hit new
failures, screenshotted directly from Revit: (1) Revit's OWN native error
dialog - "Error - cannot be ignored -- 14 Errors, 0 Warnings - Can't solve
Rebar Shape" with Show/More Info/Expand/OK/Cancel buttons, a real blocking
modal that needs a person to click something; (2) a Python-level crash,
"Failed: The referenced object is not valid, possibly because it has been
deleted from the database, or its creation was undone." Both trace back to
the same v2.3.x design gap: v2.3.x correctly added a doc.Regenerate() +
survival check after each round of candidate creations (see the v2.3.0
entry above), but (a) never told Revit HOW to resolve a validation failure
during that Regenerate() itself, so Revit fell back to its own interactive
dialog instead of quietly applying the obvious default resolution
(delete the offending element - exactly what clicking OK would do), and
(b) the survival check re-read .Id off the ORIGINAL Rebar object returned
by CreateFromCurves, AFTER doc.Regenerate() had already run - if that
original object had just been deleted by Revit's failure resolution
during that same Regenerate() call, touching it again (even just its .Id
property) throws the exact "referenced object is not valid... deleted...
or its creation was undone" exception seen live, rather than returning
something checkable. FIXED: added _SilentRebarFailurePreprocessor
(IFailuresPreprocessor), attached to the transaction right after Start()
via GetFailureHandlingOptions()/SetFailuresPreprocessor() - it resolves
every failure itself (DeleteWarning() for warnings, ResolveFailure() with
the default resolution for errors) so Revit never needs to show that
dialog; the retry ladder's own survival check is what actually notices and
reacts to the resulting deletion, so behavior stays the same as intended,
just silent instead of blocking. Also fixed the stale-reference crash:
the Id is now captured immediately when CreateFromCurves returns (before
Regenerate() runs), stored as a plain ElementId instead of the object
reference, and the post-Regenerate() survival check calls
doc.GetElement(id) fresh - the original Rebar object from CreateFromCurves
is never touched again after that point.
"""

__title__ = 'Apply Master\nBar Shape'
__author__ = 'NBT'

import os
import sys
import math
import traceback

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

from System.Collections.Generic import List
from Autodesk.Revit.DB import (
    XYZ, Line, Curve, CurveLoop, Transaction, Options, GeometryInstance, Solid,
    SolidUtils, PlanarFace, FilteredElementCollector, Element, ElementId,
    GeometryCreationUtilities, BooleanOperationsUtils, BooleanOperationsType,
    StorageType, IFailuresPreprocessor, FailureProcessingResult, FailureSeverity,
)
from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation, MultiplanarOption
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
import Autodesk.Revit.Exceptions as RevitExceptions

from pyrevit import revit, forms

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PANEL_DIR = os.path.dirname(SCRIPT_DIR)
TAB_DIR = os.path.dirname(PANEL_DIR)
EXTENSION_DIR = os.path.dirname(TAB_DIR)
LIB_DIR = os.path.join(EXTENSION_DIR, 'lib')
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyNBT.compat import m_to_internal, internal_to_m

doc = revit.doc
uidoc = revit.uidoc

TOOL_NAME = 'Apply Master Bar Shape'
TOOL_VERSION = 'v2.4.0'

PARTITION_PARAM_NAME = 'Partition'

# v1.3.0 (superseded by v2.1.0 - kept only as documentation of the old
# approach and because douglas_peucker_indices() itself is still a
# correct, general-purpose function): how closely a Douglas-Peucker
# simplification would hug the original curve, in mm.
MASTER_SIMPLIFY_TOL_MM = 25.0

# v2.1.0: resample the master curve to evenly-spaced points at roughly
# this spacing (mm) along its real arc length, instead of Douglas-Peucker
# bend reduction - confirmed live on NBT's real crosshead model
# (2026-09-18, see the v2.1.0 docstring entry) that EVEN spacing (gentle
# per-point angle change) is what lets Rebar.CreateFromCurves succeed
# once the shape is moved to a new bar's position; Douglas-Peucker's
# sharp, deviation-based bend points reliably failed there even with far
# fewer segments. The resulting point count is clamped to
# [RESAMPLE_MIN_POINTS, RESAMPLE_MAX_POINTS] - the max was chosen with
# margin under the ~16-segment failure ceiling observed live.
RESAMPLE_TARGET_SPACING_MM = 300.0
RESAMPLE_MIN_POINTS = 6
RESAMPLE_MAX_POINTS = 15

# v2.2.0: when Rebar.CreateFromCurves rejects the ideal resampled detail
# level for a specific bar (confirmed live: some real bars fail even when a
# synthetic rotate/scale test of the same shape succeeds - the exact
# trigger inside Revit's Standard-style shape-fit was not fully pinned
# down), retry with progressively fewer points from this ladder before
# giving up on that bar. 2 points (a straight line) is the guaranteed-safe
# final fallback - confirmed live, repeatedly, to always succeed regardless
# of position/rotation/scale.
CREATE_RETRY_LADDER = [11, 9, 7, 5, 4, 3, 2]


# ---------------------------------------------------------------------------
# Small helpers duplicated from Circular Rebar Array's script.py on purpose
# (see tools/shared-lib-architecture.md - this pair of tools is still young
# and under active change; duplicating a handful of small, stable-looking
# functions for now is safer than refactoring the already-working Circular
# Rebar Array script into a shared lib module mid-experiment. Revisit once
# both tools have settled.)
# ---------------------------------------------------------------------------

def mm_to_internal(value_mm):
    return m_to_internal(value_mm / 1000.0)


def internal_to_mm(value_ft):
    return internal_to_m(value_ft) * 1000.0


def get_element_solids(element):
    solids = []
    opt = Options()
    opt.ComputeReferences = False
    geo = element.get_Geometry(opt)
    if geo is None:
        return solids

    def collect(geo_elem):
        for obj in geo_elem:
            if isinstance(obj, Solid):
                if obj.Volume > 1e-6:
                    solids.append(obj)
            elif isinstance(obj, GeometryInstance):
                collect(obj.GetInstanceGeometry())

    collect(geo)
    return solids


def _arc_sweep(curve):
    try:
        return abs(curve.GetEndParameter(1) - curve.GetEndParameter(0))
    except Exception:
        return 0.0


def _loop_as_circle(loop):
    from Autodesk.Revit.DB import Arc, Ellipse
    edges = list(loop)
    if not edges:
        return None
    if len(edges) == 1:
        curve = edges[0].AsCurve()
        if isinstance(curve, Ellipse):
            if abs(curve.RadiusX - curve.RadiusY) < 1e-6:
                return curve.Center, curve.RadiusX
            return None
        if isinstance(curve, Arc):
            if abs(_arc_sweep(curve) - 2.0 * math.pi) > 1e-3:
                return None
            return curve.Center, curve.Radius
        return None
    center = None
    radius = None
    total_sweep = 0.0
    for edge in edges:
        curve = edge.AsCurve()
        if not isinstance(curve, Arc):
            return None
        if center is None:
            center, radius = curve.Center, curve.Radius
        elif center.DistanceTo(curve.Center) > 1e-4 or abs(radius - curve.Radius) > 1e-4:
            return None
        total_sweep += _arc_sweep(curve)
    if abs(total_sweep - 2.0 * math.pi) > 1e-3:
        return None
    return center, radius


def find_bottom_circle(element):
    solids = get_element_solids(element)
    best = None
    for solid in solids:
        for face in solid.Faces:
            if not isinstance(face, PlanarFace):
                continue
            if abs(face.FaceNormal.Z) < 0.99:
                continue
            edge_loops = face.EdgeLoops
            if edge_loops.Size != 1:
                continue
            circle = _loop_as_circle(edge_loops[0])
            if circle is None:
                continue
            center, radius = circle
            z = face.Origin.Z
            if best is None or z < best[0]:
                best = (z, center, radius)
    if best is None:
        return None, None
    return best[1], best[2]


def get_half_extent_ft(element):
    bbox = element.get_BoundingBox(None)
    if bbox is None:
        return mm_to_internal(5000.0)
    dx = bbox.Max.X - bbox.Min.X
    dy = bbox.Max.Y - bbox.Min.Y
    return max(dx, dy) * 1.5 + mm_to_internal(500.0)


def cut_horizontal_slice(solid, z, center_xy, half_extent):
    thickness = mm_to_internal(10.0)
    z0 = z - thickness / 2.0
    x0, y0 = center_xy.X - half_extent, center_xy.Y - half_extent
    x1, y1 = center_xy.X + half_extent, center_xy.Y + half_extent
    p1 = XYZ(x0, y0, z0)
    p2 = XYZ(x1, y0, z0)
    p3 = XYZ(x1, y1, z0)
    p4 = XYZ(x0, y1, z0)
    try:
        loop = CurveLoop()
        loop.Append(Line.CreateBound(p1, p2))
        loop.Append(Line.CreateBound(p2, p3))
        loop.Append(Line.CreateBound(p3, p4))
        loop.Append(Line.CreateBound(p4, p1))
        loops = List[CurveLoop]()
        loops.Add(loop)
        slab = GeometryCreationUtilities.CreateExtrusionGeometry(loops, XYZ.BasisZ, thickness)
    except Exception as ex:
        return None, 'could not build slab: {}'.format(str(ex))
    try:
        solid_clone = SolidUtils.Clone(solid)
        result = BooleanOperationsUtils.ExecuteBooleanOperation(
            solid_clone, slab, BooleanOperationsType.Intersect)
    except Exception as ex:
        return None, 'boolean intersection failed: {}'.format(str(ex))
    if result is None or result.Volume < 1e-9:
        return None, 'no material at this height'
    candidate_faces = [f for f in result.Faces
                        if isinstance(f, PlanarFace) and abs(f.FaceNormal.Z) > 0.9]
    if not candidate_faces:
        return None, 'no horizontal face at this height'
    face = max(candidate_faces, key=lambda f: f.Area)
    edge_loops = face.EdgeLoops
    if edge_loops.Size < 1:
        return None, 'slice face has no boundary loop'

    def loop_length(loop):
        total = 0.0
        for edge in loop:
            try:
                total += edge.AsCurve().Length
            except Exception:
                pass
        return total

    outer_loop = max([edge_loops[i] for i in range(edge_loops.Size)], key=loop_length)
    return [edge.AsCurve() for edge in outer_loop], None


def ray_boundary_hit(points, center_xy, dir_h):
    """Farthest intersection distance (feet) from center_xy along dir_h
    against the closed polygon `points` (a horizontal slice boundary,
    already tessellated) - same logic as Circular Rebar Array's version."""
    best = None
    cx, cy = center_xy.X, center_xy.Y
    dx, dy = dir_h.X, dir_h.Y
    n = len(points)
    for i in range(n):
        ax, ay = points[i].X, points[i].Y
        bx, by = points[(i + 1) % n].X, points[(i + 1) % n].Y
        ex, ey = bx - ax, by - ay
        denom = dx * ey - dy * ex
        if abs(denom) < 1e-12:
            continue
        t = ((ax - cx) * ey - (ay - cy) * ex) / denom
        if t < 0:
            continue
        s_denom = ey if abs(ey) > abs(ex) else ex
        if abs(s_denom) < 1e-12:
            continue
        if abs(ey) > abs(ex):
            s = (cy + t * dy - ay) / ey
        else:
            s = (cx + t * dx - ax) / ex
        if s < -1e-9 or s > 1.0 + 1e-9:
            continue
        if best is None or t > best:
            best = t
    return best


def tessellate_curves(curves):
    points = []
    for curve in curves:
        pts = list(curve.Tessellate())
        if points and points[-1].DistanceTo(pts[0]) < 1e-6:
            pts = pts[1:]
        points.extend(pts)
    return points


def points_to_lines(points):
    # v2.1.0: dedup threshold bumped from a near-zero 1e-6 ft to 2mm - a
    # live test with very dense points hit "Curve length is too small for
    # Revit's tolerance (ShortCurveTolerance)" from Line.CreateBound() on
    # two points that were technically distinct but sub-mm apart.
    min_len_ft = mm_to_internal(2.0)
    lines = []
    for i in range(len(points) - 1):
        if points[i].DistanceTo(points[i + 1]) > min_len_ft:
            lines.append(Line.CreateBound(points[i], points[i + 1]))
    return lines


def simplify_collinear_points(points, angle_tol_deg=0.5):
    """Same collinear-point collapse as Circular Rebar Array v1.3.6 - see
    that tool's docstring for the full "Rebar.CreateFromCurves silently
    creates nothing for a chain of near-collinear points" explanation."""
    if len(points) < 3:
        return list(points)
    result = [points[0]]
    for i in range(1, len(points) - 1):
        v1 = points[i] - result[-1]
        v2 = points[i + 1] - points[i]
        if v1.GetLength() < 1e-9 or v2.GetLength() < 1e-9:
            continue
        v1n = v1.Normalize()
        v2n = v2.Normalize()
        dot = max(-1.0, min(1.0, v1n.DotProduct(v2n)))
        angle_deg = math.degrees(math.acos(dot))
        if angle_deg > angle_tol_deg:
            result.append(points[i])
    result.append(points[-1])
    return result


def douglas_peucker_indices(pts2d, tol):
    """Ramer-Douglas-Peucker line simplification on a 2D polyline
    (list of (x, y) tuples), returning the sorted list of indices to
    KEEP so the caller can map back to whatever original data (e.g. full
    3D points) each 2D pair came from. Endpoints are always kept.

    v1.3.0: added after diagnostics revealed the real bug behind "Created
    99 replacement bars" with nothing visible - it was Rebar.CreateFromCurves
    silently returning None for EVERY bar because the master shape had far
    more small near-collinear bends (27 tessellated points) than Revit's
    Standard rebar style can auto-fit. Douglas-Peucker's perpendicular-
    distance test keeps a point only if the path would otherwise stray
    further than `tol` from a straight line between its neighbours, so a
    smooth curve collapses to a handful of straight segments that still hug
    the original shape within `tol`. Confirmed live (2026-09-18) that
    reducing to ~5-8 segments this way lets Rebar.CreateFromCurves succeed
    against NBT's real crosshead host."""
    n = len(pts2d)
    if n < 3:
        return list(range(n))
    keep = [False] * n
    keep[0] = True
    keep[n - 1] = True

    def perp_dist(pt, a, b):
        px, py = pt
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        seg_len2 = dx * dx + dy * dy
        if seg_len2 < 1e-12:
            return math.sqrt((px - ax) ** 2 + (py - ay) ** 2)
        t = ((px - ax) * dx + (py - ay) * dy) / seg_len2
        t = max(0.0, min(1.0, t))
        proj_x, proj_y = ax + t * dx, ay + t * dy
        return math.sqrt((px - proj_x) ** 2 + (py - proj_y) ** 2)

    def rdp(lo, hi):
        if hi - lo < 2:
            return
        a, b = pts2d[lo], pts2d[hi]
        max_d, max_i = 0.0, -1
        for i in range(lo + 1, hi):
            d = perp_dist(pts2d[i], a, b)
            if d > max_d:
                max_d, max_i = d, i
        if max_d > tol:
            keep[max_i] = True
            rdp(lo, max_i)
            rdp(max_i, hi)

    rdp(0, n - 1)
    return [i for i in range(n) if keep[i]]


def resample_even_by_length(points, target_count):
    """v2.1.0: resample an ordered 3D polyline to `target_count` points,
    evenly spaced by actual arc length (linear interpolation between the
    original points) - see the v2.1.0 docstring entry for why this
    replaced Douglas-Peucker bend reduction for this tool. Always keeps
    the exact first and last point."""
    n = len(points)
    if target_count >= n:
        return list(points)
    if target_count < 2:
        target_count = 2

    cum = [0.0]
    for i in range(1, n):
        cum.append(cum[-1] + points[i].DistanceTo(points[i - 1]))
    total = cum[-1]
    if total < 1e-9:
        return [points[0], points[-1]]

    result = []
    j = 1
    for i in range(target_count):
        target_d = total * i / float(target_count - 1)
        while j < n - 1 and cum[j] < target_d:
            j += 1
        d0, d1 = cum[j - 1], cum[j]
        p0, p1 = points[j - 1], points[j]
        if d1 - d0 < 1e-9:
            result.append(p0)
        else:
            t = (target_d - d0) / (d1 - d0)
            result.append(XYZ(
                p0.X + (p1.X - p0.X) * t,
                p0.Y + (p1.Y - p0.Y) * t,
                p0.Z + (p1.Z - p0.Z) * t))
    return result


# ---------------------------------------------------------------------------
# Master / target bar reading
# ---------------------------------------------------------------------------

def get_bar_curve_points(rebar_elem):
    """Ordered list of XYZ points along a Rebar's actual placed centerline
    (bar position index 0 - this tool only supports a single-bar layout,
    which is what Circular Rebar Array creates). Sorted by Z ascending so
    the result always reads base -> top regardless of which end the
    underlying curve chain happened to start from."""
    curves = list(rebar_elem.GetCenterlineCurves(
        False, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, 0))
    points = tessellate_curves(curves)
    points.sort(key=lambda p: p.Z)
    return points


def read_partition(elem):
    """Returns (value, storage_type) for the 'Partition' parameter on elem,
    or (None, None) if it has none - established pyNBT convention (see
    claude/select-by-partition-tool.md): Wohhup's "Partition" is a
    project/shared parameter looked up by its display name, not the
    built-in worksharing parameter of the same name."""
    p = elem.LookupParameter(PARTITION_PARAM_NAME)
    if p is None:
        return None, None
    st = p.StorageType
    if st == StorageType.String:
        return p.AsString(), st
    if st == StorageType.Integer:
        return p.AsInteger(), st
    if st == StorageType.Double:
        return p.AsDouble(), st
    if st == StorageType.ElementId:
        return p.AsElementId(), st
    return None, None


def apply_partition(elem, value, storage_type):
    if value is None or storage_type is None:
        return
    p = elem.LookupParameter(PARTITION_PARAM_NAME)
    if p is None or p.IsReadOnly:
        return
    try:
        p.Set(value)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

class _AllowOnlyTheseIds(ISelectionFilter):
    """Restricts a follow-up PickObject() to only the elements the user
    already picked in the first PickObjects() call, so the 'click the
    master bar' step cannot accidentally pick something new. Uses a plain
    list + ElementId's own == operator (proven reliable elsewhere in this
    tool) rather than a Python set/hash over ElementId, to sidestep any
    cross-Revit-version uncertainty about ElementId's hash behavior."""

    def __init__(self, allowed_ids):
        self.allowed_ids = list(allowed_ids)

    def AllowElement(self, element):
        for eid in self.allowed_ids:
            if element.Id == eid:
                return True
        return False

    def AllowReference(self, reference, position):
        return True


class _SilentRebarFailurePreprocessor(IFailuresPreprocessor):
    """v2.4.0: without this, doc.Regenerate()/Transaction.Commit() can pop
    up Revit's own blocking "Can't solve Rebar Shape" error dialog
    (confirmed live on NBT's machine - a real modal Revit dialog, "Error -
    cannot be ignored", with Show/More Info/Expand/OK/Cancel) the moment a
    candidate bar's shape doesn't validate. That dialog waits for a person
    to click something, which freezes an otherwise-automatic tool and
    defeats the whole point of the retry ladder (the tool is supposed to
    silently try a simpler shape, not stop and ask). This preprocessor
    resolves every failure itself, the same way clicking a warning's/
    error's own default resolution would: delete plain warnings outright,
    and for an error (like "Can't solve Rebar Shape", whose only real
    resolution is to delete the offending element - matching what OK would
    have done anyway) call ResolveFailure() with its default resolution.
    The retry ladder's post-Regenerate() survival check (doc.GetElement(id)
    is not None) is what actually notices the deletion and steps down to a
    simpler point count - this class only stops Revit from popping up a
    dialog and waiting on a person to make that same call by hand."""

    def PreprocessFailures(self, failuresAccessor):
        try:
            failures = list(failuresAccessor.GetFailureMessages())
        except Exception:
            return FailureProcessingResult.Continue
        if not failures:
            return FailureProcessingResult.Continue
        resolved_any = False
        for failure in failures:
            try:
                severity = failure.GetSeverity()
                if severity == FailureSeverity.Warning:
                    failuresAccessor.DeleteWarning(failure)
                    resolved_any = True
                elif failure.HasResolutions():
                    failuresAccessor.ResolveFailure(failure)
                    resolved_any = True
                else:
                    # No resolution offered at all (rare) - the only way
                    # left to unblock regeneration is to delete whatever
                    # element(s) this failure is complaining about, the
                    # same drastic fallback Revit itself would need.
                    failing_ids = failure.GetFailingElementIds()
                    if failing_ids and failing_ids.Count > 0:
                        failuresAccessor.DeleteElements(failing_ids)
                        resolved_any = True
            except Exception:
                pass
        if resolved_any:
            return FailureProcessingResult.ProceedWithCommit
        return FailureProcessingResult.Continue


def pick_all_bars_then_master():
    """v2.0.0 two-step interactive pick, replacing v1.x's "pre-select the
    master bar before running the tool" + typed bar count. Returns
    (master_bar, target_bars, selected_host) or (None, None, None) if the
    user cancelled or the selection was invalid (already alerted)."""
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            'STEP 1 of 2: select every bar in this array - the master bar '
            'you hand-corrected AND all the other (still auto-generated) '
            'bars - plus the host column/pier if you like (box-select is '
            'fine). Click Finish when done.',
        )
    except RevitExceptions.OperationCanceledException:
        return None, None, None

    picked = [doc.GetElement(r.ElementId) for r in refs]
    all_rebars = [e for e in picked if isinstance(e, Rebar)]
    others = [e for e in picked if not isinstance(e, Rebar)]

    if len(all_rebars) < 2:
        forms.alert(
            'Please select at least 2 Rebar elements in step 1 (the master '
            'bar plus at least 1 bar to update) - you selected {} Rebar '
            'element(s).'.format(len(all_rebars)),
            title=TOOL_NAME,
        )
        return None, None, None

    rebar_ids = [r.Id for r in all_rebars]
    try:
        master_ref = uidoc.Selection.PickObject(
            ObjectType.Element,
            _AllowOnlyTheseIds(rebar_ids),
            'STEP 2 of 2: click the ONE hand-corrected master bar (among '
            'the {} bars you just selected).'.format(len(all_rebars)),
        )
    except RevitExceptions.OperationCanceledException:
        return None, None, None

    master_bar = doc.GetElement(master_ref.ElementId)
    target_bars = [r for r in all_rebars if r.Id != master_bar.Id]
    selected_host = others[0] if others else None
    return master_bar, target_bars, selected_host


def resolve_host(master_bar, selected_host):
    """v1.6.0: read the host directly off the master bar via
    Rebar.GetHostId() - see the v1.6.0 docstring entry for why this
    replaced the earlier RebarHostData-based approach. selected_host (any
    non-Rebar element the user included in step 1) is only a fallback for
    the rare case a bar is not actually hosted."""
    real_host = None
    real_host_debug = None
    try:
        host_id = master_bar.GetHostId()
        if host_id is None or host_id == ElementId.InvalidElementId:
            real_host_debug = 'Rebar.GetHostId() returned no valid host Id (bar may not be hosted)'
        else:
            real_host = doc.GetElement(host_id)
            if real_host is None:
                real_host_debug = 'Rebar.GetHostId() returned Id {} but GetElement() found nothing'.format(host_id)
    except Exception as ex:
        real_host_debug = 'Rebar.GetHostId() raised: {}'.format(str(ex))

    if real_host is not None:
        return real_host
    if selected_host is not None:
        return selected_host

    forms.alert(
        "Could not automatically determine the master bar's host element "
        '({}), and no host element was included in your selection either - '
        'please include the host column/pier element in step 1.'.format(real_host_debug),
        title=TOOL_NAME,
    )
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    master_bar, target_bars, selected_host = pick_all_bars_then_master()
    if master_bar is None:
        return

    host = resolve_host(master_bar, selected_host)
    if host is None:
        return

    center, radius_ft = find_bottom_circle(host)
    if center is None:
        forms.alert(
            'Could not find a horizontal, full-circle base face on the host '
            'element - this tool expects the same round-shaft host Circular '
            'Rebar Array used.',
            title=TOOL_NAME,
        )
        return

    master_points = get_bar_curve_points(master_bar)
    if len(master_points) < 2:
        forms.alert(
            'Could not read a usable curve from the master rebar bar '
            '(fewer than 2 points came back from GetCenterlineCurves).',
            title=TOOL_NAME,
        )
        return

    # v2.2.0: keep master_points at FULL detail here - resampling now
    # happens per-bar, per-attempt, inside the creation retry ladder below,
    # so no detail is thrown away before it is actually needed (see the
    # v2.2.0 docstring entry).

    master_base_radius = math.sqrt(
        (master_points[0].X - center.X) ** 2 + (master_points[0].Y - center.Y) ** 2)
    master_top_radius = math.sqrt(
        (master_points[-1].X - center.X) ** 2 + (master_points[-1].Y - center.Y) ** 2)
    master_top_z = master_points[-1].Z
    master_span = master_top_radius - master_base_radius
    # offset_dist: how far inside the true outer boundary every bar sits
    # (cover + bar radius), read back from the master's own base point
    # instead of asking again - the master was placed at the same base
    # circle as every other bar in the array.
    offset_dist = radius_ft - master_base_radius

    solids = get_element_solids(host)
    half_extent = get_half_extent_ft(host)
    bar_type = doc.GetElement(master_bar.GetTypeId())

    # v2.0.0: read each target bar's OWN real position (and Partition)
    # BEFORE anything is deleted - this is what lets the tool skip the old
    # "type in the original N, assume an idealized even split" step. Each
    # bar's true angle comes straight from where it actually is.
    plan = []  # (target_bar, direction_xy, partition_value, partition_storage)
    skip_warnings = []
    for tb in target_bars:
        pts = get_bar_curve_points(tb)
        if not pts:
            skip_warnings.append('Bar Id {}: could not read a curve - skipped'.format(tb.Id))
            continue
        base_pt = pts[0]
        dx, dy = base_pt.X - center.X, base_pt.Y - center.Y
        if dx * dx + dy * dy < 1e-12:
            skip_warnings.append('Bar Id {}: sits at the center, no defined angle - skipped'.format(tb.Id))
            continue
        direction = XYZ(dx, dy, 0.0).Normalize()
        part_val, part_storage = read_partition(tb)
        plan.append((tb, direction, part_val, part_storage))

    replacements = []  # (old_bar, lines, normal_h, part_val, part_storage)
    scale_warnings = []
    for old_bar, direction, part_val, part_storage in plan:
        best_u = None
        for solid in solids:
            slice_curves, _err = cut_horizontal_slice(solid, master_top_z, center, half_extent)
            if slice_curves is None:
                continue
            pts = tessellate_curves(slice_curves)
            if len(pts) < 3:
                continue
            u = ray_boundary_hit(pts, center, direction)
            if u is not None and (best_u is None or u > best_u):
                best_u = u
        if best_u is None:
            scale_warnings.append(
                'Bar Id {}: could not find the real surface at the top height - skipped'.format(old_bar.Id))
            continue
        dir_top_radius = best_u - offset_dist
        dir_span = dir_top_radius - master_base_radius
        if abs(master_span) < 1e-9:
            scale = 1.0
            scale_warnings.append(
                'Bar Id {}: master bar has almost no vertical bulge (span ~0) - '
                'used scale 1.0, result may not be meaningful'.format(old_bar.Id))
        else:
            scale = dir_span / master_span
        if scale < 0 or scale > 5.0:
            scale_warnings.append(
                'Bar Id {}: unusual scale factor {:.2f} (master_span={:.0f}mm, '
                'dir_span={:.0f}mm) - shape may look wrong or inverted'.format(
                    old_bar.Id, scale, internal_to_mm(master_span), internal_to_mm(dir_span)))

        scaled_points = []
        for p in master_points:
            r = master_base_radius + (
                math.sqrt((p.X - center.X) ** 2 + (p.Y - center.Y) ** 2) - master_base_radius
            ) * scale
            scaled_points.append(XYZ(
                center.X + direction.X * r, center.Y + direction.Y * r, p.Z))

        if len(scaled_points) < 2:
            scale_warnings.append('Bar Id {}: no valid points after scaling - skipped'.format(old_bar.Id))
            continue
        normal_h = XYZ(-direction.Y, direction.X, 0.0)
        # v2.2.0: keep the FULL scaled curve here - the creation loop below
        # resamples it down to whatever detail level actually succeeds for
        # this specific bar (see CREATE_RETRY_LADDER).
        replacements.append((old_bar, scaled_points, normal_h, part_val, part_storage))

    if not replacements:
        forms.alert(
            'No replacement bars could be computed.\n{}'.format(
                '\n'.join((skip_warnings + scale_warnings)[:10])),
            title=TOOL_NAME,
        )
        return

    # v2.3.0: build one "job" per bar to replace, each tracking its own
    # descending ladder of fallback point counts - see the v2.3.0 docstring
    # entry for why this moved to a round-based, doc.Regenerate()-checked
    # design instead of trusting CreateFromCurves()'s return value alone.
    jobs = []
    for old_bar, scaled_points, normal_h, part_val, part_storage in replacements:
        total_len_ft = 0.0
        for i in range(1, len(scaled_points)):
            total_len_ft += scaled_points[i].DistanceTo(scaled_points[i - 1])
        ideal_count = int(internal_to_mm(total_len_ft) / RESAMPLE_TARGET_SPACING_MM) + 1
        ideal_count = max(RESAMPLE_MIN_POINTS, min(RESAMPLE_MAX_POINTS, ideal_count))

        candidate_counts = [ideal_count]
        for c in CREATE_RETRY_LADDER:
            if c < candidate_counts[-1] and c not in candidate_counts:
                candidate_counts.append(c)
        if candidate_counts[-1] != 2:
            candidate_counts.append(2)

        jobs.append({
            'old_bar': old_bar,
            'scaled_points': scaled_points,
            'normal_h': normal_h,
            'part_val': part_val,
            'part_storage': part_storage,
            'ideal_count': ideal_count,
            'candidate_counts': candidate_counts,
            'ladder_index': 0,
            'pending_id': None,
            'pending_count': None,
            'used_count': None,
            'resolved': False,
            'failed': False,
            'last_error': None,
        })

    t = Transaction(doc, 'pyNBT - {}'.format(TOOL_NAME))
    t.Start()
    # v2.4.0: silence Revit's own "Can't solve Rebar Shape" failure dialog -
    # see _SilentRebarFailurePreprocessor's docstring above. Must be set
    # after Start() (there is no FailureHandlingOptions before a
    # transaction is open) and before the first Regenerate()/Commit() call
    # that could trigger it.
    fail_opts = t.GetFailureHandlingOptions()
    fail_opts.SetFailuresPreprocessor(_SilentRebarFailurePreprocessor())
    t.SetFailureHandlingOptions(fail_opts)
    created = 0
    creation_errors = []
    fidelity_notes = []
    try:
        # v2.3.0: round-based creation. Within a round, try the next
        # untried ladder count for every unresolved job (a plain
        # CreateFromCurves() call - cheap, no regen yet). Once every job
        # has either produced a "pending" bar or exhausted its ladder, do
        # ONE doc.Regenerate() for the whole batch, then check which
        # pending bars actually survived - this is the check that was
        # missing before: CreateFromCurves can return a real, non-null
        # Rebar that Revit's own Standard-style shape-fit validation then
        # silently deletes during regeneration, which is what was causing
        # "Created 49 of 49" to still end up with real bars missing from
        # the model. A pending bar that got silently removed goes back to
        # trying the NEXT (simpler) ladder count in the following round,
        # exactly like an immediate None result.
        # v2.3.1: IronPython 2.7's max() does not support the default=
        # keyword (that's a Python 3.4+ addition) - NBT hit this live as
        # "max() got an unexpected keyword argument (default)" the first
        # time he ran v2.3.0 through the real pyRevit button. jobs is
        # already guaranteed non-empty here (checked via `replacements`
        # a few lines above), so a plain max() over a list is enough.
        max_rounds = max([len(j['candidate_counts']) for j in jobs]) + 1
        for _round in range(max_rounds):
            progressed = False

            for job in jobs:
                if job['resolved'] or job['failed'] or job['pending_id'] is not None:
                    continue
                counts = job['candidate_counts']
                while job['ladder_index'] < len(counts):
                    cnt = counts[job['ladder_index']]
                    job['ladder_index'] += 1
                    scaled_points = job['scaled_points']
                    if cnt < len(scaled_points):
                        attempt_points = resample_even_by_length(scaled_points, cnt)
                    else:
                        attempt_points = scaled_points
                    simplified = simplify_collinear_points(attempt_points)
                    lines = points_to_lines(simplified)
                    if not lines:
                        continue
                    try:
                        curve_list = List[Curve]()
                        for curve in lines:
                            curve_list.Add(curve)
                        candidate_bar = Rebar.CreateFromCurves(
                            doc, RebarStyle.Standard, bar_type, None, None, host,
                            job['normal_h'], curve_list,
                            RebarHookOrientation.Left, RebarHookOrientation.Left,
                            False, True,
                        )
                    except Exception as ex:
                        candidate_bar = None
                        job['last_error'] = str(ex)
                    if candidate_bar is not None:
                        # v2.4.0: capture the Id right now, while the
                        # reference is still fresh - touching the ORIGINAL
                        # object again after doc.Regenerate() (e.g. reading
                        # its .Id) can itself throw "The referenced object
                        # is not valid, possibly because it has been
                        # deleted from the database, or its creation was
                        # undone" if Revit's failure resolution deleted it
                        # during that regeneration (confirmed live on NBT's
                        # machine). Everything after this point uses only
                        # the Id, re-fetched fresh from doc.GetElement().
                        job['pending_id'] = candidate_bar.Id
                        job['pending_count'] = cnt
                        progressed = True
                        break
                else:
                    job['failed'] = True

            if any(j.get('pending_id') is not None for j in jobs):
                doc.Regenerate()
                for job in jobs:
                    pending_id = job.get('pending_id')
                    if pending_id is None:
                        continue
                    fresh_elem = doc.GetElement(pending_id)
                    if fresh_elem is not None:
                        job['resolved'] = True
                        job['used_count'] = job['pending_count']
                        apply_partition(fresh_elem, job['part_val'], job['part_storage'])
                        doc.Delete(job['old_bar'].Id)
                        created += 1
                        job['pending_id'] = None
                    else:
                        # Revit accepted the create call but then removed
                        # the element while resolving a failure during
                        # regeneration - try the next, simpler candidate
                        # next round.
                        job['pending_id'] = None
                        progressed = True

            if not progressed:
                break

        for job in jobs:
            old_id = job['old_bar'].Id
            if job['resolved']:
                if job['used_count'] < job['ideal_count']:
                    fidelity_notes.append(
                        'Bar Id {}: created, but simplified to {} points (ideal was {}) - the '
                        'fuller shape did not fit, worth a manual Edit Sketch check'.format(
                            old_id, job['used_count'], job['ideal_count']))
            else:
                if job['last_error']:
                    creation_errors.append(
                        'Bar Id {}: CreateFromCurves failed at every fallback detail level '
                        '(down to a straight line) - last error: {} - old bar left in place'.format(
                            old_id, job['last_error']))
                else:
                    creation_errors.append(
                        'Bar Id {}: every fallback detail level either returned no element or '
                        'was silently removed by Revit during regeneration - old bar left in '
                        'place'.format(old_id))

        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        forms.alert('Failed: {}'.format(str(ex)), title=TOOL_NAME)
        return

    all_warnings = skip_warnings + scale_warnings + creation_errors + fidelity_notes
    if all_warnings:
        msg = (
            'Created {} of {} replacement bars (master bar left untouched).\n\n'
            'Notes:\n{}\n\n[{}]'
        ).format(created, len(target_bars), '\n'.join(all_warnings[:12]), TOOL_VERSION)
    else:
        msg = (
            'Created {} replacement bars, each scaled from the master bar '
            'shape to its own real position (master bar left untouched).\n\n[{}]'
        ).format(created, TOOL_VERSION)
    forms.alert(msg, title=TOOL_NAME)


try:
    main()
except Exception:
    forms.alert('Unexpected error:\n{}'.format(traceback.format_exc()), title=TOOL_NAME)
