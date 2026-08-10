# -*- coding: utf-8 -*-
"""
pyNBT - Auto Cable Solid (Straight / Curved)

Pick 2 diem bat ky (face / edge / ref plane / curve / adaptive point) trong
Family Editor, sau do chon kieu cap:

  - STRAIGHT : noi 2 diem bang duong thang, tao Solid tru bang Swept Blend
               (giong tool Auto Cable Solid ban dau).
  - CURVED   : noi 2 diem bang duong cong theo bang station/height
               (Profile F1 / B2 / A), tao Solid bang Loft
               (giong tool AutoAdaptiveForm_Profile_F1_B2_A_Loft).

Ported va gop tu 2 file C#:
  - AutoSolidFormFrom2AnyPointsCommand.cs
  - AutoAdaptiveForm_Profile_F1_B2_A_Loft_NoRefPoints.cs
"""

__title__ = "Auto Cable\nSolid"
__author__ = "pyNBT"
__doc__ = "Pick 2 diem, chon kieu cap Thang/Cong, tu dong tao Solid noi 2 diem."

import math

from pyrevit import DB, revit, forms

from Autodesk.Revit.UI.Selection import ObjectType


doc = revit.doc
uidoc = revit.uidoc

TOOL_TITLE = "pyNBT - Auto Cable Solid"

MIN_LEN_FT = 1.0 / 304.8       # ~1mm tinh theo feet
A_SAMPLE_STEP_MM = 80.0        # buoc sample cho Profile A (mm)


# ============================================================
# HELPERS DUNG CHUNG
# ============================================================

def mm_to_ft(mm):
    return DB.UnitUtils.ConvertToInternalUnits(mm, DB.UnitTypeId.Millimeters)


def pick_any_point(prompt):
    """Pick 1 diem tren face / edge / ref plane / curve / adaptive point."""
    try:
        ref = uidoc.Selection.PickObject(ObjectType.PointOnElement, prompt)
    except Exception:
        return None
    return ref.GlobalPoint


def ask_diameter_mm(default_mm=50.0):
    result = forms.ask_for_string(
        default=str(default_mm),
        prompt="Nhap DUONG KINH cap (mm). Vi du: 50",
        title=TOOL_TITLE
    )
    if not result:
        return None

    result = result.strip().replace(",", ".")
    try:
        value = float(result)
    except ValueError:
        forms.alert("Gia tri khong hop le.\nVi du dung: 25 hoac 25.5", title=TOOL_TITLE)
        return None

    if value <= 0:
        forms.alert("Duong kinh phai > 0.", title=TOOL_TITLE)
        return None

    return value


def any_perpendicular(v):
    n = v.CrossProduct(DB.XYZ.BasisZ)
    if n.GetLength() < 1e-9:
        n = v.CrossProduct(DB.XYZ.BasisX)
    if n.GetLength() < 1e-9:
        n = v.CrossProduct(DB.XYZ.BasisY)
    return n.Normalize()


def create_circle_loop(origin, normal_dir, radius_ft):
    """
    Tao 1 vong tron (2 cung Arc) tren mat phang vuong goc voi 'normal_dir'
    tai 'origin'. Tra ve ReferenceArray (loop) hoac None neu that bai.
    Dung chung cho ca 2 nhanh Straight (profile 2 dau) va Curved (moi mat cat loft).
    """
    n = normal_dir.Normalize()
    x = any_perpendicular(n)
    y = n.CrossProduct(x).Normalize()

    plane = DB.Plane.CreateByOriginAndBasis(origin, x, y)
    sketch_plane = DB.SketchPlane.Create(doc, plane)

    a1 = DB.Arc.Create(plane, radius_ft, 0.0, math.pi)
    a2 = DB.Arc.Create(plane, radius_ft, math.pi, 2.0 * math.pi)

    mc1 = doc.FamilyCreate.NewModelCurve(a1, sketch_plane)
    mc2 = doc.FamilyCreate.NewModelCurve(a2, sketch_plane)

    r1 = mc1.GeometryCurve.Reference
    r2 = mc2.GeometryCurve.Reference

    if r1 is None or r2 is None:
        doc.Regenerate()
        r1 = mc1.GeometryCurve.Reference
        r2 = mc2.GeometryCurve.Reference

    if r1 is None or r2 is None:
        return None

    loop = DB.ReferenceArray()
    loop.Append(r1)
    loop.Append(r2)
    return loop


# ============================================================
# NHANH STRAIGHT (Swept Blend - duong thang)
# ============================================================

