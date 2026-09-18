# -*- coding: utf-8 -*-
"""
rebar_bend_standards.py
------------------------
Editable data file for the "Rebar Bend Standard Setup" pyNBT tool.

Edit ONLY this file to add/change standards or multiplier values.
script.py never hard-codes numbers - it always reads from STANDARDS below.

VERIFIED SOURCE
---------------
Values below are copied directly from TCVN 5574:2018, Clause 10.3.7
"Cac thanh thep uon" (page 143), confirmed against the primary standard
PDF Trung uploaded. Full clause text (Vietnamese):

    "Khi su dung thanh thep uon thi duong kinh uon toi thieu cua mot
    thanh don le phai sao cho tranh duoc su pha hoai hoac nut vo be tong
    nam phia trong phan uon cua thanh thep va su pha hoai thanh tai vi
    tri uon. Duong kinh toi thieu cua goi uon d_bend doi voi cot thep
    thanh phu thuoc vao duong kinh thanh thep d_s va lay khong nho hon:
    - Doi voi thanh thep tron: d_bend = 2.5*d_s khi d_s < 20 mm;
      d_bend = 4*d_s khi d_s >= 20 mm;
    - Doi voi thanh thep co gan: d_bend = 5*d_s khi d_s < 20 mm;
      d_bend = 8*d_s khi d_s >= 20 mm.
    Duong kinh goi uon cung co the duoc quy dinh theo cac dieu kien ky
    thuat doi voi tung loai cot thep cu the."

Design notes / assumptions (Trung: read this before using on a real
project):

1. Threshold is 20 mm (NOT 12 mm as an earlier draft wrongly assumed).

2. The clause gives ONE unified bend-diameter value d_bend per bar,
   depending only on that bar's own diameter d_s and its surface type
   (plain vs ribbed) - it does NOT separately define "hook" vs
   "stirrup/tie" bend diameters the way Revit's RebarBarType does
   (StandardBendDiameter / StandardHookBendDiameter /
   StirrupTieBendDiameter are 3 distinct fields). Since the clause's
   value depends only on that bar type's own diameter, this tool applies
   the SAME multiplier to all 3 Revit fields for a given bar type - this
   is consistent with the clause (stirrups/ties are normally small-
   diameter bars anyway, so they fall in the same d_s < 20 mm bracket
   and get the same formula on their own terms, not a borrowed value).

3. TCVN 5574:2018 Clause 10.3.7 does not give a separate figure for
   Revit's "MaximumBendRadius" field. As a safe default this file sets
   max_bend_radius_mult EQUAL to the standard d_bend multiplier (i.e.
   the maximum allowed bend radius is not more restrictive than the
   minimum bend diameter itself). Adjust this if Trung finds a specific
   office/project figure for it.

4. Two separate standard entries are provided below because the clause
   gives different numbers for plain/smooth bars vs ribbed/deformed
   bars. Ribbed/deformed bars (thanh co gan, e.g. CB300-V/CB400-V) are
   the normal choice for main reinforcement in Vietnamese practice, so
   that entry is listed first - but pick whichever matches the actual
   bar type in the project.

How multipliers work
---------------------
Each entry maps a bar-diameter bracket to 3 multipliers of the bar
diameter "d":
    standard      -> used for RebarBarType.StandardBendDiameter
    hook          -> used for RebarBarType.StandardHookBendDiameter
    stirrup_tie   -> used for RebarBarType.StirrupTieBendDiameter
max_bend_radius_mult is used for RebarBarType.MaximumBendRadius (also a
multiplier of d).

Brackets are given as (min_mm, ...) with min_mm INCLUSIVE - i.e. a bar
qualifies for a bracket when diameter_mm >= min_mm, and get_multipliers()
picks the bracket with the LARGEST qualifying min_mm. This directly
matches how the clause is worded ("... khi d_s < 20 mm" / "... khi
d_s >= 20 mm"): the 20mm bar itself must land in the ">= 20mm" bracket,
not the "< 20mm" one.

BUG FIXED 2026-08-20 (v2.2): the previous version used an inclusive
UPPER bound instead ("prev_max < d_mm <= max_mm" with max_mm=20 for the
small bracket), which put a bar of EXACTLY 20mm into the small (5d/2.5d)
bracket instead of the correct large (8d/4d) one - confirmed wrong by
NBT's own test (D20 showed New Std=100 instead of the correct 160). The
min_mm-inclusive scheme above cannot have this off-by-one: 20 >= 20 is
unambiguously true, so it always wins over 20 >= 0.

SINGAPORE STANDARD ADDED 2026-08-22 (v3.0)
-------------------------------------------
Source: EN 1992-1-1 (Eurocode 2 - Singapore's governing structural code
is SS EN 1992-1-1, which adopts this Clause via its National Annex
without amendment) Clause 8.3, Table 8.1N "Minimum mandrel diameter for
bent bars". Cross-checked against BS 8666:2020 "Scheduling, dimensioning,
bending and cutting of steel reinforcement for concrete" Table 2 - the
bar-bending/detailing standard commonly referenced for shape codes and
bend dimensions in Singapore practice - which reproduces the same
EN 1992-1-1 figures as absolute mm values (e.g. 8mm bar -> 32mm mandrel
= 4x8; 20mm bar -> 140mm mandrel = 7x20).

Verified via two independent sources (2026-08-22, no primary PDF
available this time - websearch/webfetch only, unlike TCVN's uploaded
PDF, so treat these numbers as verified-but-not-primary-source and flag
it to Trung if a discrepancy ever shows up against his own copy of the
standard):
1. JRC (EU Joint Research Centre) official Eurocode 2 training slides
   (eurocodes.jrc.ec.europa.eu) quoting Table 8.1N directly: minimum
   mandrel diameter = 4 x bar diameter when bar diameter <= 16mm, or
   7 x bar diameter when bar diameter > 16mm.
2. A BS 8666 shape-code reference PDF (cads.co.uk) reproducing Table 2
   as absolute mm values for bar sizes 6,8,10,12,16,20,25,32,40,50mm -
   the values divide out to exactly the same 4x/7x multipliers, with
   the split falling between 16mm (4x) and 20mm (7x), the two closest
   standard sizes either side of the ">16mm" threshold.
   (One other fetched page gave a conflicting 2x/2.5x/3x/3.5x/4x table -
   discarded as unreliable/inconsistent with both of the above and with
   the well-established 4x/7x figure, but noted here in case it ever
   needs re-checking against Trung's own primary-source copy.)

Design notes for Trung:
- Unlike TCVN 5574:2018, this source does NOT split by bar surface type
  (plain/mild-steel vs ribbed/deformed) - Table 8.1N and BS 8666 Table 2
  both apply to reinforcing steel in general (BS 8666's version is
  explicitly stated to cover B500A/B500B/B500C deformed bar grades,
  effectively all modern Singapore reinforcement). No verified figure
  for older plain/mild round bars (e.g. grade 250) has been sourced yet -
  if a real project actually needs plain round bars under this standard,
  tell Claude and this can be researched and added as a second entry,
  same as the TCVN Plain/Ribbed split.
- Like TCVN, this source gives ONE unified mandrel-diameter value per
  bar (not separate hook/stirrup-tie values), so the same multiplier is
  applied to all 3 Revit fields here too, for the same reasoning as the
  TCVN entries above.
- MaximumBendRadius has no dedicated figure in either source - same
  documented default policy as TCVN: set equal to the "standard"
  multiplier, pending a specific office figure from Trung if needed.
- The 4x/7x split point is written as min_mm=16.0001 below (not 17 or
  20), to correctly implement the source's exact ">16mm" wording for ANY
  bar diameter - including a custom/manual entry like 16.5mm or 18mm -
  not just the standard whole-mm sizes in STANDARD_DIAMETERS_MM.

SINGAPORE BAR SIZES + NAMING CORRECTED 2026-08-22 (v3.1)
----------------------------------------------------------
NBT pointed out the v3.0 guess (plain BS 4449 metric sizes, no letter
prefix - same "D" naming as Vietnam) does not match how bars are
actually labelled/sold in Singapore, and shared a reference table:
T10, T13, T16, T20, T25, T32, T40 (nominal diameter = the number, in
mm). Verified against 2 independent Singapore rebar supplier sites
(2026-08-22):
- chihantrading.com.sg: stocks R6/R8 (plain/round bars, "R" prefix) and
  T10/T13/T16/T20/T25 (deformed/high-tensile bars, "T" prefix - short
  for "Tor steel", a naming convention inherited from British colonial
  practice, still standard in Singapore/Malaysia today alongside BS
  4449). This confirms the T-prefix and that T13 (not T12) is the real
  Singapore size, unlike the plain-metric BS 4449 series used in the UK.
- progressabms.sg: lists the fuller local range T10/T13/T16/T20/T22/
  T25/T28/T32/T40/T50 with matching mm diameters.

Fix: STANDARD_DIAMETERS_MM below now uses NBT's exact reference list
[10,13,16,20,25,32,40] for Singapore (T22/T28/T50 exist in the wider
market too - add them here later if a project needs those sizes). A new
STANDARD_NAME_PREFIX dict (below) tells create_default_bar_types() to
name Singapore-created types "T10", "T13", etc. instead of "D10"/"D13"
like Vietnam.

Why this also fixes a latent bug, not just cosmetics: v3.0's Singapore
list [6,8,10,12,16,20,25,32,40,50] OVERLAPPED numerically with
Vietnam's list at 6,8,10,12,16,20,25,32 - since both were named with the
same "D" prefix, a project that used BOTH standards (e.g. switching the
dropdown back and forth) would have silently REUSED the same "D10" Bar
Type for what are actually two nationally-distinct bar products, instead
of creating a separate one per standard. The prefix fix makes every
Standard's Bar Types namespaced by their own real-world designation
("D10" vs "T10"), so they can never collide even when a project mixes
standards. The underlying bend-diameter multiplier data (4d/7d from
EN 1992-1-1 / BS 8666) is unaffected by this - it was already correct,
only the naming/size-list needed correcting.
"""

