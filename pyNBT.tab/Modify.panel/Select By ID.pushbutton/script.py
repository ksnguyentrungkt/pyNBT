# -*- coding: utf-8 -*-
"""pyNBT - Select By ID

Selects one or more elements in the active Revit view by their Element
ID - works for ANY element that has an ElementId in the model, not just
typical model elements: walls, families, model lines, detail lines,
reference planes, rooms, tags, dimensions, etc. are all selectable this
way.

Input: paste one or more Element IDs into the text box, separated by
comma, space, tab, or new line - any mix is fine. Extra text pasted
alongside the numbers (e.g. "Id: 1234567" copied from a Revit warning
dialog or schedule cell) is tolerated - every run of digits found in the
whole pasted text is read as one candidate ID.

Logic: each candidate ID is looked up against the current model
(Document.GetElement). IDs that don't resolve to a real element (typo,
ID from a different project, or the element has since been deleted) are
skipped from the selection rather than blocking the whole batch, and are
listed back to Trung in a popup so he knows which ones were skipped.

Output: a re-selection in Revit (Selection.SetElementIds) plus
UIDocument.ShowElements to zoom/pan the active view to fit the selected
element(s) - this also switches the active view automatically when a
given ID belongs to a view-specific element (e.g. a Model Line) that
only exists in a different view than the one currently open.

Runs as a simple MODAL window (window.show(modal=True)) - no
ExternalEvent plumbing needed, since the only Revit API calls happen
directly inside the Select button's click handler, which still executes
inside the tool's own valid Revit API context while the modal dialog is
open (same pattern as pyNBT's Excel Data tool).

Known edge case: a view-specific element (e.g. a Model Line) that exists
only in a view Revit cannot switch to from the current context may still
fail to select - if this happens, Trung sees a clear error popup instead
of the tool silently doing nothing.
"""
from __future__ import unicode_literals

import os
import re
import sys

from pyrevit import forms, revit

from System.Collections.Generic import List
from Autodesk.Revit.DB import ElementId

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))    # ...\Select By ID.pushbutton
PANEL_DIR = os.path.dirname(SCRIPT_DIR)                     # ...\Modify.panel
TAB_DIR = os.path.dirname(PANEL_DIR)                        # ...\pyNBT.tab
EXTENSION_DIR = os.path.dirname(TAB_DIR)                    # ...\pyNBT.extension
LIB_DIR = os.path.join(EXTENSION_DIR, 'lib')

if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyNBT.compat import make_eid

TOOL_NAME = 'Select By ID'
__title__ = 'Select\nBy ID'
__author__ = 'NBT'
__persistentengine__ = True

doc = revit.doc
uidoc = revit.uidoc

ID_PATTERN = re.compile(r'\d+')


# ---------------------------------------------------------------------------
# Standalone logic functions (no UI references - pure parsing / Revit API)
# ---------------------------------------------------------------------------

def parse_ids(text):
    """Extract every run of digits found in the pasted text, in order of
    first appearance, with duplicates removed. Any separator works
    (comma, space, tab, new line, semicolon...) since every digit run in
    the whole text is treated as one candidate ID - this also tolerates
    extra text pasted alongside the numbers (e.g. "Id: 1234567" copied
    from a Revit warning dialog).
    """
    seen = set()
    ordered = []
    for match in ID_PATTERN.findall(text or ''):
        value = int(match)
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def resolve_ids(document, id_values):
    """Look up each candidate integer ID against the model.

    Returns (valid_ids, invalid_values):
    - valid_ids: list[ElementId] that resolve to a real element still in
      the model.
    - invalid_values: list[int] of the original numbers that don't
      (wrong ID, typo, or the element has since been deleted).
    """
    valid_ids = []
    invalid_values = []
    for value in id_values:
        el = None
        try:
            eid = make_eid(value)
            el = document.GetElement(eid)
        except Exception:
            eid = None
        if el is not None:
            valid_ids.append(eid)
        else:
            invalid_values.append(value)
    return valid_ids, invalid_values


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class SelectByIdWindow(forms.WPFWindow):
    def __init__(self, xaml_file):
        forms.WPFWindow.__init__(self, xaml_file)
        self.txtInput.Focus()

    def OnClear(self, sender, args):
        self.txtInput.Text = ''
        self.txtStatus.Text = 'Ready - paste one or more Element IDs above'
        self.txtInput.Focus()

    def OnSelectInRevit(self, sender, args):
        id_values = parse_ids(self.txtInput.Text)
        if not id_values:
            forms.alert(
                'Paste at least one Element ID first.',
                title=TOOL_NAME,
            )
            return

        valid_ids, invalid_values = resolve_ids(doc, id_values)

        if not valid_ids:
            self.txtStatus.Text = '0 selected - no valid ID found'
            forms.alert(
                'None of the ID(s) entered were found in the current '
                'model:\n\n{}'.format(', '.join(str(v) for v in invalid_values)),
                title=TOOL_NAME,
            )
            return

        try:
            id_list = List[ElementId](valid_ids)
            uidoc.Selection.SetElementIds(id_list)
            uidoc.ShowElements(id_list)
        except Exception as ex:
            self.txtStatus.Text = 'Error - see popup for details'
            forms.alert(
                'Could not select the given element(s) in the current '
                'view.\n\n{}'.format(ex),
                title=TOOL_NAME,
            )
            return

        msg = 'Selected {} element(s)'.format(len(valid_ids))
        if invalid_values:
            msg += ', {} invalid ID(s) skipped'.format(len(invalid_values))
        self.txtStatus.Text = msg

        if invalid_values:
            forms.alert(
                'Selected {} element(s) in Revit.\n\n{} ID(s) not found '
                'and skipped:\n{}'.format(
                    len(valid_ids),
                    len(invalid_values),
                    ', '.join(str(v) for v in invalid_values),
                ),
                title=TOOL_NAME,
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

window = SelectByIdWindow('ui.xaml')
window.show(modal=True)
