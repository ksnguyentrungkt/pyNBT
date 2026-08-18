# -*- coding: utf-8 -*-
"""
Crosshead Shear Link Bar - pyNBT

Generates radial shear-link rebar for a flared ("trumpet") crosshead pier
head - from the flat top face of the flare down into the vertical rebar
cage of the round column shaft below it.

MVP scope (v1.0):
    - Only supports a circular flare over a circular column shaft
      (matches the geometry style shown in the reference tool video).
    - Input = pick ONE existing concrete solid (Crosshead family/mass/
      generic model) - the tool auto-detects the top flare face and the
      shaft face from it. No manual curve-picking mode in this version.
    - Bar count: toggle between "by spacing" (auto) and "by fixed count"
      (manual), matching how the reference tool worked plus an auto option.
    - "Anchor From Intersection Point": toggle between anchoring bars at a
      computed radius (approximating the column's vertical rebar cage) or
      a fixed Anchor Length.
    - Preview draws temporary ModelCurve bars in the active view before
      anything is committed as real Rebar - this is the main upgrade over
      the reference tool, which had no preview step.

Known simplifications to verify against Trung's real model on first test
(flagged inline with "ASSUMPTION" / "TODO" comments):
    - Top-face <-> shaft-top-circle point pairing is done by even angular
      spacing around the shaft axis, not by matching real construction
      lines - visually check the Preview for twisted/crossed bars.
    - Hook geometry is a single straight segment bent toward the face
      normal at the bend point - not a Revit RebarHookType. If Trung's
      detailing standard needs a specific hook shape/angle, this is the
      function to edit (see build_hook_segment).
    - "Anchor From Intersection Point" approximates the column vertical
      rebar cage as a circle of radius (shaft_radius - AnchorCover) rather
      than intersecting real Rebar curves in 3D (skew-line intersection in
      3D is unreliable for this case) - see anchor_length_to_radius().

Next planned revision (v1.1+, after MVP test feedback):
    - Support square / oval / free-form crosshead profiles.
    - Optional: read the real vertical Rebar curves for a more precise
      anchor intersection instead of the radius approximation.
"""

import math
import sys
import os

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("WindowsBase")
clr.AddReference("System")

from Autodesk.Revit.DB import (
    XYZ, UV, Line, Options, Solid, GeometryInstance, ViewDetailLevel,
    PlanarFace, CylindricalFace, Transaction,
    ElementId, FilteredElementCollector, BuiltInCategory,
    Plane, SketchPlane, ViewType,
)
from Autodesk.Revit.DB.Structure import (
    Rebar, RebarBarType, RebarStyle, RebarHookOrientation,
)
from Autodesk.Revit.UI.Selection import ObjectType

from System.Windows import (
    Window, WindowStartupLocation, Thickness, HorizontalAlignment,
    VerticalAlignment, FontWeights, TextWrapping, ResizeMode, GridLength,
    GridUnitType, CornerRadius,
)
from System.Windows.Controls import (
    Grid, RowDefinition, ColumnDefinition, StackPanel, Orientation, Border,
    TextBlock, Button, TextBox, CheckBox, RadioButton, ScrollViewer,
    ScrollBarVisibility,
)
from System.Windows.Media import Color, SolidColorBrush
from System.Collections.Generic import List

from pyrevit import revit, forms, script

# --- pyNBT shared lib (compat.py) -------------------------------------
# If pyNBT.extension/lib/compat.py already exists in Trung's project from
# an earlier tool, pyRevit's loader picks that one up automatically - no
# need to add/overwrite anything in lib. If it's missing, the fallback
# below is used instead, so this tool works standalone either way.
try:
    from compat import eid_int, make_eid
except ImportError:
    from System import Int64

    def make_eid(value):
        try:
            return ElementId(Int64(value))
        except (TypeError, OverflowError):
            return ElementId(int(value))

    def eid_int(element_id):
        if element_id is None:
            return -1
        val = getattr(element_id, "Value", None)
        if val is None:
            val = getattr(element_id, "IntegerValue", None)
        return int(val) if val is not None else -1


doc = revit.doc
uidoc = revit.uidoc
logger = script.get_logger()

TOOL_NAME = "Crosshead Shear Link Bar"
TOOL_VERSION = "v1.0 (MVP)"

# ============================================================================
# 1. THEME CONSTANTS (pyNBT palette - Navy + Gray/White/Black)
# ============================================================================

CLR_HEADER = Color.FromRgb(30, 41, 59)        # Navy header bg
CLR_HEADER_TEXT = Color.FromRgb(255, 255, 255)  # White text on navy
CLR_HEADER_SUB = Color.FromRgb(203, 213, 225)   # Light gray subtitle
CLR_ACCENT = Color.FromRgb(30, 41, 59)          # Navy accent
CLR_BG = Color.FromRgb(248, 249, 250)           # Off-white background
CLR_CARD = Color.FromRgb(255, 255, 255)         # White card
CLR_BORDER = Color.FromRgb(203, 213, 225)       # Gray border
CLR_FOOTER = Color.FromRgb(241, 245, 249)       # Light gray footer
CLR_TEXT = Color.FromRgb(30, 30, 30)            # Near-black text
CLR_MUTED = Color.FromRgb(120, 120, 120)        # Muted gray text
CLR_PRIMARY_BTN = Color.FromRgb(21, 128, 61)    # Green - primary action (Preview/Create)
CLR_PRIMARY_BTN_TXT = Color.FromRgb(255, 255, 255)
CLR_DANGER_BTN = Color.FromRgb(185, 28, 28)     # Red - Reset/Clear
CLR_SECONDARY_BTN = Color.FromRgb(255, 255, 255)
CLR_SECONDARY_BTN_TXT = Color.FromRgb(30, 41, 59)


def brush(color):
    return SolidColorBrush(color)


# ============================================================================
# 2. STANDALONE LOGIC FUNCTIONS (no UI references - pure Revit API / geometry)
# ============================================================================

