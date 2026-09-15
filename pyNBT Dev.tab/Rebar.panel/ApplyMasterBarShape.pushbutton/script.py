# -*- coding: utf-8 -*-
"""Apply Master Bar Shape

Companion tool to Circular Rebar Array's Phase 3 ("Follow crosshead profile
above shaft" checkbox). See project doc tools/rebar-radial-array.md.

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

How to use:
1. Run Circular Rebar Array first (Phase 3, checkbox ON) as usual - this
   creates N straight-ish bars plus the N green center-reference lines.
2. Pick ONE bar you consider the most representative of the crosshead shape,
   open Edit Sketch on it, and manually correct its curve (drag/add points)
   until it matches the real surface you want, base to top. Finish Sketch.
3. Delete the OTHER (N-1) auto-generated bars in the array (keep only the
   one you just hand-corrected - this is now the "master" bar). The green
   reference lines can stay or go; this tool does not touch them.
4. Select the master bar (as of v1.4.0 that is enough on its own - the
   host column/pier is read automatically from the bar's own hosting
   relationship; only select the host too if you need to override that),
   then run this tool. It asks for the number of bars in the array (same
   N as the original run), reads the master bar's actual shape, works out
   which of the N evenly-spaced angles it belongs to, and re-creates the
   remaining
   N-1 bars: each one keeps the master's exact vertical "bulge" pattern
   (height vs. how far it has moved out from its own base radius) but
   scaled to that bar's OWN true radius at the top (read once via a single
   horizontal-slice ray cast at the master's own top height, so we are not
   relying on any of the noisy per-height ray casts that produced the messy
   result in the first place - only ONE ray cast per direction, at the top,
   which is far more reliable than the transition zone in between).
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
Circular Rebar Array hit in v1.3.5 (see tools/rebar-radial-array.md). Added
full diagnostics to the result popup to find the real cause instead of
guessing: master point count/base/top radius/span, the scale factor
min/avg/max actually used across all replacement bars (a scale outside
[0, 5] is flagged per-bar as "unusual - shape may look wrong or inverted"),
the computed total curve length per bar in mm (anything under 50mm is
flagged as likely too short/degenerate to render), and - separately - the
ACTUAL curve length read back from each just-created Rebar via its own
GetCenterlineCurves (to tell apart "we computed a fine curve but Revit
silently discarded/collapsed it on creation" from "we computed a degenerate
curve in the first place"). Removed the default '8' entirely (blank field)
so the prompt cannot silently carry over a stale value from an unrelated
test again.

v1.2.0 (2026-09-14): the v1.1.0 diagnostics themselves had a bug - the code
called GetCenterlineCurves() on each new Rebar immediately after creating
it, still inside the same open Transaction and before Revit had regenerated
that element's geometry, so the read-back silently failed for every bar
("n/a - could not read back") and told us nothing. Fixed by only recording
each new bar's ElementId during the creation loop, calling doc.Regenerate()
once after the whole loop finishes (still inside the transaction), then
re-fetching every bar by id and reading its real curve length in a second
pass. The diagnostics message now also distinguishes WHY the actual length
is unreadable when that still happens: a genuine per-bar read-back
exception (now shown) vs. CreateFromCurves returning no element at all -
this narrows down whether bars are being created with degenerate/invisible
geometry or not being created at all.

v1.3.0 (2026-09-14): v1.2.0's fixed diagnostics answered the question -
"ACTUAL curve length on created bars min/avg/max = n/a
(Rebar.CreateFromCurves returned no element for any bar)". Every single
one of the 99 replacement bars failed to create, uniformly, regardless of
scale factor - not a display/regenerate timing bug at all.
simplify_collinear_points() only removes points that are (near-)EXACTLY
collinear, which does nothing for a genuinely curved hand-edited shape:
the master bar's corrected curve, read back and tessellated, came back
with 27 points - almost certainly a smoothly curved Edit Sketch segment
broken into many small, not-quite-collinear straight pieces. Revit's
Standard rebar style can only auto-fit a curve chain to a shape with a
small number of real bends; a chain that long has far more bends than
that, so createNewShape=True has nothing to match and returns None
without throwing - identical failure for every replacement bar since
they all inherit the same point/bend count, just scaled differently.
Added douglas_peucker_indices(): true perpendicular-distance line
simplification (not just an angle check) applied to the master curve's
(radius, height) profile once, before it becomes the template for every
other bar - collapses a smooth curve down to its real handful of bend
points while staying within MASTER_SIMPLIFY_TOL_MM (25mm default - small
next to normal rebar placement tolerance) of the original shape. The diagnostics popup now reports master points "raw ->
simplified" and the resulting bar segment count range so a still-too-high
segment count is visible immediately instead of guessed at.

v1.4.0 (2026-09-14): NBT re-tested v1.3.0 with the reduction actually
working (27 raw -> 11 simplified points, 10 segments per bar, well down
from 26) and Rebar.CreateFromCurves STILL returned None for all 99 bars.
That rules out "too many bends" as the real cause - Circular Rebar Array
itself already proved Rebar.CreateFromCurves CAN create real, if messy,
multi-segment bars against this exact crosshead host, so the difference
has to be something else this tool does differently, not curve
complexity. The one meaningful difference: this tool asks the user to
RE-SELECT the host element by hand every run, instead of using whatever
element Revit itself considers the master bar's actual host - if NBT
selects a different (even visually identical/overlapping) element than
the one the master bar is really hosted on, CreateFromCurves has no
reason to succeed against it. Fixed by reading the true host directly
off the master bar via
Autodesk.Revit.DB.Structure.RebarHostData.GetRebarHostData(master_bar)
.GetHostElement() instead of trusting the second selected element -
selecting a host element is now optional (kept as a manual-override
fallback only if RebarHostData cannot resolve one). The diagnostics
popup now reports which host was actually used and flags it when it
differs from whatever the user had selected, so a mismatch is visible
instead of silently wrong.

v1.5.0 (2026-09-14): NBT re-tested v1.4.0 and RebarHostData could not
confirm the master bar's real host automatically (fell back to the
user's own selection, unchanged from before) - so v1.4.0 could not
actually be tested yet, and the result was still 0 visible bars either
way. Rather than guess a 4th hypothesis blind, added a control test:
right before the main 99-bar loop, build ONE trivial 2-point straight
Line at the master bar's own angle/base/top radius (no intermediate
bends at all - the same shape family as Circular Rebar Array's proven
Phase 1 bars) and try Rebar.CreateFromCurves with it inside its own
Transaction that is always rolled back afterward (so it never actually
leaves a bar in the model). The result ("SUCCESS" / "FAILED" / an
exception message) is reported in the diagnostics popup as "control
test = ..." - this isolates whether bar_type/host themselves are usable
via this API call AT ALL, independent of curve shape: FAILED here means
the problem is bar_type/host/API mechanics, not the curved multi-point
shapes; SUCCESS means the problem is specific to those curved shapes.
Also made the host-resolution failure itself diagnostic instead of a
single generic message - it now reports the actual reason RebarHostData
could not resolve a host (class unavailable / returned None /
GetHostElement() returned None / threw an exception with its message),
plus both element Ids when host and selection differ.
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
)
from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation, MultiplanarOption
try:
    from Autodesk.Revit.DB.Structure import RebarHostData
except Exception:
    RebarHostData = None
from Autodesk.Revit.UI.Selection import ObjectType
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
TOOL_VERSION = 'v1.5.0'

# v1.3.0: how closely the simplified master shape must still hug the
# original hand-edited curve, in mm, when reducing it to its real bend
# points (see douglas_peucker_indices() below). Smaller = keeps more
# points/closer match but risks the same "too many bends" failure that
# v1.3.0 was written to fix; larger = fewer bends but a coarser match to
# what NBT actually drew.
MASTER_SIMPLIFY_TOL_MM = 25.0


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
    lines = []
    for i in range(len(points) - 1):
        if points[i].DistanceTo(points[i + 1]) > 1e-6:
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

    v1.3.0: added after v1.2.0's fixed diagnostics revealed the real bug
    behind "Created 99 replacement bars" with nothing visible - it was
    never a display/regenerate timing issue, it was
    Rebar.CreateFromCurves silently returning None for EVERY single one
    of the 99 bars (uniform 100% failure, regardless of scale factor).
    simplify_collinear_points() only removes points that are (near-)
    EXACTLY collinear (angle change < 0.5deg), which is enough for a
    perfectly straight column but does nothing for a genuinely curved
    hand-edited master shape - NBT's Edit Sketch correction, read back
    through GetCenterlineCurves() and tessellated into points, came back
    with 27 points, almost certainly because a smoothly curved sketch
    segment got broken into many small near-but-not-quite-collinear
    straight pieces. Revit's Standard rebar style can only auto-fit a
    curve chain to one of its known bend patterns when the chain has a
    small number of real bends; a 26-segment chain has far more bends
    than that, so createNewShape=True has nothing to match and returns
    None without throwing - identical for every replacement bar since
    they all inherit the same (just-scaled) point count. Fixing this
    means genuinely reducing the master shape to its real bend points,
    not just its exactly-straight runs - which is what
    Douglas-Peucker's perpendicular-distance test does: it keeps a point
    only if the path would otherwise stray further than `tol` from a
    straight line between its neighbours, so a smooth curve collapses to
    a handful of straight segments that still hug the original shape
    within `tol`."""
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


