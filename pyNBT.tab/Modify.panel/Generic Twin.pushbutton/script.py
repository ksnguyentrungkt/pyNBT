# -*- coding: utf-8 -*-
"""pyNBT - Generic Twin

Duplicates the exact solid geometry of the selected element(s) into a new,
lightweight Generic Model (DirectShape) element. Use this before drawing
rebar on a heavy/complex host - hosting rebar on the light twin instead of
the original element avoids the lag Revit causes from constantly
re-checking constraints against a heavy host while you draw.

Also supports twinning a Rebar element directly (its own bent-bar shape).
Rebar does not expose geometry through the standard get_Geometry() call,
and the view-dependent GetFullGeometryForView() approach (tried in an
earlier version of this tool) required the view to display reinforcement
"As Solid", which the user does not want to have to set up per view. So
for Rebar this tool instead builds the solid itself: it reads the bar's
centerline curves and diameter straight from the Rebar Shape data and
sweeps a circular profile of that diameter along those curves - this
works no matter how the current view happens to display rebar.

Workflow:
1. Click this tool.
2. Select one or more elements in the model.
3. Click Finish (green check) on the Options Bar to confirm.
4. A result summary popup shows how many twins were created.

No extra behavior is added on purpose - this tool only duplicates geometry
into a light Generic Model shell, so it stays fast and simple.
"""

__title__ = "Generic\nTwin"
__author__ = "pyNBT"

import math

from Autodesk.Revit.DB import (
    Transaction,
    DirectShape,
    Category,
    BuiltInCategory,
    Options,
    ViewDetailLevel,
    GeometryInstance,
    Solid,
    Curve,
    CurveLoop,
    Arc,
    XYZ,
    GeometryCreationUtilities,
)
from Autodesk.Revit.DB.Structure import Rebar, MultiplanarOption
from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException
from System.Collections.Generic import List

from pyrevit import revit, forms

doc = revit.doc
uidoc = revit.uidoc

TRANSACTION_NAME = "pyNBT - Generic Twin"
MIN_SOLID_VOLUME = 1e-9


def _walk_solids(geometry_element, solids):
    """Recursively collect Solid objects (volume > 0) from a
    GeometryElement into the solids list, descending into nested
    GeometryInstance (family-instance symbol) geometry."""
    for obj in geometry_element:
        if isinstance(obj, Solid):
            if obj.Volume > MIN_SOLID_VOLUME:
                solids.append(obj)
        elif isinstance(obj, GeometryInstance):
            inst_geom = obj.GetInstanceGeometry()
            if inst_geom:
                _walk_solids(inst_geom, solids)


def _collect_solids(element):
    """Return every Solid with non-zero volume found in a normal
    element's geometry (walls, columns, foundations, generic families,
    etc). Does NOT work for Rebar - see _collect_rebar_solids()."""
    options = Options()
    options.ComputeReferences = False
    options.IncludeNonVisibleObjects = False
    options.DetailLevel = ViewDetailLevel.Fine

    geom = element.get_Geometry(options)
    if geom is None:
        return []

    solids = []
    _walk_solids(geom, solids)
    return solids


def _perpendicular_axes(tangent):
    """Return two unit vectors (x_axis, y_axis), perpendicular to
    `tangent` and to each other, to use as the plane axes for a circular
    cross-section profile at a point on a path."""
    reference = XYZ.BasisZ
    if abs(tangent.DotProduct(reference)) > 0.999:
        reference = XYZ.BasisX
    x_axis = tangent.CrossProduct(reference).Normalize()
    y_axis = tangent.CrossProduct(x_axis).Normalize()
    return x_axis, y_axis


def _circle_profile_loop(center, radius, x_axis, y_axis):
    """Closed CurveLoop forming a circle of `radius` centered at
    `center`, lying in the plane spanned by the perpendicular unit
    vectors x_axis/y_axis. Built from two half-circle arcs, since Revit
    does not allow a single Arc to span a full 360 degrees."""
    arc1 = Arc.Create(center, radius, 0.0, math.pi, x_axis, y_axis)
    arc2 = Arc.Create(center, radius, math.pi, 2.0 * math.pi, x_axis, y_axis)
    loop = CurveLoop()
    loop.Append(arc1)
    loop.Append(arc2)
    return loop


def _sweep_solid_along_curves(curves, radius):
    """Build a Solid by sweeping a circle of `radius` along a connected
    chain of curves (one bar's centerline)."""
    curve_list = List[Curve]()
    for c in curves:
        curve_list.Add(c)
    path_loop = CurveLoop.Create(curve_list)

    first_curve = curves[0]
    attachment_param = first_curve.GetEndParameter(0)
    derivatives = first_curve.ComputeDerivatives(attachment_param, False)
    center = derivatives.Origin
    tangent = derivatives.BasisX.Normalize()
    x_axis, y_axis = _perpendicular_axes(tangent)

    profile_loop = _circle_profile_loop(center, radius, x_axis, y_axis)
    profile_loops = List[CurveLoop]()
    profile_loops.Add(profile_loop)

    return GeometryCreationUtilities.CreateSweptGeometry(
        path_loop, 0, attachment_param, profile_loops
    )


