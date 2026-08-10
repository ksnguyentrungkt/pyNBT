# -*- coding: utf-8 -*-
"""pyNBT - Generic Twin

Duplicates the exact solid geometry of the selected element(s) into a new,
lightweight Generic Model (DirectShape) element. Use this before drawing
rebar on a heavy/complex host - hosting rebar on the light twin instead of
the original element avoids the lag Revit causes from constantly
re-checking constraints against a heavy host while you draw.

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

from Autodesk.Revit.DB import (
    Transaction,
    DirectShape,
    Category,
    BuiltInCategory,
    Options,
    ViewDetailLevel,
    GeometryInstance,
    Solid,
)
from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import revit, forms

doc = revit.doc
uidoc = revit.uidoc

TRANSACTION_NAME = "pyNBT - Generic Twin"
MIN_SOLID_VOLUME = 1e-9


def _collect_solids(element):
    """Return every Solid with non-zero volume found in the element's
    geometry, including geometry nested inside family-instance symbols."""
    options = Options()
    options.ComputeReferences = False
    options.IncludeNonVisibleObjects = False
    options.DetailLevel = ViewDetailLevel.Fine

    geom = element.get_Geometry(options)
    if geom is None:
        return []

    solids = []

    def _walk(geometry_element):
        for obj in geometry_element:
            if isinstance(obj, Solid):
                if obj.Volume > MIN_SOLID_VOLUME:
                    solids.append(obj)
            elif isinstance(obj, GeometryInstance):
                inst_geom = obj.GetInstanceGeometry()
                if inst_geom:
                    _walk(inst_geom)

    _walk(geom)
    return solids


def _create_generic_twin(source_element, category_id):
    """Create a DirectShape (Generic Models) with the exact solid geometry
    of source_element. Returns the new DirectShape, or None if the source
    has no usable solid geometry."""
    solids = _collect_solids(source_element)
    if not solids:
        return None

    ds = DirectShape.CreateElement(doc, category_id)
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
    skipped = 0

    t = Transaction(doc, TRANSACTION_NAME)
    t.Start()
    try:
        for el in elements:
            twin = _create_generic_twin(el, category_id)
            if twin is not None:
                created += 1
            else:
                skipped += 1
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
    if skipped:
        message += "\nSkipped {} element(s) with no solid geometry.".format(skipped)
    forms.alert(message, title="Generic Twin")


if __name__ == "__main__":
    run()
