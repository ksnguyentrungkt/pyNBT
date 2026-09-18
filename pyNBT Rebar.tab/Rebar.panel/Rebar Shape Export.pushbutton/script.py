# -*- coding: utf-8 -*-
"""Rebar Shape Export

Reads the real bent shape of each selected Rebar element (hooks are still
suppressed for now, but real bend-radius arcs are kept) and redraws it as
DetailCurves (Line/Arc) in a single Drafting View shared by every selected
bar (each bar's shape placed side by side with a gap, left to right), with
a real Revit Dimension per straight segment, a real Radial Dimension per
bend arc, and a real Angular Dimension at every bend between two straight
segments - all auto-computed/auto-formatted by Revit itself (dimension
lines offset 50mm from the shape; bend angle always reported as the value
under 180 degrees), not manually written text - plus a small info block
per bar (Partition, Mark, Bar Type, Diameter, Quantity, Length per bar,
Total length). If a native Dimension fails to create on a given
segment/arc/angle (API differences across Revit versions), that one falls
back to a plain text label (with the real error message attached, for
diagnosis) so nothing is left unlabeled.

Optionally (prompted every run), also exports one PNG image per bar -
each drawn into its own temporary Drafting View (deleted right after
export, never left behind), named by Revit's own automatic "Rebar
Number" (Identity Data, distinct from Mark) - for attaching to a Bar
Bending Schedule when Revit's own BBS export doesn't show a bar's detail
clearly enough.

How to use: select one or more Rebar elements in the model (Rebar Set or
single bar), then click this tool. Non-Rebar elements in the selection
are skipped. All selected bars are drawn into the SAME new Drafting View.

Known limitations (Phase 1):
- Only Rebar and RebarInSystem elements (RebarInSystem is a subclass of
  Rebar, so it is included automatically) are supported. Other
  reinforcement classes - AreaReinforcement, PathReinforcement,
  FabricSheet, FabricArea - are skipped.
- Spiral/helical shaped rebar (e.g. circular column hoop reinforcement)
  and other non-planar shapes are skipped - this needs its own logic and
  is planned for a later phase.
- Hooks are still not drawn (drawn as if the bar ended at the last main
  bend) to keep geometry simple; the reported "Length per bar" still uses
  the real curve length including hooks and bend radius, so the printed
  number stays accurate even though the picture omits the hook curl.
"""

import clr
import math
import os

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    XYZ, Line, Arc, Transaction, FilteredElementCollector, ViewFamilyType,
    ViewFamily, View, ViewDrafting, BuiltInParameter, TextNote,
    ElementTypeGroup, Element, Reference, AngularDimension,
    LinearDimension, RadialDimension, ElementId, ImageExportOptions,
    ExportRange, ZoomFitType, ImageResolution, ImageFileType,
    FitDirectionType
)
from Autodesk.Revit.DB.Structure import Rebar, MultiplanarOption

# NumberingSchema/NumberingSchemaTypes give access to Revit's own automatic
# "Rebar Number" (Identity Data) - a real Revit feature distinct from Mark,
# used to cross-reference bars in a BBS (Bar Bending Schedule). Imported
# defensively since this is the first time this tool reads it and the
# exact namespace has not yet been confirmed against Trung's real Revit -
# see get_rebar_numbering_schema() below.
try:
    from Autodesk.Revit.DB import NumberingSchema, NumberingSchemaTypes
except Exception:
    NumberingSchema = None
    NumberingSchemaTypes = None

from pyrevit import revit, forms

TOOL_NAME = 'Rebar Shape Export'

FT_TO_MM = 304.8
TOL = 0.0008  # feet, ~0.25mm - point-coincidence tolerance when chaining curves
DIM_OFFSET_FT = 50.0 / FT_TO_MM  # 50mm - how far the linear dimension line sits off the shape
LEADER_LEN_FT = 50.0 / FT_TO_MM  # 50mm - angular dimension placement-arc radius
RADIAL_LEADER_LEN_FT = 150.0 / FT_TO_MM  # 150mm - radial dimension leader length, kept well past the
# angular dimension's 50mm placement arc so the two don't visually overlap/clutter at the same corner
BAR_GAP_FT = 500.0 / FT_TO_MM  # 500mm - horizontal gap between each bar's shape in the shared view
INVALID_VIEW_CHARS = '\\:{}[]|;<>?`~'
INVALID_FILENAME_CHARS = '\\/:*?"<>|'


# ---------------------------------------------------------------------------
# Standalone logic (no UI / no transaction side effects other than geometry
# creation, which the caller wraps in a Transaction)
# ---------------------------------------------------------------------------

