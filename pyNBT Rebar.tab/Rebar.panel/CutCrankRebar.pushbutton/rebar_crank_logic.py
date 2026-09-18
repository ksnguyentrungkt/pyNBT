# -*- coding: utf-8 -*-
"""
Cut & Crank Rebar - standalone Revit-API logic (no UI references here).

Pure/near-pure functions that read rebar geometry, validate it, and build
the two new Rebar elements that replace a cut/cranked straight bar (v1.4
design, corrected directly with NBT -- see project doc
"cut-crank-rebar-tool.md" section "v1.3 -> v1.4"):

    original bar   [hookS]---------------------------------------[hookE]   (deleted)
                          (phuong 1 = original elevation, unchanged)

    Bar A (4 "segments" -- hookS, straight, diagonal, lap -- but only 3
    Curves: the hook is a Rebar hook parameter, not a Curve):
        [hookS]------P1          (straight, unchanged, ORIGINAL start hook kept, on phuong 1)
                          \\
                           \\___(crank: N*D horiz / 1*D vert)___P2----(lap, L mm)----P3
                                                        (phuong 2)          (free/connecting end, no hook)

    Bar B (2 "segments" -- straight, hookE -- 1 Curve + 1 hook), on PHUONG 1
    (the ORIGINAL, un-shifted elevation -- corrected in v1.4; v1.3 wrongly
    put it on phuong 2, coincident with Bar A's lap segment):
                                                                P2B---------------------P4[hookE]
        (P2B sits at the SAME axial position as P2 -- directly under/over it,
         on phuong 1 -- so Bar B's straight run overlaps Bar A's lap segment
         axially for the lap zone, offset by exactly 1xD: the correct
         cranked-bar lap-splice detail, per NBT: "thanh thep B phai theo
         phuong 1 ban dau moi dung". P4 is positioned so that Bar A's
         straight-run-to-P2 length + Bar B's P2B-to-P4 length equals the
         ORIGINAL bar's length. Bar B keeps the ORIGINAL bar's end hook, if
         any, at P4.)

The user's PickPoint click lands ON the original bar's centerline (phuong 1)
-- but as of v1.4 that click point now marks the AXIAL position of **P2**
(end of the diagonal / start of the lap segment), NOT P1 as in v1.3
(confirmed with NBT via diagram, 2026-08-22). P1 is computed by the tool,
going back N*D from the click position along -bar_dir.

All internal lengths are Revit internal units (feet) unless a function name
ends in _mm. Keep this file free of WPF/System.Windows imports so it can be
unit-reasoned-about independently of the UI (per pyNBT DQT-pattern: logic
functions separate from the UI class).
"""

import math

import System

from Autodesk.Revit.DB import (
    XYZ, Line, Element, BuiltInParameter, UnitUtils, Plane, SketchPlane
)
from Autodesk.Revit.DB.Structure import (
    Rebar, RebarStyle, RebarHookOrientation, MultiplanarOption
)
from Autodesk.Revit.DB.ExtensibleStorage import (
    Schema, SchemaBuilder, AccessLevel, Entity
)

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
# ElementId compat helpers (Revit 2024 uses .Value on newer API surfaces in
# some places but .IntegerValue is still the safe cross-version read).
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
# Work plane fallback -- Selection.PickPoint() can fail with "No work plane
# set in current view" (Autodesk.Revit.Exceptions...) in Section/Elevation/3D
# views that don't already have an active SketchPlane -- a known Revit API
# quirk, unrelated to how NBT clicks. Plan views normally already have one,
# so this is only needed as a fallback when PickPoint actually fails.
# ---------------------------------------------------------------------------

def ensure_work_plane(doc, view, origin, normal):
    """Set `view`'s active SketchPlane to a plane through `origin` with the
    given `normal` (use the bar's own vertical plane -- same `norm` used for
    Rebar.CreateFromCurves). Must be called inside an already-started
    Transaction. Returns the new SketchPlane."""
    plane = Plane.CreateByNormalAndOrigin(normal, origin)
    sketch_plane = SketchPlane.Create(doc, plane)
    view.SketchPlane = sketch_plane
    return sketch_plane


# ---------------------------------------------------------------------------
# Hook preservation -- v1.3: BOTH resulting bars can keep an original hook.
# Bar A keeps the original bar's START hook (end_index 0) at its true,
# unmoved start point. Bar B keeps the original bar's END hook (end_index 1)
# at its far point P4. Neither the cut point, P2, nor P3 (the lap/connecting
# interface between A and B) ever gets a hook -- those are new interfaces,
# not original bar ends.
# ---------------------------------------------------------------------------

def get_end_hook(doc, rebar, end_index):
    """Read the hook type + orientation at one end of `rebar` (0 = start,
    1 = end, matching Rebar.GetHookTypeId's own convention). Returns
    (RebarHookType or None, RebarHookOrientation). Must be called BEFORE the
    rebar is deleted."""
    try:
        hook_id = rebar.GetHookTypeId(end_index)
    except Exception:
        hook_id = None
    hook_type = doc.GetElement(hook_id) if is_valid_eid(hook_id) else None

    try:
        orientation = rebar.GetHookOrientation(end_index)
    except Exception:
        orientation = RebarHookOrientation.Left

    return hook_type, orientation


# ---------------------------------------------------------------------------
# Safe ElementType name read (IronPython .Name-on-ElementType bug -- see
# pyNBT doc "pynbt-elementtype-name-read-bug.md": reading .Name directly on
# an ElementType such as RebarBarType can throw MissingMemberException).
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
# Reading the selected bar
# ---------------------------------------------------------------------------

def get_bar_type(rebar, doc):
    return doc.GetElement(rebar.GetTypeId())


