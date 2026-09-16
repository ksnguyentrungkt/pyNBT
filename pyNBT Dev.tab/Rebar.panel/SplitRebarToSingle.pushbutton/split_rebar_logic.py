# -*- coding: utf-8 -*-
"""
Split Rebar to Single - standalone Revit-API logic (no UI references here).

Converts a Rebar Set (a Rebar element with more than one bar position, e.g.
Layout Rule = Fixed Number / Maximum Spacing / ...) or a true Free Form
Rebar (Shape = Free Form) into 1+ plain Rebar elements, each with a single
bar (Layout = Single), while keeping the EXACT original shape and
dimensions of every bar (every straight segment length, every bend radius,
every hook curl - see build_replacement_curves below for how this is
guaranteed).

Design (locked with NBT, 2026-09-16):
  - Input = current Revit selection only (no whole-model scan).
  - Rebar Set (NumberOfBarPositions > 1): read each real bar position's
    true 3D centerline curves (Rebar.GetCenterlineCurves), rebuild it as
    its own single Rebar, copy the Partition value, then delete the
    original Set - only after EVERY position for that Set was rebuilt
    successfully (see convert_rebar_to_singles).
  - Free Form Rebar: same rebuild, from bar position 0 (Free Form bars are
    a single physical bar in practice). If the shape is genuinely 3D/
    warped (not flat), a standard shape-driven Rebar cannot represent it -
    that bar is left completely untouched and reported as an error/skip,
    never forced into a wrong flat shape.
  - A Rebar already Layout = Single (and not Free Form) is left alone.
  - One bad bar/position never aborts the whole run: if ANY position of a
    given Set/Free Form fails, ONLY that one original element is skipped
    (any partial replacement bars already created for it are deleted again
    so the model never ends up with duplicates); every other selected
    element still gets processed.

Known trade-off (flagged for NBT): the original's hooks are kept baked
into the replacement bar's curve chain (suppressHooks=False) instead of
being re-assigned as a real Start/End "Hook Type" parameter on the new
bar. This guarantees the new bar's shape and total length are 100%
identical to the original (the safest way to satisfy "keep the exact
original shape/dimensions"), but the new bar's Hook Type parameter will
show "None" even though the hook curl is still there, physically, as part
of the shape. Flag if any schedule relies on the Hook Type parameter
itself rather than on the physical shape/length.

v1.0.1 (2026-09-16) - CRITICAL FIX after a live test on a real Free Form
Rebar following a curved/warped wall surface: Revit's own
GetCenterlineCurves(..., MultiplanarOption.IncludeOnlyPlanarCurves, ...)
does NOT raise an exception for a genuinely non-planar bar - it silently
returns a wrong, flattened/looped approximation instead (confirmed live:
a 25-position warped set came back with every position reporting the
SAME wrong 3-segment, 3x-too-long shape). v1.0.0's own planarity check
ran on this already-corrupted data and could not catch it, so the tool
deleted the real bar and rebuilt 25 wrong ones in its place. Fixed by
cross-checking Revit's OWN "planar-only" extraction against its "all
multiplanar curves" extraction for the SAME bar position BEFORE trusting
either one (see _shape_is_reliably_planar) - only proceed when the two
agree; otherwise treat the shape as genuinely 3D/warped and leave it
completely untouched, exactly like any other unsupported shape.

Keep this file free of WPF/System.Windows imports (pyNBT DQT-pattern:
logic functions separate from the UI class).
"""

from Autodesk.Revit.DB import XYZ, Arc, Element, BuiltInParameter, ElementId
from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation, MultiplanarOption

TOL = 0.0008  # feet, ~0.25mm - point-coincidence / planarity tolerance


# ---------------------------------------------------------------------------
# ElementId / element-name compat helpers
# (see pyNBT doc "pynbt-elementtype-name-read-bug.md" for why Element.Name
# .GetValue is used instead of a plain .Name read)
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


def get_element_name(el):
    try:
        return Element.Name.GetValue(el)
    except Exception:
        try:
            return el.Name
        except Exception:
            return "(unnamed)"


# ---------------------------------------------------------------------------
# Partition read/write - looked up BY DISPLAY NAME first (Wohhup templates:
# "Partition" is a project/shared parameter, not Revit's built-in
# worksharing parameter of the same name) - matches every other pyNBT rebar
# tool (Select Partition, Rebar Shape Export, Rebar Segment).
# ---------------------------------------------------------------------------

PARTITION_PARAM_NAME = "Partition"


def get_partition_str(element):
    try:
        param = element.LookupParameter(PARTITION_PARAM_NAME)
        if param is None:
            param = element.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
        if param is None or not param.HasValue:
            return None
        val = param.AsValueString()
        if not val:
            val = param.AsString()
        return val.strip() if val and val.strip() else None
    except Exception:
        return None


def set_partition(element, value):
    if not value:
        return False
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
# Rebar classification
# ---------------------------------------------------------------------------

def is_free_form(rebar):
    try:
        return bool(rebar.IsRebarFreeForm())
    except Exception:
        return False


