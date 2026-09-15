# -*- coding: utf-8 -*-
"""Circular Rebar Array

Phase 1 of pyNBT's radial rebar tooling (see project doc
tools/rebar-radial-array.md). Places straight vertical rebar bars evenly
distributed around the circular cross-section of a selected column-like
element.

How to use:
1. Select the target element BEFORE running (or the tool will prompt you to
   pick one). The element must have a flat, horizontal, fully circular face
   somewhere on it (a straight cylindrical shaft works; a tapered/oval pier
   cap does not yet - that is Phase 3).
2. In the window: pick a Rebar Bar Type, enter the number of bars, the
   concrete cover, and the vertical bar length.
3. Click Create. The tool finds the LOWEST horizontal circular face of the
   element, computes N points evenly spaced around that circle (inset by
   cover + bar radius), and creates N straight vertical Rebar elements
   hosted to the selected element, starting at that face and going up.

Edge cases handled:
- No element selected / selection cancelled -> stop quietly, no Transaction.
- Element has no solid geometry, or no full-circle horizontal face found ->
  alert and stop before opening the window.
- Individual bar creation failures (e.g. host rejects a specific curve) are
  caught PER BAR inside the loop so one failure does not roll back the bars
  that already succeeded; the Transaction still commits at the end.
- Bar type's modeled diameter cannot be read -> falls back to 0 (no diameter
  inset, only cover is applied) and the window's status line says so, so
  results are not silently wrong.

EXPERIMENTAL Phase 3 addition (2026-09-13, v1.2.0 - horizontal-slice-scan
rewrite): a "Follow crosshead profile above shaft" checkbox. When checked,
each bar ignores the fixed "Bar length" field and instead follows the
element's REAL geometry from the base circle up to the highest horizontal
face found (the flat top of a pier cap/crosshead).

v1.1.0 tried this with a single vertical "radial half-plane" Boolean cut per
bar. NBT's first live test showed it did not work: bars came out identical
to Phase 1 (straight, stopping at the shaft top). Two real bugs caused this:
(1) the element's geometry is commonly several separate Solids (shaft,
transition fillet, cap - not Boolean-joined in the family), and the code
only ever cut against the single largest one (the shaft) - the crosshead
was never even touched; (2) a single flat vertical plane cut through a
doubly-curved blend surface (round shaft flaring into a stadium cap) is not
guaranteed to come back as one clean face - Revit can fragment it, and
picking "the largest face" can silently discard the real profile.

v1.2.0 changed method entirely: instead of one vertical cut per bar, the
pier is sliced HORIZONTALLY at a series of heights from base to top
(cut_horizontal_slice) - a horizontal cut of a vertical-ish pier is always a
single simple closed 2D loop, far more robust than a vertical cut through a
warped surface. For each bar's angle, a 2D ray cast from the column axis
against that height's boundary loop (ray_boundary_hit) gives the exact
surface radius at that angle and height - offset inward by cover to get the
bar's point at that height; stacking these from base to top gives the bar's
true multi-segment shape. v1.2.0 also tried Boolean-UNIONing the element's
separate solids into one before cutting, on the theory that a family with a
shaft/fillet/cap sketched as separate, non-joined Solids needed to be
merged first. NBT's second live test showed this STILL did not work, even
after he manually ran Revit's own Join Geometry on the solids inside the
family and reloaded - the bars still came out unchanged (shaft only).

v1.3.0 (2026-09-13) fixes the real cause: Revit's Boolean Union commonly
fails/throws on solids that only TOUCH without volumetric overlap (e.g. the
shaft's top face is exactly coincident with the transition solid's bottom
face - very likely for a family built as separate sketched solids), and
the union code's try/except was silently swallowing that failure, leaving
the "union" as just the single largest solid again - reproducing the exact
v1.1.0 bug under a different name. This also explains why NBT's own native
Join Geometry didn't help: Join Geometry is a display/cleanup relationship
between elements, not a merge of the Solid objects the Revit API hands
back - get_Geometry() still returns the same separate solids either way.

v1.3.0 removes the Union step entirely. Each height slice now cuts EVERY
one of the element's solids separately (still cheap - each solid/height
Boolean is simple), and for each bar's angle takes the FARTHEST valid ray
hit across all of them. Solids that are merely stacked and touching (not
overlapping) each contribute their own real material over their own height
range, so no merge was ever actually needed - this sidesteps the Boolean
Union failure mode altogether.

v1.3.1 (2026-09-14): NBT reported the crosshead portion still wasn't being
created and, separately, asked for the rule that every bar's cut must run
from the round shaft's true center outward to be non-negotiable. On the
math, that rule was already structurally guaranteed (every point on a bar
is computed as center + direction * radius using the SAME center/direction
for that bar at every height - it cannot end up off-axis), so no change was
needed there. What changed instead is diagnosability for the still-open
"crosshead not created" problem: (1) the horizontal slab thickness went
from 2mm to 10mm, cheap insurance against a genuinely tiny real gap between
NBT's separate solids at their shared seam; (2) the result message now
always reports, in profile mode, each solid's own Z-range (solid_z_range)
and how many of the height stations found any material and how many points
each bar actually got - so a genuine modeling gap between solids (as
opposed to a remaining code bug) is visible directly from the tool's own
output on the next test, instead of needing a hand-drawn diagram to guess
at what is happening.

v1.3.2 (2026-09-14): NBT sent a video of Revit's own "Edit Sketch" view on
a created bar: the dashed green work-plane shown by Revit visually stops
short of the true circle center, while a bar he sketched by hand shows a
plane that visually reaches the center. This is almost certainly a Revit
UI framing effect, not a geometry bug - Revit draws the Edit Sketch plane
outline cropped tightly around the sketch curve itself, and every point of
a profile-mode bar sits out near the shaft/crosshead surface (radius close
to the full detected radius minus cover), never near radius zero, so the
outline never visually extends back to the center even though the
underlying infinite plane (normal_h, built as the horizontal vector
perpendicular to `direction`) mathematically always contains the vertical
line through center - unaffected by how close the actual bar curve happens
to sit to that center. NBT's own hand-drawn reference bar, by contrast,
is modeled as a full diametral cut that passes through the center itself,
so its own sketch curve extends all the way through the center and Revit's
crop naturally includes it.

Rather than argue this abstractly again, this version adds a hard numeric
check instead of one more visual/diagnostic-text argument: right before
creating the bars, for every bar's first and last computed point, compute
the actual angle from the true center to that point and compare it to the
bar's intended angle. The result popup now always reports, in profile
mode, the true center's X/Y (mm) and the largest angular deviation found
across every bar (degrees) - this should read ~0.0000 degrees every time,
by construction, and gives NBT a plain number to check against Revit's own
coordinate readout instead of having to eyeball a cropped dashed outline.

v1.3.3 (2026-09-14): NBT's real ask, stated plainly: he wants to be able to
open a bar's Edit Sketch and see/edit a sketch that visibly passes through
the true center - the numeric proof in v1.3.2 doesn't give him that, it
only proves the math is right. Revit itself draws a Rebar's Edit Sketch
work-plane boundary cropped tightly around that bar's own curve (which
never comes near the center by design - it hugs the shaft/crosshead
surface), and that boundary is Revit's own sketch-editor rendering, not
something this API can widen or re-anchor. So instead of fighting that
rendering, profile mode now also places one real, editable Model Line per
bar directly in the model: a straight line from the true center out along
that bar's exact angle, on a horizontal work plane at the base elevation -
literally the "radial section line through the true center" NBT already
draws by hand for each bar. He can select it, edit it, snap to it, or
delete it like any other line; unlike the Rebar's own curve, this line
starts AT the center by construction, so there is no cropping to argue
about. These are added inside the same Transaction as the bars themselves,
so a cancelled/rolled-back run leaves no stray lines behind, and the
result message reports how many were created.

v1.3.4 (2026-09-14): two changes from NBT's latest round. (1) NBT's screenshot
after being asked to fully restart Revit still showed the pre-Phase-3, 3-field
dialog - proof (a second time, after video #1) that Revit had still not
reloaded any Phase-3 code at all, so nothing about bar geometry could be
concluded from that test. Rather than rely on counting dialog fields, the
window itself now shows its version directly in the header ("Tool version:
vX.Y.Z") and in the result popup, so a single screenshot settles whether
Revit is running the current script - no more guessing from field counts.
(2) NBT asked for the reference line to be built FIRST and for the bar's
cutting plane to be determined from it, "to avoid repeating past mistakes" -
the v1.3.3 reference line only ran along the base at a single height (a flat
horizontal spoke), which does look through the center in plan but reads as
unrelated to the bar in a 3D/isometric view (his own screenshots are always
isometric). The reference line now runs from the true center at the base
(r=0) up to the far edge at the crosshead top (r=half_extent, z=top_z), lying
entirely inside the SAME vertical plane (same normal_h) used to build that
bar's own curve - one plane, one line, one bar, so there is nothing left for
the two to disagree about, and the line still projects to a straight spoke
through center in Top View while also reading as an obvious diagonal
reference in the isometric views NBT has been using throughout.

v1.3.5 (2026-09-14): NBT confirmed [v1.3.4] in the result popup - proof
Revit was finally running current code - and then reported the checkbox
itself was nowhere to be seen in the dialog. That pointed at a real,
separate UI bug rather than another stale-code episode: the window's
Grid rows (header/content/footer) were built with a helper (_row) whose
'auto' default never actually set GridUnitType.Auto - WPF's true default
for an unset RowDefinition.Height is 1-star, so all three rows silently
split the fixed 490px window height into equal thirds regardless of
content. That was invisible back when the dialog only had 3 short fields
(v1.0.0), but every field added since (Bar length, the profile checkbox,
the diagnostics-driving version line) made the middle row's real content
taller than its forced third - WPF does not clip Grid row overflow, so
the extra controls rendered past the row boundary, overlapping the
footer's opaque background and disappearing from view even though they
were still really there. Fixed by making header/footer truly Auto-sized
and the content row an explicit star (fills whatever the Auto rows don't
claim), plus a modest bump of Window.Height (490 -> 540) for margin as
more fields get added later.

v1.3.6 (2026-09-14): with the UI bug fixed, NBT ran his FIRST genuinely
valid profile-mode test (checkbox visible and ticked, on a plain
untapered round column, no crosshead) - the result popup said "Created 8
bars around the circle" with zero errors and a perfect 0.0000 deg center
deviation, yet no bar geometry showed up in the model at all (only the 8
center-reference lines were visible). Root cause: for a plain round
column every height station lands on the exact same radius, so the 30
computed points per bar are all (near-)collinear - build_profile_bars_all
was feeding Rebar.CreateFromCurves a chain of ~29 tiny straight segments
with ~0-degree "bends" between them. Rebar.CreateFromCurves did not throw,
but Revit's Standard rebar shape engine matches curve chains against a
library of real bend patterns, and a chain of that many degenerate
non-bends does not produce a renderable shape - the call silently
succeeds while creating nothing visible. Fixed by adding
simplify_collinear_points(): before building the curves passed to
Rebar.CreateFromCurves (NOT before the diagnostic point counts, which
still report the raw per-station data), consecutive points whose
direction does not change by more than a small angular tolerance are
collapsed away, keeping only points where the bar's path actually bends.
A plain round shaft now collapses back down to a single straight segment
(matching Phase 1's own bars), while a real crosshead-following bar keeps
its genuine bend points and only loses the redundant collinear ones.

NBT also reported, on the SAME screenshots, that the Phase 1 (checkbox
OFF) bars "still don't pass through the center" - to be clear, that is
by design, not a bug: a Phase-1 bar is placed at the outer radius (radius
- cover - bar radius), matching where a real rebar rod actually sits in
the concrete - it was never meant to touch the center. Only the separate
green Model Line (profile mode only) represents the through-center
cutting plane used to derive the bar's shape; the bar's own rod is
supposed to stay near the surface, exactly like NBT's own hand-tied
rebar.
"""