def get_partition_str(rebar):
    """Return the "Partition" parameter value, looked up BY DISPLAY NAME
    first (matches pyNBT's "Select Partition" tool): on Wohhup templates
    "Partition" is a project/shared parameter, not Revit's rarely-used
    built-in worksharing parameter of the same name - looking up the
    built-in enum directly would silently read the wrong (usually empty)
    parameter. Falls back to the built-in only if no named one exists."""
    try:
        p = rebar.LookupParameter('Partition')
        if p is None:
            p = rebar.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
        if p is not None and p.HasValue:
            val = p.AsValueString() or p.AsString()
            if val and val.strip():
                return val.strip()
    except Exception:
        pass
    return 'N/A'


def get_mark_str(rebar):
    try:
        p = rebar.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if p is not None:
            val = p.AsString()
            if val:
                return val
    except Exception:
        pass
    try:
        p = rebar.LookupParameter('Mark')
        if p is not None:
            val = p.AsString()
            if val:
                return val
    except Exception:
        pass
    return 'N/A'


def get_bar_type_name(doc, rebar):
    try:
        bar_type = doc.GetElement(rebar.GetTypeId())
        if bar_type is not None:
            return Element.Name.GetValue(bar_type)
    except Exception:
        pass
    return 'N/A'


def get_bar_diameter_mm(doc, rebar):
    """Bar diameter in mm, trying several property/parameter names since
    the exact one available differs across Revit versions."""
    try:
        bar_type = doc.GetElement(rebar.GetTypeId())
    except Exception:
        return None
    if bar_type is None:
        return None
    for prop_name in ('BarModelDiameter', 'BarNominalDiameter', 'BarDiameter'):
        try:
            value = getattr(bar_type, prop_name)
            if value:
                return round(value * FT_TO_MM, 1)
        except Exception:
            pass
    try:
        param = bar_type.LookupParameter('Bar Diameter')
        if param is not None and param.HasValue:
            return round(param.AsDouble() * FT_TO_MM, 1)
    except Exception:
        pass
    return None


def chain_curves(curve_array):
    """Chain a (possibly out-of-order / reversed) collection of curves
    (Line and/or Arc) into one ordered, connected list, flipping any
    curve whose direction runs against the chain via Curve.CreateReversed()."""
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
    midpoint) - used only to fit the shape's 2D plane, not for drawing."""
    points = []
    for i, c in enumerate(chain):
        if i == 0:
            points.append(c.GetEndPoint(0))
        if isinstance(c, Arc):
            points.append(c.Evaluate(0.5, True))
        points.append(c.GetEndPoint(1))
    return points


def compute_plane_basis(points):
    """Return (origin, x_axis, y_axis) - a local 2D basis that the bent
    shape lies flat on. Falls back to an arbitrary basis for a straight
    (2-point) bar, since a single line has no defined bend plane."""
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
        world_z = XYZ(0, 0, 1)
        world_x = XYZ(1, 0, 0)
        up = world_x if abs(x_axis.DotProduct(world_z)) > 0.99 else world_z
        normal = x_axis.CrossProduct(up).Normalize()

    y_axis = normal.CrossProduct(x_axis).Normalize()
    return origin, x_axis, y_axis


def sanitize_view_name(name):
    out = name
    for ch in INVALID_VIEW_CHARS:
        out = out.replace(ch, '-')
    return out.strip() or 'Untitled'


def sanitize_filename(name):
    out = name
    for ch in INVALID_FILENAME_CHARS:
        out = out.replace(ch, '-')
    return out.strip() or 'Untitled'


def unique_view_name(doc, base_name):
    existing = set()
    for v in FilteredElementCollector(doc).OfClass(View):
        try:
            existing.add(v.Name)
        except Exception:
            pass
    name = base_name
    i = 2
    while name in existing:
        name = '{} ({})'.format(base_name, i)
        i += 1
    return name


def get_drafting_view_family_type(doc):
    for vft in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        if vft.ViewFamily == ViewFamily.Drafting:
            return vft
    return None


def get_default_text_type_id(doc):
    return doc.GetDefaultElementTypeId(ElementTypeGroup.TextNoteType)


def get_default_angular_dim_type(doc):
    """The project's default Angular Dimension type element - required by
    AngularDimension.Create (unlike NewDimension/NewRadialDimension, it has
    no overload that picks a default automatically). Returns None if the
    project genuinely has none, in which case the caller must fall back to
    a text label instead of attempting the native call at all."""
    try:
        type_id = doc.GetDefaultElementTypeId(ElementTypeGroup.AngularDimensionType)
        if type_id is not None:
            return doc.GetElement(type_id)
    except Exception:
        pass
    return None


def get_default_radial_dim_type(doc):
    """The project's default RADIAL-style DimensionType (as opposed to a
    DIAMETER-style one - Revit keeps these as two separate DimensionType
    categories, ElementTypeGroup.RadialDimensionType vs .DiameterDimension
    Type). RadialDimension.Create() takes no DimensionType argument at all
    (only 4 params - document, view, reference, origin), so whichever type
    it lands on internally is outside our control at creation time; found
    2026-09-14 that it can land on a Diameter-style type (Trung's test
    showed 'o320' - a diameter-doubled value with the diameter symbol -
    instead of the expected 'R160' radius label). Explicitly re-assigning
    this type to the freshly created Dimension's .DimensionType property
    right after creation is what actually forces the radius display.
    Returns None if the project genuinely has no Radial type."""
    try:
        type_id = doc.GetDefaultElementTypeId(ElementTypeGroup.RadialDimensionType)
        if type_id is not None:
            return doc.GetElement(type_id)
    except Exception:
        pass
    return None