def get_solid_from_element(elem):
    """Return the largest Solid found in the element's geometry, or None."""
    opt = Options()
    opt.ComputeReferences = True
    opt.DetailLevel = ViewDetailLevel.Fine
    geo = elem.get_Geometry(opt)
    if geo is None:
        return None

    solids = []

    def collect(geo_elem):
        for g in geo_elem:
            if isinstance(g, Solid) and g.Volume > 1e-6:
                solids.append(g)
            elif isinstance(g, GeometryInstance):
                try:
                    collect(g.GetInstanceGeometry())
                except Exception:
                    pass

    collect(geo)
    if not solids:
        return None
    solids.sort(key=lambda s: s.Volume, reverse=True)
    return solids[0]


def find_crosshead_faces(solid):
    """
    Identify the faces that define a circular-flare crosshead:
        top_face    - PlanarFace, roughly horizontal, highest centroid Z
                      (the flat top of the flare)
        shaft_face  - CylindricalFace with the largest area
                      (the round column shaft below)
        skirt_faces - every other face that isn't top/shaft/a horizontal
                      cap (e.g. the bottom of the shaft) - this is the
                      sloped/curved "wall" of the flare connecting the top
                      rim down to the shaft. Collected as a list (not a
                      single face) since a flare built from a multi-segment
                      loft can have more than one wall face - kept generic
                      rather than assuming RevolvedFace/RuledFace/etc, so
                      it works regardless of how Trung modeled the flare.

    Raises ValueError with an English message (shown to Trung via
    forms.alert) if the top/shaft faces can't be found - MVP only supports
    this circular-flare-over-round-shaft geometry. skirt_faces may come
    back empty (e.g. an unusual/simplified solid) - callers fall back to a
    straight top-to-shaft chord in that case.
    """
    top_face = None
    top_z = None
    shaft_face = None
    shaft_area = 0.0

    for face in solid.Faces:
        try:
            centroid = face.Evaluate(UV(0.5, 0.5))
        except Exception:
            continue

        if isinstance(face, PlanarFace):
            normal = face.FaceNormal
            if abs(normal.Z) > 0.99:  # horizontal-ish face (top disc or a bottom cap)
                if top_z is None or centroid.Z > top_z:
                    top_z = centroid.Z
                    top_face = face
        elif isinstance(face, CylindricalFace):
            if face.Area > shaft_area:
                shaft_area = face.Area
                shaft_face = face

    if top_face is None:
        raise ValueError(
            "Could not find a horizontal top face on the picked solid. "
            "Make sure you picked the Crosshead flare element."
        )
    if shaft_face is None:
        raise ValueError(
            "Could not find a cylindrical shaft face on the picked solid. "
            "v1.0 only supports a circular flare over a round column shaft."
        )

    skirt_faces = [
        f for f in solid.Faces
        if f is not top_face and f is not shaft_face
        and not (isinstance(f, PlanarFace) and abs(f.FaceNormal.Z) > 0.99)
    ]

    return top_face, shaft_face, skirt_faces


def sample_outer_loop_points(face, n):
    """
    Tessellate the OUTER boundary loop of a planar face into n evenly
    spaced points (by arc length). Works for any closed profile, so this
    keeps the door open for non-circular flare shapes in a later revision.

    ASSUMPTION: the outer loop is the loop with the greatest total edge
    length (inner loops, if any, are usually small voids/openings).
    """
    loops = list(face.EdgeLoops)
    if not loops:
        raise ValueError("Top face has no boundary loop.")

    def loop_length(loop):
        total = 0.0
        for e in loop:
            total += e.ApproximateLength
        return total

    outer = max(loops, key=loop_length)
    curves = [e.AsCurve() for e in outer]
    total_len = sum(c.Length for c in curves)
    if total_len < 1e-6:
        raise ValueError("Top face boundary has near-zero length.")

    step = total_len / float(n)
    points = []
    walked = 0.0
    target = 0.0

    for c in curves:
        c_len = c.Length
        if c_len < 1e-9:
            continue
        while target <= walked + c_len + 1e-9 and len(points) < n:
            local_norm = (target - walked) / c_len
            local_norm = min(max(local_norm, 0.0), 1.0)
            points.append(c.Evaluate(local_norm, True))
            target += step
        walked += c_len

    # guard against floating point shortfall leaving us one point short
    while len(points) < n:
        points.append(points[-1] if points else face.Evaluate(UV(0.5, 0.5)))

    return points[:n]


def _make_axis_basis(axis_dir):
    """Build a stable (ref_dir, ref_perp) pair of horizontal unit vectors
    perpendicular to axis_dir - a fixed world-frame "zero angle" reference
    so angles computed around the axis are comparable across different
    faces (a face's own UV parametrization has no relation to any other
    face's UV parametrization - two different faces can start their U=0
    at completely different, unrelated angles and even run in opposite
    directions)."""
    ref_dir = XYZ.BasisX - axis_dir.Multiply(axis_dir.DotProduct(XYZ.BasisX))
    if ref_dir.GetLength() < 1e-6:
        ref_dir = XYZ.BasisY - axis_dir.Multiply(axis_dir.DotProduct(XYZ.BasisY))
    ref_dir = ref_dir.Normalize()
    ref_perp = axis_dir.CrossProduct(ref_dir).Normalize()
    return ref_dir, ref_perp


def _angle_around_axis(point, axis_origin, axis_dir, ref_dir, ref_perp):
    """True angle (radians, 0..2pi) of `point` around (axis_origin, axis_dir),
    measured from ref_dir toward ref_perp."""
    v = point - axis_origin
    v = v - axis_dir.Multiply(v.DotProduct(axis_dir))  # project to horizontal plane
    x = v.DotProduct(ref_dir)
    y = v.DotProduct(ref_perp)
    a = math.atan2(y, x)
    if a < 0:
        a += 2.0 * math.pi
    return a