def get_diameter_ft(rebar, doc):
    """Bar diameter in feet, read via the REBAR_BAR_DIAMETER parameter on the
    RebarBarType (NOT the .BarDiameter property -- confirmed elsewhere in
    this project that .BarDiameter can silently return 0)."""
    bar_type = get_bar_type(rebar, doc)
    if bar_type is None:
        return None
    p = bar_type.get_Parameter(BuiltInParameter.REBAR_BAR_DIAMETER)
    if p is None:
        return None
    val = p.AsDouble()
    return val if val and val > 0 else None


def get_bar_curves(rebar):
    """v1.8 -- return the FULL ordered list of the bar's centerline Line
    segments (index 0's own start point = the rebar's true start,
    GetHookTypeId(0)/GetHookOrientation(0)'s end; the last curve's own end
    point = the rebar's true end, index 1's end).

    Generalizes get_single_centerline (v1-v1.7.4, which required exactly
    one straight segment) so bars that are already bent (multiple body
    segments) can be cut/cranked too -- NBT asked for this (2026-08 image
    of a vertical bar with an existing bend near the top): "toi dang co
    thanh thep nhu hinh ... cat tai diem toi chi mui ten mau do ... co the
    update tool dc k", and confirmed via AskUserQuestion he wants the
    general case ("Tong quat: moi thanh nhieu doan, moi huong"), not just
    the one shape in his screenshot.

    Still raises ValueError (safe to show directly to NBT) for a
    multi-bar set (Layout Rule != Single) or any curved (Arc) segment --
    arcs are not supported."""
    try:
        n_positions = rebar.NumberOfBarPositions
    except Exception:
        n_positions = 1
    if n_positions != 1:
        raise ValueError(
            "This tool only supports a single bar (Layout Rule = Single), "
            "not a multi-bar set. Change the Layout Rule or pick a single bar."
        )

    curves = rebar.GetCenterlineCurves(
        True, True, True, MultiplanarOption.IncludeOnlyPlanarCurves, 0
    )
    if curves is None or len(curves) == 0:
        raise ValueError("Could not read the bar's shape.")
    for c in curves:
        if not isinstance(c, Line):
            raise ValueError(
                "The bar has a curved (arc) segment -- arcs are not "
                "supported by this tool."
            )
    return list(curves)


ORIENT_HORIZONTAL = "horizontal"
ORIENT_VERTICAL = "vertical"


def classify_segment_orientation(seg_dir):
    """v1.8 -- classify a (normalized) segment direction as horizontal or
    vertical for cut/crank purposes -- same thresholds the v1-v1.7.4
    horizontal-only check used (abs(Z) < 0.01 = horizontal). Anything in
    between (a sloped/diagonal segment) raises ValueError -- NBT scoped
    the general-support request to "moi thanh nhieu doan, moi huong", but
    a genuinely diagonal run has no natural Up/Down or Left/Right axis to
    reuse and is left out of scope for now."""
    z = abs(seg_dir.Z)
    if z < 0.01:
        return ORIENT_HORIZONTAL
    if z > 0.99:
        return ORIENT_VERTICAL
    raise ValueError(
        "This part of the bar runs diagonally (neither horizontal nor "
        "vertical) -- only horizontal or vertical segments are supported "
        "for cut/crank. Pick a point on a horizontal or vertical run."
    )


def reverse_curve_list(curves):
    """Return a NEW list of Lines covering the same path in reverse (both
    list order and each individual Line's own start/end swapped). Used to
    re-attach an existing unchanged run of curves on whichever side of the
    new crank geometry it needs to connect from."""
    return [Line.CreateBound(c.GetEndPoint(1), c.GetEndPoint(0)) for c in reversed(curves)]


def locate_pick_segment(curves, raw_pt):
    """v1.8 -- given the bar's FULL ordered curve list (get_bar_curves) and
    an arbitrary picked point, find which segment the point lands closest
    to and return everything the rest of the tool needs about it:

      seg_index        -- index into `curves`
      seg_start/seg_end -- that segment's own two endpoints (index0/index1
                           order, i.e. seg_start is the end nearer the
                           bar's true start)
      seg_dir           -- normalized seg_start -> seg_end direction
      pt_on_seg         -- the picked point, projected onto the segment
      dist_seg_start_ft / dist_seg_end_ft -- axial distance from pt_on_seg
                           to each end of JUST this segment
      curves_before/curves_after -- the (possibly empty) unchanged curves
                           on either side, in their original order/direction
      dist_true_start_ft / dist_true_end_ft -- axial distance from
                           pt_on_seg to the bar's TRUE start/end, through
                           curves_before/curves_after
      total_len_ft       -- the whole bar's length (all segments summed)

    Raises ValueError if the point doesn't project onto any segment, or
    lands too close (<5mm) to a segment boundary/bar end -- same tolerance
    project_point_on_line (v1-v1.7.4) used."""
    best = None
    for idx, seg in enumerate(curves):
        try:
            result = seg.Project(raw_pt)
        except Exception:
            continue
        if result is None:
            continue
        if best is None or result.Distance < best[0]:
            best = (result.Distance, idx, result.XYZPoint)

    if best is None:
        raise ValueError("Could not project the clicked point onto the bar.")

    _dist_off, seg_index, pt_on_seg = best
    seg = curves[seg_index]
    seg_start = seg.GetEndPoint(0)
    seg_end = seg.GetEndPoint(1)
    seg_len_ft = seg_start.DistanceTo(seg_end)
    dist_seg_start_ft = seg_start.DistanceTo(pt_on_seg)
    dist_seg_end_ft = seg_len_ft - dist_seg_start_ft

    eps_ft = mm_to_ft(5.0)
    if dist_seg_start_ft < eps_ft or dist_seg_end_ft < eps_ft:
        raise ValueError(
            "Clicked point is too close to one end of the bar (or to an "
            "existing bend). Click a position closer to the middle of a "
            "straight run."
        )

    curves_before = list(curves[:seg_index])
    curves_after = list(curves[seg_index + 1:])
    cum_before_ft = sum(c.GetEndPoint(0).DistanceTo(c.GetEndPoint(1)) for c in curves_before)
    cum_after_ft = sum(c.GetEndPoint(0).DistanceTo(c.GetEndPoint(1)) for c in curves_after)

    seg_dir = XYZ(
        seg_end.X - seg_start.X, seg_end.Y - seg_start.Y, seg_end.Z - seg_start.Z
    ).Normalize()

    return {
        "seg_index": seg_index,
        "seg_start": seg_start,
        "seg_end": seg_end,
        "seg_dir": seg_dir,
        "pt_on_seg": pt_on_seg,
        "dist_seg_start_ft": dist_seg_start_ft,
        "dist_seg_end_ft": dist_seg_end_ft,
        "curves_before": curves_before,
        "curves_after": curves_after,
        "dist_true_start_ft": cum_before_ft + dist_seg_start_ft,
        "dist_true_end_ft": cum_after_ft + dist_seg_end_ft,
        "total_len_ft": cum_before_ft + seg_len_ft + cum_after_ft,
    }