def get_rebar_numbering_schema(doc):
    """Revit's own Rebar numbering schema (Manage > Structural Settings >
    Rebar > Number) - the source of the automatic "Rebar Number" shown in
    a rebar's Properties panel under Identity Data, distinct from Mark.
    Numbers are auto-assigned per Partition + matching shape/size, exactly
    the identifier Trung wants on each exported bar image so it lines up
    with his BBS (Bar Bending Schedule).

    FIRST ATTEMPT (2026-09-14) at this API - not yet confirmed against
    Trung's real Revit. Per Autodesk API docs/forum research:
    `NumberingSchema.GetNumberingSchema(document, NumberingSchemaTypes.
    StructuralNumberingSchemas.Rebar)`. Returns None if this fails for any
    reason (older Revit version, wrong namespace, project genuinely has no
    schema yet) - callers must treat None as "Rebar Number unavailable"
    and fall back to Mark, never guess/fabricate a number."""
    if NumberingSchema is None or NumberingSchemaTypes is None:
        return None
    try:
        return NumberingSchema.GetNumberingSchema(
            doc, NumberingSchemaTypes.StructuralNumberingSchemas.Rebar
        )
    except Exception:
        return None


def get_rebar_number_str(rebar, numbering_schema):
    """Real Revit-assigned "Rebar Number" for one bar (see
    get_rebar_numbering_schema above). Returns None if unavailable for any
    reason - caller decides the fallback (Mark), and should tell Trung
    that a fallback was used rather than silently mislabeling a file."""
    if numbering_schema is None:
        return None
    try:
        param_id = numbering_schema.NumberingParameterId
        if param_id is None:
            return None
        param = rebar.get_Parameter(param_id)
        if param is not None and param.HasValue:
            val = param.AsValueString()
            if not val:
                val = param.AsString()
            if not val:
                try:
                    val = str(param.AsInteger())
                except Exception:
                    val = None
            if val and val.strip():
                return val.strip()
    except Exception:
        pass
    return None


def export_view_to_png(doc, view, folder, base_name):
    """Export one Drafting View to a single PNG file in `folder`, named
    exactly `base_name` + '.png' (renamed after export - Revit's own
    ExportImage always prefixes the output with the FilePath value plus
    the view's own name, so the raw result is renamed to the clean name
    Trung actually wants). Returns (success, final_path_or_None, error).

    FIRST ATTEMPT (2026-09-14) at PNG export from this tool - not yet
    confirmed against Trung's real Revit. If `base_name` collides with an
    already-exported file in this same run (two bars can legitimately
    share one Rebar Number - Revit's numbering groups identical bars
    within a Partition together on purpose), a numeric suffix ('_2', '_3',
    ...) is appended so nothing is silently overwritten.

    IMPORTANT (found 2026-09-18, v1.5.1): Trung's real test of v1.5.0
    (100 bars selected at once) came back with "Exported 0 bar image(s)"
    and, for the bars that reported a reason at all, the exact .NET error
    "Modification of the document is forbidden. Typically, this is
    because there is no open transaction; consult documentation for
    Document.IsModified for other possible causes." Confirmed via
    Autodesk's own Revit API documentation for Document.Regenerate(): its
    Remarks say "when a transaction is committed there is an automatic
    call to regenerate the document" - i.e. Regenerate() is unnecessary
    right after a Commit() - and its Exceptions section documents this
    EXACT message as what Regenerate() throws when called with no open
    Transaction. This function is always called from main() AFTER the
    temp view's own Transaction (t_img) has already been committed, so
    the `doc.Regenerate()` call that used to be the first line here was
    calling a document-modifying method with no transaction open at
    all - guaranteed to throw every single time it was reached. FIX:
    removed entirely - Commit() already regenerated the document, so
    there is nothing left for this function to force-update before
    exporting."""
    try:
        options = ImageExportOptions()
        options.ExportRange = ExportRange.SetOfViews
        options.ZoomType = ZoomFitType.FitToPage
        options.FitDirection = FitDirectionType.Horizontal
        options.PixelSize = 1200
        options.ImageResolution = ImageResolution.DPI_150
        options.HLRandWFViewsFileType = ImageFileType.PNG
        view_ids = List[ElementId]()
        view_ids.Add(view.Id)
        options.SetViewsAndSheets(view_ids)
        temp_prefix = os.path.join(folder, 'pyNBT_temp_export')
        options.FilePath = temp_prefix
        doc.ExportImage(options)
        try:
            generated_base = options.GetFileName(doc, view.Id)
            generated_path = generated_base + '.png'
        except Exception:
            generated_path = temp_prefix + ' - ' + view.Name + '.png'
        if not os.path.isfile(generated_path):
            return False, None, (
                'ExportImage did not raise, but the expected output file '
                'was not found at: {}'.format(generated_path)
            )
        final_name = base_name
        suffix = 2
        while os.path.isfile(os.path.join(folder, final_name + '.png')):
            final_name = '{}_{}'.format(base_name, suffix)
            suffix += 1
        final_path = os.path.join(folder, final_name + '.png')
        os.rename(generated_path, final_path)
        return True, final_path, ''
    except Exception as ex:
        return False, None, str(ex)