__title__ = 'Circular\nRebar Array'
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
from System.Windows import (
    Window, WindowStartupLocation, Thickness, HorizontalAlignment, FontWeights,
    TextWrapping, ResizeMode
)
from System.Windows.Controls import (
    Grid, RowDefinition, ColumnDefinition, StackPanel, TextBlock, TextBox,
    ComboBox, Button, Border, Orientation, CheckBox
)
from Autodesk.Revit.DB import (
    XYZ, Line, Curve, CurveLoop, Transaction, Options, GeometryInstance, Solid,
    SolidUtils, PlanarFace, Arc, Ellipse, FilteredElementCollector, Element,
    GeometryCreationUtilities, BooleanOperationsUtils, BooleanOperationsType,
    Plane, SketchPlane
)
from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation, RebarBarType
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
from pyNBT import theme

doc = revit.doc
uidoc = revit.uidoc

TOOL_NAME = 'Circular Rebar Array'
TOOL_VERSION = 'v1.3.6'


# ---------------------------------------------------------------------------
# Standalone logic - pure Revit API work, no UI references
# ---------------------------------------------------------------------------

def mm_to_internal(value_mm):
    """mm -> internal units (feet), built on top of the shared m_to_internal."""
    return m_to_internal(value_mm / 1000.0)


