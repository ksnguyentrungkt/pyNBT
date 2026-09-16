# -*- coding: utf-8 -*-
"""
Rebar Segment - standalone Revit-API logic (no UI references here).

Draping "top layer" rebar across a warped/twisted host face (e.g. a ramp
transition slab) that a normal flat-plane rebar layout cannot follow.

Design (locked with NBT, see project doc "rebar-segment-warped-face-tool.md"):

  - NBT picks 1 host TOP FACE, then 4 corner points on it: A, b, c, d.
    Bar #1 runs A->b. The LAST bar runs c->d. Rail 1 = A->c, Rail 2 = b->d.
    Bars in between are distributed along these 2 rails.
  - Spacing is measured along the REAL surface distance on the face (not
    the straight chord) -- see build_rail_samples/point_at_arc_length.
  - V1 ASSUMES the host face is (approximately) a ruled surface in the
    A->c / b->d direction, so a straight line between the corresponding
    point on rail 1 and rail 2 hugs the real surface closely. Every
    intermediate rail point is re-projected onto the face (Face.Project)
    to snap it exactly onto the surface, which is exact when the
    assumption holds and a good approximation otherwise. NBT explicitly
    accepted starting with this simpler approach and upgrading later
    (e.g. to Revit's Free Form Rebar API) only if real testing shows the
    straight bars deviate too much from the host face.
  - Each bar is still a single straight Curve in V1 -- NO hook is
    auto-generated (NBT: "de thanh thang da, can se fix sau hoac tu fix
    bang sket" -- he will add hooks himself via Edit Sketch later, or a
    future version will automate it). The plane ("norm") passed to
    Rebar.CreateFromCurves is still computed PER BAR from that bar's own
    local direction + the host face's local normal at its midpoint (NOT
    one shared plane for the whole layout) -- this is deliberate so that
    if/when NBT adds a hook by hand later, the sketch plane Revit shows
    is already the correct local orientation instead of a flat, wrong one.
  - The first bar (A->b) and the last bar (c->d) sit right at the edge of
    the region, next to a vertical/side face of the host -- NBT requires
    those 2 bars to keep cover in BOTH directions (top face AND the
    adjacent side face), not just the top face like the middle bars. See
    apply_corner_offset.

Keep this file free of WPF/System.Windows imports (pyNBT DQT-pattern:
logic functions separate from the UI class).
"""

import math

from Autodesk.Revit.DB import (
    XYZ, Line, Element, BuiltInParameter, UnitUtils,
    FilteredElementCollector, BuiltInCategory, DirectShape, ElementId,
    OverrideGraphicSettings, Color,
)
from Autodesk.Revit.DB.Structure import Rebar, RebarBarType, RebarStyle, RebarHookOrientation

try:
    from Autodesk.Revit.DB import UnitTypeId

    def ft_to_mm(value_ft):
        return UnitUtils.ConvertFromInternalUnits(value_ft, UnitTypeId.Millimeters)

    def mm_to_ft(value_mm):
        return UnitUtils.ConvertToInternalUnits(value_mm, UnitTypeId.Millimeters)
except ImportError:
    # Older Revit API (pre-2021) used DisplayUnitType instead of UnitTypeId.
    from Autodesk.Revit.DB import DisplayUnitType

    def ft_to_mm(value_ft):
        return UnitUtils.ConvertFromInternalUnits(value_ft, DisplayUnitType.DUT_MILLIMETERS)

    def mm_to_ft(value_mm):
        return UnitUtils.ConvertToInternalUnits(value_mm, DisplayUnitType.DUT_MILLIMETERS)


# ---------------------------------------------------------------------------
# ElementId compat helpers
# ---------------------------------------------------------------------------

def eid_int(element_id):
    """Integer value of an ElementId, safe across Revit versions."""
    if element_id is None:
        return -1
    val = getattr(element_id, "Value", None)
    if val is None:
        val = getattr(element_id, "IntegerValue", None)
    return int(val) if val is not None else -1


def is_valid_eid(element_id):
    return eid_int(element_id) != -1


