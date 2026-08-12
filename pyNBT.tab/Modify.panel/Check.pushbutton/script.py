# -*- coding: utf-8 -*-
"""
pyNBT - Check (test tool)

Placeholder tool with no real functionality. Its only purpose is to verify
that the Update mechanism correctly delivers a brand-new pushbutton (not
just a modified file) to every machine running pyNBT - confirm the button
shows up after clicking Update on a machine that doesn't have it yet.

Safe to delete once the test is confirmed.
"""
from pyrevit import forms

forms.alert(
    "Check OK - pyNBT Update is working.\n\n"
    "This is a placeholder test tool with no real function.",
    title="pyNBT - Check"
)
