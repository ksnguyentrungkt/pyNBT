# -*- coding: utf-8 -*-
"""Standalone Revit-API logic for the Trim Rebar to Length tool.

No UI references here on purpose (see pyNBT build rules) -- this module only
reads/writes Revit API objects. The WPF window in script.py calls these
functions and only displays their results / error messages.
"""

import Autodesk.Revit.DB as DB
import Autodesk.Revit.DB.Structure as DBS

MM_PER_FT = 304.8
LEN_EPS_FT = 0.0005  # ~0.15mm tolerance for float length comparisons


def mm_to_ft(mm):
    return mm / MM_PER_FT


def ft_to_mm(ft):
    return ft * MM_PER_FT


def get_selected_rebars(uidoc, doc):
    """Return list of Rebar elements among the current Revit selection."""
    ids = uidoc.Selection.GetElementIds()
    bars = []
    for eid in ids:
        el = doc.GetElement(eid)
        if isinstance(el, DBS.Rebar):
            bars.append(el)
    return bars


def get_view_up_direction(view):
    """Read view.UpDirection; return None if unavailable (e.g. schedule view)."""
    try:
        d = view.UpDirection
        if d is not None and d.GetLength() > 0.0001:
            return d.Normalize()
    except Exception:
        pass
    return None


def get_ordered_centerline_curves(rebar):
    """Return ordered list of Curve (Start -> End), suppressing hooks/bend
    radius so segments are the same raw polyline CreateFromCurves expects.
    Raises ValueError with a clear message if the bar isn't a supported shape.
    """
    if rebar.NumberOfBarPositions != 1:
        raise ValueError(
            "Rebar set / array with multiple bar positions is not supported yet "
            "(only single bars). Skipped."
        )
    curves = None
    # Try a couple of MultiplanarOption / suppression combos, most permissive first.
    attempts = [
        (True, True, DBS.MultiplanarOption.IncludeAllMultiplanarCurves),
        (True, True, DBS.MultiplanarOption.IncludeOnlyPlanarCurves),
    ]
    last_err = None
    for suppress_hooks, suppress_bend, multiplanar in attempts:
        try:
            result = rebar.GetCenterlineCurves(
                False, suppress_hooks, suppress_bend, multiplanar, 0
            )
            if result and result.Count > 0:
                curves = list(result)
                break
        except Exception as ex:
            last_err = ex
            continue
    if not curves:
        raise ValueError(
            "Could not read a usable centerline for this bar ({}). Skipped.".format(
                str(last_err) if last_err else "unknown reason"
            )
        )

    # Verify continuity / ordering (curve[i].End ~= curve[i+1].Start); if reversed,
    # flip the whole list once so it reads Start -> End consistently.
    if len(curves) > 1:
        p_end0 = curves[0].GetEndPoint(1)
        p_start1 = curves[1].GetEndPoint(0)
        if p_end0.DistanceTo(p_start1) > 0.01:  # ~3mm, not touching -> maybe reversed
            p_end0_alt = curves[0].GetEndPoint(0)
            if p_end0_alt.DistanceTo(p_start1) <= 0.01:
                curves[0] = _reverse_curve(curves[0])
    return curves


def _reverse_curve(curve):
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)
    if isinstance(curve, DB.Line):
        return DB.Line.CreateBound(p1, p0)
    if isinstance(curve, DB.Arc):
        p_mid = curve.Evaluate(0.5, True)
        return DB.Arc.Create(p1, p0, p_mid)
    raise ValueError("Unsupported curve type: {}".format(type(curve)))


def total_length_ft(curves):
    return sum(c.Length for c in curves)


def resolve_keep_end(keep_top, curves, view_up_dir):
    """Decide which raw end ('start' or 'end') should be KEPT, based on which
    one sits higher on screen (view.UpDirection) at the moment Apply is
    clicked -- mirrors the screen-relative Left/Right mechanism used in the
    Crank Rebar tool (view.RightDirection), applied here to Up/Down instead.
    """
    p_start = curves[0].GetEndPoint(0)
    p_end = curves[-1].GetEndPoint(1)
    vec = p_end - p_start
    if vec.GetLength() < 0.0005:
        raise ValueError(
            "Bar start and end points coincide (closed shape?) -- cannot "
            "determine Top/Bottom for this bar."
        )
    dot = vec.Normalize().DotProduct(view_up_dir)
    if abs(dot) < 0.05:
        raise ValueError(
            "Cannot tell Top from Bottom in this view -- the bar runs nearly "
            "perpendicular to the screen's vertical direction. Switch to a "
            "Plan/Section/Elevation view where the bar's vertical extent is "
            "visible and try again."
        )
    end_is_top = dot > 0
    if keep_top:
        return "end" if end_is_top else "start"
    else:
        return "start" if end_is_top else "end"