def add_linear_dimension(doc, view, detail_curve, line2d):
    """Real Revit linear Dimension along one straight segment (offset to
    the side so it doesn't sit on top of the line). Returns (True, '') on
    success; (False, error_text) means the caller should fall back to a
    text label carrying the real error, for diagnosis.

    Calls doc.Regenerate() before reading the just-created DetailCurve's
    Reference - a freshly created element's Reference can be unreliable
    until the document regenerates, and this tool now creates many more
    detail elements per transaction than before (every selected bar drawn
    into one shared view instead of its own view), so this is cheap
    insurance against stale/None references.

    IMPORTANT (found 2026-09-14, on Trung's Revit which is a version that
    already has the newer per-type dimension classes): the legacy
    `doc.Create.NewDimension(view, line, referenceArray)` call this used
    to use raised "Invalid number of references." on this Revit version,
    even with the exact same single-reference usage that worked fine on
    earlier versions. Per Autodesk's own Revit API changelog, this Revit
    version added dedicated static factory classes for creating Linear,
    Radial and Arc Length dimensions directly in a PROJECT document (the
    same family AngularDimension already belonged to) - the legacy
    Document.Create methods were effectively superseded by these. The
    correct call now is the static `LinearDimension.Create(document,
    view, line, references)`, where `references` is a .NET
    `List[Reference]` (same convention as AngularDimension.Create), not
    the old `ReferenceArray`.

    IMPORTANT (found 2026-09-14, v1.4.3): Trung's test showed NO linear
    dimension AND no text fallback at all on any straight segment - not
    even an error label - even though `LinearDimension.Create(...)` was
    already checked for a None return in that version and never hit it.
    v1.4.4 added a read-back of the created Dimension's own `.Value`
    right after creation, and Trung's next test CONFIRMED the real cause
    this way: the call creates a real Dimension element (no exception, no
    None) but with `.Value` reading back as None/empty - i.e. a single
    whole-curve reference is not enough for this newer class-based API to
    know what two points to measure between. The legacy `NewDimension`
    used to auto-expand one whole-curve reference into its own two
    endpoints for exactly this purpose; this newer API apparently does
    not do the same.

    FIX (v1.4.5): pass TWO references instead of one - the line's own
    START and END point references, via `Curve.GetEndPointReference(0)`
    and `(1)` (Autodesk's official API: "a stable reference to the start
    point or the end point of the curve", index 0 = start, 1 = end; only
    valid on a bound curve, which a DetailCurve's own GeometryCurve
    always is). These must be read from `detail_curve.GeometryCurve`
    (the curve object that is actually part of the document) and not
    from the local unbound `Line.CreateBound(...)` object used only to
    create the DetailCurve, since only the in-document curve carries
    real, resolvable references. Giving the dimension one witness at
    each end is exactly how a single Revit element's overall length gets
    measured when a whole-curve reference alone comes back empty."""
    try:
        doc.Regenerate()
        doc_curve = detail_curve.GeometryCurve
        ref0 = doc_curve.GetEndPointReference(0)
        ref1 = doc_curve.GetEndPointReference(1)
        if ref0 is None or ref1 is None:
            return False, 'GetEndPointReference returned None for a segment endpoint after Regenerate'
        p0, p1 = line2d.GetEndPoint(0), line2d.GetEndPoint(1)
        direction = (p1 - p0).Normalize()
        offset = XYZ(-direction.Y, direction.X, 0) * DIM_OFFSET_FT
        dim_line = Line.CreateBound(p0 + offset, p1 + offset)
        ref_list = List[Reference]()
        ref_list.Add(ref0)
        ref_list.Add(ref1)
        dim = LinearDimension.Create(doc, view, dim_line, ref_list)
        if dim is None:
            return False, 'LinearDimension.Create returned None with two endpoint references'
        try:
            dim_value = dim.Value
        except Exception as ex_val:
            dim_value = None
            _ = ex_val  # value itself unreadable - treat the same as "empty"
        if dim_value is None or dim_value <= 0:
            try:
                doc.Delete(dim.Id)
            except Exception:
                pass  # not fatal - worst case an unused empty Dimension is left behind
            return False, 'Created Dimension still has no measurable Value even with two endpoint references'
        return True, ''
    except Exception as ex:
        return False, str(ex)


