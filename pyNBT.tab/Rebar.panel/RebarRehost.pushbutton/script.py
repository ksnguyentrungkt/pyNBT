# -*- coding: utf-8 -*-
"""Rebar Re-host

Change the host of selected rebar bars to a new host object (a Twin
DirectShape created by the pyNBT "Generic Twin" tool), WITHOUT deleting and
recreating the rebar - preserves ElementId, Mark, and existing schedule/tag
links.

Selection: select the rebar bars you want to re-host TOGETHER WITH exactly
one Twin (DirectShape, Generic Models category) object in a single
selection, then run this tool. The tool automatically tells the rebar
apart from the Twin host inside that one selection - no need to pick them
in two separate steps.

Reference: mirrors the "Change Host Of Rebars To New Direct Shape" command
from the Rebar Editor add-in NBT uses as a reference.
"""

__title__ = 'Rebar\nRe-host'
__author__ = 'NBT'

import os
import sys

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import Transaction, BuiltInCategory, ElementId
from Autodesk.Revit.DB.Structure import Rebar
from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import revit, forms

# --- pyNBT shared lib --------------------------------------------------
# script.py lives at:
#   pyNBT.extension/pyNBT.tab/{Panel}.panel/RebarRehost.pushbutton/script.py
# shared lib package lives at:
#   pyNBT.extension/lib/pyNBT/
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))   # ...\RebarRehost.pushbutton
PANEL_DIR = os.path.dirname(SCRIPT_DIR)                    # ...\{Panel}.panel
TAB_DIR = os.path.dirname(PANEL_DIR)                       # ...\pyNBT.tab
EXTENSION_DIR = os.path.dirname(TAB_DIR)                   # ...\pyNBT.extension
LIB_DIR = os.path.join(EXTENSION_DIR, 'lib')

if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyNBT.compat import eid_int

# ------------------------------------------------------------------------

doc = revit.doc
uidoc = revit.uidoc

TOOL_NAME = 'Rebar Re-host'
GENERIC_MODEL_CAT_ID = ElementId(BuiltInCategory.OST_GenericModel)


def is_host_candidate(element):
    """True if element could be the new Twin host: not Rebar, and its
    category is Generic Models (the category DirectShape Twins are
    created in by the Generic Twin tool)."""
    if element is None:
        return False
    category = element.Category
    if category is None:
        return False
    return category.Id == GENERIC_MODEL_CAT_ID


def classify_selection(document, element_ids):
    """Split a mixed selection into (rebar_list, host_candidates, skipped_count).

    rebar_list      -> Rebar elements to re-host
    host_candidates -> non-Rebar elements in the Generic Models category
                        (the Twin host is expected to be exactly one of these)
    skipped_count   -> everything else (walls, columns, etc. picked by mistake) -
                        silently ignored, does not block the run
    """
    rebar_list = []
    host_candidates = []
    skipped_count = 0

    for eid in element_ids:
        element = document.GetElement(eid)
        if element is None:
            continue
        if isinstance(element, Rebar):
            rebar_list.append(element)
        elif is_host_candidate(element):
            host_candidates.append(element)
        else:
            skipped_count += 1

    return rebar_list, host_candidates, skipped_count


def rehost_rebars(document, rebar_list, host_id):
    """Call Rebar.SetHostId(document, host_id) for every rebar in rebar_list.

    Each call is isolated in its own try/except so one invalid rebar (e.g.
    host rejected by RebarHostData) cannot break the whole batch.
    Returns (success_count, failed_list) where failed_list holds
    (rebar_element, error_message) tuples.
    """
    success_count = 0
    failed_list = []

    for rebar in rebar_list:
        try:
            rebar.SetHostId(document, host_id)
            success_count += 1
        except Exception as ex:
            failed_list.append((rebar, str(ex)))

    return success_count, failed_list


def get_selection():
    """Use the current pre-selection if there is one; otherwise prompt the
    user to pick elements (rebar bars + one Twin host) on screen."""
    pre_selected_ids = list(uidoc.Selection.GetElementIds())
    if pre_selected_ids:
        return pre_selected_ids

    try:
        picked_refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            'Select the rebar bars to re-host together with the new Twin '
            'host object, then click Finish'
        )
    except OperationCanceledException:
        return None

    return [ref.ElementId for ref in picked_refs]


def main():
    element_ids = get_selection()
    if not element_ids:
        return

    rebar_list, host_candidates, skipped_count = classify_selection(doc, element_ids)

    if not host_candidates:
        forms.alert(
            'No Twin host found in the selection.\n\n'
            'Select the rebar bars together with exactly one Generic Model '
            '(Twin) object, then run this tool again.',
            title=TOOL_NAME
        )
        return

    if len(host_candidates) > 1:
        forms.alert(
            'More than one Generic Model object found in the selection '
            '({0}).\n\nSelect the rebar bars together with exactly ONE '
            'Twin object, then run this tool again.'.format(len(host_candidates)),
            title=TOOL_NAME
        )
        return

    if not rebar_list:
        forms.alert(
            'No rebar found in the selection.\n\n'
            'Select the rebar bars to re-host together with the Twin host '
            'object.',
            title=TOOL_NAME
        )
        return

    host = host_candidates[0]
    host_id = host.Id

    t = Transaction(doc, 'pyNBT - Rebar Re-host')
    t.Start()
    try:
        success_count, failed_list = rehost_rebars(doc, rebar_list, host_id)
        t.Commit()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        forms.alert('Error: {0}'.format(str(ex)), title=TOOL_NAME)
        return

    total = len(rebar_list)
    lines = [
        'Re-hosted {0}/{1} rebar bar(s) to Twin #{2}.'.format(
            success_count, total, eid_int(host_id)
        )
    ]

    if skipped_count:
        lines.append('Skipped {0} non-rebar element(s) in the selection.'.format(skipped_count))

    if failed_list:
        lines.append('')
        lines.append('Failed ({0}):'.format(len(failed_list)))
        for rebar, err in failed_list:
            lines.append('  - Rebar #{0}: {1}'.format(eid_int(rebar.Id), err))

    forms.alert('\n'.join(lines), title=TOOL_NAME)


main()