# ---------------------------------------------------------------------------
# Safe ElementType name read (IronPython .Name-on-ElementType bug -- see
# pyNBT doc "pynbt-elementtype-name-read-bug.md").
# ---------------------------------------------------------------------------

def get_element_name(el):
    try:
        return Element.Name.GetValue(el)
    except Exception:
        try:
            return el.Name
        except Exception:
            try:
                p = el.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
                return p.AsString() if p is not None else "(unnamed)"
            except Exception:
                return "(unnamed)"


# ---------------------------------------------------------------------------
# RebarBarType list (for the UI dropdown)
# ---------------------------------------------------------------------------

def get_bar_types(doc):
    types = list(FilteredElementCollector(doc).OfClass(RebarBarType).ToElements())
    types.sort(key=lambda t: get_element_name(t))
    return types


# ---------------------------------------------------------------------------
# Partition auto-numbering -- NBT: tool must auto-assign a Partition value
# to the whole batch of bars created in one run, guaranteed not to collide
# with any Partition value already used anywhere in the model.
#
# Looked up BY NAME (LookupParameter), not the built-in
# BuiltInParameter.ELEM_PARTITION_PARAM -- confirmed elsewhere in this
# project (Select Partition, Rebar Shape Export) that on Wohhup templates
# "Partition" shown in Properties is a project/shared parameter with that
# display name, not Revit's built-in worksharing parameter of the same
# name -- using the built-in enum silently reads the wrong (usually empty)
# parameter.
# ---------------------------------------------------------------------------

PARTITION_PARAM_NAME = "Partition"
PARTITION_PREFIX = "RS"

REBAR_PARTITION_CATEGORIES = [
    BuiltInCategory.OST_Rebar,
    BuiltInCategory.OST_AreaRein,
    BuiltInCategory.OST_PathRein,
    BuiltInCategory.OST_FabricReinforcement,
]


def _read_partition_value(element):
    try:
        param = element.LookupParameter(PARTITION_PARAM_NAME)
        if param is None:
            param = element.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
        if param is None or not param.HasValue:
            return None
        val = param.AsValueString()
        if not val:
            val = param.AsString()
        return val
    except Exception:
        return None


def get_existing_partition_values(doc):
    """Every distinct Partition value currently used on any rebar-family
    element in the model (Rebar / AreaReinforcement / PathReinforcement /
    FabricReinforcement)."""
    values = set()
    for bic in REBAR_PARTITION_CATEGORIES:
        try:
            collector = FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType()
        except Exception:
            continue
        for el in collector:
            val = _read_partition_value(el)
            if val:
                values.add(val)
    return values


def generate_unique_partition(doc):
    """A new "RS-NN" Partition value guaranteed not to collide with any
    value already in the model."""
    existing = get_existing_partition_values(doc)
    n = 1
    while True:
        candidate = "{}-{:02d}".format(PARTITION_PREFIX, n)
        if candidate not in existing:
            return candidate
        n += 1


def set_partition(element, value):
    try:
        param = element.LookupParameter(PARTITION_PARAM_NAME)
        if param is None:
            param = element.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
        if param is not None and not param.IsReadOnly:
            param.Set(value)
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Face geometry helpers -- work identically for a planar OR a curved/warped
# Face (RuledFace, HermiteFace, ...): Face.Project / Face.ComputeNormal are
# virtual on the base Face class, no per-type branching needed.
# ---------------------------------------------------------------------------

def project_point_to_face(face, point):
    """Snap `point` exactly onto `face`. Returns XYZ or None if the
    projection fails -- per the official Revit API docs, `Face.Project`
    returns null "if the nearest point is outside of this face", i.e. the
    point's nearest location on the face's underlying surface falls
    outside the face's TRIMMED boundary loop. This is a real, fairly
    common edge case here: NBT snaps the 4 corner picks onto existing
    Adaptive Points sitting right AT the face's corners/edges, which is
    exactly where trim-boundary tolerance issues bite."""
    try:
        result = face.Project(point)
        if result is None:
            return None
        return result.XYZPoint
    except Exception:
        return None