def run_straight(p1, p2):
    dia_mm = ask_diameter_mm(50.0)
    if dia_mm is None:
        return
    radius_ft = mm_to_ft(dia_mm) / 2.0

    t = DB.Transaction(doc, "pyNBT - Auto Cable Solid (Straight)")
    t.Start()
    try:
        direction = (p2 - p1).Normalize()

        # --- Path line ---
        path_plane_normal = any_perpendicular(direction)
        path_plane = DB.Plane.CreateByNormalAndOrigin(path_plane_normal, p1)
        path_sketch_plane = DB.SketchPlane.Create(doc, path_plane)

        path_line = DB.Line.CreateBound(p1, p2)
        path_curve = doc.FamilyCreate.NewModelCurve(path_line, path_sketch_plane)

        path_ref = path_curve.GeometryCurve.Reference
        if path_ref is None:
            doc.Regenerate()
            path_ref = path_curve.GeometryCurve.Reference

        if path_ref is None:
            t.RollBack()
            forms.alert("Khong lay duoc Reference cua path line.", title=TOOL_TITLE)
            return

        path_refs = DB.ReferenceArray()
        path_refs.Append(path_ref)

        # --- 2 profile tron tai p1, p2 ---
        profile_start = create_circle_loop(p1, direction, radius_ft)
        profile_end = create_circle_loop(p2, direction, radius_ft)

        if profile_start is None or profile_end is None:
            t.RollBack()
            forms.alert("Khong tao duoc circle profile.", title=TOOL_TITLE)
            return

        profiles = DB.ReferenceArrayArray()
        profiles.Append(profile_start)
        profiles.Append(profile_end)

        solid_form = doc.FamilyCreate.NewSweptBlendForm(True, path_refs, profiles)

        if solid_form is None:
            t.RollBack()
            forms.alert("NewSweptBlendForm tra ve null (khong tao duoc Form).", title=TOOL_TITLE)
            return

        t.Commit()

    except Exception as ex:
        t.RollBack()
        forms.alert("Co loi xay ra:\n{}".format(ex), title=TOOL_TITLE)
        return

    forms.alert("Done: Da tao Solid THANG (cable).", title=TOOL_TITLE)


# ============================================================
# NHANH CURVED (Loft - duong cong theo bang station/height)
# ============================================================

class SH(object):
    """Station/Height pair (feet)."""
    __slots__ = ("s", "h")

    def __init__(self, s, h):
        self.s = s
        self.h = h


def build_perp_basis(axis):
    U = axis.CrossProduct(DB.XYZ.BasisZ)
    if U.GetLength() < 1e-9:
        U = axis.CrossProduct(DB.XYZ.BasisX)
    if U.GetLength() < 1e-9:
        U = axis.CrossProduct(DB.XYZ.BasisY)
    U = U.Normalize()
    V = axis.CrossProduct(U).Normalize()
    return U, V


def pick_up_vector(dir_choice, U, V):
    if dir_choice == "U +":
        return U
    if dir_choice == "U -":
        return -U
    if dir_choice == "V +":
        return V
    return -V


def build_profile_table(profile_kind, b2_variant):
    if profile_kind == "B2":
        seg_mm = [225, 699, 500, 500, 500, 500, 500]

        if b2_variant == "7-12":
            y_mm = [712, 570, 437, 323, 244, 196, 181, 181]
        elif b2_variant == "13-15":
            y_mm = [712, 575, 441, 328, 248, 201, 185, 185]
        else:
            y_mm = [712, 583, 449, 336, 256, 209, 193, 193]

        stations_mm = [0.0]
        cum = 0.0
        for seg in seg_mm:
            cum += seg
            stations_mm.append(cum)

        n = min(len(stations_mm), len(y_mm))
        table = [SH(mm_to_ft(stations_mm[i]), mm_to_ft(y_mm[i])) for i in range(n)]
        table.sort(key=lambda x: x.s)
        return table

    if profile_kind == "A":
        inc_mm = [320, 355, 500, 500, 500, 500, 349, 500, 500]
        h_mm = [787, 684, 543, 421, 331, 250, 198, 151, 135, 135]

        s_mm = [0.0]
        cum = 0.0
        for inc in inc_mm:
            cum += inc
            s_mm.append(cum)

        n = min(len(s_mm), len(h_mm))
        table = [SH(mm_to_ft(s_mm[i]), mm_to_ft(h_mm[i])) for i in range(n)]
        table.sort(key=lambda x: x.s)
        return table

    # F1 mac dinh
    f1_raw = [
        (0, 490), (500, 379), (1000, 283), (1500, 220),
        (2000, 185), (2544, 151), (3044, 135),
    ]
    table = [SH(mm_to_ft(s), mm_to_ft(h)) for s, h in f1_raw]
    table.sort(key=lambda x: x.s)
    return table


