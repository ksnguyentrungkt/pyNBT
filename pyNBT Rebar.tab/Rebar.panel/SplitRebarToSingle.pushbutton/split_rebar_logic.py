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

v1.0.2 (2026-09-17) - Two more safety nets, added after another "shape
lost" report on the same curved-wall scenario that could not be
re-diagnosed live (Revit connection was down at the time), so instead
of trusting the v1.0.1 check alone, the tool now VERIFIES its own
result instead of only pre-checking the input:
  1. _shape_is_reliably_planar now also compares each pair of curves
     one-by-one (not just the two total lengths) - two genuinely
     different curve sets could coincidentally sum to the same total
     length, which would have slipped past the v1.0.1 check.
  2. After Rebar.CreateFromCurves actually builds a new bar, its real
     built length is measured back (GetCenterlineCurves again, this
     time on the NEW bar) and compared to the original bar position's
     true length. Any mismatch deletes the just-built bar immediately
     and fails loudly, instead of leaving a silently-wrong bar behind.
  3. convert_rebar_to_singles now also refuses to finish a Set/Free
     Form conversion if two or more of the newly-built bars start at
     the same point - by definition every bar position is a physically
     distinct bar, so an exact-duplicate start point is a hard proof
     that Revit's curve extraction returned the same wrong geometry
     more than once (the exact symptom from the very first incident).