def derive_plane_norm(seg_dir, curves_before, curves_after, fallback_reference_dir):
    """v1.8 -- pick the plane normal ("norm", for Rebar.CreateFromCurves)
    for the new crank geometry.

    `seg_dir` is itself a piece of the ORIGINAL bar, which (having been
    built by a single Rebar.CreateFromCurves call) is entirely planar
    already. So if any curve immediately adjacent to the picked segment
    (in curves_before/curves_after) runs in a genuinely different
    direction -- i.e. there is a real existing bend next to the pick
    point, not just a straight continuation -- crossing that curve's
    direction with `seg_dir` reproduces that SAME existing plane's normal
    (up to sign), which the new geometry MUST match to stay coplanar with
    the unchanged curves it gets attached to.

    Falls back to `fallback_reference_dir` crossed with seg_dir (world Z
    for a horizontal segment, the view's own RightDirection for a
    vertical one -- see script.py's call site) only when nothing adjacent
    constrains the plane: a single straight bar (the v1-v1.7.4 case,
    where this reduces EXACTLY to the old `bar_dir.CrossProduct(BasisZ)`
    formula), or an existing bend that happens to be collinear with the
    picked segment."""
    for c in reversed(curves_before):
        d = XYZ(
            c.GetEndPoint(1).X - c.GetEndPoint(0).X,
            c.GetEndPoint(1).Y - c.GetEndPoint(0).Y,
            c.GetEndPoint(1).Z - c.GetEndPoint(0).Z,
        ).Normalize()
        cross = seg_dir.CrossProduct(d)
        if cross.GetLength() > 0.01:
            return cross.Normalize()
    for c in curves_after:
        d = XYZ(
            c.GetEndPoint(1).X - c.GetEndPoint(0).X,
            c.GetEndPoint(1).Y - c.GetEndPoint(0).Y,
            c.GetEndPoint(1).Z - c.GetEndPoint(0).Z,
        ).Normalize()
        cross = seg_dir.CrossProduct(d)
        if cross.GetLength() > 0.01:
            return cross.Normalize()
    return seg_dir.CrossProduct(fallback_reference_dir).Normalize()


def derive_offset_dir(norm, seg_dir):
    """v1.8 -- the perpendicular direction the 1xD crank offset moves
    along, derived from the plane normal + the segment's own axial
    direction. For a horizontal segment with norm = seg_dir x Z (the
    legacy formula), this reduces EXACTLY to world +Z (verified via the
    vector identity (A x B) x A = B|A|^2 - A(A.B), with A=seg_dir,
    B=Z, A.B=0 since seg_dir is horizontal) -- i.e. fully backward
    compatible with v1-v1.7.4's hardcoded Z offset. For a vertical
    segment (norm derived from the view's RightDirection), this reduces
    to that same RightDirection -- the horizontal "lean" NBT asked for."""
    return norm.CrossProduct(seg_dir).Normalize()


# ---------------------------------------------------------------------------
# v1.5 -- Lap/Crank input modes + per-project Setup (preset) storage.
#
# Lap length can now be entered directly in mm (LAP_MODE_MM), or as a
# multiple of the bar diameter (LAP_MODE_XD) -- in which case the raw
# N*D value is rounded UP to the nearest 10mm (NBT: "tu dong lam tron len
# chan 10").
#
# Crank slope's horizontal projection can now be entered directly in mm
# (CRANK_MODE_MM), or as a multiple of the bar diameter (CRANK_MODE_XD, the
# original v1-v1.4 behaviour).
#
# A "Setup" (preset) bundles one Lap mode+value and one Crank mode+value
# under a name (e.g. "A"), stored per-project via Revit Extensible Storage
# on doc.ProjectInformation (a per-document singleton element that always
# exists -- no need to create/find a dedicated DataStorage element).
# AccessLevel.Public is used for both read and write so SetVendorId() is not
# required. The blob itself is a hand-rolled tab-delimited / newline format
# (NOT json, to avoid any IronPython stdlib risk) -- see _serialize_presets /
# _deserialize_presets.
#
# Direction (Up/Down) is confirmed NOT part of a saved Setup -- always
# chosen fresh each run (confirmed with NBT via AskUserQuestion, 2026-08-22).
# ---------------------------------------------------------------------------

LAP_MODE_MM = "mm"
LAP_MODE_XD = "xD"
CRANK_MODE_XD = "xD"
CRANK_MODE_MM = "mm"
CRANK_SIDE_LEFT = "left"
CRANK_SIDE_RIGHT = "right"

_PRESET_SCHEMA_GUID = System.Guid("3f1a9e2c-7b44-4b18-9e2a-6d1f8c2b5a11")
_PRESET_SCHEMA_NAME = "pyNBT_CutCrankPresets"
_PRESET_FIELD_NAME = "PresetsBlob"