def extend_tail_constant(table, step_ft, const_h, target_len_ft):
    table = sorted(table, key=lambda t: t.s)

    if len(table) == 0:
        table.append(SH(0, const_h))
        table.append(SH(step_ft, const_h))
        return table

    last_s = table[-1].s

    if target_len_ft is not None:
        target = target_len_ft
        step = step_ft if step_ft >= 10 * MIN_LEN_FT else 10 * MIN_LEN_FT
        while last_s + step < target - 1e-6:
            last_s += step
            table.append(SH(last_s, const_h))
        if table[-1].s < target:
            table.append(SH(target, const_h))
    else:
        if len(table) == 1:
            table.append(SH(last_s + step_ft, const_h))

    table.sort(key=lambda t: t.s)
    return table


def safe_scale_factor_to_l(table_scaled, L):
    if table_scaled is None or len(table_scaled) < 2:
        return 1.0
    s_max = max(t.s for t in table_scaled)
    if s_max < 1e-9:
        return 1.0
    return L / s_max


def scale_stations_to_length(table, L):
    if table is None or len(table) < 2:
        return table

    s_min = min(t.s for t in table)
    s_max = max(t.s for t in table)

    if abs(s_min) > 1e-9:
        table = [SH(t.s - s_min, t.h) for t in table]
        s_max = s_max - s_min

    if s_max < 10 * MIN_LEN_FT:
        return table

    scale = L / s_max
    scaled = sorted([SH(t.s * scale, t.h) for t in table], key=lambda t: t.s)
    scaled[0].s = 0.0
    scaled[-1].s = L
    return scaled


def height_at(s, table):
    if table is None or len(table) == 0:
        return 0.0
    if s <= table[0].s:
        return table[0].h
    if s >= table[-1].s:
        return table[-1].h
    for i in range(len(table) - 1):
        s0 = table[i].s
        s1 = table[i + 1].s
        if s0 <= s <= s1:
            t = (s - s0) / (s1 - s0)
            return table[i].h + (table[i + 1].h - table[i].h) * t
    return table[-1].h


def remove_too_close(pts, min_dist):
    if pts is None or len(pts) < 2:
        return pts
    cleaned = [pts[0]]
    for i in range(1, len(pts)):
        if (pts[i] - cleaned[-1]).GetLength() >= min_dist:
            cleaned.append(pts[i])
    if not cleaned[-1].IsAlmostEqualTo(pts[-1]):
        cleaned.append(pts[-1])
    return cleaned


def build_path_points_baseline(p1, p2, axis, up, L, table):
    h0 = height_at(0.0, table)
    hL = height_at(L, table)

    pts = []
    for item in table:
        s = item.s
        if s < -1e-9 or s > L + 1e-9:
            continue
        h_raw = item.h
        t = 0.0 if L < 1e-9 else (s / L)
        baseline = h0 + (hL - h0) * t
        h = h_raw - baseline
        pts.append(p1 + axis * s + up * h)

    if len(pts) == 0:
        pts.append(p1)

    pts[0] = p1
    if not pts[-1].IsAlmostEqualTo(p2):
        pts.append(p2)
    else:
        pts[-1] = p2

    return pts


def build_path_points_sampled_baseline(p1, p2, axis, up, L, table, step_ft):
    if table is None or len(table) < 2:
        return [p1, p2]
    if step_ft < 5 * MIN_LEN_FT:
        step_ft = 5 * MIN_LEN_FT

    h0 = height_at(0.0, table)
    hL = height_at(L, table)

    pts = []

    n = int(math.ceil(L / step_ft))
    for i in range(n + 1):
        s = min(L, i * step_ft)
        h_raw = height_at(s, table)
        t = 0.0 if L < 1e-9 else (s / L)
        baseline = h0 + (hL - h0) * t
        h = h_raw - baseline
        pts.append(p1 + axis * s + up * h)

    for item in table:
        s = item.s
        if s <= 1e-9 or s >= L - 1e-9:
            continue
        h_raw = height_at(s, table)
        t = s / L
        baseline = h0 + (hL - h0) * t
        h = h_raw - baseline
        pts.append(p1 + axis * s + up * h)

    pts.sort(key=lambda p: (p - p1).DotProduct(axis))

    if len(pts) == 0:
        pts.append(p1)
    pts[0] = p1
    if not pts[-1].IsAlmostEqualTo(p2):
        pts.append(p2)
    else:
        pts[-1] = p2

    return remove_too_close(pts, 2 * MIN_LEN_FT)