v1.0.3 (2026-09-17) - TRUE ROOT CAUSE FOUND AND FIXED, live on NBT's
actual curved-wall Free Form Rebar (Id 27602943, host Wall Id 27602796,
25 bar positions). The v1.0.2 post-build length check (#2 above) fired
on EVERY position: expected 49.7405 ft (confirmed correct - matches
Revit's own GetCenterlineCurves on the ORIGINAL bar exactly), but the
just-built new bar always came back 68.5290 ft - the exact same wrong
length reported in the very first v1.0.0 incident, on a totally
different rebar, which is what finally proved this was never about
curve extraction (IncludeOnlyPlanarCurves) at all: the extracted input
curves were correct and provably planar the whole time. The bug is in
Rebar.CreateFromCurves(...) itself: the second-to-last argument,
`useExistingShapeIfPossible`, was passed as True, which lets Revit
snap the new bar onto an existing RebarShape family definition that
loosely resembles the given curves (same segment count/pattern) INSTEAD
OF building a bespoke shape sized to match them - and it was matching
onto the wrong one every time. Fixed by passing `useExistingShapeIfPossible
= False` - verified live immediately after: built length matched
expected exactly (49.7405 ft), and a full real run on that same 25-
position bar produced 25 correct, distinct, verified-matching single
bars with 0 new warnings. The v1.0.2 safety nets (#1-3 above) are kept
as defense-in-depth - they are what caught this live, before any wrong
bar could be left in NBT's model - but the actual fix is this one
argument.

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
    trusting either one. Returns (is_reliable, all_curves_total_length) -
    the length is returned even on failure (0.0 if it could not be
    computed) so the caller can reuse it as the "expected length" for the
    post-build verification in _build_single_bar.

    Why this exists (see v1.0.1 note in this module's docstring): for a
    genuinely non-planar/warped bar (e.g. Free Form Rebar following a
    curved wall surface), GetCenterlineCurves(..., IncludeOnlyPlanarCurves,
    ...) does NOT raise - it silently returns a wrong, flattened/looped
    approximation. Checking planarity on THAT already-corrupted data (what
    v1.0.0 did) can never catch the problem. Comparing curve count and
    total length between the two extraction modes is a reliable proxy:
    when Revit did not need to discard/flatten anything, both modes agree;
    when it did, they disagree (different curve count and/or a
    wrong/inflated total length).

    v1.0.2: also compares curve length pair-by-pair (in extraction order),
    not just the two grand totals - two genuinely different curve sets
    could coincidentally sum to the same total length, which the v1.0.1
    total-only comparison would have missed."""
    try:
        all_curves = list(rebar_ref.GetCenterlineCurves(
            True, False, False, MultiplanarOption.IncludeAllMultiplanarCurves, bar_position
        ))
        planar_curves = list(rebar_ref.GetCenterlineCurves(
            True, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, bar_position
        ))
    except Exception:
        return False, 0.0
    if not all_curves or not planar_curves:
        return False, 0.0
    all_len = sum(c.Length for c in all_curves)
    if len(all_curves) != len(planar_curves):
        return False, all_len
    planar_len = sum(c.Length for c in planar_curves)
    if abs(all_len - planar_len) > MULTIPLANAR_LEN_TOL_FT:
        return False, all_len
    for ac, pc in zip(all_curves, planar_curves):
        if abs(ac.Length - pc.Length) > MULTIPLANAR_LEN_TOL_FT:
            return False, all_len
    return True, all_len


def _bar_start_point(rebar_obj):
    """Real-world start point of a (single-position) Rebar, used only for
    the v1.0.2 duplicate-shape safety net in convert_rebar_to_singles."""
    try:
        curves = list(rebar_obj.GetCenterlineCurves(
            True, False, False, MultiplanarOption.IncludeAllMultiplanarCurves, 0
        ))
        if curves:
            return curves[0].GetEndPoint(0)
    except Exception:
        pass
    return None


def _build_single_bar(doc, style, bar_type, host, rebar_ref, bar_position):
    """Build ONE replacement single Rebar for `bar_position` of `rebar_ref`.
    Raises on any failure (caller is responsible for catching and cleaning
    up). Hooks are kept baked into the curve chain (suppressHooks=False) so
    the new bar's shape/length is guaranteed identical to the original -
    see the trade-off note in this module's docstring."""
    reliable, expected_len = _shape_is_reliably_planar(rebar_ref, bar_position)
    if not reliable:
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

    new_bar = Rebar.CreateFromCurves(
        doc, style, bar_type, None, None, host, normal, chain,
        RebarHookOrientation.Left, RebarHookOrientation.Left,
        False, True,
        # useExistingShapeIfPossible=False (v1.0.3 fix - see docstring):
        # True let Revit snap onto an existing, WRONG RebarShape family
        # definition instead of building a bespoke shape from `chain`.
        # createNewShape=True is unchanged.
    )

    # v1.0.2: end-to-end verification. Everything above is a PRE-check on
    # the original's geometry; this instead measures what Revit actually
    # built and compares it to the original bar position's true length
    # (from IncludeAllMultiplanarCurves - reliable even for a genuinely
    # warped bar, since it never flattens). If they disagree, the new bar
    # does not really match the original, whatever the pre-checks said -
    # delete it immediately and fail loudly rather than leave a silently
    # wrong shape in the model.
    try:
        built_curves = list(new_bar.GetCenterlineCurves(
            True, False, False, MultiplanarOption.IncludeAllMultiplanarCurves, 0
        ))
        built_len = sum(c.Length for c in built_curves) if built_curves else None
    except Exception:
        built_len = None

    if built_len is None or abs(built_len - expected_len) > MULTIPLANAR_LEN_TOL_FT:
        try:
            doc.Delete(new_bar.Id)
        except Exception:
            pass
        got_str = "N/A" if built_len is None else "{0:.4f} ft".format(built_len)
        raise ValueError(
            "Built bar length does not match the original bar's true shape "
            "(expected {0:.4f} ft, got {1}) - rolled back instead of "
            "leaving a wrong shape in the model.".format(expected_len, got_str)
        )

    return new_bar


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

    # v1.0.2: duplicate-shape safety net. Every bar position is, by
    # definition, a physically distinct bar - two positions can share the
    # same shape (identical stirrups, say) but never the same real-world
    # START POINT. If any two of the bars just built DO start at the same
    # point, that is a hard proof Revit's curve extraction returned the
    # same (wrong) geometry more than once - exactly the symptom from the
    # very first incident (25 positions, all identical). Refuse to finish
    # this conversion at all rather than risk leaving duplicated/wrong
    # bars in the model.
    if len(new_rebars) > 1:
        starts = [_bar_start_point(rb) for rb in new_rebars]
        for i in range(len(starts)):
            if starts[i] is None:
                continue
            for j in range(i + 1, len(starts)):
                if starts[j] is None:
                    continue
                if starts[i].DistanceTo(starts[j]) < TOL:
                    for created in new_rebars:
                        try:
                            doc.Delete(created.Id)
                        except Exception:
                            pass
                    return {
                        "kind": "error",
                        "new_count": 0,
                        "message": (
                            "Two or more rebuilt bars started at the same "
                            "point - Revit's geometry extraction likely "
                            "returned the same wrong shape for multiple bar "
                            "positions. Original left untouched."
                        ),
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