def sample_shaft_top_circle(shaft_face, n):
    """
    Sample n points around the TOP circle of the cylindrical shaft face -
    i.e. the seam where the flare meets the round column. Uses the face's
    own UV parametrization (U = angle, V = height along axis) so the
    points come out as a mathematically exact circle rather than a
    tessellated edge loop.

    Returns (points, axis_origin, axis_direction, radius, angles,
    ref_dir, ref_perp) - angles/ref_dir/ref_perp let the caller place
    matching points (at the exact same true angle) on OTHER faces, such
    as the flare top boundary - see compute_top_circle_points_matching().
    """
    bbox = shaft_face.GetBoundingBox()
    u_min, u_max = bbox.Min.U, bbox.Max.U
    v_min, v_max = bbox.Min.V, bbox.Max.V

    # Work out which V (Min or Max) corresponds to the TOP of the shaft by
    # comparing world Z at both ends.
    z_at_min_v = shaft_face.Evaluate(UV((u_min + u_max) / 2.0, v_min)).Z
    z_at_max_v = shaft_face.Evaluate(UV((u_min + u_max) / 2.0, v_max)).Z
    top_v = v_max if z_at_max_v >= z_at_min_v else v_min

    points = []
    for i in range(n):
        u = u_min + (u_max - u_min) * (float(i) / n)
        points.append(shaft_face.Evaluate(UV(u, top_v)))

    # NOTE: CylindricalFace.Axis is an XYZ *direction* vector, not a Line -
    # the origin point on the axis is a separate property (CylindricalFace.Origin).
    axis_dir = shaft_face.Axis.Normalize()
    axis_origin = shaft_face.Origin
    # Revit doesn't expose CylindricalFace.Radius directly in all API
    # versions - derive it from the sampled point distance to the axis.
    radius = point_to_axis_distance(points[0], axis_origin, axis_dir)

    ref_dir, ref_perp = _make_axis_basis(axis_dir)
    angles = [_angle_around_axis(p, axis_origin, axis_dir, ref_dir, ref_perp) for p in points]

    return points, axis_origin, axis_dir, radius, angles, ref_dir, ref_perp


def compute_top_circle_points_matching(top_face, axis_origin, axis_dir, angles, ref_dir, ref_perp):
    """
    Generate points on the flare TOP boundary at the exact same angles as
    the shaft points (one-to-one) - this is what keeps every bar radial
    (top point directly above/outboard of its paired shaft point) instead
    of twisting and crossing near the center, which happens if the top
    loop and the shaft circle are each parametrized independently (their
    "angle zero" and rotation direction have no relation to each other).

    MVP ASSUMPTION: the flare top boundary is a circle centered ON the
    shaft axis (true for a body-of-revolution flare, which is the only
    shape v1.0 supports). Center = where the axis line crosses the top
    face's plane; radius = distance from that center to the boundary.
    """
    loops = list(top_face.EdgeLoops)
    if not loops:
        raise ValueError("Top face has no boundary loop.")

    def loop_length(loop):
        return sum(e.ApproximateLength for e in loop)

    outer = max(loops, key=loop_length)
    boundary_point = outer[0].AsCurve().Evaluate(0.0, True)

    plane_point = top_face.Origin
    plane_normal = top_face.FaceNormal
    denom = axis_dir.DotProduct(plane_normal)
    if abs(denom) < 1e-9:
        raise ValueError("Shaft axis runs parallel to the top face - can't locate a shared center.")
    t = (plane_point - axis_origin).DotProduct(plane_normal) / denom
    center = axis_origin + axis_dir.Multiply(t)
    radius = point_to_axis_distance(boundary_point, axis_origin, axis_dir)

    points = []
    for a in angles:
        points.append(XYZ(
            center.X + radius * (math.cos(a) * ref_dir.X + math.sin(a) * ref_perp.X),
            center.Y + radius * (math.cos(a) * ref_dir.Y + math.sin(a) * ref_perp.Y),
            center.Z + radius * (math.cos(a) * ref_dir.Z + math.sin(a) * ref_perp.Z),
        ))
    return points


def point_to_axis_distance(point, axis_origin, axis_dir):
    """Perpendicular distance from `point` to the infinite line
    (axis_origin, axis_dir)."""
    v = point - axis_origin
    along = v.DotProduct(axis_dir)
    closest = axis_origin + axis_dir.Multiply(along)
    return point.DistanceTo(closest)


def sample_skirt_profile_for_angle(skirt_faces, axis_origin, axis_dir, ref_dir, ref_perp,
                                    target_angle, samples_per_dim=16):
    """
    Trace the flare's sloped/curved "skirt" surface at a fixed angle around
    the shaft axis, returning points from top to bottom (NOT yet including
    the exact top-rim/shaft-seam points - the caller splices those on).

    Generic by design: does NOT assume a specific Revit face subtype
    (RevolvedFace / RuledFace / ConicalFace / a lofted free-form face all
    expose a UV domain) - for every skirt face, at each "row" (one of
    samples_per_dim+1 evenly spaced V values), scan across U and keep
    whichever point's true angle-around-axis is closest to target_angle.
    That gives one point per row = a clean profile line, without needing
    to know what U/V actually represent for this face.

    ASSUMPTION: V correlates with position along the profile (top-to-
    bottom-ish) and U correlates with angle - true for elementary
    revolved/conical/cylindrical faces, a reasonable approximation for a
    simple loft. If Trung's flare uses an unusual multi-directional
    surface this may need revisiting - check the Preview visually.
    """
    all_points = []

    for face in skirt_faces:
        try:
            bbox = face.GetBoundingBox()
        except Exception:
            continue
        u_min, u_max = bbox.Min.U, bbox.Max.U
        v_min, v_max = bbox.Min.V, bbox.Max.V
        if u_max - u_min < 1e-9 or v_max - v_min < 1e-9:
            continue

        row_points = []
        for iv in range(samples_per_dim + 1):
            v = v_min + (v_max - v_min) * (float(iv) / samples_per_dim)
            best_pt = None
            best_diff = None
            for iu in range(samples_per_dim + 1):
                u = u_min + (u_max - u_min) * (float(iu) / samples_per_dim)
                try:
                    p = face.Evaluate(UV(u, v))
                except Exception:
                    continue
                a = _angle_around_axis(p, axis_origin, axis_dir, ref_dir, ref_perp)
                diff = abs(a - target_angle)
                diff = min(diff, 2.0 * math.pi - diff)
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    best_pt = p
            # ~10 degrees - skip rows where nothing on this face got close
            # to the target angle (face doesn't wrap all the way around)
            if best_pt is not None and best_diff is not None and best_diff < 0.175:
                row_points.append(best_pt)

        all_points.extend(row_points)

    if not all_points:
        return []

    # order top -> bottom along the shaft axis
    all_points.sort(key=lambda p: -p.DotProduct(axis_dir))
    return all_points