def trim_curve_from_start(curve, length_ft):
    """New curve = the first length_ft of curve (same start point, moved end)."""
    total = curve.Length
    if length_ft >= total - LEN_EPS_FT:
        return curve
    t = length_ft / total
    p0 = curve.GetEndPoint(0)
    p_cut = curve.Evaluate(t, True)
    if isinstance(curve, DB.Line):
        return DB.Line.CreateBound(p0, p_cut)
    if isinstance(curve, DB.Arc):
        p_mid = curve.Evaluate(t / 2.0, True)
        return DB.Arc.Create(p0, p_cut, p_mid)
    raise ValueError("Unsupported curve type: {}".format(type(curve)))


def trim_curve_from_end(curve, length_ft):
    """New curve = the last length_ft of curve (same end point, moved start)."""
    total = curve.Length
    if length_ft >= total - LEN_EPS_FT:
        return curve
    t = 1.0 - (length_ft / total)
    p_cut = curve.Evaluate(t, True)
    p1 = curve.GetEndPoint(1)
    if isinstance(curve, DB.Line):
        return DB.Line.CreateBound(p_cut, p1)
    if isinstance(curve, DB.Arc):
        p_mid = curve.Evaluate(t + (1.0 - t) / 2.0, True)
        return DB.Arc.Create(p_cut, p1, p_mid)
    raise ValueError("Unsupported curve type: {}".format(type(curve)))


def compute_trimmed_curves(curves, keep_end, desired_len_ft):
    """Return a new ordered curve list (Start->End orientation preserved)
    representing exactly desired_len_ft of the original bar, measured from
    the kept end. Raises ValueError with a clear message on invalid input.
    """
    total = total_length_ft(curves)
    if desired_len_ft <= LEN_EPS_FT:
        raise ValueError("Desired length must be greater than zero.")
    if desired_len_ft >= total - LEN_EPS_FT:
        raise ValueError(
            "Desired length ({:.0f}mm) is not shorter than the bar's current "
            "length ({:.0f}mm) -- nothing to trim.".format(
                ft_to_mm(desired_len_ft), ft_to_mm(total)
            )
        )

    if keep_end == "start":
        remaining = desired_len_ft
        new_curves = []
        for c in curves:
            clen = c.Length
            if remaining >= clen - LEN_EPS_FT:
                new_curves.append(c)
                remaining -= clen
            else:
                new_curves.append(trim_curve_from_start(c, remaining))
                remaining = 0.0
                break
        return new_curves
    else:
        remaining = desired_len_ft
        rev = []
        for c in reversed(curves):
            clen = c.Length
            if remaining >= clen - LEN_EPS_FT:
                rev.append(c)
                remaining -= clen
            else:
                rev.append(trim_curve_from_end(c, remaining))
                remaining = 0.0
                break
        return list(reversed(rev))


def compute_plane_normal(curves):
    """Normal of the plane containing the (original, untrimmed) centerline --
    works for a single straight bar as well as a multi-segment bent bar, as
    long as the whole shape is planar (required by Rebar.CreateFromCurves
    anyway, since the original bar was built that way)."""
    pts = [curves[0].GetEndPoint(0)]
    for c in curves:
        pts.append(c.GetEndPoint(1))
    p0 = pts[0]
    for i in range(1, len(pts) - 1):
        for j in range(i + 1, len(pts)):
            v1 = pts[i] - p0
            v2 = pts[j] - p0
            if v1.GetLength() < 0.0005 or v2.GetLength() < 0.0005:
                continue
            n = v1.CrossProduct(v2)
            if n.GetLength() > 0.0005:
                return n.Normalize()
    # Degenerate (perfectly straight single bar): fall back to a normal
    # perpendicular to the bar direction, same convention as Crank Rebar.
    bar_dir = (pts[-1] - pts[0]).Normalize()
    n = bar_dir.CrossProduct(DB.XYZ.BasisZ)
    if n.GetLength() < 0.0005:
        n = bar_dir.CrossProduct(DB.XYZ.BasisX)
    return n.Normalize()


