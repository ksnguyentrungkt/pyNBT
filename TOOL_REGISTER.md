# pyNBT Tool Register

Every tool in `pyNBT.extension`. Check here before starting a new one — if
something close already exists, extend it instead of duplicating.

**Last synced with the real `E:\101.Revit Add-Ins\pyNBT.extension` folder on
2026-09-18, by re-running `device_list_dir` on every tab/panel — not just
trusting the previous copy of this file.** See the incident note at the
bottom before assuming any path in here is current: this file had been
silently wrong for two days.

## pyNBT.tab (Setup / Modify / Update)

### Setup.panel

| Tool | Folder | Status | Purpose |
|---|---|---|---|
| Rebar Bend Standard | `RebarBendStandardSetup.pushbutton` | draft (v3.1 — TCVN 5574:2018 verified + Singapore BS8666/EN1992-1-1, awaiting NBT's live test) | Auto-configure bend diameter (Standard/Hook/Tie/Max Radius) on every RebarBarType per selected national standard |

### Modify.panel

| Tool | Folder | Status | Purpose |
|---|---|---|---|
| Align Tag | `Align Tag.pushbutton` | active | Rotate Floor/Beam/Column/Wall/Foundation tags and/or text notes to run parallel to a picked reference line |
| Copy Sheet + Views | `CopySheetWithViews.pushbutton` | draft (v1.0, 2026-09-16 — awaiting NBT's live test) | Batch-copy one or more sheets together with their views onto new sheets: tick source sheets, type new Sheet Numbers (validated for duplicates), reuses the same Legend/Schedule instance on the new sheet (Revit's native behavior, not a copy), offers "With Detailing" vs plain "Duplicate" for other view types. Does not copy sheet-level parameters or revision clouds. See project doc "copy-sheet-with-views-tool.md" |
| Datum Control | `Datum Bubble Control.pushbutton` | active | Batch show/hide the end bubble for Grids and Levels in the active view |
| Excel Data | `Excel Data.pushbutton` | draft (v1.0.2 — awaiting NBT's live test on this office PC) | Export any Schedule to Excel and re-import the edited file to update the matching elements' parameters. Merges NBT's original Export/Import scripts into one window; generalized to any parameter and any category, and fixes a row/element mismatch bug in the original (now reads each field per-element instead of trusting schedule row order) |
| Generic Twin | `Generic Twin.pushbutton` | active | Duplicate an element's geometry into a lightweight Generic Model "twin" (for hosting rebar / setting cover without slowing the model) |
| Join Control | `Join Control.pushbutton` | active | Batch Allow/Disallow Join for Structural Framing, Structural Columns, and Walls |
| Parameter Transfer | `Parameter Transfer.pushbutton` | active | Convert a Project Parameter into a real Shared Parameter (GUID), copying and verifying every value first (CSV backup) |
| Quick Color | `Quick Color.pushbutton` | active | Apply a fast view-specific graphic override color (surface + cut, solid fill) to the current selection or picked elements |
| Select By Family | `SelectByFamily.pushbutton` | active (confirmed working in Revit by NBT, 2026-09-11) | Group the current selection by Family name (counts shown), tick Family(ies), re-select only those elements in Revit |
| Select By ID | `Select By ID.pushbutton` | **undocumented in this register — found 2026-09-18 during a folder re-sync, purpose not confirmed** | (need NBT/Claude to confirm and backfill) |
| Wall Top Elevation | `Wall Top Elevation.pushbutton` | active — **missing bundle.yaml, purpose not confirmed** | (need NBT to confirm) |

### Update.panel

| Tool | Folder | Status | Purpose |
|---|---|---|---|
| Update | `Update.pushbutton` | active | Pull the latest pyNBT tools from the shared GitHub repo and reload pyRevit automatically |

## pyNBT Rebar.tab (all rebar + viaduct tools live here, mix of active + draft)

NBT reorganized this tab himself on 2026-09-17 (outside any Claude session),
consolidating what used to be `pyNBT.tab\Rebar.panel` + `pyNBT.tab\Viaduct.panel`
and `pyNBT Dev.tab\Rebar.panel` into this one dedicated tab. See the incident
note at the bottom.

### Rebar.panel

| Tool | Folder | Status | Purpose |
|---|---|---|---|
| ApplyMasterBarShape | `ApplyMasterBarShape.pushbutton` | **undocumented in this register — predates the 2026-09-14 sync, purpose not re-confirmed** | (need NBT/Claude to confirm and backfill) |
| CircularRebarArray | `CircularRebarArray.pushbutton` | **undocumented in this register — predates the 2026-09-14 sync, purpose not re-confirmed** | (need NBT/Claude to confirm and backfill) |
| Crank Rebar | `CutCrankRebar.pushbutton` | active | Cut a straight rebar at a picked point and crank the continuing segment by 1×bar diameter, with lap-splice presets per project |
| Rebar Shape Export | `Rebar Shape Export.pushbutton` | **undocumented in this register — predates the 2026-09-14 sync; actually at v1.5.0+ per project doc "rebar-shape-export.md", purpose not re-confirmed here** | Export the true bent shape of selected Rebar into a Drafting View with real Dimensions (Linear/Radial/Angular) + optional per-bar PNG export named by Rebar Number |
| Rebar Re-host | `RebarRehost.pushbutton` | active | Re-host selected rebar to a new Twin (DirectShape) object without losing ElementId/Mark/tag links |
| Rebar Segment | `RebarSegment.pushbutton` | draft (v1.1.1 — simplifies "Parallel" Layout Mode per NBT, before he'd even tested v1.1.0: (1) direction is now a Model/Detail Line NBT draws himself with Revit's own Line tool then picks, instead of clicking 2 approximate points; (2) only 2 side faces picked (not 4) — applied to every bar's own 2 ends unconditionally; a new "Edge Distance" number replaces the 2 end-face picks for setting back the first/last row from A-b/c-d. "Rail-to-rail" mode (still 4 side faces) and v1.0.7's cover fix are unchanged. Delivered to the correct path and byte-verified 2026-09-18 — see incident note below. Awaiting NBT's first live test of BOTH v1.0.7's cover fix and Parallel mode) | Drape top-layer rebar across a warped/twisted host face (e.g. a ramp transition slab or a curved wall): pick a top face + 4 corner points (A, b, c, d) + side faces, choose a Layout Mode (rail-to-rail or parallel+clip), bars follow the face's real curved surface, cover measured to the bar's outer surface. V1 = straight bars only, no auto hook. See project doc "rebar-segment-warped-face-tool.md" |
| Renumber | `Renumber.pushbutton` | active | Renumber Rebar Number / Schedule Mark / Mark / Comments / Partition of selected rebar in reading order |
| Select Partition | `SelectByPartition.pushbutton` | active | Statistic every Partition value on rebar/reinforcement elements in the model, then select the matching ones |
| Split Rebar to Single | `SplitRebarToSingle.pushbutton` | draft (v1.0.3 — root cause found and verified live on NBT's real curved-wall model: `Rebar.CreateFromCurves`'s `useExistingShapeIfPossible=True` was silently snapping the new bar to an unrelated, wrong RebarShape family already in the project; fixed by setting it `False`. Verified on 2 real cases: a 20-bar column Set and the 25-bar Free Form curved-wall bar that originally broke. Not yet confirmed by NBT clicking the button himself in pyRevit — only run so far via Claude/revit-mcp) | Convert a selected Rebar Set or Free-Form Rebar into individual single Rebar elements, one per real bar position, preserving each bar's exact shape/length/hooks-as-curve-geometry (the "Hook Type" parameter shows "None" on the new bars — schedule/BBS impact not yet confirmed) and copying the Partition value across. See project doc "split-rebar-to-single-tool.md" |
| Trim Rebar to Length | `TrimRebarLength.pushbutton` | draft (v1.0.2 — 2 hotfixes shipped: v1.0.1 fixed a bar-loss bug via per-bar SubTransaction rollback, v1.0.2 fixed a `RebarHookOrientation` None crash on bars with no hook at the kept end; v1.0.2 not yet re-tested live by NBT) | Trim a selected single Rebar (Rebar Sets not yet supported) to a desired remaining length from one end (Keep Top/Keep Bottom, determined from `view.UpDirection`), preserving the exact shape and hooks of the kept portion. See project doc "trim-rebar-length-tool.md" |

### Viaduct.panel

| Tool | Folder | Status | Purpose |
|---|---|---|---|
| Add Points | `Add Points.pushbutton` | active | Place survey coordinate points — native ReferencePoint in family docs, triangular DirectShape in project docs |
| AutoCableSolid | `AutoCableSolid.pushbutton` | active — **missing bundle.yaml** | Build cable solids: Straight (Swept Blend along a picked line) or Curved (Loft along a station/height profile) |

## pyNBT Dev.tab (stale — effectively empty, needs cleanup)

Contains only a stray, orphaned `Rebar.panel\RebarSegment.pushbutton` copy —
NOT loaded by Revit as a live tool location, just leftover files. Safe for
NBT to delete the whole `pyNBT Dev.tab` folder once he's confirmed nothing
else references it; no tool actually runs from here anymore.

## Notes

- `Wall Top Elevation` and `AutoCableSolid` have no `bundle.yaml` — pyRevit
  is likely falling back to a default tooltip. Worth a quick fix pass.
- The extension is already a git repo with tags `1.0.3`–`1.0.9` — new
  entries in this register should stay in sync with the next version bump.
- **2026-09-11:** `Select By Family` was promoted from `pyNBT Dev.tab\Modify.panel`
  to `pyNBT.tab\Modify.panel` after NBT confirmed it works. Claude's session
  had no file-delete capability at the time, so the old copy under
  `pyNBT Dev.tab\Modify.panel\SelectByFamily.pushbutton\` was left behind —
  NBT deleted it manually.

### Incident — this register was wrong about tab paths for 2 days (2026-09-17 → 2026-09-18)

NBT reorganized the extension's tab/panel layout himself on 2026-09-17,
outside any Claude session: he pulled all rebar tools and the Viaduct
panel out of `pyNBT.tab` and out of `pyNBT Dev.tab` and consolidated them
into one new tab, `pyNBT Rebar.tab`. This is confirmed real (verified by
`device_list_dir` on every panel on 2026-09-18) and matches what two
project docs from 2026-09-17 (`trim-rebar-length-tool.md`,
`split-rebar-to-single-tool.md`) already noted at the time.

This register, however, was never actually corrected to match — it still
said RebarSegment (and the other rebar tools) lived under `pyNBT Dev.tab`.
A separate Claude session working on the Rebar Segment tool trusted that
stale path for three straight version bumps (v1.0.7, v1.1.0, v1.1.1) and
delivered every one of them to `pyNBT Dev.tab\Rebar.panel\RebarSegment.pushbutton`
— a location Revit was not loading from at all. NBT correctly kept seeing
the old v1.1.0 badge after every reload and reported it; the actual live
file was only found and fixed on 2026-09-18 by cross-checking
`device_list_dir` against every candidate folder instead of trusting this
file.

**Lesson for future sessions:** before delivering a tool file with
`device_commit_files`, verify the target folder with `device_list_dir`
against the real device — don't trust this register's paths blindly,
especially right after NBT mentions reorganizing anything himself. This
register is a helpful index, not a source of truth for exact paths.