def get_bar_position_count(rebar):
    try:
        n = rebar.NumberOfBarPositions
        return int(n) if n and n > 0 else 1
    except Exception:
        return 1


def bar_exists_at_position(rebar, index):
    try:
        return bool(rebar.DoesBarExistAtPosition(index))
    except Exception:
        return True


def get_rebar_style(rebar):
    """Rebar has no direct `.Style` property (confirmed via live reflection
    on 2026-09-16 - it does not exist on the Rebar class at all). Style is
    only independently readable via the Free Form accessor; a shape-driven
    bar's style is not exposed the same way, so RebarStyle.Standard is
    used there - correct for the overwhelming majority of shape-driven
    sets. Flag if this ever needs to distinguish StirrupTie shape-driven
    sets."""
    try:
        if rebar.IsRebarFreeForm():
            accessor = rebar.GetFreeFormAccessor()
            return accessor.RebarStyle
    except Exception:
        pass
    return RebarStyle.Standard


def classify_rebar(rebar):
    """Return 'freeform', 'set', or 'single' for a Rebar element -
    'single' means already Layout = Single AND not Free Form, i.e.
    nothing to do."""
    if is_free_form(rebar):
        return "freeform"
    if get_bar_position_count(rebar) > 1:
        return "set"
    return "single"


# ---------------------------------------------------------------------------
# Curve-chain helpers (same approach as pyNBT "Rebar Shape Export": order a
# possibly out-of-order/reversed curve collection into one connected chain,
# then fit a flat plane through its points).
# ---------------------------------------------------------------------------

def chain_curves(curve_array):
    """Chain a (possibly out-of-order / reversed) collection of curves
    (Line and/or Arc) into one ordered, connected list, flipping any curve
    whose direction runs against the chain via Curve.CreateReversed()."""
    curves = list(curve_array)
    if not curves:
        return []
    chain = [curves[0]]
    used = [False] * len(curves)
    used[0] = True
    changed = True
    while changed:
        changed = False
        head = chain[0].GetEndPoint(0)
        tail = chain[-1].GetEndPoint(1)
        for i, c in enumerate(curves):
            if used[i]:
                continue
            p0, p1 = c.GetEndPoint(0), c.GetEndPoint(1)
            if p0.DistanceTo(tail) < TOL:
                chain.append(c)
                used[i] = True
                changed = True
            elif p1.DistanceTo(tail) < TOL:
                chain.append(c.CreateReversed())
                used[i] = True
                changed = True
            elif p1.DistanceTo(head) < TOL:
                chain.insert(0, c)
                used[i] = True
                changed = True
            elif p0.DistanceTo(head) < TOL:
                chain.insert(0, c.CreateReversed())
                used[i] = True
                changed = True
    return chain


def chain_reference_points(chain):
    """Flat list of points along the chain (endpoints, plus each Arc's
    midpoint) - used to fit the shape's plane and to check flatness."""
    points = []
    for i, c in enumerate(chain):
        if i == 0:
            points.append(c.GetEndPoint(0))
        if isinstance(c, Arc):
            points.append(c.Evaluate(0.5, True))
        points.append(c.GetEndPoint(1))
    return points


def compute_normal_from_points(points):
    """Fit a plane normal through `points`. Returns None if every point is
    (near-)coincident (degenerate shape). For a straight 2-point bar (no
    third, non-collinear point to fit a real bend plane with), falls back
    to any vector not parallel to the bar - Rebar.CreateFromCurves only
    needs a normal that is not parallel to the curve, it does not need to
    match a "real" bend plane for a straight bar."""
    origin = points[0]
    x_axis = None
    for p in points[1:]:
        vec = p - origin
        if vec.GetLength() > TOL:
            x_axis = vec.Normalize()
            break
    if x_axis is None:
        return None

    normal = None
    for p in points[2:]:
        vec = p - origin
        cross = x_axis.CrossProduct(vec)
        if cross.GetLength() > TOL:
            normal = cross.Normalize()
            break

    if normal is None:
        fallback_ref = XYZ.BasisZ if abs(x_axis.Z) < 0.9 else XYZ.BasisX
        normal = x_axis.CrossProduct(fallback_ref)
        if normal.GetLength() < TOL:
            normal = x_axis.CrossProduct(XYZ.BasisX)
        normal = normal.Normalize()

    return normal


def is_planar_within_tolerance(points, origin, normal, tol_ft):
    """True if every point in `points` lies within `tol_ft` of the plane
    (origin, normal). Used to detect a genuinely 3D/warped Free Form shape
    that a standard (flat) Rebar shape cannot represent."""
    for p in points:
        vec = p - origin
        if abs(vec.DotProduct(normal)) > tol_ft:
            return False
    return True


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------

MULTIPLANAR_LEN_TOL_FT = 0.0033  # ~1mm - agreement tolerance, see check below