def internal_to_mm(value_ft):
    """internal units (feet) -> mm, built on top of the shared internal_to_m."""
    return internal_to_m(value_ft) * 1000.0


def get_element_solids(element):
    """Return all Solids with non-zero volume belonging to element, including
    solids nested inside GeometryInstance (family instances)."""
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
                inst_geo = obj.GetInstanceGeometry()
                collect(inst_geo)

    collect(geo)
    return solids


def solid_z_range(solid):
    """Min/max Z (feet) spanned by a Solid's own edges - a cheap, always-
    available bounding range (Solid has no direct BoundingBox in the Revit
    API). Used purely for diagnostics: shows NBT the real height range each
    separate solid occupies, so a genuine vertical gap between solids (as
    opposed to a code bug) is visible directly in the tool's own result
    message instead of needing a hand-drawn diagram to guess at it."""
    z_values = []
    for edge in solid.Edges:
        try:
            curve = edge.AsCurve()
            z_values.append(curve.GetEndPoint(0).Z)
            z_values.append(curve.GetEndPoint(1).Z)
        except Exception:
            continue
    if not z_values:
        return None, None
    return min(z_values), max(z_values)


def _arc_sweep(curve):
    try:
        return abs(curve.GetEndParameter(1) - curve.GetEndParameter(0))
    except Exception:
        return 0.0


def _loop_as_circle(loop):
    """If the edges of `loop` (an EdgeArray, the outer boundary of a face)
    together trace exactly one full circle, return (center, radius).
    Otherwise return None.

    Handles three ways Revit can store a "circle":
    - a single Ellipse edge with RadiusX == RadiusY
    - a single Arc edge swept a full 2*pi
    - two (or more) Arc edges sharing the same center/radius whose sweeps add
      up to 2*pi (a common way circular profiles get split into halves)
    """
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

    # Multiple edges: only accept a set of Arcs sharing center/radius whose
    # sweeps sum to a full circle.
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
    """Find the lowest, horizontal, full-circle planar face of element.

    Returns (center_xyz, radius_ft, debug_lines) - center/radius are None if
    no full-circle face was found; debug_lines always lists every horizontal
    planar face that was examined, to help diagnose a miss.
    """
    solids = get_element_solids(element)
    best = None  # (z, center, radius)
    debug_lines = []

    for solid in solids:
        for face in solid.Faces:
            if not isinstance(face, PlanarFace):
                continue
            normal = face.FaceNormal
            if abs(normal.Z) < 0.99:
                continue  # not (close enough to) horizontal

            edge_loops = face.EdgeLoops
            z = face.Origin.Z

            if edge_loops.Size != 1:
                debug_lines.append(
                    'z={:.0f}mm: {} boundary loops (has holes) - skipped'.format(
                        internal_to_mm(z), edge_loops.Size))
                continue

            loop = edge_loops[0]
            circle = _loop_as_circle(loop)
            if circle is None:
                curve_types = [type(e.AsCurve()).__name__ for e in loop]
                debug_lines.append(
                    'z={:.0f}mm: {} edge(s), types={} - not a full circle'.format(
                        internal_to_mm(z), loop.Size, curve_types))
                continue

            center, radius = circle
            debug_lines.append(
                'z={:.0f}mm: FULL CIRCLE found, radius={:.0f}mm'.format(
                    internal_to_mm(z), internal_to_mm(radius)))
            if best is None or z < best[0]:
                best = (z, center, radius)

    if best is None:
        return None, None, debug_lines
    return best[1], best[2], debug_lines


def find_top_face(solids):
    """Return (outer_loop, z) of the HIGHEST horizontal planar face found
    among all given solids, or (None, None) if none exists. This is expected
    to be the flat top of a pier cap/crosshead (any shape - not required to
    be circular, unlike find_bottom_circle)."""
    best = None
    for solid in solids:
        for face in solid.Faces:
            if not isinstance(face, PlanarFace):
                continue
            normal = face.FaceNormal
            if abs(normal.Z) < 0.99:
                continue
            edge_loops = face.EdgeLoops
            if edge_loops.Size < 1:
                continue
            z = face.Origin.Z
            if best is None or z > best[1]:
                best = (edge_loops[0], z)
    if best is None:
        return None, None
    return best