def offset_point_radially(point, axis_origin, axis_dir, delta_radius):
    """Move `point` toward the axis by delta_radius, keeping its height
    along axis_dir and its angle around the axis unchanged - used to push
    a surface point inward by the concrete Cover distance."""
    v = point - axis_origin
    along = v.DotProduct(axis_dir)
    horiz = v - axis_dir.Multiply(along)
    r = horiz.GetLength()
    if r < 1e-9:
        return point  # point sits on the axis already - nothing to offset
    new_r = max(r - delta_radius, 0.0)
    horiz_dir = horiz.Normalize()
    return XYZ(
        axis_origin.X + axis_dir.X * along + horiz_dir.X * new_r,
        axis_origin.Y + axis_dir.Y * along + horiz_dir.Y * new_r,
        axis_origin.Z + axis_dir.Z * along + horiz_dir.Z * new_r,
    )


def offset_point_along(point, direction, distance):
    d = direction.Normalize()
    return XYZ(point.X + d.X * distance, point.Y + d.Y * distance, point.Z + d.Z * distance)


def build_hook_segment(bend_point, face_normal, hook_length):
    """
    Simple single-segment hook: bends from the bend_point toward the
    (inward) face normal by `hook_length`.

    ASSUMPTION - confirm against Trung's rebar detailing standard: real
    L-hooks are usually drawn at a fixed 90 or 135 degree bend relative to
    the main bar direction with a standard bend radius, not a raw vector
    toward the face normal. If the Preview shows hooks pointing the wrong
    way, this is the function to fix first.
    """
    inward = face_normal.Negate().Normalize()
    end_point = offset_point_along(bend_point, inward, hook_length)
    if bend_point.DistanceTo(end_point) < 1e-6:
        return None
    return Line.CreateBound(bend_point, end_point)


def anchor_length_to_radius(start_point, direction, axis_origin, target_radius):
    """
    Solve for t (distance along `direction` from start_point) such that the
    horizontal distance from the resulting point to the vertical axis
    equals target_radius. This approximates "extend the bar until it
    reaches the column's vertical rebar cage" without needing true 3D
    curve intersection against individual vertical bars (which is
    unreliable for skew lines).

    Returns t (float, > 0) or None if the ray direction never reaches that
    radius (e.g. it's already past it, or running parallel to the axis).
    """
    dx, dy = direction.X, direction.Y
    px = start_point.X - axis_origin.X
    py = start_point.Y - axis_origin.Y

    a = dx * dx + dy * dy
    b = 2.0 * (px * dx + py * dy)
    c = px * px + py * py - target_radius * target_radius

    if a < 1e-9:
        return None
    disc = b * b - 4 * a * c
    if disc < 0:
        return None
    sqrt_disc = math.sqrt(disc)
    t1 = (-b + sqrt_disc) / (2 * a)
    t2 = (-b - sqrt_disc) / (2 * a)
    candidates = [t for t in (t1, t2) if t is not None and t > 1e-6]
    if not candidates:
        return None
    return min(candidates)


def compute_bar_count(perimeter, spacing):
    n = int(math.floor(perimeter / spacing))
    return max(3, n)


def build_all_bar_curves(top_face, shaft_face, skirt_faces, n, cover, hook_cover, hook_length,
                          anchor_cover, anchor_length, use_intersection_anchor):
    """
    Compute the full multi-segment curve list for every bar, plus a small
    stats dict for the UI summary. Pure geometry - no Revit document
    writes happen here, so this same function backs both Preview and the
    real Rebar creation.

    Each bar HUGS the flare's actual curved outer surface (offset inward
    by Cover) from the top rim down to the shaft seam, instead of cutting
    a straight chord through the concrete - this is what makes the bars
    read as a dense vertical "cage" following the trumpet silhouette
    (Trung's reference shape) rather than a fan converging toward the
    center.

    Returns (list_of_bars, stats_dict)
        list_of_bars: List[(curves, bar_normal)] - for each bar, a list of
            connected curves (hook + profile segments + anchor extension)
            and the plane normal that curve chain is planar with respect
            to (required by Rebar.CreateFromCurves - each bar has its OWN
            normal since each sits in a different rotated vertical plane
            around the shaft).
    """
    shaft_points, axis_origin, axis_dir, shaft_radius, angles, ref_dir, ref_perp = \
        sample_shaft_top_circle(shaft_face, n)
    top_points = compute_top_circle_points_matching(
        top_face, axis_origin, axis_dir, angles, ref_dir, ref_perp
    )

    top_normal = top_face.FaceNormal
    target_radius = max(shaft_radius - anchor_cover, shaft_radius * 0.3)

    all_curves = []
    total_length = 0.0
    skipped = 0

    for i in range(n):
        p_top = top_points[i]
        p_shaft = shaft_points[i]
        target_angle = angles[i]

        # 1. Raw surface profile at this angle, top -> bottom. Falls back
        #    to a straight top-to-shaft chord if no skirt face was found
        #    (e.g. an unusual solid) rather than failing the whole bar.
        skirt_pts = sample_skirt_profile_for_angle(
            skirt_faces, axis_origin, axis_dir, ref_dir, ref_perp, target_angle
        ) if skirt_faces else []

        raw_profile = [p_top] + skirt_pts + [p_shaft]
        # drop near-duplicate consecutive points (zero-length segments)
        cleaned = [raw_profile[0]]
        for p in raw_profile[1:]:
            if p.DistanceTo(cleaned[-1]) > 1e-4:
                cleaned.append(p)
        raw_profile = cleaned
        if len(raw_profile) < 2:
            skipped += 1
            continue

        # 2. Offset every profile point inward (toward the axis) by Cover -
        #    keeps the bar hugging just inside the surface all the way down.
        offset_profile = [offset_point_radially(p, axis_origin, axis_dir, cover) for p in raw_profile]

        # 3. Hook at the top: bend point = further inset from the first
        #    (cover-adjusted) profile point by Hook Cover, along that first
        #    segment's local direction.
        first_dir_vec = offset_profile[1] - offset_profile[0]
        if first_dir_vec.GetLength() < 1e-6:
            skipped += 1
            continue
        first_dir = first_dir_vec.Normalize()
        p_hook_bend = offset_point_along(offset_profile[0], first_dir, hook_cover) \
            if hook_cover > 1e-9 else offset_profile[0]

        # 4. Anchor extension at the bottom: continue past the last profile
        #    point along the local direction of the last segment, either to
        #    the approximated rebar-cage radius or a fixed Anchor Length.
        last_dir_vec = offset_profile[-1] - offset_profile[-2]
        if last_dir_vec.GetLength() < 1e-6:
            skipped += 1
            continue
        last_dir = last_dir_vec.Normalize()
        if use_intersection_anchor:
            t = anchor_length_to_radius(offset_profile[-1], last_dir, axis_origin, target_radius)
            if t is None:
                t = anchor_length
            p_bottom = offset_point_along(offset_profile[-1], last_dir, t)
        else:
            p_bottom = offset_point_along(offset_profile[-1], last_dir, anchor_length)

        # IMPORTANT: Rebar.CreateFromCurves requires the curve list to form
        # one continuous connected chain (each curve's end = next curve's
        # start) - rejected as "not a valid CurveLoop" otherwise.
        curves_for_bar = []

        hook_segment = build_hook_segment(p_hook_bend, top_normal, hook_length)
        if hook_segment is not None:
            # build_hook_segment returns bend->tip; reverse so the chain
            # starts at the tip and flows down into the profile below
            hook_tip = hook_segment.GetEndPoint(1)
            reversed_hook = Line.CreateBound(hook_tip, p_hook_bend)
            curves_for_bar.append(reversed_hook)
            total_length += reversed_hook.Length

        try:
            # p_hook_bend -> profile point 1 -> ... -> last profile point -> p_bottom
            chain_points = [p_hook_bend] + offset_profile[1:] + [p_bottom]
            new_segments = []
            for j in range(len(chain_points) - 1):
                new_segments.append(Line.CreateBound(chain_points[j], chain_points[j + 1]))
        except Exception:
            skipped += 1
            continue
        curves_for_bar.extend(new_segments)
        total_length += sum(s.Length for s in new_segments)

        # Rebar.CreateFromCurves needs a `norm` perpendicular to the plane
        # the curves lie in. By construction every point in this bar's
        # chain sits in the SAME vertical plane through the axis at
        # target_angle (that's the definition of "profile at a fixed
        # angle" for a body of revolution) - so the plane's normal is just
        # the tangential (circumferential) direction at that angle.
        bar_normal = XYZ(
            -math.sin(target_angle) * ref_dir.X + math.cos(target_angle) * ref_perp.X,
            -math.sin(target_angle) * ref_dir.Y + math.cos(target_angle) * ref_perp.Y,
            -math.sin(target_angle) * ref_dir.Z + math.cos(target_angle) * ref_perp.Z,
        )
        if bar_normal.GetLength() < 1e-6:
            skipped += 1
            continue
        bar_normal = bar_normal.Normalize()

        all_curves.append((curves_for_bar, bar_normal))

    stats = {
        "count": len(all_curves),
        "skipped": skipped,
        "total_length_mm": total_length * 304.8,  # Revit internal units = feet
        "avg_length_mm": (total_length / len(all_curves) * 304.8) if all_curves else 0.0,
    }
    return all_curves, stats


