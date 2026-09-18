# -*- coding: utf-8 -*-
"""Split Rebar to Single

Converts every selected Rebar Set (a Rebar with more than one bar,
Layout Rule != Single) and every selected true Free Form Rebar (Shape =
Free Form) into plain single Rebar elements (Layout = Single) - one per
physical bar - while keeping each bar's exact original shape and
dimensions (every straight length, bend radius, and hook curl is read
straight from the original bar's real 3D geometry, not recomputed).

How to use: select the Rebar Set(s) and/or Free Form Rebar(s) to split in
the model, then run this tool. Non-Rebar elements in the selection are
skipped. Rebar that is already Layout = Single (and not Free Form) is
left untouched.

This is a destructive, delete-and-recreate operation (the original
Set/Free Form bar is deleted once its replacement bars are built), so the
tool asks for confirmation before touching anything.

Known trade-off: hooks are kept as baked-in curve geometry on the new
bars rather than re-assigned as a real Hook Type parameter, so the new
bar's shape/length is always identical to the original, but its Hook
Type parameter will read "None". A genuinely 3D/warped Free Form shape
(not flat) cannot be represented by a standard single Rebar and is
skipped with an error message instead of being forced into a wrong flat
shape.
"""

__title__ = 'Split Rebar\nto Single'
__author__ = 'NBT'

import os
import sys

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import Transaction
from Autodesk.Revit.DB.Structure import Rebar
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import revit, forms

# --- pyNBT shared lib --------------------------------------------------
# script.py lives at:
#   pyNBT.extension/{Tab}/{Panel}.panel/SplitRebarToSingle.pushbutton/script.py
# shared lib package lives at:
#   pyNBT.extension/lib/pyNBT/
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))   # ...\SplitRebarToSingle.pushbutton
PANEL_DIR = os.path.dirname(SCRIPT_DIR)                    # ...\{Panel}.panel
TAB_DIR = os.path.dirname(PANEL_DIR)                       # ...\pyNBT(.Dev).tab
EXTENSION_DIR = os.path.dirname(TAB_DIR)                   # ...\pyNBT.extension
LIB_DIR = os.path.join(EXTENSION_DIR, 'lib')

if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyNBT.compat import eid_int

if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from split_rebar_logic import classify_rebar, convert_rebar_to_singles

# ------------------------------------------------------------------------

doc = revit.doc
uidoc = revit.uidoc

TOOL_NAME = 'Split Rebar to Single'


def classify_selection(document, element_ids):
    """Split the current selection into (rebar_list, skipped_count) -
    skipped_count covers any non-Rebar element picked by mistake (walls,
    AreaReinforcement, etc.) and never blocks the tool."""
    rebar_list = []
    skipped_count = 0
    for eid in element_ids:
        element = document.GetElement(eid)
        if element is None:
            continue
        if isinstance(element, Rebar):
            rebar_list.append(element)
        else:
            skipped_count += 1
    return rebar_list, skipped_count


def build_confirmation_message(rebar_list):
    set_count = 0
    freeform_count = 0
    single_count = 0
    for rebar in rebar_list:
        kind = classify_rebar(rebar)
        if kind == 'set':
            set_count += 1
        elif kind == 'freeform':
            freeform_count += 1
        else:
            single_count += 1

    lines = ['This will delete and rebuild the following as single bars:']
    if set_count:
        lines.append('  - {0} Rebar Set(s)'.format(set_count))
    if freeform_count:
        lines.append('  - {0} Free Form Rebar(s)'.format(freeform_count))
    if single_count:
        lines.append('')
        lines.append('{0} bar(s) already Single will be left untouched.'.format(single_count))

    if not set_count and not freeform_count:
        return None

    lines.append('')
    lines.append('This cannot be undone by this tool (use Revit Undo if needed). Continue?')
    return '\n'.join(lines)


def main():
    element_ids = list(uidoc.Selection.GetElementIds())
    if not element_ids:
        forms.alert(
            'Please select the Rebar Set(s) / Free Form Rebar(s) to split '
            'first, then run this tool again.',
            title=TOOL_NAME,
        )
        return

    rebar_list, skipped_count = classify_selection(doc, element_ids)
    if not rebar_list:
        forms.alert(
            'No Rebar element found in the selection.',
            title=TOOL_NAME,
        )
        return

    confirm_message = build_confirmation_message(rebar_list)
    if confirm_message is None:
        forms.alert(
            'Every selected Rebar is already Single - nothing to split.',
            title=TOOL_NAME,
        )
        return

    proceed = forms.alert(confirm_message, title=TOOL_NAME, yes=True, no=True)
    if not proceed:
        return

    set_done = 0
    set_bar_total = 0
    freeform_done = 0
    single_skipped = 0
    errors = []

    t = Transaction(doc, 'pyNBT - Split Rebar to Single')
    t.Start()
    try:
        for rebar in rebar_list:
            original_id = eid_int(rebar.Id)
            try:
                result = convert_rebar_to_singles(doc, rebar)
            except Exception as ex:
                errors.append((original_id, str(ex)))
                continue

            if result['kind'] == 'single':
                single_skipped += 1
            elif result['kind'] == 'set':
                set_done += 1
                set_bar_total += result['new_count']
            elif result['kind'] == 'freeform':
                freeform_done += 1
            elif result['kind'] == 'error':
                errors.append((original_id, result['message']))
        t.Commit()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        forms.alert('Error: {0}'.format(str(ex)), title=TOOL_NAME)
        return

    lines = []
    if set_done:
        lines.append('Split {0} Rebar Set(s) into {1} single bar(s).'.format(set_done, set_bar_total))
    if freeform_done:
        lines.append('Converted {0} Free Form Rebar(s) into single bar(s).'.format(freeform_done))
    if single_skipped:
        lines.append('{0} bar(s) already Single - left untouched.'.format(single_skipped))
    if skipped_count:
        lines.append('Skipped {0} non-Rebar element(s) in the selection.'.format(skipped_count))
    if not lines:
        lines.append('Nothing was changed.')

    if errors:
        lines.append('')
        lines.append('Failed ({0}) - left untouched:'.format(len(errors)))
        for eid_val, err in errors:
            lines.append('  - Rebar #{0}: {1}'.format(eid_val, err))

    forms.alert('\n'.join(lines), title=TOOL_NAME)


main()