def determine_long_axis(top_loop):
    """Best-effort long-axis direction (horizontal unit XYZ) of the top face
    boundary loop, plus a short string saying how it was determined.
    Prefers a straight Line edge (a stadium/oval cap made of 2 straight sides
    + 2 end arcs); falls back to an Ellipse edge's major axis; falls back to
    global X with a note if neither is found."""
    if top_loop is None:
        return XYZ.BasisX, 'no-top-face-found'
    for edge in top_loop:
        curve = edge.AsCurve()
        if isinstance(curve, Line):
            direction = curve.Direction
            direction = XYZ(direction.X, direction.Y, 0.0)
            if direction.GetLength() > 1e-6:
                return direction.Normalize(), 'line-edge'
    for edge in top_loop:
        curve = edge.AsCurve()
        if isinstance(curve, Ellipse):
            major_dir = curve.XDirection if curve.RadiusX >= curve.RadiusY else curve.YDirection
            major_dir = XYZ(major_dir.X, major_dir.Y, 0.0)
            if major_dir.GetLength() > 1e-6:
                return major_dir.Normalize(), 'ellipse-edge'
    return XYZ.BasisX, 'fallback-global-X'


def get_half_extent_ft(element):
    """A generous horizontal distance (feet) guaranteed to reach past the
    element's outer edge from its center, for building the radial cutting
    box. Falls back to a fixed 5m if no bounding box is available."""
    bbox = element.get_BoundingBox(None)
    if bbox is None:
        return mm_to_internal(5000.0)
    dx = bbox.Max.X - bbox.Min.X
    dy = bbox.Max.Y - bbox.Min.Y
    return max(dx, dy) * 1.5 + mm_to_internal(500.0)


def height_stations(base_z, top_z, count=30):
    """`count` evenly spaced Z heights (feet) from base_z to top_z inclusive.
    A horizontal slice is cut at each one and shared by every bar, so this
    count controls both smoothness and how many Booleans the tool runs."""
    total = top_z - base_z
    if total <= 1e-9:
        return [base_z]
    n = max(6, count)
    return [base_z + total * i / float(n - 1) for i in range(n)]


def cut_horizontal_slice(solid, z, center_xy, half_extent):
    """Cut `solid` with a thin horizontal slab centered at height z. Returns
    (loop_curves, None) - the outer boundary loop of the resulting
    cross-section, in order - or (None, reason_string). A horizontal cut of
    a vertical-ish pier is always a single simple closed 2D loop, which is
    far more robust than cutting with a vertical plane through a curved
    shaft-to-cap blend surface (v1.1.0's approach)."""
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
    """2D ray cast from center_xy along dir_h against the closed polyline
    `points` (world XYZ, cyclic - point[n-1] connects back to point[0]).
    Returns the distance (feet) from center_xy to the farthest boundary
    crossing along +dir_h, or None if the ray does not cross the loop."""
    perp = XYZ(-dir_h.Y, dir_h.X, 0.0)

    def local(pt):
        rel = XYZ(pt.X - center_xy.X, pt.Y - center_xy.Y, 0.0)
        return rel.DotProduct(dir_h), rel.DotProduct(perp)

    n = len(points)
    best_u = None
    for i in range(n):
        u0, v0 = local(points[i])
        u1, v1 = local(points[(i + 1) % n])
        if v0 * v1 > 0:
            continue  # both on the same side of the ray - no crossing
        if abs(v0 - v1) < 1e-12:
            continue
        frac = v0 / (v0 - v1)
        u_cross = u0 + frac * (u1 - u0)
        if u_cross <= 1e-6:
            continue  # crossing behind the ray origin - wrong half
        if best_u is None or u_cross > best_u:
            best_u = u_cross
    return best_u


def build_profile_bars_all(solids, center_xy, base_z, top_z, directions,
                            half_extent, offset_dist, stations=30):
    """Compute the profile-following 3D points for EVERY bar direction at
    once, sharing the same set of horizontal slices (one Boolean per height
    PER SOLID, not per bar). Returns (per_bar_points, slice_errors) where
    per_bar_points[i] is the (possibly short/empty) ordered point list for
    directions[i], base to top.

    Cuts each of `solids` SEPARATELY at each height rather than requiring a
    single merged solid - v1.2.0 tried Boolean-UNIONing all solids first,
    but Revit's Boolean Union commonly fails on solids that only TOUCH
    without volumetric overlap (e.g. a shaft's top face exactly coincident
    with the transition solid's bottom face) - a very likely case for a
    family built as separate sketched solids, and NBT confirmed that even
    using Revit's own Join Geometry + reload did not fix it (Join Geometry
    is a display/cleanup relationship between elements, not a merge of the
    underlying Solid objects the API reads back - it doesn't change what
    get_Geometry() returns at all). The Union call failing silently inside a
    try/except would reproduce the exact "only the shaft, unchanged" bug
    all over again with no visible error. Cutting each solid independently
    and, for each bar angle, taking the FARTHEST valid ray hit across all of
    them sidesteps Boolean Union entirely - solids that are merely stacked
    and touching each contribute their own real material at their own
    height range, so no merge is needed."""
    per_bar_points = [[] for _ in directions]
    slice_errors = []
    for z in height_stations(base_z, top_z, stations):
        point_sets = []
        for solid in solids:
            curves, _err = cut_horizontal_slice(solid, z, center_xy, half_extent)
            if curves is None:
                continue  # this solid simply has no material at this height - normal
            points = tessellate_curves(curves)
            if len(points) >= 3:
                point_sets.append(points)
        if not point_sets:
            slice_errors.append(
                'z={:.0f}mm: no material found in any solid at this height'.format(internal_to_mm(z)))
            continue
        for i, direction in enumerate(directions):
            best_u = None
            for points in point_sets:
                u = ray_boundary_hit(points, center_xy, direction)
                if u is not None and (best_u is None or u > best_u):
                    best_u = u
            if best_u is None:
                continue
            r = best_u - offset_dist
            if r <= 0:
                continue
            per_bar_points[i].append(
                XYZ(center_xy.X + direction.X * r, center_xy.Y + direction.Y * r, z))
    return per_bar_points, slice_errors