def _get_or_create_preset_schema():
    schema = Schema.Lookup(_PRESET_SCHEMA_GUID)
    if schema is not None:
        return schema
    builder = SchemaBuilder(_PRESET_SCHEMA_GUID)
    builder.SetSchemaName(_PRESET_SCHEMA_NAME)
    builder.SetReadAccessLevel(AccessLevel.Public)
    builder.SetWriteAccessLevel(AccessLevel.Public)
    builder.AddSimpleField(_PRESET_FIELD_NAME, System.String)
    return builder.Finish()


def sanitize_preset_name(name):
    if name is None:
        raise ValueError("Setup name is empty.")
    cleaned = name.replace("\t", " ").replace("\n", " ").replace("\r", " ").strip()
    if not cleaned:
        raise ValueError("Setup name is empty.")
    return cleaned[:60]


def _serialize_presets(default_name, presets):
    lines = ["DEFAULT\t{}".format(default_name or "")]
    for p in presets:
        lines.append(
            "PRESET\t{}\t{}\t{:.6f}\t{}\t{:.6f}\t{}\t{}".format(
                p["name"], p["lap_mode"], p["lap_value"],
                p["crank_mode"], p["crank_value"],
                p.get("crank_side", CRANK_SIDE_LEFT),
                "1" if p.get("direction_up", True) else "0",
            )
        )
    return "\n".join(lines)


def _deserialize_presets(blob):
    """v1.7 -- PRESET lines now carry 2 more fields (crank_side,
    direction_up) than v1.5/v1.6 did. `len(parts) >= 6` (the old minimum) is
    kept as the acceptance check so presets saved by older tool versions
    still load -- missing fields just fall back to sensible defaults
    (Left / Up) instead of raising."""
    default_name = ""
    presets = []
    if not blob:
        return default_name, presets
    for raw_line in blob.split("\n"):
        line = raw_line.strip("\r")
        if not line:
            continue
        parts = line.split("\t")
        if parts[0] == "DEFAULT" and len(parts) >= 2:
            default_name = parts[1]
        elif parts[0] == "PRESET" and len(parts) >= 6:
            try:
                presets.append({
                    "name": parts[1],
                    "lap_mode": parts[2],
                    "lap_value": float(parts[3]),
                    "crank_mode": parts[4],
                    "crank_value": float(parts[5]),
                    "crank_side": parts[6] if len(parts) > 6 else CRANK_SIDE_LEFT,
                    "direction_up": (parts[7] == "1") if len(parts) > 7 else True,
                })
            except ValueError:
                continue
    return default_name, presets


def load_presets(doc):
    """Read-only -- no Transaction needed."""
    schema = Schema.Lookup(_PRESET_SCHEMA_GUID)
    if schema is None:
        return "", []
    pi = doc.ProjectInformation
    entity = pi.GetEntity(schema)
    if entity is None or not entity.IsValid():
        return "", []
    blob = entity.Get[System.String](_PRESET_FIELD_NAME)
    return _deserialize_presets(blob)


def save_presets(doc, default_name, presets):
    """Must be called inside an already-started Transaction."""
    schema = _get_or_create_preset_schema()
    entity = Entity(schema)
    entity.Set[System.String](
        _PRESET_FIELD_NAME, _serialize_presets(default_name, presets)
    )
    doc.ProjectInformation.SetEntity(entity)


def upsert_preset(
    presets, name, lap_mode, lap_value, crank_mode, crank_value,
    crank_side=CRANK_SIDE_LEFT, direction_up=True,
):
    """Returns a NEW list; does not mutate input.

    v1.7 -- `crank_side`/`direction_up` are now part of a Setup (NBT
    explicitly reversed the earlier v1.5 decision that direction should NOT
    be saved -- confirmed 2026-08-22, this time as a direct request rather
    than something needing a fresh AskUserQuestion). Defaulted so existing
    call sites that haven't been updated yet don't break."""
    updated = [p for p in presets if p["name"] != name]
    updated.append({
        "name": name, "lap_mode": lap_mode, "lap_value": lap_value,
        "crank_mode": crank_mode, "crank_value": crank_value,
        "crank_side": crank_side, "direction_up": direction_up,
    })
    return updated


def find_preset(presets, name):
    for p in presets:
        if p["name"] == name:
            return p
    return None


def remove_preset(presets, name):
    """v1.6 -- returns a NEW list with the Setup named `name` removed
    (no-op, still returns a copy, if not found). Does not touch the
    "default" pointer -- the caller (script.py) is responsible for
    clearing `default_preset_name` first if the Setup being deleted is
    currently pinned as default, then persisting both together via
    save_presets()."""
    return [p for p in presets if p["name"] != name]


def round_up_to_10mm(value_mm):
    return math.ceil(value_mm / 10.0) * 10.0


def compute_lap_len_ft(mode, value, diameter_ft):
    """`value` is either a direct mm length (LAP_MODE_MM) or a multiple of
    the bar diameter (LAP_MODE_XD, in which case the raw N*D mm value is
    rounded UP to the nearest 10mm)."""
    if mode == LAP_MODE_XD:
        raw_mm = value * ft_to_mm(diameter_ft)
        return mm_to_ft(round_up_to_10mm(raw_mm))
    return mm_to_ft(value)


def compute_crank_horiz_ft(mode, value, diameter_ft):
    """`value` is either a direct mm length (CRANK_MODE_MM) or a multiple of
    the bar diameter (CRANK_MODE_XD, the original behaviour)."""
    if mode == CRANK_MODE_MM:
        return mm_to_ft(value)
    return value * diameter_ft