def project_point_to_face_tolerant(face, point, anchor_point, max_attempts=12):
    """Like `project_point_to_face`, but if the raw point fails to land on
    `face` (see that function's docstring -- typically a corner point that
    sits just outside the trimmed boundary), progressively nudge it a
    small step toward `anchor_point` -- a point ALREADY confirmed to be on
    this face (e.g. the `GlobalPoint` of the `Reference` returned by
    `PickObject(ObjectType.Face)`, which is guaranteed on-face since
    that's literally how the face was picked) -- and retry. A 1-12% nudge
    toward a known-interior point is enough to clear a boundary-tolerance
    miss without visibly moving the corner. Returns None if even a 12%
    nudge doesn't land on the face (a genuine mismatch, not just a
    boundary tolerance issue)."""
    result = project_point_to_face(face, point)
    if result is not None:
        return result
    if anchor_point is None:
        return None
    for i in range(1, max_attempts + 1):
        nudged = _lerp_xyz(point, anchor_point, i * 0.01)
        result = project_point_to_face(face, nudged)
        if result is not None:
            return result
    return None


def get_face_corner_vertices(face):
    """Read the face's own boundary corners directly from its edge loops,
    so the 4 pick points can snap onto REAL points that already sit on the
    face -- guaranteed valid -- instead of relying on a manual mouse click
    landing close enough for Face.Project's fragile trim-boundary
    tolerance (see project_point_to_face). This is the normal shape of a
    ramp/wall top face: 2 straight end edges (each becomes one bar's
    direction) + 2 long rail edges (straight or curved) -- exactly 4 edges,
    so the loop's 4 curve-start-points ARE the 4 corners NBT needs (A, b,
    c, d), no picking precision required at all.

    Returns an ordered list of 4 XYZ corner points for the face's outer
    boundary loop, or None if the face's boundary is not a simple 4-edge
    loop (an opening, a wall miter/join adding extra edges, multiple
    loops, etc.) -- callers should fall back to the manual
    project_point_to_face_tolerant approach in that case."""
    try:
        loops = list(face.GetEdgesAsCurveLoops())
    except Exception:
        return None
    if not loops:
        return None

    def _loop_perimeter(loop):
        total = 0.0
        try:
            for curve in loop:
                total += curve.Length
        except Exception:
            pass
        return total

    # The outer boundary is the longest loop; any hole (inner loop) is
    # always smaller than the boundary that contains it.
    outer_loop = max(loops, key=_loop_perimeter)

    curves = list(outer_loop)
    if len(curves) != 4:
        return None

    corners = []
    for curve in curves:
        try:
            corners.append(curve.GetEndPoint(0))
        except Exception:
            return None
    return corners


def pop_nearest_corner(point, corners):
    """Find the entry in the mutable list `corners` closest to `point`
    (straight-line 3D distance), remove it from the list, and return it.
    Returns None if `corners` is empty. Used so each of the 4 corner picks
    (A, b, c, d) consumes one of the face's real corner vertices -- NBT
    only needs to click roughly NEAR the corner he means; this snaps
    exactly onto it and prevents the same corner being used twice."""
    if not corners:
        return None
    best_i = 0
    best_dist = point.DistanceTo(corners[0])
    for i in range(1, len(corners)):
        d = point.DistanceTo(corners[i])
        if d < best_dist:
            best_dist = d
            best_i = i
    return corners.pop(best_i)


