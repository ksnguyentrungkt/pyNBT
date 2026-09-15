# pyNBT Tool Register

Every tool in `pyNBT.extension`. Check here before starting a new one — if
something close already exists, extend it instead of duplicating.
Last synced with the real `E:\101.Revit Add-Ins\pyNBT.extension` folder on
2026-09-14.

## pyNBT.tab (production — live in Revit)

### Modify.panel

| Tool | Folder | Status | Purpose |
|---|---|---|---|
| Align Tag | `Align Tag.pushbutton` | active | Rotate Floor/Beam/Column/Wall/Foundation tags and/or text notes to run parallel to a picked reference line |
| Datum Control | `Datum Bubble Control.pushbutton` | active | Batch show/hide the end bubble for Grids and Levels in the active view |
| Generic Twin | `Generic Twin.pushbutton` | active | Duplicate an element's geometry into a lightweight Generic Model "twin" (for hosting rebar / setting cover without slowing the model) |
| Join Control | `Join Control.pushbutton` | active | Batch Allow/Disallow Join for Structural Framing, Structural Columns, and Walls |
| Parameter Transfer | `Parameter Transfer.pushbutton` | active | Convert a Project Parameter into a real Shared Parameter (GUID), copying and verifying every value first (CSV backup) |
| Quick Color | `Quick Color.pushbutton` | active | Apply a fast view-specific graphic override color (surface + cut, solid fill) to the current selection or picked elements |
| Select By Family | `SelectByFamily.pushbutton` | active (confirmed working in Revit by NBT, 2026-09-11) | Group the current selection by Family name (counts shown), tick Family(ies), re-select only those elements in Revit |
| Wall Top Elevation | `Wall Top Elevation.pushbutton` | active — **missing bundle.yaml, purpose not confirmed** | (need NBT to confirm) |

### Rebar.panel

| Tool | Folder | Status | Purpose |
|---|---|---|---|
| Crank Rebar | `CutCrankRebar.pushbutton` | active | Cut a straight rebar at a picked point and crank the continuing segment by 1×bar diameter, with lap-splice presets per project |
| Rebar Re-host | `RebarRehost.pushbutton` | active | Re-host selected rebar to a new Twin (DirectShape) object without losing ElementId/Mark/tag links |
| Renumber | `Renumber.pushbutton` | active | Renumber Rebar Number / Schedule Mark / Mark / Comments / Partition of selected rebar in reading order |
| Select Partition | `SelectByPartition.pushbutton` | active | Statistic every Partition value on rebar/reinforcement elements in the model, then select the matching ones |

### Viaduct.panel

| Tool | Folder | Status | Purpose |
|---|---|---|---|
| Add Points | `Add Points.pushbutton` | active | Place survey coordinate points — native ReferencePoint in family docs, triangular DirectShape in project docs |
| AutoCableSolid | `AutoCableSolid.pushbutton` | active — **missing bundle.yaml** | Build cable solids: Straight (Swept Blend along a picked line) or Curved (Loft along a station/height profile) |

## pyNBT Dev.tab (staging — not yet confirmed live, or utility tools)

### Setup.panel

| Tool | Folder | Status | Purpose |
|---|---|---|---|
| Rebar Bend Standard | `RebarBendStandardSetup.pushbutton` | draft (v3.1 — TCVN 5574:2018 verified + Singapore BS8666/EN1992-1-1, awaiting NBT's live test) | Auto-configure bend diameter (Standard/Hook/Tie/Max Radius) on every RebarBarType per selected national standard |

### Modify.panel

| Tool | Folder | Status | Purpose |
|---|---|---|---|
| Excel Data | `Excel Data.pushbutton` | draft (v1.0.2 — awaiting NBT's live test on this office PC) | Export any Schedule to Excel and re-import the edited file to update the matching elements' parameters. Merges NBT's original Export/Import scripts into one window; generalized to any parameter and any category, and fixes a row/element mismatch bug in the original (now reads each field per-element instead of trusting schedule row order) |

### Update.panel

| Tool | Folder | Status | Purpose |
|---|---|---|---|
| Update | `Update.pushbutton` | active | Pull the latest pyNBT tools from the shared GitHub repo and reload pyRevit automatically |

## Notes

- `Wall Top Elevation` and `AutoCableSolid` have no `bundle.yaml` — pyRevit
  is likely falling back to a default tooltip. Worth a quick fix pass.
- The extension is already a git repo with tags `1.0.3`–`1.0.9` — new
  entries in this register should stay in sync with the next version bump.
- **2026-09-11:** `Select By Family` was promoted from `pyNBT Dev.tab\Modify.panel`
  to `pyNBT.tab\Modify.panel` after NBT confirmed it works. Claude's session
  had no file-delete capability at the time, so the old copy under
  `pyNBT Dev.tab\Modify.panel\SelectByFamily.pushbutton\` was left behind —
  NBT deleted it manually. If this note is still here, that folder may
  still need deleting.