def tessellate_curves(curves):
    """Flatten a connected chain of Curves into an ordered list of XYZ
    points, de-duplicating shared endpoints between consecutive curves."""
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
    """Collapse consecutive (near-)collinear points into a single straight
    run, keeping only points where the path actually bends by more than
    `angle_tol_deg`. Added in v1.3.6: a plain, untapered round column has
    the exact same radius at every height station, so ALL ~30 points
    build_profile_bars_all computes for a bar sit on one straight vertical
    line - passed straight to Rebar.CreateFromCurves, that is ~29 tiny
    segments with ~0-degree "bends" at every internal point. Revit's
    Standard rebar shape engine matches curve chains against a library of
    real bend patterns; that many degenerate non-bends does not match
    anything, so the API call did not throw but also created no visible
    bar geometry (NBT's first genuinely-valid profile-mode test: "Created
    8 bars" / 0 errors / 0.0000 deg deviation in the popup, yet nothing
    but the reference lines actually showed up in the model). Simplifying
    first means a plain shaft collapses back to one straight segment
    (identical to a Phase 1 bar), while a real crosshead-following bar
    keeps its genuine bends and only loses the redundant collinear points
    in between. Only used for the curves handed to Rebar.CreateFromCurves
    - the raw, unsimplified per-station points are still what the popup's
    diagnostic counts and angle-deviation check report."""
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


def get_bar_model_diameter_ft(bar_type):
    """Modeled bar diameter in feet, with fallbacks across Revit API versions.
    Returns 0.0 (no diameter inset applied) if nothing could be read."""
    for attr in ('BarModelDiameter', 'BarNominalDiameter', 'BarDiameter'):
        try:
            value = getattr(bar_type, attr)
            if value and value > 0:
                return value
        except Exception:
            pass
    try:
        param = bar_type.get_Parameter(__import__('Autodesk.Revit.DB', fromlist=['BuiltInParameter']).BuiltInParameter.REBAR_BAR_DIAMETER)
        if param is not None:
            return param.AsDouble()
    except Exception:
        pass
    return 0.0


def get_rebar_bar_types(document):
    """Return a list of (display_name, RebarBarType) tuples, name-sorted."""
    types = list(FilteredElementCollector(document).OfClass(RebarBarType).ToElements())
    named = []
    for bt in types:
        try:
            name = Element.Name.GetValue(bt)
        except Exception:
            name = 'Bar Type {}'.format(bt.Id.IntegerValue)
        named.append((name, bt))
    named.sort(key=lambda pair: pair[0])
    return named


def bar_angles(count):
    """Evenly-spaced angles (radians) and their horizontal direction unit
    vectors around the circle - the single shared layout used by both the
    simple straight mode and the profile-following mode, so bar N is always
    at the same position in either mode."""
    result = []
    for i in range(count):
        angle = 2.0 * math.pi * i / count
        result.append(XYZ(math.cos(angle), math.sin(angle), 0.0))
    return result


def build_bar_lines(center, radius_ft, directions, cover_ft, bar_radius_ft, length_ft):
    """Return a list of (curves, normal_XYZ) tuples, one per bar - simple
    Phase 1 mode: a single straight vertical Line per bar."""
    placement_radius = radius_ft - cover_ft - bar_radius_ft
    result = []
    for direction in directions:
        base_pt = center + direction.Multiply(placement_radius)
        top_pt = XYZ(base_pt.X, base_pt.Y, base_pt.Z + length_ft)
        line = Line.CreateBound(base_pt, top_pt)
        result.append(([line], direction))
    return result


def create_center_reference_lines(document, center, base_z, top_z, directions, half_extent):
    """Create one real, editable Model Line per bar direction - THE ACTUAL
    CUTTING PLANE used for that bar, drawn as a visible line, built BEFORE
    the bar shape is reasoned about as anything else. Each line runs from
    the true center at the base (r=0, z=base_z) out to the far edge at the
    crosshead top (r=half_extent, z=top_z), so it lies entirely inside the
    exact same vertical plane (normal_h - the same normal passed to
    Rebar.CreateFromCurves for that bar) that the bar's own curve is cut
    from. This is deliberately built the same way NBT draws it by hand: a
    vertical, through-center, through-this-bar's-angle section line, first
    - the bar's profile is then read off this plane, not the other way
    around, so there is exactly one plane per bar and no room for the two
    to disagree. In Top View this line's horizontal projection is a
    straight spoke through the true center (every point on it is
    center + direction * r for some r, by construction); in a 3D/isometric
    view it appears as a rising diagonal, matching the reference lines NBT
    already sketches by hand. Unlike the Rebar's own Edit Sketch view (whose
    dashed boundary Revit crops tightly around the bar's own curve, which
    sits near the outer surface and never visually reaches the center),
    this line starts AT the center and is a normal, independent Model Line
    NBT can select, snap to, edit, or delete freely.
    Returns (created_count, list_of_error_strings)."""
    created = 0
    errors = []
    span_z = top_z if (top_z is not None and top_z > base_z) else base_z
    for idx, direction in enumerate(directions):
        try:
            normal_h = XYZ(-direction.Y, direction.X, 0.0)
            plane = Plane.CreateByNormalAndOrigin(normal_h, XYZ(center.X, center.Y, base_z))
            sketch_plane = SketchPlane.Create(document, plane)
            start_pt = XYZ(center.X, center.Y, base_z)
            end_pt = XYZ(
                center.X + direction.X * half_extent,
                center.Y + direction.Y * half_extent,
                span_z)
            line = Line.CreateBound(start_pt, end_pt)
            document.Create.NewModelCurve(line, sketch_plane)
            created += 1
        except Exception as ex:
            errors.append('Reference line {}: {}'.format(idx + 1, str(ex)))
    return created, errors


def create_radial_bars(document, host, bar_type, bars):
    """Create one Rebar per (curves, normal) pair, hosted to host - curves is
    a list of 1+ connected Curves (a single Line for a straight bar, or a
    multi-segment chain for a profile-following bar). Each bar is created
    inside its own try/except so one failure does not stop the rest.
    Returns (created_count, list_of_error_strings)."""
    created = 0
    errors = []
    for idx, (curves, normal) in enumerate(bars):
        try:
            curve_list = List[Curve]()
            for curve in curves:
                curve_list.Add(curve)
            Rebar.CreateFromCurves(
                document,
                RebarStyle.Standard,
                bar_type,
                None,
                None,
                host,
                normal,
                curve_list,
                RebarHookOrientation.Left,
                RebarHookOrientation.Left,
                False,
                True,
            )
            created += 1
        except Exception as ex:
            errors.append('Bar {}: {}'.format(idx + 1, str(ex)))
    return created, errors