# Generic bootstrap list - used ONLY when a project has NO Rebar Bar Type
# at all yet and no Standard has been picked in the UI yet (main()'s
# very first "create a starter set?" prompt, before the main window even
# opens). Once a Standard is selected, its own entry in
# STANDARD_DIAMETERS_MM below is created automatically instead (v3.0 -
# happens the moment the window opens or the dropdown is changed, no
# button needed any more).
DEFAULT_BAR_DIAMETERS_MM = [6, 8, 10, 12, 14, 16, 18, 20, 22, 25, 28, 32]

# Per-standard bar diameters (mm). Different standards use different bar
# size ranges (VN's TCVN 1651-2 sizes are not the same as Singapore's
# BS 4449 sizes) - the tool auto-creates whichever sizes are missing for
# THIS list, for whichever Standard is currently selected in the
# dropdown (v2.4/v3.0), so switching Standard also switches which sizes
# get created. Keep the keys identical to the STANDARDS dict keys below.
# If a Standard has no entry here, the tool falls back to
# DEFAULT_BAR_DIAMETERS_MM.
STANDARD_DIAMETERS_MM = {
    "TCVN 5574:2018 - Ribbed bars (thanh co gan)": [6, 8, 10, 12, 14, 16, 18, 20, 22, 25, 28, 32],
    "TCVN 5574:2018 - Plain bars (thanh tron)": [6, 8, 10],
    # NBT: adjust the Plain-bars list above if you also use other plain
    # sizes (currently just 6/8/10, the common range for stirrups/ties).

    # Singapore - real market sizes as sold/labelled locally (T10..T40),
    # per NBT's reference table + 2 Singapore supplier sites (v3.1). Not
    # the plain BS 4449 metric series (which has 12 instead of 13, and no
    # letter prefix) - see the module docstring's v3.1 section.
    "BS 8666:2020 / EN 1992-1-1 (Singapore) - Ribbed bars (deformed)":
        [10, 13, 16, 20, 25, 32, 40],
    # NBT: T22/T28/T50 also exist in the Singapore market (per
    # progressabms.sg) - add them to the list above if a project needs
    # those sizes too.
}