def get_view_right_direction(view):
    """v1.7 -- the 'rightward' XYZ direction of `view`'s own screen, used to
    resolve NBT's Left/Right crank-side choice into an actual 3D direction
    (see resolve_crank_toward_start's docstring for why this exists).
    Returns None if unavailable -- should not normally happen for a
    graphical view where PickPoint just succeeded, but callers must still
    handle None (e.g. some schedule/legend-like views have no orientation)."""
    try:
        right = view.RightDirection
        if right is not None and right.GetLength() > 1e-6:
            return right.Normalize()
    except Exception:
        pass
    return None


def resolve_crank_toward_start(crank_side, bar_dir, view_right_dir):
    """v1.7 -- NBT asked for an explicit Left/Right choice for which side of
    the pick point gets the crank, instead of the tool always cranking
    toward the ORIGINAL bar's start point regardless of which way that bar
    happened to be drawn. Root cause of the "crank lands on alternating
    sides" behaviour NBT reported (2026-08-22, confirmed via
    AskUserQuestion): Revit's Line.GetEndPoint(0)/(1) order for a Rebar's
    centerline is whatever direction that particular bar happened to be
    sketched in -- unrelated to left/right on screen -- so "always crank
    toward start" landed on a different visual side for different bars.

    NBT confirmed Left/Right should match the view he was looking at at the
    moment he picked the point (not a fixed model-wide axis), so `bar_dir`
    (start->end, normalized, horizontal) is compared against that view's OWN
    RightDirection via dot product to work out which physical direction
    (+bar_dir or -bar_dir) is screen-rightward for THIS bar in THAT view.

    Returns True if the diagonal should be built toward the original bar's
    START (the v1.4-v1.6 behaviour) -- False if it should be built toward
    the original END instead. The caller (script.py) then mirrors
    start_pt/bar_dir/hooks/dist-to-click before calling
    compute_full_geometry -- that function itself never needs to know about
    Left/Right, it always just builds "toward whichever end it's told is
    start".

    Raises ValueError if bar_dir runs too nearly into/out of the screen for
    Left/Right to be meaningful in this view (or if view_right_dir is None).

    v1.7.1 -- NBT tested v1.7 in real Revit and reported Left/Right came out
    mirrored (2026-08-22): picking "Left" put the crank on the visual right,
    and vice versa. The sign relationship below (`dot > 0`) was derived from
    the textbook assumption that `View.RightDirection` behaves exactly like
    a plain "more positive along this vector = further right on screen"
    axis. NBT's real-world test is the actual ground truth here (this tool
    has no way to render a view and check itself), so this comparison is now
    calibrated to match what he observed, instead of the theoretical
    derivation -- see the flipped `dot < 0` below.
    """
    if view_right_dir is None:
        raise ValueError(
            "Could not determine screen left/right in this view. Pick the "
            "point again from a Plan, Section, or Elevation view."
        )
    dot = bar_dir.DotProduct(view_right_dir)
    if abs(dot) < 0.05:
        raise ValueError(
            "Cannot tell Left from Right in this view -- the bar runs "
            "almost straight into/out of the screen here. Pick the point "
            "from a Plan, Section, or Elevation view where the bar runs "
            "across the screen (left-to-right or right-to-left)."
        )
    want_left = (crank_side != CRANK_SIDE_RIGHT)
    return want_left == (dot < 0)


def start_is_screen_left(bar_dir, view_right_dir):
    """True if the ORIGINAL bar's start point (line.GetEndPoint(0)) renders
    on the visual LEFT side of the screen. Returns True (assume Start=left)
    if view_right_dir is unavailable, matching the schematic's original
    pre-v1.7 behaviour (always anchor Start at the left of the picture) as a
    safe fallback.

    Used ONLY by script.py's preview schematic, to decide which physical
    end (Start or End) should be drawn at the left edge of the 2D diagram so
    it visually matches what actually happens in the Revit view -- it has no
    effect on the real 3D geometry, which is unaffected by how the preview
    happens to draw it.

    v1.7.4 -- v1.7.2 used the SAME sign as resolve_crank_toward_start's
    calibrated `dot < 0` (on the theory that the preview should mirror
    whenever the real geometry does). NBT tested that directly against real
    Revit (multiple bars, 2026-08-22) and reported the preview was STILL
    backwards -- consistently, every time -- even though the real 3D result
    itself is confirmed correct. That means the preview's own left/right
    display needs the OPPOSITE sign from the real-geometry calibration, not
    the same one (this function is independent of resolve_crank_toward_start
    on purpose -- it only affects how the picture is drawn, never the real
    geometry -- so recalibrating it here cannot un-fix the real result)."""
    if view_right_dir is None:
        return True
    return bar_dir.DotProduct(view_right_dir) > 0


def resolve_crank_toward_start_general(orientation, crank_side, direction_up, seg_dir, view_right_dir):
    """v1.8 -- generalized "which end gets the crank" resolution, covering
    both bar orientations with the SAME two button pairs NBT already has
    (confirmed via AskUserQuestion: "Dung lai dung 2 nut Left/Right hien
    co" for the vertical case, rather than adding new buttons):

      HORIZONTAL segment -- unchanged from v1.7.4: the Left/Right buttons
      (`crank_side`) pick the end, resolved against the current view's
      screen-right direction (resolve_crank_toward_start).

      VERTICAL segment -- the two physical ends are unambiguously "top"
      and "bottom" regardless of which view you're looking from, so
      Left/Right (a screen-relative idea) doesn't apply here. The tool
      instead reuses the Up/Down buttons (`direction_up`) for this axis,
      resolved against GLOBAL Z (not the view): 'Up' builds the crank
      toward whichever end is physically higher.

    NOT yet tested against a real Revit vertical bar -- flag this to NBT
    and expect it may need a sign flip after his first test, same as
    every other Left/Right/Up/Down calibration in this tool's history."""
    if orientation == ORIENT_HORIZONTAL:
        return resolve_crank_toward_start(crank_side, seg_dir, view_right_dir)
    start_is_up_end = seg_dir.Z < 0
    return direction_up == start_is_up_end