def add_radial_dimension(doc, view, detail_curve, arc2d, radial_dim_type):
    """Real Revit Radial Dimension on one bend arc (auto-labeled 'R...'
    by Revit using the project's own dimension type/units). Returns
    (True, '') on success; (False, error_text) means the caller should
    fall back to a text label carrying the real error, for diagnosis.
    See add_linear_dimension above for why doc.Regenerate() is called
    first.

    IMPORTANT (found 2026-09-14, three rounds): the legacy `doc.Create.
    NewRadialDimension(...)` raised "'Document' object has no attribute
    'NewRadialDimension'" on Trung's Revit - that method has been removed
    on this Revit version in favor of the dedicated static
    `RadialDimension.Create(...)`. First attempt guessed a 5-argument
    signature `(document, view, reference, origin, isDiameterDimension)`
    (by analogy with the Autodesk API changelog's own paraphrase) but
    that raised "Create() takes exactly 4 arguments (5 given)" - proving
    this Revit version's actual overload takes only 4: `(document, view,
    reference, origin)`. Fixing the arg count made the call succeed with
    no exception, but Trung's next test showed the label as 'o320' (a
    doubled value with the diameter symbol) instead of the expected
    'R160' - Revit has two entirely separate DimensionType categories
    (ElementTypeGroup.RadialDimensionType vs .DiameterDimensionType) and
    this 4-arg overload apparently doesn't reliably land on the Radial
    one by default. The fix: explicitly re-assign the created Dimension's
    `.DimensionType` property to the project's actual default Radial
    type right after creation (see get_default_radial_dim_type) - this
    forces the radius-style 'R...' label Trung actually wants.

    `origin` is placed RADIAL_LEADER_LEN_FT (150mm) past the arc, not the
    same LEADER_LEN_FT (50mm) the Angular Dimension's placement arc uses -
    Trung reported (2026-09-14) the Angle and Radius labels landing right
    on top of each other at every bend when both used the same 50mm
    distance from the corner; keeping Radial further out than Angular's
    fixed-radius placement arc keeps the two legible side by side."""
    try:
        doc.Regenerate()
        curve_ref = detail_curve.GeometryCurve.Reference
        if curve_ref is None:
            return False, 'GeometryCurve.Reference is None after Regenerate'
        center = arc2d.Center
        mid_pt = arc2d.Evaluate(0.5, True)
        direction = (mid_pt - center).Normalize()
        origin = mid_pt + direction * RADIAL_LEADER_LEN_FT
        dim = RadialDimension.Create(doc, view, curve_ref, origin)
        if dim is None:
            return False, 'RadialDimension.Create returned None'
        if radial_dim_type is not None:
            try:
                if dim.DimensionType.Id != radial_dim_type.Id:
                    dim.DimensionType = radial_dim_type
            except Exception:
                # Not fatal - the dimension itself was created either way;
                # worst case it keeps showing as diameter-style.
                pass
        return True, ''
    except Exception as ex:
        return False, str(ex)


def add_text_label(doc, view, position, text, text_type_id):
    TextNote.Create(doc, view.Id, position, text, text_type_id)