def run_curved(p1, p2):
    chord = p2 - p1
    L = chord.GetLength()
    if L < 10 * MIN_LEN_FT:
        forms.alert("Hai diem qua gan nhau.", title=TOOL_TITLE)
        return
    axis = chord.Normalize()

    # 1) Chon huong U/V
    dir_choice = forms.ask_for_one_item(
        ["U +", "U -", "V +", "V -"],
        default="U +",
        prompt="Chon phuong height (U/V):",
        title=TOOL_TITLE
    )
    if dir_choice is None:
        return

    # 2) Chon Profile
    profile_choice = forms.ask_for_one_item(
        ["Profile F1", "Profile B2", "Profile A"],
        default="Profile F1",
        prompt="Chon Profile:",
        title=TOOL_TITLE
    )
    if profile_choice is None:
        return
    profile_kind = profile_choice.replace("Profile ", "")

    # 3) Neu B2 -> chon variant
    b2_variant = None
    if profile_kind == "B2":
        b2_choice = forms.ask_for_one_item(
            ["B2: 7-12", "B2: 13-15", "B2: 16-22"],
            default="B2: 7-12",
            prompt="Chon dong Y1 cua B2:",
            title=TOOL_TITLE
        )
        if b2_choice is None:
            return
        b2_variant = b2_choice.split(": ")[1]

    # 4) Duong kinh
    dia_mm = ask_diameter_mm(116.0)
    if dia_mm is None:
        return
    radius_ft = mm_to_ft(dia_mm) / 2.0

    # 5) Basis U/V quanh axis
    U, V = build_perp_basis(axis)
    up = pick_up_vector(dir_choice, U, V).Normalize()
    if axis.CrossProduct(up).GetLength() < 1e-9:
        up = any_perpendicular(axis)

    # 6) Bang station/height
    table = build_profile_table(profile_kind, b2_variant)

    if profile_kind == "A":
        table = extend_tail_constant(table, mm_to_ft(500.0), mm_to_ft(135.0), None)
        table = scale_stations_to_length(table, L)
        scale_factor = safe_scale_factor_to_l(table, L)
        table = extend_tail_constant(table, mm_to_ft(500.0) * scale_factor, mm_to_ft(135.0), L)
    else:
        table = scale_stations_to_length(table, L)

    # 7) Build path points
    if profile_kind == "A":
        step_ft = mm_to_ft(A_SAMPLE_STEP_MM)
        path_pts = build_path_points_sampled_baseline(p1, p2, axis, up, L, table, step_ft)
    else:
        path_pts = build_path_points_baseline(p1, p2, axis, up, L, table)
        path_pts = remove_too_close(path_pts, 2 * MIN_LEN_FT)

    if path_pts is None or len(path_pts) < 2:
        forms.alert("Khong du diem de loft.", title=TOOL_TITLE)
        return

    # 8) Tao Loft Solid
    t = DB.Transaction(doc, "pyNBT - Auto Cable Solid (Curved)")
    t.Start()
    try:
        loft_profiles = DB.ReferenceArrayArray()
        for pt in path_pts:
            loop = create_circle_loop(pt, axis, radius_ft)
            if loop is None:
                t.RollBack()
                forms.alert("Khong tao duoc mat cat tai 1 diem tren duong cong.", title=TOOL_TITLE)
                return
            loft_profiles.Append(loop)

        doc.Regenerate()

        loft_form = doc.FamilyCreate.NewLoftForm(True, loft_profiles)

        if loft_form is None:
            t.RollBack()
            forms.alert("NewLoftForm tra ve null (khong tao duoc Solid).", title=TOOL_TITLE)
            return

        t.Commit()

    except Exception as ex:
        t.RollBack()
        forms.alert("Co loi xay ra:\n{}".format(ex), title=TOOL_TITLE)
        return

    done_msg = profile_kind
    if profile_kind == "B2":
        done_msg += " ({})".format(b2_variant)
    forms.alert("Done: Da tao Solid CONG (cable) - Profile {}".format(done_msg), title=TOOL_TITLE)


# ============================================================
# MAIN
# ============================================================

def main():
    if not doc.IsFamilyDocument:
        forms.alert(
            "Tool nay chi chay trong Family Editor (Family Document).",
            title=TOOL_TITLE
        )
        return

    # 1) Pick 2 diem (dung chung cho ca Straight va Curved)
    p1 = pick_any_point("Pick Point #1 (face / edge / ref plane / curve / adaptive point)")
    if p1 is None:
        return

    p2 = pick_any_point("Pick Point #2 (face / edge / ref plane / curve / adaptive point)")
    if p2 is None:
        return

    if p1.IsAlmostEqualTo(p2):
        forms.alert("Hai diem trung nhau. Hay chon 2 diem khac nhau.", title=TOOL_TITLE)
        return

    # 2) Chon kieu cap: Thang hay Cong
    cable_type = forms.ask_for_one_item(
        ["Straight (Thang)", "Curved (Cong)"],
        default="Straight (Thang)",
        prompt="Chon kieu cap:",
        title=TOOL_TITLE
    )
    if cable_type is None:
        return

    # 3) Chay nhanh tuong ung
    if cable_type.startswith("Straight"):
        run_straight(p1, p2)
    else:
        run_curved(p1, p2)


main()