def resolve_offset_sign_general(orientation, crank_side, direction_up):
    """v1.8 -- generalized "which way the 1xD crank offset leans" (the
    axis NOT used by resolve_crank_toward_start_general above):

      HORIZONTAL segment -- unchanged from v1.7.4: Up/Down (`direction_up`)
      picks the sign, True = +offset_dir (world +Z for a horizontal bar).

      VERTICAL segment -- reuses Left/Right (`crank_side`) instead, since
      Up/Down is now busy picking the end (see above). True = +offset_dir,
      which derive_offset_dir works out to be ~the view's own
      RightDirection for a vertical segment -- so 'Right' leans the crank
      toward screen-right. NOT yet tested against a real Revit vertical
      bar -- same caveat as resolve_crank_toward_start_general."""
    if orientation == ORIENT_HORIZONTAL:
        return direction_up
    return crank_side != CRANK_SIDE_LEFT


def flip_hook_orientation(orientation):
    """v1.7.3 -- swap RebarHookOrientation.Left <-> .Right.

    Root cause of NBT's report (2026-08-22): he manually set an existing
    bar's hook to bend one way (a "U" shape facing down), ran this tool, and
    the SAME hook came out on the new bars bent the OTHER way (facing up) --
    consistently, on both preserved hook ends, regardless of Crank
    side/direction chosen that run.

    `RebarHookOrientation.Left`/`.Right` is only meaningful together with
    the plane normal ("norm") passed to `Rebar.CreateFromCurves` -- the same
    Left/Right value bends to the opposite physical side if `norm` points
    the other way. This tool always computes its own `norm` from
    `self.bar_dir` (see script.py's `do_apply`:
    `bar_dir.CrossProduct(XYZ.BasisZ)`), which is not guaranteed to match
    whatever normal the ORIGINAL bar happened to be built with -- there is
    no simple, reliable Revit API call to read an existing curve-based
    Rebar's own normal back out to guarantee a match. Since the flip showed
    up the same way on both preserved hooks, it's a constant, systematic
    mismatch (this tool always computes `norm` the same way) rather than a
    per-bar coincidence -- so a single Left<->Right swap on both read-back
    hook orientations, applied right after `get_end_hook()`, corrects it."""
    if orientation == RebarHookOrientation.Left:
        return RebarHookOrientation.Right
    return RebarHookOrientation.Left


def get_standard_bend_diameter_ft(bar_type):
    """Standard Bend Diameter of a RebarBarType (RebarStyle.Standard), in
    Revit internal units (feet). Returns None if unavailable (older Revit
    API, or a non-standard-style bar type) -- callers should skip the
    bend-clash validation below in that case rather than fail the tool."""
    try:
        val = bar_type.StandardBendDiameter
        return val if val and val > 0 else None
    except Exception:
        return None


def min_horiz_for_bend_ft(vert_ft, bend_diameter_ft, safety_factor=1.15):
    """v1.6.1 -- minimum horizontal projection (feet) for the crank's
    diagonal segment so the two automatic bend fillets Revit inserts at P1
    and P2 (Rebar.CreateFromCurves bends every vertex between consecutive
    curves using the bar type's own bend diameter -- per the Revit API docs:
    "Bends and hooks should not be included in the array of curves", i.e.
    Revit adds them itself) have enough straight length to fit without
    clashing into each other.

    Root cause of NBT's bug report (2026-08-22): "Crank slope" left at its
    "12" x D default, then the mode radio flipped to mm without changing
    the number -- reinterpreted as 12mm horizontal for a 1xD (12.7mm for a
    D13 bar) vertical rise, an unworkably steep crank whose two bend fillets
    overlap, visually clashing the two resulting bars in the model. v1.6.1
    fixes the root cause separately (mode-switch now auto-converts the
    number, see script.py's _convert_mode_value) -- this function is the
    defense-in-depth check that blocks Apply/Preview with a clear message
    for ANY combination (mm or x D) that is still too steep, instead of
    silently creating bad geometry.

    Solved numerically (bisection) since the required tangent length at each
    bend depends on the crank's angle, which itself depends on horiz_ft --
    no closed form. Returns 0.0 if there is nothing to check (no vertical
    rise, or bend_diameter_ft not available).
    """
    if vert_ft <= 0 or not bend_diameter_ft or bend_diameter_ft <= 0:
        return 0.0
    radius_ft = bend_diameter_ft / 2.0

    def tangent_ok(h):
        theta = math.atan2(vert_ft, h)
        diag = math.sqrt(h * h + vert_ft * vert_ft)
        tangent = radius_ft * math.tan(theta / 2.0)
        return diag >= safety_factor * 2.0 * tangent

    lo, hi = mm_to_ft(0.5), max(vert_ft * 50.0, mm_to_ft(2000.0))
    if not tangent_ok(hi):
        return hi  # bend diameter unworkable even at a very shallow slope
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if tangent_ok(mid):
            hi = mid
        else:
            lo = mid
    return hi


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def project_point_on_line(line, pick_pt):
    """Project an arbitrary 3D point onto the (bound) centerline Line.

    Returns (point_on_line, dist_from_start_ft, dist_from_end_ft, total_len_ft).
    Raises ValueError if the projected point lands right at (or past) one of
    the bar's ends -- the user needs to click somewhere in the middle.
    """
    result = line.Project(pick_pt)
    if result is None:
        raise ValueError("Could not project the clicked point onto the bar.")

    pt_on_line = result.XYZPoint
    start = line.GetEndPoint(0)
    end = line.GetEndPoint(1)
    total_len = start.DistanceTo(end)
    dist_from_start = start.DistanceTo(pt_on_line)
    dist_from_end = end.DistanceTo(pt_on_line)

    eps_ft = mm_to_ft(5.0)  # keep the cut point at least ~5mm from either end
    if dist_from_start < eps_ft or dist_from_end < eps_ft:
        raise ValueError(
            "Clicked point is too close to one end of the bar. "
            "Click a position closer to the middle of the bar."
        )

    return pt_on_line, dist_from_start, dist_from_end, total_len