def _collect_rebar_solids(rebar, document, errors):
    """Return a solid tube for every physical bar in a Rebar element (a
    'set' can contain several bars) - built by reading each bar's
    centerline curves and the bar type's diameter straight from the
    Rebar Shape data, then sweeping a circle of that diameter along the
    curves. Independent of the current view's display settings.

    Any failure is appended as a short text description to `errors`
    (shared list passed in by the caller) instead of being silently
    swallowed - this is temporary/diagnostic so the actual Revit
    exception text can be seen and reported back."""
    try:
        bar_type = document.GetElement(rebar.GetTypeId())
        try:
            # Revit 2022+ renamed/split this into BarModelDiameter (actual
            # modeling/geometry thickness) and BarNominalDiameter (used
            # for formulas). We want the modeling one.
            diameter = bar_type.BarModelDiameter
        except AttributeError:
            # Pre-2022 Revit API only had BarDiameter.
            diameter = bar_type.BarDiameter
        radius = diameter / 2.0
    except Exception as ex:
        errors.append("BarDiameter: {}".format(ex))
        return []

    if radius <= 0:
        errors.append("BarDiameter: radius <= 0 ({})".format(radius))
        return []

    try:
        bar_count = rebar.NumberOfBarPositions
    except Exception as ex:
        errors.append("NumberOfBarPositions: {}".format(ex))
        bar_count = 1

    solids = []
    for i in range(bar_count):
        try:
            if not rebar.DoesBarExistAtPosition(i):
                continue
        except Exception:
            pass

        try:
            curves = list(
                rebar.GetCenterlineCurves(
                    True, False, False,
                    MultiplanarOption.IncludeAllMultiplanarCurves, i
                )
            )
        except Exception as ex:
            errors.append("GetCenterlineCurves[{}]: {}".format(i, ex))
            continue

        if not curves:
            errors.append("GetCenterlineCurves[{}]: returned no curves".format(i))
            continue

        try:
            solid = _sweep_solid_along_curves(curves, radius)
        except Exception as ex:
            errors.append("sweep[{}] ({} curves): {}".format(i, len(curves), ex))
            continue

        if solid is not None and solid.Volume > MIN_SOLID_VOLUME:
            solids.append(solid)
        else:
            errors.append("sweep[{}]: produced zero-volume solid".format(i))

    return solids


def _create_generic_twin(source_element, category_id, document, errors):
    """Create a DirectShape (Generic Models) with the exact solid geometry
    of source_element. Returns the new DirectShape, or None if the source
    has no usable solid geometry."""
    if isinstance(source_element, Rebar):
        solids = _collect_rebar_solids(source_element, document, errors)
    else:
        solids = _collect_solids(source_element)

    if not solids:
        return None

    ds = DirectShape.CreateElement(document, category_id)
    ds.SetShape(list(solids))

    try:
        src_name = source_element.Name
    except Exception:
        src_name = "Element"
    try:
        ds.Name = "{}_Twin".format(src_name)
    except Exception:
        # Name collisions or unsupported rename shouldn't block creation.
        pass

    return ds


def run():
    generic_model_category = Category.GetCategory(doc, BuiltInCategory.OST_GenericModel)
    category_id = generic_model_category.Id

    if not DirectShape.IsValidCategoryId(category_id, doc):
        forms.alert(
            "Generic Models category is not valid for DirectShape in this "
            "document. Cannot create Generic Twin.",
            title="Generic Twin",
        )
        return

    try:
        picked_refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            "Select element(s) to duplicate as a Generic Twin, "
            "then click Finish"
        )
    except OperationCanceledException:
        # User pressed Escape / cancelled the pick - do nothing.
        return

    if not picked_refs:
        return

    elements = [doc.GetElement(ref) for ref in picked_refs]

    created = 0
    skipped_rebar = 0
    skipped_other = 0
    debug_errors = []

    t = Transaction(doc, TRANSACTION_NAME)
    t.Start()
    try:
        for el in elements:
            twin = _create_generic_twin(el, category_id, doc, debug_errors)
            if twin is not None:
                created += 1
            elif isinstance(el, Rebar):
                skipped_rebar += 1
            else:
                skipped_other += 1
        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        forms.alert(
            "Error while creating Generic Twin:\n{}".format(str(ex)),
            title="Generic Twin",
        )
        return

    message = "Created {} Generic Twin element(s).".format(created)
    if skipped_rebar:
        message += (
            "\nSkipped {} rebar element(s) - could not build a solid from "
            "this bar's shape.".format(skipped_rebar)
        )
    if skipped_other:
        message += "\nSkipped {} element(s) with no solid geometry.".format(skipped_other)
    if debug_errors:
        message += "\n\n[DEBUG - send this to Claude]\n" + "\n".join(debug_errors[:5])
    forms.alert(message, title="Generic Twin")


if __name__ == "__main__":
    run()