def create_corner_marker(doc, point, half_size_ft, color_rgb):
    """Create a small temporary DirectShape crosshair centered on `point`,
    colored with `color_rgb` (r, g, b each 0-255) so NBT can SEE exactly
    which point each of the 4 corner picks (A, b, c, d) actually landed on.
    Needed because v1.0.3 switched from "click precisely and hope
    Face.Project accepts it" to "click roughly near a corner and snap to
    the nearest real vertex" -- with no visual confirmation at all, a click
    could silently snap to the WRONG corner (e.g. an adjacent one) with
    nothing on screen to show for it. Must run inside an active
    Transaction. Returns the new DirectShape's ElementId (the caller is
    responsible for deleting it later via delete_elements_safe)."""
    lines = [
        Line.CreateBound(point - XYZ(half_size_ft, 0, 0), point + XYZ(half_size_ft, 0, 0)),
        Line.CreateBound(point - XYZ(0, half_size_ft, 0), point + XYZ(0, half_size_ft, 0)),
        Line.CreateBound(point - XYZ(0, 0, half_size_ft), point + XYZ(0, 0, half_size_ft)),
    ]
    ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_Lines))
    ds.SetShape(lines)
    r, g, b = color_rgb
    ogs = OverrideGraphicSettings()
    ogs.SetProjectionLineColor(Color(r, g, b))
    ogs.SetProjectionLineWeight(6)
    doc.ActiveView.SetElementOverrides(ds.Id, ogs)
    return ds.Id


def delete_elements_safe(doc, element_ids):
    """Delete every id in `element_ids` (used to clean up the temporary
    corner markers created by create_corner_marker). Ignores ids that no
    longer exist so a repeated cleanup call never raises. Must run inside
    an active Transaction."""
    for eid in element_ids:
        try:
            doc.Delete(eid)
        except Exception:
            pass


def face_normal_at_point(face, point, anchor_point=None):
    """Local outward unit normal of `face` at the point on it closest to
    `point`. Returns None on failure. `point` is often one of the tool's
    exact picked corner points (A/b/c/d), which can sit right on the
    face's trim boundary -- when `anchor_point` is given (a point already
    known to be on/near `face`), nudge toward it on a Face.Project failure
    the same way project_point_to_face_tolerant does, instead of giving up
    immediately (see that function's docstring for why Face.Project can
    reject a point exactly on the boundary)."""
    try:
        result = face.Project(point)
        if result is None and anchor_point is not None:
            for i in range(1, 13):
                nudged = _lerp_xyz(point, anchor_point, i * 0.01)
                result = face.Project(nudged)
                if result is not None:
                    break
        if result is None:
            return None
        uv = result.UVPoint
        normal = face.ComputeNormal(uv)
        return normal.Normalize()
    except Exception:
        return None


def offset_from_face(face, point, distance_ft, anchor_point=None):
    """Move `point` (assumed already at/near `face`) INWARD by
    `distance_ft` -- opposite the face's outward normal at that point.
    This is the cover offset. Returns the original point if the normal
    can't be read (fails safe rather than raising, so 1 bad face read
    doesn't abort the whole batch). See face_normal_at_point for
    `anchor_point`."""
    normal = face_normal_at_point(face, point, anchor_point)
    if normal is None:
        return point
    return XYZ(
        point.X - normal.X * distance_ft,
        point.Y - normal.Y * distance_ft,
        point.Z - normal.Z * distance_ft,
    )


def apply_corner_offset(point, top_face, side_face, top_cover_ft, side_cover_ft, anchor_point=None):
    """Offset `point` inward from the TOP face by `top_cover_ft`, and --
    only when `side_face` is given (first/last bar's endpoints) -- ALSO
    inward from that adjacent SIDE face by `side_cover_ft`, per NBT's
    requirement that the 2 edge bars keep cover in both directions.
    `anchor_point` (the bar's OTHER endpoint -- see build_rebar_segment_batch)
    is passed through to the trim-boundary nudge fallback for the exact
    corner points A/b/c/d (see face_normal_at_point)."""
    pt = offset_from_face(top_face, point, top_cover_ft, anchor_point)
    if side_face is not None:
        pt = offset_from_face(side_face, pt, side_cover_ft, anchor_point)
    return pt


def _lerp_xyz(p0, p1, t):
    return XYZ(
        p0.X + (p1.X - p0.X) * t,
        p0.Y + (p1.Y - p0.Y) * t,
        p0.Z + (p1.Z - p0.Z) * t,
    )