def get_rebar_bar_type(diameter_mm):
    """Find (or approximate-match) a RebarBarType by nominal diameter in mm.
    Falls back to the first available RebarBarType with a warning, since a
    project may name bar types differently (e.g. "T40" vs "D40")."""
    collector = FilteredElementCollector(doc).OfClass(RebarBarType)
    best = None
    best_diff = None
    for bt in collector:
        try:
            dia_mm = bt.BarModelDiameter * 304.8
        except Exception:
            continue
        diff = abs(dia_mm - diameter_mm)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best = bt
    return best


def create_real_rebar(host_element, bar_curve_lists, bar_type):
    """Create actual Structural Rebar elements from precomputed curve
    lists. Must be called inside an open Transaction by the caller."""
    created_ids = []
    for curves, bar_normal in bar_curve_lists:
        try:
            rebar = Rebar.CreateFromCurves(
                doc, RebarStyle.Standard, bar_type, None, None,
                host_element, bar_normal, curves,
                RebarHookOrientation.Right, RebarHookOrientation.Right,
                True, True,
            )
            if rebar is not None:
                created_ids.append(rebar.Id)
        except Exception as ex:
            logger.warning("Rebar.CreateFromCurves failed for one bar: {}".format(ex))
            continue
    return created_ids


# ============================================================================
# 3. SELECTION FILTER
# ============================================================================

class AnyElementFilter(object):
    """Minimal ISelectionFilter-compatible object accepting any element with
    solid geometry - pyRevit's PickObject wraps this for us."""
    def AllowElement(self, elem):
        return True

    def AllowReference(self, reference, position):
        return True


# ============================================================================
# 4. UI HELPERS
# ============================================================================

def add_row(grid, height=GridLength.Auto):
    grid.RowDefinitions.Add(RowDefinition(Height=height))


def add_col(grid, width=GridLength.Auto):
    grid.ColumnDefinitions.Add(ColumnDefinition(Width=width))


def make_label(text, bold=False, color=CLR_TEXT, size=13):
    tb = TextBlock()
    tb.Text = text
    tb.Foreground = brush(color)
    tb.FontSize = size
    tb.TextWrapping = TextWrapping.Wrap
    if bold:
        tb.FontWeight = FontWeights.Bold
    return tb


def make_textbox(default_text=""):
    tb = TextBox()
    tb.Text = default_text
    tb.Padding = Thickness(6, 4, 6, 4)
    tb.Margin = Thickness(0, 2, 0, 10)
    tb.BorderBrush = brush(CLR_BORDER)
    return tb


def make_field(parent_panel, label_text, default_text):
    parent_panel.Children.Add(make_label(label_text))
    box = make_textbox(default_text)
    parent_panel.Children.Add(box)
    return box


def make_button(text, bg, fg, height=36):
    btn = Button()
    btn.Content = text
    btn.Background = brush(bg)
    btn.Foreground = brush(fg)
    btn.Height = height
    btn.Margin = Thickness(4, 0, 4, 0)
    btn.Padding = Thickness(10, 4, 10, 4)
    btn.BorderThickness = Thickness(0)
    btn.FontWeight = FontWeights.SemiBold
    btn.Cursor = None
    return btn