def bar_angles(count):
    result = []
    for i in range(count):
        angle = 2.0 * math.pi * i / count
        result.append(XYZ(math.cos(angle), math.sin(angle), 0.0))
    return result


# ---------------------------------------------------------------------------
# Master bar reading
# ---------------------------------------------------------------------------

def get_master_curve_points(master_bar):
    """Ordered list of XYZ points along the master Rebar's actual placed
    centerline (bar position index 0 - this tool only supports a
    single-bar layout, which is what Circular Rebar Array creates).
    Sorted by Z ascending so the result always reads base -> top regardless
    of which end the underlying curve chain happened to start from."""
    curves = list(master_bar.GetCenterlineCurves(
        False, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, 0))
    points = tessellate_curves(curves)
    points.sort(key=lambda p: p.Z)
    return points


def nearest_direction_index(point, center, directions):
    dx, dy = point.X - center.X, point.Y - center.Y
    if dx * dx + dy * dy < 1e-12:
        return 0
    angle = math.atan2(dy, dx)
    best_i, best_diff = 0, None
    for i, d in enumerate(directions):
        d_angle = math.atan2(d.Y, d.X)
        diff = abs((angle - d_angle + math.pi) % (2.0 * math.pi) - math.pi)
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    return best_i


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def get_master_and_host():
    """Expects the hand-corrected master Rebar to be selected, either
    ALONE or together with the host column/pier element (at most 1 other
    element). v1.4.0: the host is now read directly from the master bar
    itself via RebarHostData.GetRebarHostData(), instead of trusting
    whatever second element the user happened to select - see the
    v1.4.0 docstring entry for why (v1.3.0's fix cut the master curve's
    bend count from 26 down to 9 and Rebar.CreateFromCurves STILL
    returned None for all 99 bars, which does not fit a "too many bends"
    explanation and points at something more fundamental: the host
    Rebar.CreateFromCurves needs may not be the same element the user is
    re-selecting by hand each time). Returns (master_bar, host,
    host_note) or (None, None, None) after alerting the user why it
    could not proceed. host_note is a short string describing where the
    host came from, included in the result popup so a mismatch is
    visible instead of silent."""
    selected_ids = list(uidoc.Selection.GetElementIds())
    elements = [doc.GetElement(eid) for eid in selected_ids]
    rebars = [e for e in elements if isinstance(e, Rebar)]
    others = [e for e in elements if not isinstance(e, Rebar)]
    if len(rebars) != 1 or len(others) > 1:
        forms.alert(
            'Please pre-select the hand-corrected master rebar bar - and, '
            'optionally, the host column/pier element - before running '
            'this tool: select exactly 1 Rebar element, plus at most 1 '
            'other element (you currently have {} Rebar and {} other '
            'element(s) selected).'.format(len(rebars), len(others)),
            title=TOOL_NAME,
        )
        return None, None, None

    master_bar = rebars[0]
    selected_host = others[0] if others else None

    real_host = None
    real_host_debug = None
    if RebarHostData is None:
        real_host_debug = 'RebarHostData class not available in this Revit API'
    else:
        try:
            host_data = RebarHostData.GetRebarHostData(master_bar)
            if host_data is None:
                real_host_debug = 'GetRebarHostData() returned None for the master bar'
            else:
                real_host = host_data.GetHostElement()
                if real_host is None:
                    real_host_debug = 'GetHostElement() returned None'
        except Exception as ex:
            real_host_debug = 'GetRebarHostData()/GetHostElement() raised: {}'.format(str(ex))

    if real_host is not None:
        host = real_host
        if selected_host is not None and selected_host.Id != real_host.Id:
            host_note = (
                'read from the master bar itself (RebarHostData, host Id {}) - this is '
                'DIFFERENT from the other element you had selected (Id {}), so that '
                'one was ignored'
            ).format(real_host.Id, selected_host.Id)
        else:
            host_note = 'read from the master bar itself (RebarHostData, host Id {})'.format(real_host.Id)
    elif selected_host is not None:
        host = selected_host
        host_note = (
            'from your selection (Id {}) - could not confirm the master bar\'s '
            'real host automatically: {}'
        ).format(selected_host.Id, real_host_debug)
    else:
        forms.alert(
            "Could not automatically determine the master bar's host "
            'element, and no host element was selected either - please '
            'also select the host column/pier element.',
            title=TOOL_NAME,
        )
        return None, None, None

    return master_bar, host, host_note


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    master_bar, host, host_note = get_master_and_host()
    if master_bar is None:
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

    count_str = forms.ask_for_string(
        default='',
        prompt='Number of bars in the ORIGINAL Circular Rebar Array run '
               '(the total count before you deleted any bars - e.g. if you '
               'started with 100 bars and deleted 99, enter 100, not 99):',
        title=TOOL_NAME,
    )
    if not count_str:
        return
    try:
        count = int(count_str.strip())
    except Exception:
        forms.alert('Number of bars must be a whole number.', title=TOOL_NAME)
        return
    if count < 2:
        forms.alert('Number of bars must be at least 2.', title=TOOL_NAME)
        return

    master_points = get_master_curve_points(master_bar)
    if len(master_points) < 2:
        forms.alert(
            'Could not read a usable curve from the selected master rebar bar '
            '(fewer than 2 points came back from GetCenterlineCurves).',
            title=TOOL_NAME,
        )
        return

    master_points_raw_count = len(master_points)
    if master_points_raw_count >= 3:
        # v1.3.0: reduce the master curve down to its real bend points
        # (radius vs height) BEFORE using it as the template for every
        # other bar - see douglas_peucker_indices() docstring for why
        # simplify_collinear_points() alone was not enough.
        r_z_pairs = [
            (math.sqrt((p.X - center.X) ** 2 + (p.Y - center.Y) ** 2), p.Z)
            for p in master_points
        ]
        tol_ft = mm_to_internal(MASTER_SIMPLIFY_TOL_MM)
        kept_idx = douglas_peucker_indices(r_z_pairs, tol_ft)
        master_points = [master_points[i] for i in kept_idx]

    directions = bar_angles(count)
    master_dir_index = nearest_direction_index(master_points[0], center, directions)

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

    # v1.5.0 control test: v1.3.0 (cut bend count 26 -> 9) and v1.4.0
    # (auto-detect the real host) each fixed a real, confirmed difference
    # from Circular Rebar Array's proven-working code, and
    # Rebar.CreateFromCurves STILL returned None for all 99 bars both
    # times. Before trying a 4th hypothesis blind, create ONE trivial
    # control bar - a plain 2-point straight Line at the master's OWN
    # angle and base/top radius (no intermediate bends at all, built the
    # same way Circular Rebar Array's proven Phase 1 bars are) - inside
    # its own throwaway Transaction that always gets rolled back. If this
    # ALSO returns None, the problem is bar_type/host/API mechanics, not
    # curve shape - if it SUCCEEDS, the problem is specific to the
    # multi-point curved shapes built for the other 99 directions.
    control_direction = directions[master_dir_index]
    control_base = center + control_direction.Multiply(master_base_radius)
    control_top = XYZ(control_base.X, control_base.Y, master_top_z)
    if control_base.DistanceTo(control_top) > 1e-6:
        control_normal = XYZ(-control_direction.Y, control_direction.X, 0.0)
        control_curves = List[Curve]()
        control_curves.Add(Line.CreateBound(control_base, control_top))
        t_test = Transaction(doc, 'pyNBT - {} (control test, rolled back)'.format(TOOL_NAME))
        t_test.Start()
        try:
            control_bar = Rebar.CreateFromCurves(
                doc, RebarStyle.Standard, bar_type, None, None, host, control_normal,
                control_curves, RebarHookOrientation.Left, RebarHookOrientation.Left,
                False, True,
            )
            control_result = (
                'SUCCESS (a plain straight bar CAN be created with this bar_type/host)'
                if control_bar is not None else
                'FAILED (Rebar.CreateFromCurves returned no element even for a plain '
                'straight bar - problem is bar_type/host, not curve shape)'
            )
        except Exception as ex:
            control_result = 'EXCEPTION: {}'.format(str(ex))
        finally:
            if t_test.HasStarted():
                t_test.RollBack()
    else:
        control_result = 'skipped (master base and top points coincide)'

    scale_warnings = []
    bars = []
    scales = []
    curve_lengths_mm = []
    segment_counts = []
    for i, direction in enumerate(directions):
        if i == master_dir_index:
            continue
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
                'Bar {}: could not find the real surface at the top height - skipped'.format(i + 1))
            continue
        dir_top_radius = best_u - offset_dist
        dir_span = dir_top_radius - master_base_radius
        if abs(master_span) < 1e-9:
            scale = 1.0
            scale_warnings.append(
                'Bar {}: master bar has almost no vertical bulge (span ~0) - '
                'used scale 1.0, result may not be meaningful'.format(i + 1))
        else:
            scale = dir_span / master_span
        if scale < 0 or scale > 5.0:
            scale_warnings.append(
                'Bar {}: unusual scale factor {:.2f} (master_span={:.0f}mm, '
                'dir_span={:.0f}mm) - shape may look wrong or inverted'.format(
                    i + 1, scale, internal_to_mm(master_span), internal_to_mm(dir_span)))
        scales.append(scale)

        scaled_points = []
        for p in master_points:
            r = master_base_radius + (
                math.sqrt((p.X - center.X) ** 2 + (p.Y - center.Y) ** 2) - master_base_radius
            ) * scale
            scaled_points.append(XYZ(
                center.X + direction.X * r, center.Y + direction.Y * r, p.Z))

        simplified = simplify_collinear_points(scaled_points)
        lines = points_to_lines(simplified)
        if not lines:
            scale_warnings.append('Bar {}: no valid points after scaling - skipped'.format(i + 1))
            continue
        total_len_mm = internal_to_mm(sum(line.Length for line in lines))
        curve_lengths_mm.append(total_len_mm)
        if total_len_mm < 50.0:
            scale_warnings.append(
                'Bar {}: resulting curve is only {:.0f}mm long in total - '
                'likely too short/degenerate to render as a visible bar'.format(i + 1, total_len_mm))
        normal_h = XYZ(-direction.Y, direction.X, 0.0)
        segment_counts.append(len(lines))
        bars.append((lines, normal_h, len(simplified)))

    if not bars:
        forms.alert(
            'No replacement bars could be computed.\n{}'.format('\n'.join(scale_warnings[:8])),
            title=TOOL_NAME,
        )
        return

    t = Transaction(doc, 'pyNBT - {}'.format(TOOL_NAME))
    t.Start()
    created = 0
    created_lengths_mm = []
    creation_errors = []
    readback_errors = []
    created_bar_ids = []
    try:
        for idx, (lines, normal_h, _npts) in enumerate(bars):
            try:
                curve_list = List[Curve]()
                for curve in lines:
                    curve_list.Add(curve)
                new_bar = Rebar.CreateFromCurves(
                    doc, RebarStyle.Standard, bar_type, None, None, host, normal_h,
                    curve_list, RebarHookOrientation.Left, RebarHookOrientation.Left,
                    False, True,
                )
                created += 1
                if new_bar is not None:
                    # v1.2.0: don't read GetCenterlineCurves() back right away -
                    # Revit has not regenerated this element's geometry yet inside
                    # the still-open transaction, so an immediate read-back can
                    # throw/return nothing. Just remember the id; read back for
                    # real in a second pass below, after doc.Regenerate().
                    created_bar_ids.append(new_bar.Id)
            except Exception as ex:
                creation_errors.append('Bar {}: {}'.format(idx + 1, str(ex)))

        # v1.2.0: force Revit to compute geometry for the bars just created,
        # then re-fetch each one by its ElementId and read its REAL curve
        # length - this is what "ACTUAL curve length on created bars" in the
        # diagnostics below is meant to report.
        doc.Regenerate()
        for bar_id in created_bar_ids:
            try:
                fresh_bar = doc.GetElement(bar_id)
                actual_curves = list(fresh_bar.GetCenterlineCurves(
                    False, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, 0))
                created_lengths_mm.append(
                    internal_to_mm(sum(c.Length for c in actual_curves)))
            except Exception as ex:
                readback_errors.append(str(ex))

        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        forms.alert('Failed: {}'.format(str(ex)), title=TOOL_NAME)
        return

    if created_lengths_mm:
        actual_str = '{:.0f}/{:.0f}/{:.0f}mm'.format(
            min(created_lengths_mm), sum(created_lengths_mm) / float(len(created_lengths_mm)),
            max(created_lengths_mm))
    elif readback_errors:
        actual_str = 'n/a (read-back error: {})'.format(readback_errors[0])
    elif created_bar_ids:
        actual_str = 'n/a (0 of {} created bars had readable curves)'.format(len(created_bar_ids))
    else:
        actual_str = 'n/a (Rebar.CreateFromCurves returned no element for any bar)'

    diag = ''
    if scales:
        seg_str = '{}/{}'.format(min(segment_counts), max(segment_counts)) if segment_counts else 'n/a'
        diag = (
            '\n\nDiagnostics: host = {}; control test = {}; '
            'master base/top radius = {:.0f}/{:.0f}mm (span {:.0f}mm), '
            'master points = {} raw -> {} simplified (tol {:.0f}mm), '
            'master_dir_index = {} of {}; '
            'bar segments (bends+1) min/max = {}; '
            'scale factor min/avg/max = {:.2f}/{:.2f}/{:.2f}; '
            'computed curve length min/avg/max = {:.0f}/{:.0f}/{:.0f}mm; '
            'ACTUAL curve length on created bars min/avg/max = {}'
        ).format(
            host_note, control_result,
            internal_to_mm(master_base_radius), internal_to_mm(master_top_radius),
            internal_to_mm(master_span), master_points_raw_count, len(master_points),
            MASTER_SIMPLIFY_TOL_MM, master_dir_index, count,
            seg_str,
            min(scales), sum(scales) / float(len(scales)), max(scales),
            min(curve_lengths_mm) if curve_lengths_mm else 0.0,
            (sum(curve_lengths_mm) / float(len(curve_lengths_mm))) if curve_lengths_mm else 0.0,
            max(curve_lengths_mm) if curve_lengths_mm else 0.0,
            actual_str,
        )

    all_errors = scale_warnings + creation_errors
    if all_errors:
        msg = 'Created {} of {} replacement bars (master bar left untouched).\n\nErrors:\n{}{}\n\n[{}]'.format(
            created, count - 1, '\n'.join(all_errors[:10]), diag, TOOL_VERSION)
    else:
        msg = 'Created {} replacement bars, scaled from the master bar shape (master bar left untouched).{}\n\n[{}]'.format(
            created, diag, TOOL_VERSION)
    forms.alert(msg, title=TOOL_NAME)


try:
    main()
except Exception:
    forms.alert('Unexpected error:\n{}'.format(traceback.format_exc()), title=TOOL_NAME)