def build_rail_samples(face, p_start, p_end, n_segments=48):
    """Approximate the curve traced on `face` between `p_start` and
    `p_end` by linearly interpolating in 3D and re-projecting each sample
    onto the face (snapping it exactly onto the surface), then walking the
    resulting polyline to build a cumulative-arc-length table.

    Returns a list of (cum_dist_ft, point_on_face) tuples, ordered from
    p_start (cum_dist 0) to p_end (cum_dist = total curve length on the
    face). Raises ValueError if any sample fails to project onto the face
    (means p_start/p_end are not actually on/near this face)."""
    pts = []
    for i in range(n_segments + 1):
        t = float(i) / n_segments
        # v1.0.6 -- p_start/p_end are themselves one of the tool's already
        # picked/snapped corner points (A, b, c or d), which can sit EXACTLY
        # on the face's trim boundary. Calling Face.Project on that exact
        # same point can fail for the same trim-tolerance reason fixed for
        # the 4-corner pick itself (see project_point_to_face's docstring) --
        # this is exactly what NBT's "Could not project a sample point"
        # error at Create Rebar time turned out to be. Trust the endpoints
        # directly instead of re-projecting them.
        if i == 0:
            pts.append(p_start)
            continue
        if i == n_segments:
            pts.append(p_end)
            continue
        raw = _lerp_xyz(p_start, p_end, t)
        anchor = p_start if t < 0.5 else p_end
        snapped = project_point_to_face_tolerant(face, raw, anchor)
        if snapped is None:
            raise ValueError(
                "Could not project a sample point onto the picked top face -- "
                "check that all 4 corner points (A, b, c, d) are on that "
                "same face."
            )
        pts.append(snapped)

    samples = [(0.0, pts[0])]
    cum = 0.0
    for i in range(1, len(pts)):
        cum += pts[i].DistanceTo(pts[i - 1])
        samples.append((cum, pts[i]))
    return samples


def point_at_arc_length(samples, target_dist_ft):
    """Interpolate a point on the polyline `samples` (as built by
    build_rail_samples) at cumulative distance `target_dist_ft` from the
    start. Clamps to the 2 ends."""
    if target_dist_ft <= samples[0][0]:
        return samples[0][1]
    if target_dist_ft >= samples[-1][0]:
        return samples[-1][1]
    for i in range(1, len(samples)):
        d0, p0 = samples[i - 1]
        d1, p1 = samples[i]
        if target_dist_ft <= d1:
            span = d1 - d0
            t = 0.0 if span <= 1e-9 else (target_dist_ft - d0) / span
            return _lerp_xyz(p0, p1, t)
    return samples[-1][1]


def compute_bar_positions(face, a_pt, b_pt, c_pt, d_pt, spacing_ft, n_samples=48):
    """Returns a list of (rail1_pt, rail2_pt) pairs, one per bar position,
    from the first bar (a_pt, b_pt) to the last bar (c_pt, d_pt).

    Rail 1 = a_pt -> c_pt, Rail 2 = b_pt -> d_pt, each walked on the face's
    real surface (see build_rail_samples). Spacing follows Revit's
    "Maximum Spacing" convention: the actual spacing used is <=
    `spacing_ft`, evenly distributed along Rail 1's real length so the
    last bar always lands exactly on (c_pt, d_pt) -- never an odd leftover
    gap at one end."""
    if spacing_ft <= 0:
        raise ValueError("Spacing must be greater than 0.")

    rail1 = build_rail_samples(face, a_pt, c_pt, n_samples)
    rail2 = build_rail_samples(face, b_pt, d_pt, n_samples)
    rail1_len = rail1[-1][0]
    rail2_len = rail2[-1][0]
    ref_len = max(rail1_len, rail2_len)

    if ref_len <= mm_to_ft(1.0):
        raise ValueError(
            "The 4 picked points (A, b, c, d) don't span any real distance "
            "on the face -- check the pick order/positions."
        )

    n_intervals = int(math.ceil(ref_len / spacing_ft))
    if n_intervals < 1:
        n_intervals = 1

    positions = []
    for i in range(n_intervals + 1):
        frac = float(i) / n_intervals
        p1 = point_at_arc_length(rail1, frac * rail1_len)
        p2 = point_at_arc_length(rail2, frac * rail2_len)
        positions.append((p1, p2))
    return positions