def add_angle_dimension(doc, view, prev_line, prev_detail, next_line, next_detail,
                         vertex, angular_dim_type, text_type_id):
    """Bend angle at the corner between two straight segments flanking one
    arc, reported the way rebar detailing expects: the interior angle,
    always under 180 degrees (176.19 deg for a near-straight bar, 90 deg
    for a right-angle bend - never its supplement).

    The placement arc for the native Angular Dimension is centered on the
    bend arc's OWN center point (passed in as `vertex`) rather than on
    where the two straight legs would mathematically cross if extended -
    for a shallow bend (legs only a few degrees apart) that crossing point
    can land a meter or more away, which was destabilizing the dimension.
    The bend arc's center always sits right at the real corner, so this
    keeps the dimension small and stable no matter how sharp or shallow
    the bend is.

    Tries a real Revit Angular Dimension first; always computes the number
    itself with plain vector math, so a text fallback (carrying the actual
    Revit exception message, for diagnosis) is available if the native
    call still doesn't match this Revit version's exact API signature.

    IMPORTANT (API history for this one call - three earlier guesses were
    each disproven by the actual .NET error text returned on Trung's
    Revit, not by reasoning about the API in the abstract):
    - There is no "NewAngularDimension" method on `Document.Create` at all
      (raised "'Document' object has no attribute 'NewAngularDimension'").
      Looking it up properly: `NewAngularDimension` only exists on
      `Autodesk.Revit.Creation.FamilyItemFactory` (i.e. `Document.
      FamilyCreate`), which only works inside a FAMILY document - never
      available for Rebar, which always lives in a project document.
    - `Document.Create.NewDimension` (used above for Linear Dimensions)
      only has a `Line` overload, never an `Arc` one, in ANY Revit version
      (confirmed via Revit API docs) - passing an Arc raised "expected
      Line, got Arc". There is no plain `Dimension.Create` static method
      either (a next wrong guess raised "'type' object has no attribute
      'Create'").
    - The actually-correct API, confirmed via Autodesk's own Revit API
      documentation: the dedicated `Autodesk.Revit.DB.AngularDimension`
      class (Revit 2022+) has a static factory method built specifically
      for project documents: `AngularDimension.Create(document, view,
      arc, references, dimensionType)` - `references` must be a .NET
      `List[Reference]` (not the legacy `ReferenceArray` used by the
      older Linear/Radial calls), and `dimensionType` must be an actual
      Angular DimensionType element (there is no auto-pick overload)."""
    d0 = (prev_line.GetEndPoint(1) - prev_line.GetEndPoint(0)).Normalize()
    d1 = (next_line.GetEndPoint(1) - next_line.GetEndPoint(0)).Normalize()
    cos_angle = max(-1.0, min(1.0, d0.DotProduct(d1)))
    bend_angle_deg = 180.0 - math.degrees(math.acos(cos_angle))

    success = False
    error_detail = ''
    if angular_dim_type is None:
        error_detail = 'project has no Angular Dimension type'
    else:
        try:
            ref0 = prev_detail.GeometryCurve.Reference
            ref1 = next_detail.GeometryCurve.Reference
            if ref0 is not None and ref1 is not None:
                # Sweep from the incoming leg REVERSED (pointing back out
                # of the corner, away from the bend) to the outgoing leg.
                # That sweep always equals the interior bend angle itself
                # (<= 180 deg by construction, matching bend_angle_deg
                # above) instead of the sharp turn angle on the other
                # side of the corner.
                rev_d0 = XYZ(-d0.X, -d0.Y, -d0.Z)
                a0 = math.atan2(rev_d0.Y, rev_d0.X)
                a1 = math.atan2(d1.Y, d1.X)
                diff = a1 - a0
                while diff <= -math.pi:
                    diff += 2 * math.pi
                while diff > math.pi:
                    diff -= 2 * math.pi
                if diff >= 0:
                    start_angle, end_angle = a0, a0 + diff
                else:
                    start_angle, end_angle = a0 + diff, a0
                placement_arc = Arc.Create(
                    vertex, LEADER_LEN_FT, start_angle, end_angle,
                    XYZ.BasisX, XYZ.BasisY
                )
                ref_list = List[Reference]()
                ref_list.Add(ref0)
                ref_list.Add(ref1)
                AngularDimension.Create(doc, view, placement_arc, ref_list, angular_dim_type)
                success = True
        except Exception as ex_outer:
            error_detail = str(ex_outer)

    if not success:
        label_text = 'Angle {:.1f} deg'.format(bend_angle_deg)
        if error_detail:
            label_text += ' (dim failed: {})'.format(error_detail[:120])
        add_text_label(doc, view, vertex, label_text, text_type_id)


# ---------------------------------------------------------------------------
# Main per-rebar processing
# ---------------------------------------------------------------------------