# Bar Type NAME PREFIX per Standard (v3.1) - e.g. Vietnam's D10/D25 vs
# Singapore's T10/T25 - so create_default_bar_types() names each
# Standard's Bar Types the way they are actually labelled/sold in that
# country, and so two Standards' Bar Types can never collide under one
# shared numeric name even when their size lists overlap (see the module
# docstring's v3.1 section for why this also fixes a real bug, not just
# cosmetics). Falls back to "D" (Vietnam's convention) for any Standard
# not listed here, matching this tool's original/default behaviour.
STANDARD_NAME_PREFIX = {
    "TCVN 5574:2018 - Ribbed bars (thanh co gan)": "D",
    "TCVN 5574:2018 - Plain bars (thanh tron)": "D",
    "BS 8666:2020 / EN 1992-1-1 (Singapore) - Ribbed bars (deformed)": "T",
}

STANDARDS = {

    # Ribbed / deformed bars (thanh co gan) - e.g. CB300-V, CB400-V.
    # Normal choice for main reinforcement in Vietnamese practice.
    "TCVN 5574:2018 - Ribbed bars (thanh co gan)": [
        # (min_mm inclusive, standard, hook, stirrup_tie, max_bend_radius_mult)
        (0,  5.0, 5.0, 5.0, 5.0),   # d_s < 20 mm
        (20, 8.0, 8.0, 8.0, 8.0),   # d_s >= 20 mm
    ],

    # Plain / smooth bars (thanh tron) - e.g. CB240-T, often used only
    # for small stirrups/ties in older or specific detailing.
    "TCVN 5574:2018 - Plain bars (thanh tron)": [
        # (min_mm inclusive, standard, hook, stirrup_tie, max_bend_radius_mult)
        (0,  2.5, 2.5, 2.5, 2.5),   # d_s < 20 mm
        (20, 4.0, 4.0, 4.0, 4.0),   # d_s >= 20 mm
    ],

    # Singapore - EN 1992-1-1 Table 8.1N / BS 8666:2020 Table 2 (see the
    # module docstring above for the 2 verification sources). Only one
    # entry for now (deformed/ribbed B500A/B/C bars, i.e. virtually all
    # modern Singapore reinforcement) - no verified figure yet for plain
    # round bars under this standard.
    "BS 8666:2020 / EN 1992-1-1 (Singapore) - Ribbed bars (deformed)": [
        # (min_mm inclusive, standard, hook, stirrup_tie, max_bend_radius_mult)
        (0,        4.0, 4.0, 4.0, 4.0),   # d_s <= 16 mm
        (16.0001,  7.0, 7.0, 7.0, 7.0),   # d_s > 16 mm
    ],
}


def get_multipliers(standard_name, diameter_mm):
    """
    Look up (standard, hook, stirrup_tie, max_bend_radius) multipliers
    for a given bar diameter (mm) under the given standard name.
    Returns None if the standard or the diameter range is not found.

    Picks the bracket with the largest min_mm that diameter_mm still
    qualifies for (min_mm <= diameter_mm) - see the module docstring for
    why this is inclusive on the lower bound.
    """
    table = STANDARDS.get(standard_name)
    if not table:
        return None
    match = None
    match_min = None
    for min_mm, standard, hook, stirrup_tie, max_bend_radius in table:
        if diameter_mm >= min_mm and (match_min is None or min_mm > match_min):
            match_min = min_mm
            match = {
                "standard": standard,
                "hook": hook,
                "stirrup_tie": stirrup_tie,
                "max_bend_radius": max_bend_radius,
            }
    return match