def compute_bar_norm(face, p1, p2):
    """Plane normal to pass into Rebar.CreateFromCurves for the single
    straight segment p1->p2 -- computed from THIS bar's own local
    direction and the host face's local normal at its midpoint, NOT one
    shared plane for the whole layout (see module docstring)."""
    direction = XYZ(p2.X - p1.X, p2.Y - p1.Y, p2.Z - p1.Z)
    if direction.GetLength() < 1e-9:
        raise ValueError("A bar's two ends came out coincident -- cannot build it.")
    direction = direction.Normalize()

    mid = XYZ((p1.X + p2.X) / 2.0, (p1.Y + p2.Y) / 2.0, (p1.Z + p2.Z) / 2.0)
    local_normal = face_normal_at_point(face, mid)
    if local_normal is None:
        local_normal = XYZ.BasisZ

    norm = direction.CrossProduct(local_normal)
    if norm.GetLength() < 1e-9:
        # direction and local_normal happen to be (near-)parallel -- fall
        # back to any vector not parallel to direction so we still get a
        # valid plane.
        fallback = XYZ.BasisZ if abs(direction.Z) < 0.9 else XYZ.BasisX
        norm = direction.CrossProduct(fallback)
    return norm.Normalize()


def create_straight_rebar(doc, bar_type, host, norm, p1, p2):
    line = Line.CreateBound(p1, p2)
    return Rebar.CreateFromCurves(
        doc, RebarStyle.Standard, bar_type, None, None, host, norm,
        [line],
        RebarHookOrientation.Left, RebarHookOrientation.Left,
        True, True,
    )


def build_rebar_segment_batch(
    doc, top_face, side_face_a, side_face_c, host_element,
    a_pt, b_pt, c_pt, d_pt,
    bar_type, spacing_ft, cover_ft,
    n_samples=48,
):
    """Must be called inside an already-started Transaction. Creates 1
    straight Rebar per layout position between (a_pt, b_pt) and (c_pt,
    d_pt), spaced along the host face's real surface distance, offset
    inward for cover (top face for every bar; ALSO the adjacent side face
    -- side_face_a for the first bar, side_face_c for the last bar -- for
    those 2 bars' endpoints only, per NBT's corner-cover requirement). A
    fresh, unique Partition value is generated once and assigned to every
    bar in this batch.

    Returns (created_rebars, partition_value, errors) -- `errors` is a
    list of (bar_index, message) for any single position that failed to
    build; other positions still get created (1 bad position never aborts
    the whole batch)."""
    positions = compute_bar_positions(top_face, a_pt, b_pt, c_pt, d_pt, spacing_ft, n_samples)
    partition_value = generate_unique_partition(doc)

    created = []
    errors = []
    last_index = len(positions) - 1

    for i, (p1_raw, p2_raw) in enumerate(positions):
        try:
            side_face = None
            if i == 0:
                side_face = side_face_a
            elif i == last_index:
                side_face = side_face_c

            # anchor_point = this bar's OTHER endpoint -- a nearby point
            # already on/near the face, used to nudge past a Face.Project
            # trim-boundary failure at the exact corner points (see
            # apply_corner_offset / face_normal_at_point).
            p1 = apply_corner_offset(p1_raw, top_face, side_face, cover_ft, cover_ft, anchor_point=p2_raw)
            p2 = apply_corner_offset(p2_raw, top_face, side_face, cover_ft, cover_ft, anchor_point=p1_raw)

            norm = compute_bar_norm(top_face, p1, p2)
            rebar = create_straight_rebar(doc, bar_type, host_element, norm, p1, p2)
            set_partition(rebar, partition_value)
            created.append(rebar)
        except Exception as ex:
            errors.append((i, str(ex)))

    return created, partition_value, errors