# ---------------------------------------------------------------------------
# Selection helper
# ---------------------------------------------------------------------------

def get_host_element():
    """Return the pre-selected element, or prompt the user to pick one.
    Returns None if there is no usable single-element selection (and the
    user is told why)."""
    selected_ids = list(uidoc.Selection.GetElementIds())
    if len(selected_ids) == 1:
        return doc.GetElement(selected_ids[0])
    if len(selected_ids) > 1:
        forms.alert(
            'Please select exactly one element (you have {} selected).'.format(len(selected_ids)),
            title=TOOL_NAME,
        )
        return None
    try:
        ref = uidoc.Selection.PickObject(ObjectType.Element, 'Select the circular column/pier element')
        return doc.GetElement(ref.ElementId)
    except RevitExceptions.OperationCanceledException:
        return None


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _row(grid, height='auto'):
    """Add a RowDefinition to `grid`. BUG FIXED in v1.3.5: leaving
    rd.Height unset does NOT make a row size-to-content in WPF - the real
    default is 1-star (equal share of remaining space), so with 3 calls
    like _row(outer) for header/content/footer, all three silently got an
    equal 1/3 slice of the fixed Window.Height regardless of how much
    content each one actually held. That was invisible while the dialog
    only had 3 short fields (v1.0.0-era), but every field added since
    (Bar length, the profile checkbox, the version line, the status text)
    made the middle row's real content taller than its forced 1/3 share -
    Revit/WPF does not clip a Grid row's overflow, so the extra controls
    were rendered PAST the row boundary and ended up overlapping/hidden
    behind the footer's opaque background. NBT reported not seeing the
    checkbox at all even on a confirmed-current build - this sizing bug,
    not stale code, was the real cause. Fix: 'auto' now means an actual
    GridUnitType.Auto row (sized to its content), and 'star' means an
    explicit 1-star row (fills whatever space Auto rows do not claim)."""
    from System.Windows import GridLength, GridUnitType
    rd = RowDefinition()
    if height == 'auto':
        rd.Height = GridLength(1, GridUnitType.Auto)
    elif height == 'star':
        rd.Height = GridLength(1, GridUnitType.Star)
    else:
        rd.Height = GridLength(float(height), GridUnitType.Pixel)
    grid.RowDefinitions.Add(rd)


def _labeled_row(parent_panel, label_text):
    row = StackPanel()
    row.Orientation = Orientation.Horizontal
    row.Margin = Thickness(0, 6, 0, 6)
    label = TextBlock()
    label.Text = label_text
    label.Width = 150
    label.VerticalAlignment = System_VerticalAlignment_Center
    label.Foreground = theme.brush(theme.CLR_TEXT)
    row.Children.Add(label)
    parent_panel.Children.Add(row)
    return row


# VerticalAlignment.Center needs the enum, imported narrowly to avoid clutter above
from System.Windows import VerticalAlignment as _VA
System_VerticalAlignment_Center = _VA.Center