def parse_float_mm(text_box, field_name, default=0.0):
    try:
        return float(text_box.Text.strip())
    except Exception:
        forms.alert(
            "'{}' must be a number. Using default {} mm for now.".format(field_name, default),
            title=TOOL_NAME,
        )
        return default


# ============================================================================
# 5. MAIN WINDOW
# ============================================================================

class CrossheadShearLinkWindow(Window):

    def __init__(self):
        self.host_element = None
        self.top_face = None
        self.shaft_face = None
        self.skirt_faces = None
        self.preview_ids = []

        self.Title = TOOL_NAME
        self.Width = 620
        self.Height = 640
        self.MinWidth = 560
        self.MinHeight = 560
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.ResizeMode = ResizeMode.CanResize
        self.Background = brush(CLR_BG)

        self.Closed += self.on_window_closed

        root = Grid()
        add_row(root, GridLength.Auto)     # header
        add_row(root, GridLength(1, GridUnitType.Star))  # content
        add_row(root, GridLength.Auto)     # footer
        self.Content = root

        root.Children.Add(self._build_header())
        content = self._build_content()
        Grid.SetRow(content, 1)
        root.Children.Add(content)
        footer = self._build_footer()
        Grid.SetRow(footer, 2)
        root.Children.Add(footer)

    # -- header ------------------------------------------------------
    def _build_header(self):
        header = Border()
        header.Background = brush(CLR_HEADER)
        header.Padding = Thickness(16, 12, 16, 12)

        grid = Grid()
        add_col(grid, GridLength(1, GridUnitType.Star))
        add_col(grid, GridLength.Auto)
        header.Child = grid

        left = StackPanel()
        title = make_label(TOOL_NAME, bold=True, color=CLR_HEADER_TEXT, size=17)
        subtitle = make_label(
            "Auto shear-link rebar for flared crosshead pier heads - pyNBT",
            color=CLR_HEADER_SUB, size=11,
        )
        left.Children.Add(title)
        left.Children.Add(subtitle)
        grid.Children.Add(left)

        badge = Border()
        badge.Background = brush(Color.FromRgb(51, 65, 85))
        badge.CornerRadius = CornerRadius(4)
        badge.Padding = Thickness(8, 4, 8, 4)
        badge.VerticalAlignment = VerticalAlignment.Center
        badge.Child = make_label(TOOL_VERSION, color=CLR_HEADER_SUB, size=11)
        Grid.SetColumn(badge, 1)
        grid.Children.Add(badge)

        return header

    # -- content -------------------------------------------------------
    def _build_content(self):
        content = Grid()
        content.Margin = Thickness(16, 16, 16, 16)
        add_col(content, GridLength(260, GridUnitType.Pixel))
        add_col(content, GridLength(16, GridUnitType.Pixel))
        add_col(content, GridLength(1, GridUnitType.Star))

        left = self._build_left_panel()
        content.Children.Add(left)

        right = self._build_right_panel()
        Grid.SetColumn(right, 2)
        content.Children.Add(right)

        return content

    def _card(self):
        b = Border()
        b.Background = brush(CLR_CARD)
        b.BorderBrush = brush(CLR_BORDER)
        b.BorderThickness = Thickness(1)
        b.CornerRadius = CornerRadius(6)
        b.Padding = Thickness(14)
        return b

    def _build_left_panel(self):
        card = self._card()
        scroller = ScrollViewer()
        scroller.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        panel = StackPanel()
        scroller.Content = panel
        card.Child = scroller

        panel.Children.Add(make_label("1. SELECTION", bold=True, size=12, color=CLR_MUTED))
        pick_btn = make_button("Pick Crosshead Solid...", CLR_ACCENT, CLR_HEADER_TEXT)
        pick_btn.HorizontalAlignment = HorizontalAlignment.Stretch
        pick_btn.Margin = Thickness(0, 4, 0, 4)
        pick_btn.Click += self.on_pick_click
        panel.Children.Add(pick_btn)

        self.lbl_selection = make_label("No element selected.", color=CLR_MUTED, size=11)
        self.lbl_selection.Margin = Thickness(0, 0, 0, 12)
        panel.Children.Add(self.lbl_selection)

        panel.Children.Add(make_label("2. REBAR PARAMETERS", bold=True, size=12, color=CLR_MUTED))
        self.box_rebar_size = make_field(panel, "Rebar Size (mm)", "40")
        self.box_cover = make_field(panel, "Cover (mm)", "50")
        self.box_hook_cover = make_field(panel, "Hook Cover (mm)", "90")
        self.box_hook_length = make_field(panel, "Hook Length (mm)", "900")

        panel.Children.Add(make_label("3. BAR COUNT", bold=True, size=12, color=CLR_MUTED))
        self.rb_by_spacing = RadioButton()
        self.rb_by_spacing.Content = "By spacing (auto count)"
        self.rb_by_spacing.IsChecked = True
        self.rb_by_spacing.Margin = Thickness(0, 4, 0, 4)
        self.rb_by_spacing.Checked += self.on_bar_count_mode_changed
        panel.Children.Add(self.rb_by_spacing)
        self.box_spacing = make_textbox("200")
        panel.Children.Add(self.box_spacing)

        self.rb_by_count = RadioButton()
        self.rb_by_count.Content = "Fixed count"
        self.rb_by_count.Margin = Thickness(0, 4, 0, 4)
        self.rb_by_count.Checked += self.on_bar_count_mode_changed
        panel.Children.Add(self.rb_by_count)
        self.box_fixed_count = make_textbox("87")
        self.box_fixed_count.IsEnabled = False
        panel.Children.Add(self.box_fixed_count)

        panel.Children.Add(make_label("4. ANCHOR (bottom end)", bold=True, size=12, color=CLR_MUTED))
        self.chk_anchor_intersection = CheckBox()
        self.chk_anchor_intersection.Content = "Anchor From Intersection Point"
        self.chk_anchor_intersection.IsChecked = True
        self.chk_anchor_intersection.Margin = Thickness(0, 4, 0, 10)
        panel.Children.Add(self.chk_anchor_intersection)
        self.box_anchor_cover = make_field(panel, "Anchor Cover (mm)", "300")
        self.box_anchor_length = make_field(panel, "Anchor Length (mm)", "1500")

        return card

    def _build_right_panel(self):
        card = self._card()
        panel = StackPanel()
        card.Child = panel

        panel.Children.Add(make_label("PREVIEW / RESULT", bold=True, size=12, color=CLR_MUTED))
        self.lbl_summary = make_label(
            "Pick a Crosshead solid, adjust parameters, then click Preview.\n"
            "Preview draws temporary lines in the active view only - nothing "
            "is written to the model until you click Create Rebar.",
            color=CLR_MUTED, size=12,
        )
        self.lbl_summary.Margin = Thickness(0, 4, 0, 14)
        panel.Children.Add(self.lbl_summary)

        stat_grid = Grid()
        add_col(stat_grid, GridLength(1, GridUnitType.Star))
        add_col(stat_grid, GridLength(1, GridUnitType.Star))
        add_row(stat_grid)
        add_row(stat_grid)
        panel.Children.Add(stat_grid)

        self.lbl_stat_count = self._stat_tile(stat_grid, 0, 0, "0", "Bars")
        self.lbl_stat_length = self._stat_tile(stat_grid, 0, 1, "0 mm", "Avg. length")

        panel.Children.Add(make_label(" ", size=6))
        panel.Children.Add(make_label("LOG", bold=True, size=12, color=CLR_MUTED))
        self.lbl_log = make_label("", color=CLR_TEXT, size=11)
        panel.Children.Add(self.lbl_log)

        return card

    def _stat_tile(self, grid, row, col, value, caption):
        b = Border()
        b.Background = brush(CLR_BG)
        b.BorderBrush = brush(CLR_BORDER)
        b.BorderThickness = Thickness(1)
        b.CornerRadius = CornerRadius(6)
        b.Margin = Thickness(0, 0, 8 if col == 0 else 0, 8)
        b.Padding = Thickness(10)
        Grid.SetRow(b, row)
        Grid.SetColumn(b, col)

        inner = StackPanel()
        b.Child = inner
        value_lbl = make_label(value, bold=True, color=CLR_ACCENT, size=20)
        caption_lbl = make_label(caption, color=CLR_MUTED, size=11)
        inner.Children.Add(value_lbl)
        inner.Children.Add(caption_lbl)

        grid.Children.Add(b)
        return value_lbl

    # -- footer -------------------------------------------------------
    def _build_footer(self):
        footer = Border()
        footer.Background = brush(CLR_FOOTER)
        footer.Padding = Thickness(16, 10, 16, 10)

        grid = Grid()
        add_col(grid, GridLength(1, GridUnitType.Star))
        add_col(grid, GridLength.Auto)
        footer.Child = grid

        sig = make_label(
            "{} {} | {} | Nguyen Bao Trung".format(TOOL_NAME, TOOL_VERSION, doc.Title),
            color=CLR_MUTED, size=10,
        )
        sig.VerticalAlignment = VerticalAlignment.Center
        grid.Children.Add(sig)

        btn_row = StackPanel()
        btn_row.Orientation = Orientation.Horizontal
        Grid.SetColumn(btn_row, 1)
        grid.Children.Add(btn_row)

        self.btn_reset = make_button("Reset", CLR_DANGER_BTN, CLR_HEADER_TEXT, height=34)
        self.btn_reset.Click += self.on_reset_click
        btn_row.Children.Add(self.btn_reset)

        self.btn_preview = make_button("Preview", CLR_PRIMARY_BTN, CLR_PRIMARY_BTN_TXT, height=34)
        self.btn_preview.Click += self.on_preview_click
        btn_row.Children.Add(self.btn_preview)

        self.btn_create = make_button("Create Rebar", CLR_ACCENT, CLR_HEADER_TEXT, height=34)
        self.btn_create.Click += self.on_create_click
        btn_row.Children.Add(self.btn_create)

        self.btn_close = make_button("Close", CLR_SECONDARY_BTN, CLR_SECONDARY_BTN_TXT, height=34)
        self.btn_close.BorderBrush = brush(CLR_BORDER)
        self.btn_close.BorderThickness = Thickness(1)
        self.btn_close.Click += self.on_close_click
        btn_row.Children.Add(self.btn_close)

        return footer

    # -- event handlers -------------------------------------------------
    def on_bar_count_mode_changed(self, sender, args):
        by_spacing = self.rb_by_spacing.IsChecked
        self.box_spacing.IsEnabled = by_spacing
        self.box_fixed_count.IsEnabled = not by_spacing

    def on_pick_click(self, sender, args):
        try:
            self.Hide()
            ref = uidoc.Selection.PickObject(ObjectType.Element, "Pick the Crosshead solid/family")
            self.host_element = doc.GetElement(ref.ElementId)
            solid = get_solid_from_element(self.host_element)
            if solid is None:
                forms.alert("No solid geometry found on the picked element.", title=TOOL_NAME)
                self.host_element = None
                return
            self.top_face, self.shaft_face, self.skirt_faces = find_crosshead_faces(solid)
            self.lbl_selection.Text = "Selected: {} (Id {})".format(
                self.host_element.Name, eid_int(self.host_element.Id)
            )
            self.lbl_selection.Foreground = brush(CLR_TEXT)
        except Exception as ex:
            if "cancel" not in str(ex).lower():
                forms.alert("Selection failed: {}".format(ex), title=TOOL_NAME)
        finally:
            self.ShowDialog() if not self.IsVisible else None
            self.Show()
            self.Activate()

    def _read_params(self):
        rebar_size = parse_float_mm(self.box_rebar_size, "Rebar Size", 40.0)
        cover_mm = parse_float_mm(self.box_cover, "Cover", 50.0)
        hook_cover_mm = parse_float_mm(self.box_hook_cover, "Hook Cover", 90.0)
        hook_length_mm = parse_float_mm(self.box_hook_length, "Hook Length", 900.0)
        anchor_cover_mm = parse_float_mm(self.box_anchor_cover, "Anchor Cover", 300.0)
        anchor_length_mm = parse_float_mm(self.box_anchor_length, "Anchor Length", 1500.0)

        # mm -> feet (Revit internal units)
        mm_to_ft = 1.0 / 304.8
        return {
            "rebar_size_mm": rebar_size,
            "cover": cover_mm * mm_to_ft,
            "hook_cover": hook_cover_mm * mm_to_ft,
            "hook_length": hook_length_mm * mm_to_ft,
            "anchor_cover": anchor_cover_mm * mm_to_ft,
            "anchor_length": anchor_length_mm * mm_to_ft,
            "use_intersection_anchor": bool(self.chk_anchor_intersection.IsChecked),
        }

    def _compute_n(self):
        loops = list(self.top_face.EdgeLoops)
        outer = max(loops, key=lambda loop: sum(e.ApproximateLength for e in loop))
        perimeter = sum(e.ApproximateLength for e in outer)

        if self.rb_by_spacing.IsChecked:
            spacing_mm = parse_float_mm(self.box_spacing, "Spacing", 200.0)
            spacing_ft = spacing_mm / 304.8
            n = compute_bar_count(perimeter, spacing_ft)
            self.box_fixed_count.Text = str(n)
            return n
        else:
            try:
                n = int(float(self.box_fixed_count.Text.strip()))
                return max(3, n)
            except Exception:
                forms.alert("Fixed count must be a whole number.", title=TOOL_NAME)
                return 0

    def _compute_bars(self):
        if self.host_element is None or self.top_face is None or self.shaft_face is None:
            forms.alert("Pick a Crosshead solid first.", title=TOOL_NAME)
            return None, None

        n = self._compute_n()
        if n < 3:
            return None, None

        p = self._read_params()
        try:
            curve_lists, stats = build_all_bar_curves(
                self.top_face, self.shaft_face, self.skirt_faces, n,
                p["cover"], p["hook_cover"], p["hook_length"],
                p["anchor_cover"], p["anchor_length"], p["use_intersection_anchor"],
            )
        except Exception as ex:
            forms.alert("Geometry calculation failed: {}".format(ex), title=TOOL_NAME)
            return None, None

        return curve_lists, stats

    def _clear_preview(self):
        if not self.preview_ids:
            return
        t = Transaction(doc, "pyNBT - clear preview")
        t.Start()
        try:
            id_list = List[ElementId]()
            for eid in self.preview_ids:
                id_list.Add(eid)
            doc.Delete(id_list)
            t.Commit()
        except Exception:
            if t.HasStarted():
                t.RollBack()
        self.preview_ids = []

    def on_preview_click(self, sender, args):
        self._clear_preview()
        curve_lists, stats = self._compute_bars()
        if curve_lists is None:
            return

        t = Transaction(doc, "pyNBT - preview")
        t.Start()
        try:
            for curves, bar_normal in curve_lists:
                for c in curves:
                    origin = c.GetEndPoint(0)
                    # sketch plane just needs SOME plane containing this
                    # curve, doesn't need to match the bar's real normal -
                    # fall back to Y if the curve happens to run parallel
                    # to bar_normal itself.
                    plane_normal = bar_normal.CrossProduct(c.Direction) \
                        if hasattr(c, "Direction") else XYZ.BasisY
                    if plane_normal.GetLength() < 1e-6:
                        plane_normal = XYZ.BasisY
                    plane = Plane.CreateByNormalAndOrigin(plane_normal.Normalize(), origin)
                    sketch = SketchPlane.Create(doc, plane)
                    mc = doc.Create.NewModelCurve(c, sketch)
                    self.preview_ids.append(mc.Id)
            t.Commit()
        except Exception as ex:
            if t.HasStarted():
                t.RollBack()
            forms.alert("Preview failed: {}".format(ex), title=TOOL_NAME)
            return

        uidoc.RefreshActiveView()
        self.lbl_stat_count.Text = str(stats["count"])
        self.lbl_stat_length.Text = "{:.0f} mm".format(stats["avg_length_mm"])
        note = ""
        if stats["skipped"]:
            note = " ({} bar(s) skipped - degenerate geometry)".format(stats["skipped"])
        self.lbl_summary.Text = (
            "Preview drawn in the active view as temporary lines.{}\n"
            "Check the shape/orientation, then click Create Rebar to "
            "commit real Rebar elements, or adjust parameters and "
            "Preview again.".format(note)
        )
        self.lbl_log.Text = "Preview OK - {} bars, total length {:.0f} mm".format(
            stats["count"], stats["total_length_mm"]
        )

    def on_create_click(self, sender, args):
        curve_lists, stats = self._compute_bars()
        if curve_lists is None:
            return

        p = self._read_params()
        bar_type = get_rebar_bar_type(p["rebar_size_mm"])
        if bar_type is None:
            forms.alert(
                "No RebarBarType found in this project - load/define one first.",
                title=TOOL_NAME,
            )
            return

        self._clear_preview()

        t = Transaction(doc, "pyNBT - {}".format(TOOL_NAME))
        t.Start()
        try:
            created_ids = create_real_rebar(self.host_element, curve_lists, bar_type)
            t.Commit()
        except Exception as ex:
            if t.HasStarted():
                t.RollBack()
            forms.alert("Create Rebar failed - nothing was changed. Error: {}".format(ex), title=TOOL_NAME)
            return

        self.lbl_log.Text = "Created {} of {} Rebar elements.".format(len(created_ids), stats["count"])
        self.lbl_summary.Text = (
            "Done - {} real Rebar elements were created and hosted on the "
            "picked Crosshead element.".format(len(created_ids))
        )
        forms.alert("Created {} Rebar elements.".format(len(created_ids)), title=TOOL_NAME)

    def on_reset_click(self, sender, args):
        self._clear_preview()
        self.host_element = None
        self.top_face = None
        self.shaft_face = None
        self.skirt_faces = None
        self.lbl_selection.Text = "No element selected."
        self.lbl_selection.Foreground = brush(CLR_MUTED)
        self.lbl_stat_count.Text = "0"
        self.lbl_stat_length.Text = "0 mm"
        self.lbl_log.Text = ""
        self.lbl_summary.Text = "Pick a Crosshead solid, adjust parameters, then click Preview."
        uidoc.RefreshActiveView()

    def on_close_click(self, sender, args):
        self.Close()

    def on_window_closed(self, sender, args):
        # Only removes temporary Preview lines (its own Transaction) -
        # real Rebar created via Create Rebar was already committed
        # independently and is never touched here.
        self._clear_preview()


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    window = CrossheadShearLinkWindow()
    window.ShowDialog()