def get_end_hook(rebar, end_index):
    """Return (hook_type_id, hook_orientation) for end_index (0=start,1=end).
    hook_type_id is None when there is no hook at that end, but
    hook_orientation is ALWAYS a real RebarHookOrientation value (never None):
    Rebar.CreateFromCurves takes startHookOrientation/endHookOrientation as a
    non-nullable .NET enum parameter, so it must receive a concrete value even
    when the hook TYPE itself is null (Revit simply ignores the orientation in
    that case). Passing None there is what produced the IronPython error
    "expected RebarHookOrientation, got NoneType"."""
    try:
        hook_id = rebar.GetHookTypeId(end_index)
    except Exception:
        return None, DBS.RebarHookOrientation.Left
    if hook_id is None or hook_id == DB.ElementId.InvalidElementId:
        return None, DBS.RebarHookOrientation.Left
    try:
        orientation = rebar.GetHookOrientation(end_index)
    except Exception:
        orientation = DBS.RebarHookOrientation.Left
    return hook_id, orientation


def process_one_rebar(doc, rebar, keep_top, desired_len_ft, view_up_dir):
    """Trim a single Rebar element in place (delete + recreate shorter).
    Returns (success, message). Does NOT start/commit the OUTER Transaction
    -- caller wraps the whole batch in one Transaction. All computation and
    validation happens BEFORE doc.Delete(), so a bar that fails to compute
    is left completely untouched. The delete + recreate itself is wrapped in
    its own SubTransaction: if Rebar.CreateFromCurves fails (invalid host,
    non-planar geometry, curve continuity, etc.) AFTER the original was
    already deleted, that SubTransaction is rolled back so the original bar
    is restored instead of being permanently lost.
    """
    try:
        curves = get_ordered_centerline_curves(rebar)
        keep_end = resolve_keep_end(keep_top, curves, view_up_dir)
        new_curves = compute_trimmed_curves(curves, keep_end, desired_len_ft)
        norm = compute_plane_normal(curves)

        hook0_id, hook0_orient = get_end_hook(rebar, 0)
        hook1_id, hook1_orient = get_end_hook(rebar, 1)

        if keep_end == "start":
            start_hook_id, start_orient = hook0_id, hook0_orient
            end_hook_id, end_orient = None, DBS.RebarHookOrientation.Left
        else:
            start_hook_id, start_orient = None, DBS.RebarHookOrientation.Left
            end_hook_id, end_orient = hook1_id, hook1_orient

        start_hook_type = doc.GetElement(start_hook_id) if start_hook_id else None
        end_hook_type = doc.GetElement(end_hook_id) if end_hook_id else None

        bar_type = doc.GetElement(rebar.GetTypeId())
        host = doc.GetElement(rebar.GetHostId())
        if host is None:
            raise ValueError(
                "Could not resolve this bar's host element (GetHostId() -- "
                "Rebar.CreateFromCurves requires a valid host). Skipped, "
                "original bar left untouched."
            )

        from System.Collections.Generic import List
        curve_list = List[DB.Curve](new_curves)

        rebar_id = rebar.Id
        sub = DB.SubTransaction(doc)
        sub.Start()
        try:
            doc.Delete(rebar_id)
            new_rebar = DBS.Rebar.CreateFromCurves(
                doc,
                DBS.RebarStyle.Standard,
                bar_type,
                start_hook_type,
                end_hook_type,
                host,
                norm,
                curve_list,
                start_orient,
                end_orient,
                False,
                True,
            )
            if new_rebar is None:
                raise ValueError(
                    "Rebar.CreateFromCurves returned no element -- geometry "
                    "was likely rejected by Revit (host/plane/curve issue)."
                )
            sub.Commit()
        except Exception:
            if sub.HasStarted() and not sub.HasEnded():
                sub.RollBack()
            raise

        kept_label = "Top" if keep_top else "Bottom"
        return True, "OK (kept {}, new length {:.0f}mm)".format(
            kept_label, ft_to_mm(desired_len_ft)
        )
    except Exception as ex:
        return False, str(ex)