class CircularRebarArrayWindow(Window):
    def __init__(self, host, center, radius_ft, bar_types,
                 pier_solids=None, top_z=None, axis_dir=None, axis_source=None,
                 half_extent=None):
        self.host = host
        self.center = center
        self.radius_ft = radius_ft
        self.bar_types = bar_types  # list of (name, RebarBarType)
        # Phase 3 (experimental profile-following mode) inputs:
        self.pier_solids = pier_solids or []
        self.top_z = top_z
        self.axis_dir = axis_dir
        self.axis_source = axis_source
        self.half_extent = half_extent
        # Diagnostic only (shown in the result message so a real modeling
        # gap between solids - vs. a code bug - is visible without needing
        # a separate hand-drawn illustration to track down): each solid's
        # own Z range.
        ranges = []
        for s in self.pier_solids:
            zmin, zmax = solid_z_range(s)
            if zmin is not None:
                ranges.append('{:.0f}-{:.0f}mm'.format(internal_to_mm(zmin), internal_to_mm(zmax)))
        self.solid_ranges_text = ', '.join(ranges) if ranges else 'n/a'

        self.Title = TOOL_NAME
        self.Width = 380
        self.Height = 540
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.ResizeMode = ResizeMode.NoResize
        self.Background = theme.brush(theme.CLR_BG)

        outer = Grid()
        _row(outer)          # header - Auto: exactly as tall as its own text
        _row(outer, 'star')  # content - Star: gets all space Auto rows don't need
        _row(outer)          # footer - Auto: exactly as tall as the buttons
        self.Content = outer

        # --- Header --------------------------------------------------
        header = Border()
        header.Background = theme.brush(theme.CLR_HEADER)
        header.Padding = Thickness(14, 10, 14, 10)
        header_panel = StackPanel()
        title_tb = TextBlock()
        title_tb.Text = TOOL_NAME
        title_tb.FontWeight = FontWeights.Bold
        title_tb.FontSize = 15
        title_tb.Foreground = theme.brush(theme.CLR_HEADER_TEXT)
        sub_tb = TextBlock()
        sub_tb.Text = 'Detected radius: {:.0f} mm'.format(internal_to_mm(radius_ft))
        sub_tb.FontSize = 11
        sub_tb.Foreground = theme.brush(theme.CLR_HEADER_SUB)
        sub_tb.Margin = Thickness(0, 2, 0, 0)
        # Tool version, always visible the moment the window opens - so NBT
        # can confirm from a single screenshot whether Revit actually loaded
        # the latest script, instead of having to count dialog fields or
        # guess from behavior (pyRevit's rocketmode/bincache has repeatedly
        # kept an old compiled copy running even after the script file on
        # disk was updated - see project doc tools/rebar-radial-array.md).
        version_tb = TextBlock()
        version_tb.Text = 'Tool version: {}'.format(TOOL_VERSION)
        version_tb.FontSize = 10
        version_tb.Foreground = theme.brush(theme.CLR_HEADER_SUB)
        version_tb.Margin = Thickness(0, 1, 0, 0)
        header_panel.Children.Add(title_tb)
        header_panel.Children.Add(sub_tb)
        header_panel.Children.Add(version_tb)
        header.Child = header_panel
        Grid.SetRow(header, 0)
        outer.Children.Add(header)

        # --- Content ---------------------------------------------------
        content = Border()
        content.Background = theme.brush(theme.CLR_CARD)
        content.Padding = Thickness(16)
        content_panel = StackPanel()
        content.Child = content_panel
        Grid.SetRow(content, 1)
        outer.Children.Add(content)

        _labeled_row(content_panel, 'Bar type')
        self.combo_bar_type = ComboBox()
        self.combo_bar_type.ItemsSource = [name for name, _bt in bar_types]
        if bar_types:
            self.combo_bar_type.SelectedIndex = 0
        self.combo_bar_type.Margin = Thickness(0, 0, 0, 12)
        content_panel.Children.Add(self.combo_bar_type)

        self.txt_count = self._add_field(content_panel, 'Number of bars', '8')
        self.txt_cover = self._add_field(content_panel, 'Cover (mm)', '40')
        self.txt_length = self._add_field(content_panel, 'Bar length (mm)', '3000')

        self.chk_profile = CheckBox()
        self.chk_profile.Content = 'Follow crosshead profile above shaft (experimental)'
        self.chk_profile.Margin = Thickness(0, 6, 0, 0)
        self.chk_profile.Foreground = theme.brush(theme.CLR_TEXT)
        if self.top_z is None:
            self.chk_profile.IsEnabled = False
            self.chk_profile.Content = (
                'Follow crosshead profile (disabled - no upper face detected)')
        content_panel.Children.Add(self.chk_profile)

        self.status_tb = TextBlock()
        self.status_tb.Text = ''
        self.status_tb.TextWrapping = TextWrapping.Wrap
        self.status_tb.Foreground = theme.brush(theme.CLR_MUTED)
        self.status_tb.Margin = Thickness(0, 8, 0, 0)
        content_panel.Children.Add(self.status_tb)

        # --- Footer ------------------------------------------------------
        footer = Border()
        footer.Background = theme.brush(theme.CLR_FOOTER)
        footer.Padding = Thickness(14, 10, 14, 10)
        footer_panel = StackPanel()
        footer_panel.Orientation = Orientation.Horizontal
        footer_panel.HorizontalAlignment = HorizontalAlignment.Right

        btn_cancel = Button()
        btn_cancel.Content = 'Cancel'
        btn_cancel.Padding = Thickness(14, 6, 14, 6)
        btn_cancel.Margin = Thickness(0, 0, 8, 0)
        btn_cancel.Background = theme.brush(theme.CLR_CARD)
        btn_cancel.Foreground = theme.brush(theme.CLR_TEXT)
        btn_cancel.Click += self.on_cancel
        footer_panel.Children.Add(btn_cancel)

        btn_create = Button()
        btn_create.Content = 'Create'
        btn_create.Padding = Thickness(18, 6, 18, 6)
        btn_create.Background = theme.brush(theme.CLR_APPLY)
        btn_create.Foreground = theme.brush(theme.CLR_APPLY_TEXT)
        btn_create.FontWeight = FontWeights.Bold
        btn_create.Click += self.on_create
        footer_panel.Children.Add(btn_create)

        footer.Child = footer_panel
        Grid.SetRow(footer, 2)
        outer.Children.Add(footer)

    def _add_field(self, panel, label_text, default_value):
        row = _labeled_row(panel, label_text)
        box = TextBox()
        box.Text = default_value
        box.Width = 160
        row.Children.Add(box)
        return box

    def on_cancel(self, sender, args):
        self.DialogResult = False
        self.Close()

    def on_create(self, sender, args):
        try:
            count = int(self.txt_count.Text.strip())
            cover_mm = float(self.txt_cover.Text.strip())
            length_mm = float(self.txt_length.Text.strip())
        except Exception:
            self.status_tb.Text = 'Number of bars / cover / length must be valid numbers.'
            self.status_tb.Foreground = theme.brush(theme.CLR_ERROR)
            return

        if count < 2:
            self.status_tb.Text = 'Number of bars must be at least 2.'
            self.status_tb.Foreground = theme.brush(theme.CLR_ERROR)
            return

        if self.combo_bar_type.SelectedIndex < 0:
            self.status_tb.Text = 'Please select a bar type.'
            self.status_tb.Foreground = theme.brush(theme.CLR_ERROR)
            return

        bar_type = self.bar_types[self.combo_bar_type.SelectedIndex][1]
        bar_diameter_ft = get_bar_model_diameter_ft(bar_type)
        bar_radius_ft = bar_diameter_ft / 2.0

        cover_ft = mm_to_internal(cover_mm)
        length_ft = mm_to_internal(length_mm)

        placement_radius_ft = self.radius_ft - cover_ft - bar_radius_ft
        if placement_radius_ft <= 0:
            self.status_tb.Text = (
                'Cover + bar diameter is larger than the detected radius '
                '({:.0f} mm) - reduce cover or check the bar type.'.format(
                    internal_to_mm(self.radius_ft)))
            self.status_tb.Foreground = theme.brush(theme.CLR_ERROR)
            return

        directions = bar_angles(count)
        profile_mode = bool(self.chk_profile.IsChecked) and self.top_z is not None
        profile_errors = []

        max_center_dev_deg = 0.0
        if profile_mode:
            offset_dist = cover_ft + bar_radius_ft
            per_bar_points, slice_errors = build_profile_bars_all(
                self.pier_solids, self.center, self.center.Z, self.top_z,
                directions, self.half_extent, offset_dist
            )
            profile_errors.extend(slice_errors[:6])
            # Numeric proof every bar is exactly radial from the true center.
            # Revit's own "Edit Sketch" view draws the dashed work-plane
            # cropped tightly around the bar's own curve, so it can visually
            # look like it stops short of the center even though the
            # underlying plane (and every point on the bar) is still exactly
            # on the line through center - this computes the real angular
            # deviation as a number instead of relying on that cropped view.
            for idx, direction in enumerate(directions):
                pts = per_bar_points[idx]
                if not pts:
                    continue
                expected_deg = math.degrees(math.atan2(direction.Y, direction.X))
                for p in (pts[0], pts[-1]):
                    dx, dy = p.X - self.center.X, p.Y - self.center.Y
                    if dx * dx + dy * dy < 1e-12:
                        continue
                    actual_deg = math.degrees(math.atan2(dy, dx))
                    dev = abs((actual_deg - expected_deg + 180.0) % 360.0 - 180.0)
                    if dev > max_center_dev_deg:
                        max_center_dev_deg = dev
            bars = []
            for idx, direction in enumerate(directions):
                normal_h = XYZ(-direction.Y, direction.X, 0.0)
                # v1.3.6: collapse (near-)collinear points before building
                # curves - see simplify_collinear_points() docstring. The
                # raw per_bar_points (used above for the angle-deviation
                # check and below for the popup's point-count diagnostics)
                # are left untouched.
                simplified_pts = simplify_collinear_points(per_bar_points[idx])
                lines = points_to_lines(simplified_pts)
                if not lines:
                    profile_errors.append('Bar {}: no valid profile points found'.format(idx + 1))
                    continue
                bars.append((lines, normal_h))
        else:
            bars = build_bar_lines(
                self.center, self.radius_ft, directions, cover_ft, bar_radius_ft, length_ft
            )

        if not bars:
            detail = '\n'.join(profile_errors[:8]) if profile_errors else ''
            self.status_tb.Text = 'No bar shapes could be computed.\n{}'.format(detail)
            self.status_tb.Foreground = theme.brush(theme.CLR_ERROR)
            return

        t = Transaction(doc, 'pyNBT - {}'.format(TOOL_NAME))
        t.Start()
        try:
            created, errors = create_radial_bars(doc, self.host, bar_type, bars)
            ref_lines_created = 0
            if profile_mode:
                ref_lines_created, ref_line_errors = create_center_reference_lines(
                    doc, self.center, self.center.Z, self.top_z, directions, self.half_extent)
                errors = errors + ref_line_errors
            t.Commit()
        except Exception as ex:
            if t.HasStarted():
                t.RollBack()
            self.status_tb.Text = 'Failed: {}'.format(str(ex))
            self.status_tb.Foreground = theme.brush(theme.CLR_ERROR)
            return

        all_errors = profile_errors + errors
        diameter_note = ''
        if bar_diameter_ft == 0.0:
            diameter_note = ' (bar diameter could not be read from the bar type - only cover was applied)'
        mode_note = ''
        if profile_mode:
            counts = [len(pts) for pts in per_bar_points]
            stations_total = len(height_stations(self.center.Z, self.top_z))
            pts_summary = '{}/{:.0f}/{}'.format(
                min(counts) if counts else 0,
                (sum(counts) / float(len(counts))) if counts else 0,
                max(counts) if counts else 0,
            )
            mode_note = (
                ' (profile mode, long axis from {}; true center (mm): X={:.0f}, Y={:.0f}; '
                'solids Z-range: {}; height stations: {}; points per bar min/avg/max: {}; '
                'max angle deviation from true center across all bars: {:.4f} deg '
                '(should read ~0.0000); {} center-reference lines added - select/edit/delete '
                'them freely, each runs from the true center along its bar\'s exact angle)'
            ).format(
                self.axis_source, internal_to_mm(self.center.X), internal_to_mm(self.center.Y),
                self.solid_ranges_text, stations_total, pts_summary, max_center_dev_deg,
                ref_lines_created,
            )

        if all_errors:
            msg = 'Created {} of {} bars.{}{}\n\nErrors:\n{}\n\n[{}]'.format(
                created, count, diameter_note, mode_note, '\n'.join(all_errors), TOOL_VERSION)
        else:
            msg = 'Created {} bars around the circle.{}{}\n\n[{}]'.format(
                created, diameter_note, mode_note, TOOL_VERSION)

        self.DialogResult = True
        self.Close()
        forms.alert(msg, title=TOOL_NAME)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    host = get_host_element()
    if host is None:
        return

    center, radius_ft, debug_lines = find_bottom_circle(host)
    if center is None:
        detail = '\n'.join(debug_lines[:8]) if debug_lines else '(no horizontal planar face found at all)'
        forms.alert(
            'Could not find a horizontal, full-circle face on the selected '
            'element. This Phase-1 tool only supports a straight cylindrical '
            'shaft (a tapered/oval pier cap is not supported yet).\n\n'
            'Faces examined:\n{}'.format(detail),
            title=TOOL_NAME,
        )
        return

    bar_types = get_rebar_bar_types(doc)
    if not bar_types:
        forms.alert('No Rebar Bar Types found in this project.', title=TOOL_NAME)
        return

    # Phase 3 (experimental): gather what's needed for profile-following mode.
    # Failure here never blocks Phase 1 - the checkbox just stays disabled.
    solids = get_element_solids(host)
    top_loop, top_z = find_top_face(solids)
    axis_dir, axis_source = determine_long_axis(top_loop)
    half_extent = get_half_extent_ft(host)
    if top_z is not None and top_z <= center.Z + mm_to_internal(50.0):
        # "top" face found is not meaningfully above the base - e.g. a plain
        # cylinder with no crosshead above it - treat as no upper geometry.
        top_z = None

    window = CircularRebarArrayWindow(
        host, center, radius_ft, bar_types,
        pier_solids=solids, top_z=top_z, axis_dir=axis_dir,
        axis_source=axis_source, half_extent=half_extent,
    )
    window.ShowDialog()


try:
    main()
except Exception:
    forms.alert('Unexpected error:\n{}'.format(traceback.format_exc()), title=TOOL_NAME)