def process_rebar(doc, rebar, view, x_offset, angular_dim_type, radial_dim_type, text_type_id):
    """Draw one rebar's shape + dimensions + info block into the shared
    `view`, placed so its leftmost point sits at `x_offset` (feet, in the
    view's own flat 2D space) - this is what lets multiple bars share one
    Drafting View side by side instead of each getting its own view.
    Returns the shape's own width in feet, so the caller can advance
    `x_offset` for the next bar by that width plus a gap."""
    partition = get_partition_str(rebar)
    mark = get_mark_str(rebar)
    bar_type_name = get_bar_type_name(doc, rebar)
    diameter_mm = get_bar_diameter_mm(doc, rebar)
    quantity = rebar.Quantity

    # Full geometry (hooks + real bend radius) -> used only to report the
    # true single-bar length, so the info text stays accurate even though
    # the picture below is simplified.
    full_curves = rebar.GetCenterlineCurves(
        True, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, 0
    )
    length_per_bar_mm = round(
        sum(c.Length for c in full_curves) * FT_TO_MM, 1
    )

    # Drawing geometry: hooks suppressed, but real bend-radius arcs kept
    # (suppressBendRadius=False) so bends draw as true arcs, not sharp
    # corners, and can carry a real Radial Dimension.
    draw_curves = rebar.GetCenterlineCurves(
        True, True, False, MultiplanarOption.IncludeOnlyPlanarCurves, 0
    )
    chain = chain_curves(draw_curves)
    if len(chain) < 1:
        raise Exception('Could not read a usable shape (no curves).')

    ref_points = chain_reference_points(chain)
    basis = compute_plane_basis(ref_points)
    if basis is None:
        raise Exception('All shape points coincide - nothing to draw.')
    origin, x_axis, y_axis = basis

    def to_2d_raw(p3d):
        vec = p3d - origin
        return XYZ(vec.DotProduct(x_axis), vec.DotProduct(y_axis), 0)

    # Fit this bar's own local 2D box first (unshifted), so its width is
    # known, then shift every point so the shape's leftmost point lands
    # exactly at x_offset in the shared view - this is what places each
    # bar side by side in the same Drafting View instead of overlapping.
    pts2d_raw = [to_2d_raw(p) for p in ref_points]
    min_u = min(p.X for p in pts2d_raw)
    max_u = max(p.X for p in pts2d_raw)
    max_v = max(p.Y for p in pts2d_raw)
    shift_x = x_offset - min_u
    shape_width_ft = max_u - min_u

    def to_2d(p3d):
        p = to_2d_raw(p3d)
        return XYZ(p.X + shift_x, p.Y, 0)

    # Pass 1: draw every segment (Line/Arc) and dimension it on its own
    # (linear length for a Line, radius for an Arc). Keep a parallel list
    # so pass 2 can find, for each Arc, the straight segment right before
    # and right after it (needed to dimension the bend angle between them).
    drawn = []
    for curve in chain:
        p0 = to_2d(curve.GetEndPoint(0))
        p1 = to_2d(curve.GetEndPoint(1))
        if p0.DistanceTo(p1) < TOL:
            drawn.append(None)
            continue

        if isinstance(curve, Arc):
            mid = to_2d(curve.Evaluate(0.5, True))
            geo_curve = Arc.Create(p0, p1, mid)
            detail_curve = doc.Create.NewDetailCurve(view, geo_curve)
            ok, err = add_radial_dimension(doc, view, detail_curve, geo_curve, radial_dim_type)
            if not ok:
                radius_mm = round(geo_curve.Radius * FT_TO_MM, 0)
                label_text = 'R{:.0f}'.format(radius_mm)
                if err:
                    label_text += ' (dim failed: {})'.format(err[:100])
                add_text_label(
                    doc, view, geo_curve.Evaluate(0.5, True),
                    label_text, text_type_id
                )
            drawn.append(('arc', geo_curve, detail_curve))
        else:
            geo_curve = Line.CreateBound(p0, p1)
            detail_curve = doc.Create.NewDetailCurve(view, geo_curve)
            ok, err = add_linear_dimension(doc, view, detail_curve, geo_curve)
            if not ok:
                seg_len_mm = round(p0.DistanceTo(p1) * FT_TO_MM, 0)
                mid = (p0 + p1) * 0.5
                direction = (p1 - p0).Normalize()
                offset = XYZ(-direction.Y, direction.X, 0) * DIM_OFFSET_FT
                label_text = '{:.0f}'.format(seg_len_mm)
                if err:
                    label_text += ' (dim failed: {})'.format(err[:100])
                add_text_label(
                    doc, view, mid + offset,
                    label_text, text_type_id
                )
            drawn.append(('line', geo_curve, detail_curve))

    # Pass 2: bend angle at every arc that sits directly between two
    # straight segments (skips an arc at the very start/end of the bar,
    # or two arcs back-to-back, since there is no single angle to show).
    for i, item in enumerate(drawn):
        if item is None or item[0] != 'arc':
            continue
        prev_item = drawn[i - 1] if i - 1 >= 0 else None
        next_item = drawn[i + 1] if i + 1 < len(drawn) else None
        if prev_item and prev_item[0] == 'line' and next_item and next_item[0] == 'line':
            add_angle_dimension(
                doc, view,
                prev_item[1], prev_item[2],
                next_item[1], next_item[2],
                item[1].Center,
                angular_dim_type,
                text_type_id
            )

    info_lines = [
        'Partition: {}'.format(partition),
        'Mark: {}'.format(mark),
        'Bar Type: {}'.format(bar_type_name),
        'Diameter: {} mm'.format(diameter_mm if diameter_mm else 'N/A'),
        'Quantity: {}'.format(quantity),
        'Length per bar: {:.0f} mm'.format(length_per_bar_mm),
        'Total length: {:.0f} mm'.format(length_per_bar_mm * quantity),
    ]
    TextNote.Create(
        doc, view.Id, XYZ(x_offset, max_v + 0.3, 0),
        '\n'.join(info_lines), text_type_id
    )

    return shape_width_ft


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    doc = revit.doc
    selection = revit.get_selection()
    elements = list(selection)

    if not elements:
        forms.alert(
            'Please select one or more Rebar elements first, then run this tool again.',
            title=TOOL_NAME
        )
        return

    rebars = [e for e in elements if isinstance(e, Rebar)]
    skipped_not_rebar = len(elements) - len(rebars)

    if not rebars:
        forms.alert(
            'No Rebar element found in the current selection.\n'
            'This tool only supports plain Rebar elements for now '
            '(not AreaReinforcement / PathReinforcement / FabricSheet).',
            title=TOOL_NAME
        )
        return

    drafting_vft = get_drafting_view_family_type(doc)
    if drafting_vft is None:
        forms.alert(
            'This project has no Drafting view family type - cannot create a view.',
            title=TOOL_NAME
        )
        return
    text_type_id = get_default_text_type_id(doc)
    angular_dim_type = get_default_angular_dim_type(doc)
    radial_dim_type = get_default_radial_dim_type(doc)
    numbering_schema = get_rebar_numbering_schema(doc)

    export_images = forms.alert(
        'Also export a PNG image for each bar (in its own temporary view, '
        'deleted right after export), named by Revit\'s own automatic '
        '"Rebar Number" (Identity Data - not Mark)?',
        title=TOOL_NAME, yes=True, no=True
    )
    export_folder = None
    if export_images:
        export_folder = forms.pick_folder()
        if not export_folder:
            export_images = False

    # All selected bars are drawn into ONE shared Drafting View, side by
    # side left to right, instead of a separate view per bar. Name it
    # after the Partition when every selected bar shares the same one,
    # otherwise a generic batch name.
    partitions = set(get_partition_str(r) for r in rebars)
    if len(partitions) == 1:
        base_name = 'REBAR SHAPE - {}'.format(next(iter(partitions)))
    else:
        base_name = 'REBAR SHAPE - Batch'
    view_name = unique_view_name(doc, sanitize_view_name(base_name))

    created_marks = []
    processed_rebars = []
    errors = []

    t = Transaction(doc, 'pyNBT - Rebar Shape Export')
    t.Start()
    try:
        view = ViewDrafting.Create(doc, drafting_vft.Id)
        view.Name = view_name
        try:
            view.Scale = 5
        except Exception:
            pass

        cursor_x = 0.0
        for rebar in rebars:
            try:
                width_ft = process_rebar(doc, rebar, view, cursor_x, angular_dim_type, radial_dim_type, text_type_id)
                cursor_x += width_ft + BAR_GAP_FT
                created_marks.append(get_mark_str(rebar))
                processed_rebars.append(rebar)
            except Exception as ex:
                mark = get_mark_str(rebar)
                errors.append('Mark {}: {}'.format(mark, str(ex)))
        t.Commit()
    except Exception as ex:
        if t.HasStarted():
            t.RollBack()
        forms.alert('Unexpected error, nothing was created: {}'.format(str(ex)), title=TOOL_NAME)
        return

    image_lines = []
    if export_images:
        image_ok = 0
        image_fail = []
        used_names = set()
        for rebar in processed_rebars:
            rebar_number = get_rebar_number_str(rebar, numbering_schema)
            used_fallback = False
            if not rebar_number:
                rebar_number = get_mark_str(rebar)
                used_fallback = True
            base_name = sanitize_filename(rebar_number)

            temp_view_holder = {}
            t_img = Transaction(doc, 'pyNBT - temp view for image export')
            t_img.Start()
            try:
                temp_view = ViewDrafting.Create(doc, drafting_vft.Id)
                temp_view.Name = unique_view_name(doc, 'pyNBT_temp_export')
                process_rebar(doc, rebar, temp_view, 0.0, angular_dim_type, radial_dim_type, text_type_id)
                t_img.Commit()
                temp_view_holder['view'] = temp_view
            except Exception as ex:
                if t_img.HasStarted():
                    t_img.RollBack()
                image_fail.append('{}: could not build temp view ({})'.format(rebar_number, str(ex)))
                continue

            temp_view = temp_view_holder['view']
            ok, final_path, err = export_view_to_png(doc, temp_view, export_folder, base_name)
            if ok:
                image_ok += 1
                if used_fallback:
                    image_fail.append(
                        '{}: exported OK, but Rebar Number was unavailable - '
                        'used Mark instead for the file name'.format(base_name)
                    )
            else:
                image_fail.append('{}: {}'.format(base_name, err))

            t_cleanup = Transaction(doc, 'pyNBT - cleanup temp export view')
            t_cleanup.Start()
            try:
                doc.Delete(temp_view.Id)
                t_cleanup.Commit()
            except Exception:
                if t_cleanup.HasStarted():
                    t_cleanup.RollBack()

        image_lines.append('')
        image_lines.append('Exported {} bar image(s) to: {}'.format(image_ok, export_folder))
        if image_fail:
            image_lines.append('{} image note(s)/failure(s):'.format(len(image_fail)))
            image_lines.extend('  - {}'.format(m) for m in image_fail)

    summary = [
        'Created 1 drafting view ("{}") with {} rebar shape(s):'.format(view_name, len(created_marks))
    ]
    summary.extend('  - Mark {}'.format(m) for m in created_marks)
    if skipped_not_rebar:
        summary.append('')
        summary.append('Skipped {} non-Rebar element(s) in selection.'.format(skipped_not_rebar))
    if errors:
        summary.append('')
        summary.append('{} rebar(s) could not be processed (likely spiral/'
                        'non-planar shape - not supported yet):'.format(len(errors)))
        summary.extend('  - {}'.format(e) for e in errors)
    summary.extend(image_lines)

    forms.alert('\n'.join(summary), title=TOOL_NAME)


main()