def compute_full_geometry(
    start_pt, click_point, bar_dir, offset_dir, diameter_ft, horiz_ft, lap_len_ft,
    offset_positive, dist_start_to_click_ft, dist_click_to_far_ft,
    bend_diameter_ft=None,
):
    """Compute every point needed for Bar A + Bar B (v1.4 design -- see
    project doc "cut-crank-rebar-tool.md", section "v1.3 -> v1.4"), given:
      start_pt                -- the reference end the crank is built from
                                   (the picked segment's own near end -- see
                                   script.py's _resolve_crank_reference)
      click_point              -- where the user clicked, projected onto the
                                   picked segment. v1.4: this click now marks
                                   **P2** (end of the diagonal / start of the
                                   lap segment), NOT P1 (start of the
                                   diagonal) as in v1.3.
      bar_dir                   -- normalized XYZ, direction start_pt->click_point
                                   (the picked segment's own axial direction,
                                   possibly negated -- v1-v1.7.4 assumed this
                                   was always horizontal; v1.8 generalizes to
                                   any horizontal or vertical direction)
      offset_dir                -- normalized XYZ, the direction the 1xD
                                   crank offset moves along (v1-v1.7.4
                                   hardcoded this to world +Z; v1.8:
                                   derive_offset_dir -- reduces to that exact
                                   same +Z for a horizontal segment)
      diameter_ft               -- bar diameter D, feet
      horiz_ft                  -- crank's horizontal projection, feet (v1.5:
                                   pre-computed by the caller via
                                   compute_crank_horiz_ft(), from either an
                                   xD or a direct-mm input mode)
      lap_len_ft                -- length of Bar A's lap/connecting segment (P2->P3), feet
                                   (v1.5: pre-computed by the caller via
                                   compute_lap_len_ft(), from either an xD
                                   or a direct-mm input mode)
      offset_positive            -- True = crank moves along +offset_dir,
                                   False = along -offset_dir (v1-v1.7.4:
                                   this was `direction_up`, True = +Z)
      dist_start_to_click_ft    -- distance from start_pt to click_point (along
                                   `bar_dir`) -- this IS the axial distance
                                   from start_pt to P2.
      dist_click_to_far_ft      -- axial distance from click_point to the FAR
                                   end of the picked segment (i.e. that
                                   segment's own remaining length past the
                                   click, on Bar B's side) -- v1.8 renamed
                                   from total_len_ft: any unchanged curves
                                   beyond that far end are attached by the
                                   caller (script.py), not built here.

    v1.4 design (corrected per NBT, 2026-08-22): Bar A's diagonal runs from
    **P1** (on phuong 1, the ORIGINAL/un-shifted elevation) to **P2** (on
    phuong 2, shifted by 1xD). Bar B does NOT continue on phuong 2 like
    v1.3 assumed -- it runs on **phuong 1** (the original elevation),
    starting at the axial position directly under/over P2 (= click_point
    itself, since click_point already sits on phuong 1 at P2's axial
    position). This makes Bar A's lap segment (phuong 2) and Bar B's
    straight run (phuong 1) sit parallel, offset by exactly 1xD, and overlap
    axially over the Lap length -- the correct cranked-bar lap-splice detail
    (NBT: "thanh thep B phai theo phuong 1 ban dau moi dung").

    Returns a dict with:
      P1   -- start of Bar A's diagonal, on phuong 1 (computed by going back
              N*D from P2 along -bar_dir)
      P2   -- end of Bar A's diagonal / start of Bar A's lap segment, on
              phuong 2 (shifted by 1xD from phuong 1)
      P2B  -- start of Bar B, on phuong 1, at the SAME axial position as P2
              (this is exactly click_point -- kept as its own key for
              clarity at the call site)
      P3   -- free/connecting end of Bar A's lap segment, on phuong 2
      P4   -- the picked segment's own far end (script.py attaches any
              unchanged curves_before/curves_after beyond this point, plus
              whichever original hook lands there, if any)
      horiz_ft, vert_ft, diagonal_len_ft, lap_len_ft, b_len_ft (Bar B's
      straight length), straight_a_len_ft (Bar A's straight run, start_pt->P1)

    Raises ValueError if N*D leaves no room for the straight run before P1,
    or if there's no room left for Bar B past P2.
    """
    vert_ft = diameter_ft if offset_positive else -diameter_ft

    # v1.6.1 -- block a crank whose horizontal projection is too short for
    # this bar type's own Standard Bend Diameter to fit two clean bends at a
    # 1xD rise (see min_horiz_for_bend_ft's docstring for the bug this
    # guards against). Skipped entirely if bend_diameter_ft is unavailable.
    if bend_diameter_ft:
        min_horiz_ft = min_horiz_for_bend_ft(abs(vert_ft), bend_diameter_ft)
        if horiz_ft < min_horiz_ft:
            raise ValueError(
                "Crank slope's horizontal length ({:.0f}mm) is too short for "
                "this bar's Standard Bend Diameter ({:.0f}mm) at a 1xD rise "
                "-- the two bends would clash into each other. Increase "
                "Crank slope to at least {:.0f}mm horizontal (or a larger "
                "x D multiplier).".format(
                    ft_to_mm(horiz_ft), ft_to_mm(bend_diameter_ft),
                    ft_to_mm(min_horiz_ft),
                )
            )

    eps_ft = mm_to_ft(5.0)
    straight_a_len_ft = dist_start_to_click_ft - horiz_ft
    if straight_a_len_ft <= eps_ft:
        raise ValueError(
            "N x D is too large compared to the distance from the bar's "
            "start to the clicked point -- there's no room left for Bar A's "
            "straight run before the crank. Click further from the start, "
            "or use a smaller N."
        )

    p1 = XYZ(
        click_point.X - bar_dir.X * horiz_ft,
        click_point.Y - bar_dir.Y * horiz_ft,
        click_point.Z - bar_dir.Z * horiz_ft,
    )
    p2 = XYZ(
        click_point.X + offset_dir.X * vert_ft,
        click_point.Y + offset_dir.Y * vert_ft,
        click_point.Z + offset_dir.Z * vert_ft,
    )
    p3 = XYZ(
        p2.X + bar_dir.X * lap_len_ft,
        p2.Y + bar_dir.Y * lap_len_ft,
        p2.Z + bar_dir.Z * lap_len_ft,
    )

    # Bar B starts on phuong 1 (original elevation) at the SAME axial
    # position as P2 -- i.e. exactly click_point, since click_point already
    # sits on phuong 1 at P2's axial coordinate.
    p2b = XYZ(click_point.X, click_point.Y, click_point.Z)

    # Bar B's straight length: whatever axial distance is left between P2
    # and the picked segment's own far end -- any unchanged curves beyond
    # that (an existing bend already on that side) are attached by the
    # caller, not built here (v1.8 -- see dist_click_to_far_ft above).
    b_len_ft = dist_click_to_far_ft
    if b_len_ft <= mm_to_ft(1.0):
        raise ValueError(
            "The crank (N x D) reaches past the original bar's far end -- "
            "there's no room left for Bar B. Pick a cut point closer to the "
            "start, or use a smaller N."
        )

    p4 = XYZ(
        p2b.X + bar_dir.X * b_len_ft,
        p2b.Y + bar_dir.Y * b_len_ft,
        p2b.Z + bar_dir.Z * b_len_ft,
    )

    diagonal_len_ft = p1.DistanceTo(p2)

    return {
        "P1": p1,
        "P2": p2,
        "P2B": p2b,
        "P3": p3,
        "P4": p4,
        "horiz_ft": horiz_ft,
        "vert_ft": abs(vert_ft),
        "diagonal_len_ft": diagonal_len_ft,
        "lap_len_ft": lap_len_ft,
        "b_len_ft": b_len_ft,
        "straight_a_len_ft": straight_a_len_ft,
    }