def _shape_is_reliably_planar(rebar_ref, bar_position):
    """Cross-check Revit's own "planar-only" curve extraction against its
    "all multiplanar curves" extraction for the SAME bar position, before
    trusting either one.

    Why this exists (see v1.0.1 note in this module's docstring): for a
    genuinely non-planar/warped bar (e.g. Free Form Rebar following a
    curved wall surface), GetCenterlineCurves(..., IncludeOnlyPlanarCurves,
    ...) does NOT raise - it silently returns a wrong, flattened/looped
    approximation. Checking planarity on THAT already-corrupted data (what
    v1.0.0 did) can never catch the problem. Comparing curve count and
    total length between the two extraction modes is a reliable proxy:
    when Revit did not need to discard/flatten anything, both modes agree;
    when it did, they disagree (different curve count and/or a
    wrong/inflated total length)."""
    try:
        all_curves = list(rebar_ref.GetCenterlineCurves(
            True, False, False, MultiplanarOption.IncludeAllMultiplanarCurves, bar_position
        ))
        planar_curves = list(rebar_ref.GetCenterlineCurves(
            True, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, bar_position
        ))
    except Exception:
        return False
    if not all_curves or not planar_curves:
        return False
    if len(all_curves) != len(planar_curves):
        return False
    all_len = sum(c.Length for c in all_curves)
    planar_len = sum(c.Length for c in planar_curves)
    return abs(all_len - planar_len) <= MULTIPLANAR_LEN_TOL_FT


def _build_single_bar(doc, style, bar_type, host, rebar_ref, bar_position):
    """Build ONE replacement single Rebar for `bar_position` of `rebar_ref`.
    Raises on any failure (caller is responsible for catching and cleaning
    up). Hooks are kept baked into the curve chain (suppressHooks=False) so
    the new bar's shape/length is guaranteed identical to the original -
    see the trade-off note in this module's docstring."""
    if not _shape_is_reliably_planar(rebar_ref, bar_position):
        raise ValueError(
            "Shape follows a curved/warped host surface (genuinely "
            "multiplanar) - Revit cannot represent it as a flat standard "
            "shape without silently distorting it, so this bar was left "
            "untouched."
        )

    raw_curves = list(rebar_ref.GetCenterlineCurves(
        True, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, bar_position
    ))
    if not raw_curves:
        raise ValueError("No centerline geometry returned for this bar position.")

    chain = chain_curves(raw_curves)
    points = chain_reference_points(chain)
    normal = compute_normal_from_points(points)
    if normal is None:
        raise ValueError("Bar shape is degenerate (start/end points coincide).")
    if not is_planar_within_tolerance(points, points[0], normal, TOL):
        raise ValueError(
            "Shape is not flat (genuinely 3D/warped) - a standard single-bar "
            "Rebar cannot represent this shape."
        )

    return Rebar.CreateFromCurves(
        doc, style, bar_type, None, None, host, normal, chain,
        RebarHookOrientation.Left, RebarHookOrientation.Left,
        True, True,
    )


def convert_rebar_to_singles(doc, rebar):
    """Convert ONE selected Rebar element (Set or Free Form) into 1+ new
    single Rebar elements with identical shape/dimensions, then delete the
    original. Must run inside an already-started Transaction.

    Returns a dict:
      {'kind': 'set' | 'freeform' | 'single' | 'error',
       'new_count': int,
       'message': str or None}

    'single' -> already Layout = Single and not Free Form - left untouched.
    'error'  -> could not convert; original left completely untouched
                (any partial replacement bars are deleted again);
                `message` explains why.
    """
    kind = classify_rebar(rebar)
    if kind == "single":
        return {"kind": "single", "new_count": 0, "message": None}

    bar_type = doc.GetElement(rebar.GetTypeId())
    host_id = rebar.GetHostId()
    host = doc.GetElement(host_id) if is_valid_eid(host_id) else None
    style = get_rebar_style(rebar)
    partition_value = get_partition_str(rebar)

    position_count = get_bar_position_count(rebar)
    new_rebars = []

    for i in range(position_count):
        if not bar_exists_at_position(rebar, i):
            continue
        try:
            new_rebar = _build_single_bar(doc, style, bar_type, host, rebar, i)
            if partition_value:
                set_partition(new_rebar, partition_value)
            new_rebars.append(new_rebar)
        except Exception as ex:
            for created in new_rebars:
                try:
                    doc.Delete(created.Id)
                except Exception:
                    pass
            return {"kind": "error", "new_count": 0, "message": str(ex)}

    if not new_rebars:
        return {
            "kind": "error",
            "new_count": 0,
            "message": "No bar position produced valid geometry.",
        }

    try:
        doc.Delete(rebar.Id)
    except Exception as ex:
        # Could not remove the original - undo the replacements too, so the
        # model never ends up with both the original AND the new bars
        # overlapping in the same place.
        for created in new_rebars:
            try:
                doc.Delete(created.Id)
            except Exception:
                pass
        return {
            "kind": "error",
            "new_count": 0,
            "message": "Could not delete the original bar: {}".format(ex),
        }

    return {"kind": kind, "new_count": len(new_rebars), "message": None}
