# pyNBT — Tool Build Rules

This file is what Claude reads before writing any pyNBT tool. It is derived
from the actual code already in this extension (not invented) — every
convention below matches an existing tool. New tools must follow it unless
NBT explicitly asks for an exception.

## 1. Folder & naming

- Extension root: `pyNBT.extension\`
  - `pyNBT.tab\` — production tab, tools NBT actually uses day to day
  - `pyNBT Dev.tab\` — staging tab for tools not yet confirmed working in a
    real Revit test
  - `lib\pyNBT\` — shared code (`compat.py`, `theme.py`). Import it, never
    copy-paste its contents into a tool.
- One tool = one `{ToolName}.pushbutton\` folder, containing at minimum:
  - `script.py` (required)
  - `bundle.yaml` (required — `title` + `tooltip`, optionally `author`)
  - `icon.png` (required — 96×96, drawn at 384×384 and downsampled with
    LANCZOS for crisp anti-aliasing, per the icon-guide)
  - optionally a second `.py` file when the tool has a large standalone
    data table or logic block worth editing on its own (e.g.
    `rebar_bend_standards.py`, `rebar_crank_logic.py`)
- Panel = a group of related tools. Current panels: `Modify`, `Rebar`,
  `Viaduct` (production), `Setup`, `Update` (dev/staging). Pick the closest
  existing panel first; only propose a new one when nothing fits.

## 2. Script header (every `script.py`)

```python
# -*- coding: utf-8 -*-
"""<Tool Name>

<what it does, how to select/run it, edge cases — plain language, this is
what NBT and future-you read to remember what the tool does>
"""

__title__ = 'Tool\nName'   # matches the button label, \n where it wraps
__author__ = 'NBT'
```

`TOOL_NAME = '...'` is declared as a constant near the top of the file and
reused in every `forms.alert(..., title=TOOL_NAME)` call.

## 3. Shared lib import (mandatory bootstrap block)

Every tool that needs `pyNBT.compat` or `pyNBT.theme` starts with this exact
block (copy it, never re-derive the path or re-declare a color/helper
locally):

```python
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))   # ...\{Tool}.pushbutton
PANEL_DIR = os.path.dirname(SCRIPT_DIR)                    # ...\{Panel}.panel
TAB_DIR = os.path.dirname(PANEL_DIR)                       # ...\pyNBT.tab (or pyNBT Dev.tab)
EXTENSION_DIR = os.path.dirname(TAB_DIR)                   # ...\pyNBT.extension
LIB_DIR = os.path.join(EXTENSION_DIR, 'lib')

if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyNBT.compat import eid_int          # only what's actually used
from pyNBT.theme import CLR_HEADER, brush  # only if the tool has a WPF window
```

## 4. Safe-Stack (allowed imports)

**Always OK:** `os`, `sys`, `math`, `json`, `csv`, `re`, `datetime`, `clr`,
`System.Windows.*` (WPF, only after the matching `clr.AddReference`),
`Autodesk.Revit.DB` / `.UI` / `.DB.Structure` / `.Exceptions`,
`pyrevit.revit` / `.forms` / `.script`.

**Ask NBT before using:** any DLL not already vendored in pyRevit or the
Revit API; any Python-3-only library (pandas, numpy, openpyxl) — IronPython
2.7 cannot run these; a library suggested by another AI/tool that needs a
separate pip install into IronPython.

**Never:** COM automation (`System.Activator.CreateInstance`, Excel COM —
known to break in pyRevit's IronPython, use paste-based input instead);
unlicensed/copyleft code pasted from the internet; `System.Windows.Forms`
dialogs inside a modeless WPF window (use `Microsoft.Win32.OpenFileDialog`
instead — the Forms version returns `Nullable<bool>`, not `DialogResult`,
and is unreliable in this context).

## 5. Transactions & safety

- Every model write is wrapped in `Transaction(doc, 'pyNBT - {Tool Name}')`
  with `try/except` + `RollBack()` on error — same pattern as
  `RebarRehost.pushbutton/script.py`.
- A modeless WPF window uses `ExternalEvent`/`IExternalEventHandler`; any UI
  update coming from an event handler goes through
  `window.Dispatcher.Invoke(System.Action(...))`.
- Read `RebarBarType.Name` (or any `ElementType.Name`) via
  `Element.Name.GetValue(el)`, never plain `.Name` — direct attribute read
  throws in this IronPython engine even though writing to it works fine.
- **3-step test before a tool ever touches a real project:** (1) run on a
  detached/test file, (2) NBT confirms the result visually in Revit, (3)
  only then use it on a live project. No tool goes near a real deliverable
  model on its first run.

## 6. UI (when the tool has a window)

- Colors: import from `lib/pyNBT/theme.py` (`CLR_HEADER`, `CLR_APPLY`,
  `CLR_BG`, `CLR_BORDER`, ...) — never declare a new palette in a tool.
- Layout: header (title + version/date) / body (left = selection controls,
  right = preview or results) / footer (status text + primary action button
  in `CLR_APPLY`, secondary buttons neutral).
- WPF gotchas: `clr.AddReference` for `PresentationFramework`,
  `PresentationCore`, `WindowsBase`, `System.Xaml` must all come before any
  `System.Windows` import; `GridLength` needs `float()` conversion
  (`GridLength(float("60"))`, not `GridLength("60")`).

## 7. Versioning

- `VERSION` file at the extension root (currently `1.0.9`) plus a matching
  git tag — bump on a meaningfully-tested batch of changes, not on every
  draft.
- `pyNBT Dev.tab` holds tools NBT hasn't confirmed working in a real Revit
  test yet. Once confirmed, the tool moves into `pyNBT.tab` (correct panel)
  in the same step that marks it `active` in `TOOL_REGISTER.md`.

## 8. Before writing anything

Check `TOOL_REGISTER.md` first. If a tool close to the request already
exists, extend it instead of creating a near-duplicate.