# ---------------------------------------------------------------------------
# Model edit (must be called inside an already-started Transaction)
# ---------------------------------------------------------------------------

def create_cut_crank_bars(
    doc, rebar, bar_type, host, norm, start_pt, p1, p2, p2b, p3, p4,
    start_hook_type=None, start_hook_orient=RebarHookOrientation.Left,
    end_hook_type=None, end_hook_orient=RebarHookOrientation.Left,
    bar_a_prefix=None, bar_b_suffix=None,
):
    """Delete the original `rebar` and create two new Rebar elements (v1.4
    design -- Bar B corrected to run on phuong 1, see project doc
    "cut-crank-rebar-tool.md" section "v1.3 -> v1.4"):

      Bar A: [bar_a_prefix] -> start_pt -> p1 -> p2 -> p3   (the new straight
             / diagonal crank / lap curves, on phuong 1 then phuong 2, with
             any UNCHANGED existing curves before start_pt prepended as-is
             -- v1.8, empty for a single-segment bar. Keeps the ORIGINAL
             bar's START hook at the very first point, if any. p3 is a
             free/connecting end -- no hook.)
      Bar B: p2b -> p4 -> [bar_b_suffix]                     (the new
             straight curve, on phuong 1 -- the ORIGINAL, un-shifted
             elevation -- with any UNCHANGED existing curves past p4
             appended as-is -- v1.8, empty for a single-segment bar. p2b
             sits at the SAME axial position as p2 (directly under/over
             it), so Bar B's straight run overlaps Bar A's lap segment
             axially over the lap zone, offset by exactly 1xD -- the
             lap-splice detail. Keeps the ORIGINAL bar's END hook at the
             very last point, if any.)

    Pass the original rebar's hook type/orientation for each end via
    start_hook_type/start_hook_orient (end 0) and end_hook_type/end_hook_orient
    (end 1) -- read with get_end_hook() BEFORE calling this, since the
    original rebar is deleted here. `bar_a_prefix`/`bar_b_suffix` (v1.8) must
    already be correctly ordered/oriented to connect directly to start_pt /
    p4 respectively -- see script.py's _resolve_crank_reference (uses
    reverse_curve_list when the crank was built toward the bar's original
    END instead of its START).

    Returns (bar_a, bar_b).
    """
    doc.Delete(rebar.Id)
    bar_a_prefix = list(bar_a_prefix) if bar_a_prefix else []
    bar_b_suffix = list(bar_b_suffix) if bar_b_suffix else []

    line_a1 = Line.CreateBound(start_pt, p1)
    line_a2 = Line.CreateBound(p1, p2)
    line_a3 = Line.CreateBound(p2, p3)
    bar_a = Rebar.CreateFromCurves(
        doc, RebarStyle.Standard, bar_type, start_hook_type, None, host, norm,
        bar_a_prefix + [line_a1, line_a2, line_a3],
        start_hook_orient, RebarHookOrientation.Left,
        True, True,
    )

    line_b1 = Line.CreateBound(p2b, p4)
    bar_b = Rebar.CreateFromCurves(
        doc, RebarStyle.Standard, bar_type, None, end_hook_type, host, norm,
        [line_b1] + bar_b_suffix,
        RebarHookOrientation.Left, end_hook_orient,
        True, True,
    )

    return bar_a, bar_b
