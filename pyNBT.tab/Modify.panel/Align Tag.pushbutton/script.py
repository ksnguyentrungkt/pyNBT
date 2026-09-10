# -*- coding: utf-8 -*-
"""pyNBT - Align Tag

Rotate ONE OR MANY tags (Floor / Structural Framing (beam) / Structural
Column / Architectural Column / Wall / Structural Foundation) AND/OR
Text Notes to run parallel to a reference LINE Trung defines by clicking
two points, so the element(s) read tilted in sync with whatever
direction Trung wants instead of staying at the default horizontal
angle.

Single-shot tool: pick tag(s), click 2 points, the tag(s) rotate
immediately, and the tool ends right there - no loop asking for the next
pair. To align another batch, just click the ribbon button again.

Workflow (v9.0):
    1. Run the tool. Either pre-select tags in Revit BEFORE clicking the
       ribbon button (normal click, Ctrl-click, or a window/crossing box
       select), or - if nothing is pre-selected - the tool prompts you
       to pick: click tags one by one, drag a box to grab many at once,
       Ctrl-click to add, Shift-click to remove, then press Finish (or
       Enter) when done. Floor, Beam, Column (Structural or
       Architectural), and Wall tags can all be mixed in the same batch.
    2. Click the FIRST point of the reference line.
    3. Click the SECOND point of the reference line. The two points can
       be anywhere - free clicks in space, or snapped (Revit's normal
       object-snap behavior applies) onto endpoints/intersections/
       gridlines/geometry in the host model OR a linked file, since
       picking POINTS instead of an element sidesteps all the old
       "which element, which link" questions entirely.
    4. EVERY tag picked in step 1 rotates immediately, all parallel to
       the SAME line from point 1 to point 2, and the tool finishes
       right away. Press Esc at any pick step to cancel without changing
       anything. If a tag in the batch can't be aligned for some reason,
       it is skipped (not a hard stop) and reported at the end - see the
       v9.0 design note below.

Design notes:
    v1.0-v3.0 all worked by picking a REFERENCE ELEMENT (beam / column /
    grid / wall / slab) and reading that element's own placement data to
    get a direction. That history (edge-picking vs element-picking,
    Grid.Curve vs Location.Curve vs HandOrientation, linked-model
    Tab/ObjectType.LinkedElement handling, the v1.9-v3.0 saga of getting
    the ROTATION MATH right) is kept below for the record, but v4.0
    replaces the entire "pick a reference element" step - see the v4.0
    note.
    v1.3 switched from edge-picking (ObjectType.Edge) to element-picking
    (ObjectType.Element) because Structural Framing (beams) commonly
    display as a symbolic line at Coarse Detail Level instead of solid
    geometry, so there was often no real "Edge" to pick at all, even for
    elements in the host model. Reading the element's own placement data
    (Location curve, HandOrientation, sketch profile) instead of its view
    geometry fixed that, and is also easier to click.
    v1.4 added explicit Grid support (Grid has no Location curve the way
    beams/walls do - it exposes its line via Grid.Curve instead) and a
    clearer error when the pick resolved to the link as a whole.
    v1.5: testing showed pressing Tab during an ObjectType.Element pick
    does NOT drill into a link's individual elements through the API the
    way it does in normal interactive selection - it always resolves to
    the RevitLinkInstance. The fix is to detect that case and immediately
    re-pick using ObjectType.LinkedElement, Revit's dedicated mode for
    picking one specific element inside a link (no Tab needed there).
    v1.6 added multi-tag selection, v1.7 split it into a second ribbon
    button, v1.8 folded it back into one tool behind an opt-in tick
    screen. Trung then asked to drop multi-select entirely and keep only
    the single-pick flow, which he found simpler and nicer to use - so
    v1.9 removes the tick screen and multi-select code path completely.
    v1.9 bug fix attempt (found by Trung re-running the tool on an
    already aligned tag): ElementTransformUtils.RotateElement always
    rotates BY a RELATIVE amount from the tag's CURRENT rotation - it
    does not set an absolute angle. Tried toggling TagOrientation back to
    Horizontal and then to AnyModelDirection every time before rotating,
    hoping that would force the rotation back to a known 0 baseline.
    v1.9 also removed the pick-another-pair loop per Trung's request (the
    tool felt slow to "finish" while it kept re-prompting) - the tool now
    does exactly one Tag + one reference and ends immediately.
    v1.10: Trung reported the v1.9 toggle trick did NOT actually fix the
    repeated-align bug, and that rotating felt slow. Attempted fix: read
    the tag's current rotation back from tag.Location.Rotation, compute
    the difference to the target angle, and rotate by only that
    difference, plus a conditional Regenerate() the first time a tag was
    switched into free rotation.
    v1.11: Trung tested v1.10 and reported the SAME bug still happened
    (re-aligning an already-aligned tag to a new reference still came out
    skewed, not parallel), and that rotation still took about 6 seconds.
    Root cause: tag.Location.Rotation is not a trustworthy read-back for
    an IndependentTag in AnyModelDirection orientation - it does not
    reliably reflect the angle actually applied by a prior
    RotateElement call, so the "current angle" v1.10 computed from it
    was wrong from the start, and the conditional Regenerate() was
    likely the source of the slowness. Fixed per Trung's suggestion
    ("bring the old angle back to 0, then add the new angle") by no
    longer asking Revit for the tag's rotation at all: pyNBT now stores
    the last angle IT applied directly on the tag itself, via Revit
    Extensible Storage (a small schema with one Double field). Each run
    reads that stored angle (0.0 if the tag has never been aligned by
    this tool before), rotates by exactly (new angle - stored angle)
    normalized to the shortest turn, then writes the new angle back to
    storage for next time.
    v1.12/v1.13: the Extensible Storage field itself needed two more
    fixes before it would actually work (unit/Spec errors on a Double
    field). v1.13 sidestepped that by storing the angle as a plain Int64
    of MILLIDEGREES instead - integer fields need no unit Spec at all.
    v1.14: added DEBUG_TIMING - prints a per-step timing breakdown to the
    pyRevit output window so a test run tells us exactly which step is
    slow instead of guessing.
    v2.0: broadened the tool beyond Floor Tags to also accept Beam
    (Structural Framing), Column, and Wall tags, and renamed the tool
    from "Align Floor Tag" to "Align Tag" to match. Only the tag-picking
    filter needed to change (FloorTagFilter -> TagFilter with 5 allow-
    listed categories) - the rotation math and reference-reading code
    were never Floor-specific.
    v2.1: v2.0 threw an AttributeError on load - the Structural Column
    Tags category enum was misspelled as BuiltInCategory.
    OST_StructColumnTags; the real name is OST_StructuralColumnTags.
    v2.2: Trung's DEBUG_TIMING data showed Transaction.Commit taking
    0.59s of a 0.64s total - confirmed the delay is Revit's own
    document/view regeneration, not this script, and that 0.64s is
    reasonable. Trung also asked for the timing output window to stop
    popping up on every run, so DEBUG_TIMING defaults to False.
    v3.0: Trung found tags he had rotated BY HAND (outside the tool, at
    any point before or after a previous align) came out wrong when the
    tool ran on them - the Extensible Storage memory from v1.11 only
    knew about rotations the TOOL itself had applied, so a hand-rotated
    tag's remembered angle didn't match its true angle. Fixed by no
    longer tracking or reading any prior angle at all: every run now
    force-resets the tag to a guaranteed 0-degree baseline first
    (TagOrientation -> Horizontal -> Regenerate -> AnyModelDirection ->
    Regenerate), then rotates directly to the absolute target. The
    Extensible Storage mechanism was removed entirely as no longer
    needed.
    v4.0: Trung asked to change how the REFERENCE DIRECTION is defined.
    Instead of picking a beam / column / grid / wall / slab and reading
    its placement data, Trung now clicks 2 points directly in the view
    and the tool treats the line between them as the reference direction
    to align the tag to - literally "click 2 points, that's my line".
    This is a deliberate simplification, not just a new option:
        - It removes the entire "which element, which category, does it
          have a Location.Curve or HandOrientation or Sketch.Profile"
          branch (get_reference_direction_deg, _floor_direction, the
          Grid-specific branch) - a line between 2 points needs none of
          that.
        - It removes the entire linked-model / Tab / ObjectType.
          LinkedElement handling (pick_reference, the RevitLinkInstance
          checks) - PickPoint() picks a raw 3D point in the ACTIVE VIEW,
          using Revit's normal object-snap behavior, and snaps onto
          geometry from the host model or a linked file exactly the same
          way, with no special-casing needed at all.
        - It is strictly more flexible: Trung can snap onto any 2 points
          he wants (2 grid intersections, 2 beam endpoints, a corner and
          a midpoint, or just eyeball a direction) instead of being
          limited to "the direction of one existing element".
    The rotation math in align_tag_to_angle() (the v3.0 force-reset-then-
    rotate design) is UNCHANGED - it never cared how angle_deg was
    determined, only what to do with it once known. Only the input side
    changed: pick_line_points() (2x uidoc.Selection.PickPoint calls) and
    get_line_direction_deg() (angle of p2-p1, same 0-360 / 90-270 flip
    convention as before) replace pick_reference() and
    get_reference_direction_deg(). The TagFilter/category-picking step
    (which TAG to align) is unchanged.
    v4.1: Trung tested v4.0 and reported total silence - no error
    popup, no traceback, and the tag did not visibly rotate at all,
    even though the tool ran to completion cleanly. Since v3.0's force-
    reset-then-rotate design had never actually been confirmed working
    by Trung before he asked to pivot to 2-point picking, this pointed
    at a previously-undiscovered bug in align_tag_to_angle() itself, not
    the new point-picking code. Working theory: calling doc.Regenerate()
    right after switching TagOrientation to AnyModelDirection but BEFORE
    RotateElement let Revit "check-point" the tag's internal rotation
    bookkeeping at 0, so the later RotateElement transform never made it
    into that bookkeeping and got silently discarded when Commit() did
    its own implicit regenerate. Fixed by moving the regenerate to run
    AFTER RotateElement instead of before it - see the updated design
    note inside align_tag_to_angle() for the exact new step order. This
    is an unverified hypothesis fix; if Trung still sees no rotation
    after this, the next step is a different diagnostic, not another
    guess at step ordering.
    v4.2: Trung reported a NEW/recurring problem after v4.1 - the FIRST
    align on a tag works correctly, but re-aligning the SAME tag a
    SECOND time (to a different reference line) comes out wrong / not
    parallel - UNLESS he first manually rotates the tag back to 0 by
    hand in Revit's own UI, after which the tool works correctly again.
    This is the same class of bug v1.9-v3.0 kept fighting, resurfacing
    inside v4.1's own force-reset step. Working theory: the reset step
    (TagOrientation -> Horizontal -> Regenerate -> AnyModelDirection)
    was happening INSIDE the same Transaction as the rotate step, and a
    mid-transaction Regenerate() does not reliably clear whatever
    internal "last custom rotation" bookkeeping Revit keeps for an
    IndependentTag - flipping back to AnyModelDirection can silently
    restore that old remembered angle instead of staying at 0, even
    though Regenerate() ran in between. Trung's manual workaround is the
    key clue: a real rotate-back-to-0 committed through Revit's own UI
    (a genuine standalone Transaction.Commit(), not just a Regenerate)
    is what actually clears it. Fixed by giving the reset step its own
    REAL Transaction with a REAL Commit (see _reset_tag_orientation()),
    separate from the rotate step's Transaction - both wrapped in one
    TransactionGroup in align_one() so Trung's Undo history still shows
    this as a single action, even though 2 Transactions are committed
    under the hood. Still a hypothesis fix (no live Revit available to
    verify against directly) - if the second-align case still fails
    after this, the next step is checking whether TagOrientation itself
    is even the right lever, vs. some other tag-rotation API.
    v5.0: Trung tested v4.2 and it STILL failed the second-align case -
    and this time gave us a real number to diagnose with: the rotation
    actually needed was only about 8 degrees, but the tool applied
    something like 300+ degrees instead. That number is the smoking
    gun. It means the "reset to a 0-degree baseline" premise itself was
    NEVER true, in ANY version (v3.0's in-transaction Regenerate, v4.1's
    reordered Regenerate, v4.2's real separate Commit) - the tag's real
    underlying rotation was still sitting at whatever the FIRST align
    had set it to, and RotateElement kept adding the new absolute target
    on top of that old angle as if it were a delta from 0, overshooting
    by roughly the old angle's amount. Every "reset" attempt so far
    tried to force TagOrientation through Horizontal and back, on the
    theory that this would zero out some internal Revit bookkeeping -
    that theory is now considered WRONG, not just unverified.
    v5.0 abandons "force a 0-degree baseline" entirely and switches to
    exactly what Trung suggested: read the tag's ACTUAL current
    rotation, and rotate by the DIFFERENCE to the target (shortest way
    round), the same way you'd manually correct an angle - via
    tag.Location.Rotation, which Revit updates in-place whenever
    RotateElement runs (this also naturally handles hand-rotated tags,
    the original v3.0 case, with no special-casing needed - whatever
    Location.Rotation reports IS the tag's true current angle,
    regardless of who rotated it last). v1.10/v1.11 tried this exact
    approach once before and Trung's testing then showed the same
    repeated-align bug - but that attempt ran alongside Extensible
    Storage and a conditional Regenerate() that were later found to be
    part of the problem, so it was never a clean test of
    Location.Rotation by itself. This time it runs alone, with a new
    opt-in DEBUG_ANGLE flag (default False, same silent-by-default rule
    as DEBUG_TIMING) that prints the exact current/target/delta numbers
    read on each run - if this still misfires, flipping that flag on
    for one test run gives real data instead of another guess.
    v6.0: Trung ran v5.0 and got a clean, immediate error instead of a
    wrong rotation: "Could not read this tag's current rotation angle
    (Location.Rotation was not available)", on Revit/pyRevit
    6.4.0.26100+0515, on a Floor Tag. That answers the open question
    from v5.0's own note for good: tag.Location.Rotation is NOT usable
    for an IndependentTag in this environment at all (not merely
    "unreliable" as v1.11 guessed - it is simply not exposed), so there
    is no live Revit property this tool can read to find a tag's true
    current rotation angle. Every approach that depended on reading
    Revit's own state back (TagOrientation toggling in v3.0-v4.2,
    Location.Rotation in v5.0) has now failed for a concrete, confirmed
    reason - not just "still buggy, keep guessing at the mechanism".
    v6.0 goes back to the ONE mechanism from this tool's whole history
    that never actually failed on repeated TOOL-ONLY use: pyNBT tracks
    the angle IT last applied, itself, via Extensible Storage (the same
    Int64-millidegrees design from v1.13, which had no unit/Spec issues)
    - not to "cheat" by remembering the true current angle, but because
    there is no other way left to know it. On each run: read the stored
    angle (0.0 if this tag was never aligned by the tool before, which
    matches a fresh tag's real 0-degree state), compute the shortest
    delta to the new target, RotateElement by that delta, then store the
    new absolute angle for next time. New schema/GUID for v6.0 so any
    leftover v1.11-v2.2 era entities (from before v3.0 ripped this
    mechanism out) are simply ignored, not reused.
    Known, accepted trade-off (same one that motivated v3.0's rewrite in
    the first place): if Trung rotates a tag BY HAND between two tool
    runs, the stored angle goes stale and the next tool run will be
    wrong again, exactly like pre-v3.0. There is no known way to avoid
    this without a live-readable rotation property, which v5.0 just
    proved does not exist here. If hand-rotation-between-runs turns out
    to matter in practice, the next idea is a lightweight "resync"
    command Trung runs once by hand after any manual rotation, rather
    than trying to auto-detect it.
    v7.0: Trung tested v6.0 on a Structural Column Tag and sent Properties
    palette screenshots that turned out to solve this whole saga. Before
    running the tool, the tag's own Properties palette showed a field
    literally called "Angle" = 350.00 degrees (only visible once
    Orientation = Model). After running the tool, Angle read 68.00
    degrees - but Trung measured the tag actually needed 78 degrees to
    be parallel with his reference line. Working out the arithmetic:
    (350 + 78) mod 360 = 68 - which proves two things at once. First,
    RotateElement's effect maps onto this "Angle" parameter in a
    perfectly direct, additive, absolute way (rotate by +78 truly adds
    78 to Angle, no hidden conversion). Second, this tool's own angle
    MATH (get_line_direction_deg, from the 2 picked points) was already
    computing the CORRECT target the whole time - 78 degrees, matching
    Trung's own manual measurement exactly. The only thing that was ever
    wrong was v6.0's assumed starting point: brand new to v6.0's storage
    (never aligned by THIS SCHEMA before), so it assumed the tag's
    current angle was 0 - but the tag's REAL current angle was 350 (set
    by something before v6.0 ever ran on it), so the correctly-computed
    +78 delta landed at the wrong absolute result (68 instead of 78).
    This is the same "wrong assumed baseline" failure mode that broke
    v3.0 through v6.0, in a new shape.
    v7.0's fix removes the need for ANY baseline or delta at all: since
    "Angle" is a real, directly settable Revit Parameter (found via
    tag.LookupParameter("Angle"), the exact field Trung sees on screen),
    just SET it straight to the absolute target angle computed from the
    2 picked points. No RotateElement, no relative delta, no remembering
    or reading any "current" angle, no Extensible Storage. This also
    means hand-rotated tags are no longer a special case at all (the
    v3.0 problem this whole saga started from) - whatever the tag's
    current Angle is, Set() simply overwrites it with the correct
    absolute value, unconditionally, every run.
    v8.0: Trung tested v7.0 on a SECOND tag, in a DIFFERENT view ("A102 -
    LAND FACILITIES - ROOF PL..." instead of the "CONCRETE BODY PLAN..."
    view the first, confirmed-correct 78-degree case was in) - and it
    came out wrong again (Angle ended up 348.74 degrees, visibly not
    parallel to the reference line he drew). This is a NEW root cause,
    not a repeat of the old "wrong current angle" family of bugs - v7.0
    no longer reads or assumes any "current" angle at all, it only
    computes a target from the 2 picked points and writes it directly.
    So the bug has to be in the computed TARGET itself, and the fact
    that it worked in one view but not another points at the VIEW,
    specifically: uidoc.Selection.PickPoint() returns points in true
    project (world) XYZ coordinates, the same in every view - but the
    tag's "Angle" parameter is very likely measured relative to the
    VIEW's own on-screen "horizontal" (its RightDirection), not world
    X. get_line_direction_deg() was computing everything in world XY,
    with no correction for the view's own rotation relative to the
    project. In the first (working) view, the view's RightDirection
    happens to line up with world +X (an unrotated plan view), so world
    angle and view-relative angle were the same number by coincidence -
    hiding the bug. The second view (a roof plan) is evidently rotated
    relative to the project, exposing it.
    v8.0 fix: before writing to the tag's Angle parameter, convert the
    picked line's world-frame direction into the angle relative to the
    TAG'S OWN VIEW (via tag.OwnerViewId, not just "whatever the active
    view happens to be", to be exact regardless of how the tool is
    invoked) by subtracting that view's RightDirection angle (in world
    XY) from the picked line's world angle. The "avoid upside-down tag
    text" 180-degree flip now happens AFTER this correction, since it
    needs to reflect how the text will actually look ON SCREEN in that
    view, not its raw project-coordinate direction. This is a new,
    reasoned hypothesis (matches both data points collected so far: 0
    correction needed where it worked, correction evidently needed where
    it didn't) but still unverified against live Revit - see the
    confirmation checklist in the project doc for what to test, including
    ruling out imprecise point-picking as a secondary factor.
    v9.0: Trung asked to be able to align MANY tags in one run instead of
    one at a time - either clicking tags one by one or dragging a box
    ("quét") to grab a bunch. Scoped with 3 confirmed decisions:
        1. ALL picked tags share ONE reference line - Trung clicks the 2
           reference points ONCE per batch, not once per tag. Every tag
           in the batch ends up parallel to that same line.
        2. Different tag categories CAN be mixed in one batch (a Floor
           tag and a Column tag can be picked together and aligned in
           the same run) - no need to separate by category.
        3. If some tags in the batch fail (e.g. a category where the
           "Angle" parameter isn't found), the tool SKIPS just those and
           keeps aligning the rest, then reports which ones failed at
           the end - it does not abort the whole batch over one bad tag.
    Implementation: uidoc.Selection.PickObjects() (instead of the old
    single-pick PickObject()) natively supports individual clicks AND
    window/crossing box-select in the same picking session, still
    filtered live by TagFilter so only alignable tag categories can be
    picked at all - no custom UI window needed, stays Tier 1. Also added
    pre-selection support: if Trung already has tags selected (via
    normal Revit selection, box-select, Ctrl-click, etc.) BEFORE
    clicking the ribbon button, the tool uses that selection directly
    instead of prompting again - a common, low-risk pyRevit convenience
    pattern. All tags in the batch are aligned inside ONE Transaction
    (align_many(), replacing the old single-tag align_one()) so the
    whole batch is still just ONE step in Trung's Undo history, matching
    how the tool has always behaved for a single tag.
    v10.0: Trung asked to add Structural Foundation Tags (raft/pad
    footing tags etc.) to the supported categories - his screenshot
    confirms this category ALSO has the same "Angle" parameter (visible
    in Properties once Orientation = Model) that v7.0/v8.0 already rely
    on, so no new mechanism is needed, just adding the category to the
    allow-list. Also added a belt-and-suspenders safety net while doing
    this: TagFilter now ALSO accepts a tag if its category's own display
    NAME matches a known list (e.g. "Structural Foundation Tags"), not
    only via the BuiltInCategory enum lookup - this tool has been bitten
    once before (v2.0) by a single misspelled enum name
    (OST_StructColumnTags) silently/loudly breaking category support, so
    matching by name as a fallback means a wrong or renamed enum no
    longer means "this category just doesn't work", as long as the
    display name still matches.
    v11.0: Trung asked to also be able to align Text Notes (plain
    annotation text, not attached to any host element like a tag is) -
    merged into this same tool rather than a separate one, per his
    choice. Text Notes are a genuinely different element type
    (TextNote, not IndependentTag) with no TagOrientation and, as far as
    could be confirmed, no "Angle" instance parameter the way tags have
    (that field only ever showed up in Properties once a tag's
    Orientation = Model - Text Notes have no such Orientation concept at
    all). So align_element_to_angle() (renamed from align_tag_to_angle)
    now tries 2 mechanisms in order: (1) the "Angle" parameter approach
    from v7.0/v8.0, tried FIRST on any element in case it happens to
    exist; (2) if not found, falls back to reading the element's real
    current rotation via Location.Rotation and rotating by the shortest
    delta - the SAME approach that was confirmed UNAVAILABLE for
    IndependentTag back in v5.0. It is NOT assumed to work for Text Note
    either - it's a reasonable thing to try (Text Note is a simpler,
    plain point-placed element, unlike a tag's multiple orientation
    modes), but genuinely unverified. If neither mechanism works, the
    tool raises a clear, honest error asking for the element type and a
    Properties palette screenshot - the same diagnostic pattern that
    solved the tag case - rather than silently producing a wrong angle.
    Picking, filtering, and category logic were all generalized from
    "tag-only" to "tag or Text Note" (AlignableElementFilter,
    _is_alignable_element(), get_elements_to_align()) - Text Notes need
    no category allow-list, any Text Note is accepted.
    v11.1: Trung's first Text Note test ("BIN CENTER") hit exactly the
    "neither mechanism worked" error - confirmed Text Notes have NO
    numeric angle field in Properties at all (only a click-and-drag
    curved-arrow rotate grip in the UI, no Properties field to read by
    eye the way Tag's "Angle" was found). That note also had Left/Right
    Attachment settings configured, a possible lead (an attached-between-
    points Text Note may not expose a plain Location the same way a
    simple free note does). Rather than guess again, _get_location_
    rotation_deg() now reports (via DEBUG_ANGLE) exactly WHICH sub-step
    returned nothing - element.Location raising, being None, having no
    Rotation attribute (and if so, what type Location actually was), or
    a degrees-conversion failure - so the next test gives real evidence
    instead of another blind guess.
    v12.0: Trung's follow-up ("kha nang k xoay dc" - "maybe it just can't
    be rotated") confirmed the diagnostic loop had run its course -
    getting a clean DEBUG_ANGLE console capture back proved harder in
    practice than getting a working tool. Both live-read mechanisms tried
    for Text Note are now confirmed dead ends: no "Angle" parameter
    (v11.0/v11.1 - Text Notes genuinely have none, only a UI-only rotate
    grip), and Location.Rotation returns nothing usable either (v11.1 -
    same failure class Tags hit back in v5.0). There is no third live
    property left to try.
    v12.0 stops trying to read Text Note's rotation from Revit at all,
    and instead reuses the ONE mechanism from this tool's own history
    that is PROVEN reliable for tool-only repeated use: pyNBT remembers,
    in its own Extensible Storage entity on the element, the angle IT
    last applied - the exact same design v6.0 used for Tags before the
    v7.0 "Angle" parameter breakthrough was found. This is scoped to
    Text Note ONLY - Tags are untouched and keep using the direct
    "Angle" parameter Set() from v7.0/v8.0, which has worked correctly
    ever since. On each run for a Text Note: read the stored angle (0.0
    if this note was never aligned by the tool before - matching a fresh
    note's real 0-degree state), rotate by the shortest delta to the new
    target via ElementTransformUtils.RotateElement (using element.Coord,
    Text Note's own insertion point, as the rotation axis origin - not
    Location.Point, which v11.1 found unusable for this element type),
    then store the new absolute angle back for next time.
    Known, accepted trade-off (the same one v6.0 accepted for Tags, and
    v3.0 was originally written to avoid): if Trung rotates a Text Note
    BY HAND (using its own curved-arrow grip) between two tool runs, or
    before the FIRST tool run ever touches it, the stored angle goes
    stale and that next tool run will land on the wrong absolute angle -
    there is no live-readable property left to detect this. Every run
    AFTER the tool itself has touched a given Text Note will be
    correct, since the tool always knows the angle it last set. If this
    turns out to matter in practice, the fix is a quick re-run of the
    tool (it will self-correct on the next pass, same as Tags did in the
    v6.0 era), not a code change.
    v12.1: Trung hit a brand new crash, unrelated to Text Note - a
    traceback ("No work plane set in current view",
    Autodesk.Revit.Exceptions.InvalidOperationException) coming from
    pick_line_points()'s call to uidoc.Selection.PickPoint(). This is a
    known Revit API constraint: PickPoint() needs the ACTIVE VIEW to
    already have a work plane set before it will even start prompting -
    plan/section/elevation/drafting views normally carry one implicitly,
    but a 3D view does not by default, which is what exposed this (the
    tool had never been run from a 3D view before). Fixed by having
    pick_line_points() set a harmless, invisible horizontal work plane
    (through the world origin) on the active view first, ONLY if it
    doesn't already have one - this does not restrict or change which
    points/snaps Trung can pick in any way, it purely satisfies
    PickPoint()'s own requirement. A clear error (asking to switch to a
    plan/section/elevation view) is shown instead of a crash on the rare
    chance a view still refuses a work plane for some other reason.
    v12.2: Trung reported the SAME "No work plane" crash happened in a
    SECTION view, not a 3D view as v12.1 assumed - so this is not a "3D
    views only" quirk. Root cause of why v12.1's fix did not fully cover
    this: it set a fixed HORIZONTAL plane (through the world origin)
    unconditionally, which is the natural plane for a plan view but is
    the wrong kind of plane for a section/elevation view (whose own work
    plane naturally runs ALONG its cut direction, not horizontally) -
    Revit likely rejected or ignored that mismatched plane. Fixed by
    building the plane FROM THE VIEW ITSELF instead - using the view's
    own ViewDirection as the plane's normal and its own Origin as the
    plane's origin point - which is the correct, expected shape of a
    work plane for ANY view type (plan, section, elevation, or 3D), with
    the old horizontal-through-origin plane kept only as a last-resort
    fallback if that ever fails.
    v12.3: Trung's first real test from a SECTION view surfaced a much
    deeper issue than the work plane crash - aligning a Floor Tag along
    a mostly-vertical reference line in that section wrongly reported
    "those two points are the same point", while a Beam Tag test (a
    reference line with some horizontal component) did not error but
    could still have been silently wrong. Root cause: EVERY angle
    calculation in this tool, since v4.0 introduced 2-point picking,
    only ever looked at the picked points' X and Y coordinates - never
    Z. That was invisible for years because every test (v4.0 through
    v12.2) happened in a PLAN view, where the view's own 2D drawing
    plane genuinely IS the world XY plane, so ignoring Z cost nothing.
    A SECTION or ELEVATION view's 2D plane is different: it is
    RightDirection (horizontal, world X/Y) crossed with UpDirection
    (world Z, vertical) - a VERTICAL plane - so a reference line that is
    mostly vertical on screen has almost its ENTIRE length in Z, which
    the old X/Y-only math was blind to: it saw dx=dy=0 and concluded the
    two points must be the same point, when really they just differed
    in the one axis (Z) it never looked at.
    v12.3 fixes this at the root instead of patching around it:
    get_line_direction() (replacing get_line_direction_deg()) now keeps
    the full 3D vector between the two picked points and checks
    "too close together" using the REAL 3D distance
    (p1.DistanceTo(p2)), not just X/Y. _view_relative_angle_deg()
    (replacing the v8.0 pair _get_view_rotation_deg() +
    view_relative_angle_deg(), which also only ever used X/Y) now
    projects that 3D direction onto the TARGET view's own RightDirection
    and UpDirection using the dot product, and takes the angle of that
    2D projection - this is the general, correct definition of "the
    angle of this direction on screen", valid for a plan, section,
    elevation, or 3D view alike, since RightDirection/UpDirection/
    ViewDirection always form a proper orthonormal basis for any Revit
    view. Plan-view results are unaffected (the math reduces to exactly
    the old X/Y calculation when the view's plane already is XY); the
    fix only changes behavior for views where it previously did not
    account for Z at all.
"""

__title__ = "Align\nTag"
__doc__ = ("Pick (or pre-select) one or many Floor / Beam / Column / "
           "Wall tags and/or Text Notes, then click 2 points to define "
           "the reference line to align them all to; the element(s) "
           "rotate immediately and the tool finishes right away.")

import os
import sys
import math
import time

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

import System

from Autodesk.Revit.DB import (
    Transaction, BuiltInCategory, BuiltInParameter, IndependentTag,
    TagOrientation, TextNote, Line, XYZ, Plane, SketchPlane,
    ElementTransformUtils,
)
from Autodesk.Revit.DB.ExtensibleStorage import (
    Schema, SchemaBuilder, Entity, AccessLevel,
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import (
    OperationCanceledException, InvalidOperationException,
)

from pyrevit import forms, revit

# ---------------------------------------------------------------------------
# pyNBT shared lib (compat.py / theme.py) - see shared-lib-architecture.md
# theme is not needed here (no WPF window in this tool), only compat.
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(__file__)
LIB_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", "lib"))
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyNBT.compat import eid_int

doc = revit.doc
uidoc = revit.uidoc

TOOL_NAME = "Align Tag"
TOOL_VERSION = "12.3"

# v7.0: the exact parameter name Trung sees in the Properties palette
# (confirmed from his own screenshot) once a tag's Orientation = Model -
# this IS the tag's rotation angle, directly settable, no RotateElement
# or baseline tracking needed at all. Kept as a couple of BuiltInParameter
# fallbacks too, in case the display name ever differs - see
# _get_angle_parameter().
_ANGLE_PARAM_NAME = "Angle"
_ANGLE_PARAM_BIP_FALLBACKS = [
    "TAG_ROTATION_ANGLE",
    "LEADER_TAG_ROTATION_ANGLE",
    "TAG_ANGLE",
]

# v12.0: Text Note has no "Angle" parameter and no usable Location.Rotation
# (both confirmed dead ends - see the v12.0 design note at the top of this
# file), so pyNBT tracks the angle IT last applied to a Text Note itself,
# via Extensible Storage - the same Int64-millidegrees design v6.0 used for
# Tags before the v7.0 "Angle" parameter breakthrough made it unnecessary
# there. New schema/GUID, separate from any old tag-era schema, and scoped
# to Text Note only - Tags never touch this, they still use the direct
# "Angle" parameter Set() from v7.0/v8.0.
_TEXTNOTE_ANGLE_SCHEMA_GUID = System.Guid("7c2a9e6d-4f31-4a8b-9d5e-2b6c1f9a3e70")
_TEXTNOTE_ANGLE_SCHEMA_NAME = "pyNBT_AlignTextNoteAngle"
_TEXTNOTE_ANGLE_FIELD_NAME = "AngleMilliDeg"

# v1.14 added this timing breakdown to find slow steps; v2.2's real
# numbers showed the tool itself is fast (Revit's own regenerate on
# commit is where the time goes), so this now defaults to off - flip
# back to True only to investigate a genuine new slowness report.
DEBUG_TIMING = False

# v5.0: prints diagnostic angle numbers on every run, so if the angle
# logic ever misfires again we get real data instead of another blind
# guess. Same silent-by-default rule as DEBUG_TIMING - flip to True only
# to diagnose a fresh report of wrong-angle results.
DEBUG_ANGLE = False


def _log_timing(label, elapsed_seconds):
    if DEBUG_TIMING:
        print("  [pyNBT timing] {}: {:.2f}s".format(label, elapsed_seconds))


# ---------------------------------------------------------------------------
# Standalone Revit-logic functions (no UI references - see dqt-patterns.md #2)
# ---------------------------------------------------------------------------

# v2.0: tag categories this tool is allowed to pick and rotate. Both column
# tag categories are included (Structural Column Tags AND Architectural
# Column Tags) since a project can carry either or both.
# v2.1: build this defensively, one name at a time, instead of a single
# expression - v2.0 crashed the WHOLE tool at load time (AttributeError)
# because ONE misspelled enum name (OST_StructColumnTags, should have been
# OST_StructuralColumnTags) blew up the whole set() literal. Looking each
# name up individually means a future Revit version dropping/renaming one
# tag category degrades to "that one category isn't pickable" instead of
# "the tool doesn't run at all".
# v10.0: added Structural Foundation Tags (raft/pad footing tags etc.) -
# Trung confirmed via screenshot this category also has the same "Angle"
# parameter the tool already relies on.
_ALIGNABLE_TAG_CATEGORY_NAMES = [
    "OST_FloorTags",
    "OST_StructuralFramingTags",       # beam tags
    "OST_StructuralColumnTags",        # structural column tags
    "OST_ColumnTags",                  # architectural column tags
    "OST_WallTags",
    "OST_StructuralFoundationTags",    # foundation tags (v10.0)
]
_ALIGNABLE_TAG_CATEGORIES = set()
for _cat_name in _ALIGNABLE_TAG_CATEGORY_NAMES:
    _bic = getattr(BuiltInCategory, _cat_name, None)
    if _bic is not None:
        _ALIGNABLE_TAG_CATEGORIES.add(int(_bic))

# v10.0: belt-and-suspenders fallback - ALSO accept a tag by its
# category's own display name (as Trung sees it in the Properties
# palette header, e.g. "Structural Foundation Tags"), not only via the
# BuiltInCategory enum lookup above. This tool has been bitten once
# before (v2.0) by a single misspelled enum name silently/loudly
# breaking a whole category - matching by name too means a wrong or
# future-renamed enum degrades to "still works, just via the name
# fallback" instead of "that category quietly stops being pickable".
_ALIGNABLE_TAG_CATEGORY_DISPLAY_NAMES = set([
    "Floor Tags",
    "Structural Framing Tags",
    "Structural Column Tags",
    "Column Tags",
    "Wall Tags",
    "Structural Foundation Tags",
])


def _is_alignable_tag_category(cat):
    """True if cat (an Element.Category) is one of the tag categories
    this tool supports - checked via BuiltInCategory id first, falling
    back to matching the category's display name (v10.0, see the
    comment above _ALIGNABLE_TAG_CATEGORY_DISPLAY_NAMES for why)."""
    if cat is None:
        return False
    try:
        if eid_int(cat.Id) in _ALIGNABLE_TAG_CATEGORIES:
            return True
    except Exception:
        pass
    try:
        return cat.Name in _ALIGNABLE_TAG_CATEGORY_DISPLAY_NAMES
    except Exception:
        return False


def _is_alignable_element(element):
    """True if element is something this tool can align (v11.0): an
    IndependentTag in one of the supported tag categories, OR a
    TextNote (any Text Note is alignable - it has no sub-category the
    way tags do)."""
    if isinstance(element, IndependentTag):
        try:
            return _is_alignable_tag_category(element.Category)
        except Exception:
            return False
    return isinstance(element, TextNote)


class AlignableElementFilter(ISelectionFilter):
    """Only allow picking elements this tool can align: Floor, Structural
    Framing (beam), Structural Column, (Architectural) Column, Wall, and
    Structural Foundation tags, plus Text Notes (v11.0)."""

    def AllowElement(self, element):
        try:
            return _is_alignable_element(element)
        except Exception:
            return False

    def AllowReference(self, reference, position):
        return True


def _safe_element_text(element):
    """Short, human-readable label for an element in error messages
    (v11.0: handles both IndependentTag and TextNote)."""
    try:
        if isinstance(element, IndependentTag):
            txt = element.TagText
            return txt if txt else "(no text)"
        if isinstance(element, TextNote):
            txt = element.Text
            return txt if txt else "(empty text note)"
    except Exception:
        pass
    try:
        return "Element {}".format(eid_int(element.Id))
    except Exception:
        return "Element"


def get_elements_to_align(uidoc, doc):
    """Return the list of elements to align (v9.0: one or many; v11.0:
    tags AND/OR Text Notes). Two ways in, matching a common pyRevit
    convenience pattern:

    1. If Trung already has a selection BEFORE clicking the ribbon
       button (normal click, Ctrl-click add, or a window/crossing box
       select, all native Revit selection - nothing pyNBT-specific),
       that selection is used directly - filtered down to just the
       element types/categories this tool supports, silently ignoring
       anything else that happened to be selected alongside them. No
       selection dialog is skipped or shown either way - this is purely
       "did Trung already point at what he wants".
    2. Otherwise, prompts uidoc.Selection.PickObjects() - Revit's own
       native multi-pick session, supporting individual clicks AND
       window/crossing box-select ("quét") in the same session, Ctrl to
       add / Shift to remove, Finish (or Enter) to confirm, Esc to
       cancel - filtered live by AlignableElementFilter so only
       alignable tags/Text Notes can be picked at all, same as the
       single-tag flow always was.

    Different tag categories, and tags mixed with Text Notes, can be
    freely combined in one batch (Trung's confirmed choice) - this
    function does not separate or restrict beyond what
    AlignableElementFilter already allows.

    Returns [] if nothing valid ends up picked, or the user cancels.
    """
    pre_selected_ids = uidoc.Selection.GetElementIds()
    if pre_selected_ids:
        elements = []
        for eid in pre_selected_ids:
            el = doc.GetElement(eid)
            try:
                if _is_alignable_element(el):
                    elements.append(el)
            except Exception:
                pass
        if elements:
            return elements

    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, AlignableElementFilter(),
            "Pick Floor / Beam / Column / Wall / Foundation tags and/or "
            "Text Notes to align - click each one, or drag a box to "
            "select many at once. Click Finish (or press Enter) when "
            "done.")
    except OperationCanceledException:
        return []
    elements = []
    for r in refs:
        el = doc.GetElement(r)
        if _is_alignable_element(el):
            elements.append(el)
    return elements


def _ensure_work_plane(doc, view):
    """Make sure view has a work plane set (v12.1, fixed in v12.2).
    PickPoint() refuses to even start prompting if the active view has
    none - Trung hit this ("No work plane set in current view") in a
    SECTION view, not a 3D view as first guessed in v12.1 - so this is
    NOT a "3D views only" quirk, some section/elevation views apparently
    lack one too.

    v12.1's first attempt set a fixed HORIZONTAL plane (through the
    world origin) as the work plane unconditionally. That is a reasonable
    plane for a plan view, but is likely the wrong kind of plane for a
    section/elevation view (whose natural, expected work plane runs
    ALONG the view's own cut direction, not horizontally) - Revit may
    silently refuse or ignore a horizontal plane there, which would
    explain why the crash could still happen in a section view even
    after v12.1's fix.

    v12.2 fixes this by building the plane FROM THE VIEW ITSELF - using
    the view's own ViewDirection as the plane's normal and the view's
    own Origin as the plane's origin point - which matches what Revit
    actually expects for any view type (plan, section, elevation, or
    3D), not just plan views. If that still fails for some reason, falls
    back to the old horizontal-through-origin plane as a second attempt,
    purely as a last resort.

    Either way this has no visible effect and does not restrict
    snapping in any way - Trung can still snap onto any real geometry
    exactly as before - it purely satisfies PickPoint()'s own
    precondition. Runs in its own small Transaction; any failure here is
    swallowed (not fatal) so the subsequent PickPoint() call still gets
    a chance to run (and will raise its own clear, caught error if the
    view truly cannot proceed).
    """
    try:
        if view.SketchPlane is not None:
            return
    except Exception:
        pass

    def _try_set_plane(plane):
        t = Transaction(doc, "pyNBT - set temporary work plane")
        try:
            t.Start()
            sketch_plane = SketchPlane.Create(doc, plane)
            view.SketchPlane = sketch_plane
            t.Commit()
            return True
        except Exception:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            return False

    try:
        view_plane = Plane.CreateByNormalAndOrigin(
            view.ViewDirection, view.Origin)
        if _try_set_plane(view_plane):
            return
    except Exception:
        pass

    try:
        fallback_plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ.Zero)
        _try_set_plane(fallback_plane)
    except Exception:
        pass


def pick_line_points(uidoc, doc):
    """Pick 2 points in the active view that define the reference line to
    align the tag to (v4.0). Uses Revit's normal PickPoint object-snap
    behavior - the points can be free clicks in space, or snapped onto
    endpoints / intersections / gridlines / any geometry, in the host
    model OR a linked file, with no special handling needed either way
    (unlike the old element-picking flow, which needed a whole separate
    branch just for linked elements). Returns (p1, p2) as XYZ, or None if
    the user cancelled (Esc) at either point.

    v12.1: ensures the active view has a work plane first (see
    _ensure_work_plane) - PickPoint() needs one to exist before it will
    even start, which a 3D view does not carry by default. If a view
    still refuses to pick (InvalidOperationException) even after that,
    shows a clear error instead of crashing.
    """
    view = uidoc.ActiveView
    _ensure_work_plane(doc, view)

    no_work_plane_msg = (
        "This view has no work plane, and pyNBT could not set one "
        "automatically, so points can't be picked here. Please switch "
        "to a plan, section, or elevation view and run the tool again.")

    try:
        p1 = uidoc.Selection.PickPoint(
            "Click the FIRST point of the reference line")
    except OperationCanceledException:
        return None
    except InvalidOperationException:
        forms.alert(no_work_plane_msg, title=TOOL_NAME)
        return None
    try:
        p2 = uidoc.Selection.PickPoint(
            "Click the SECOND point of the reference line")
    except OperationCanceledException:
        return None
    except InvalidOperationException:
        forms.alert(no_work_plane_msg, title=TOOL_NAME)
        return None
    return p1, p2


def get_line_direction(p1, p2):
    """Return (direction, None) or (None, error_message) for the 3D
    world-space vector from p1 to p2 (v12.3 - see the design note at the
    top of this file for why this replaced the old XY-only
    get_line_direction_deg()). The "too close together" check now uses
    the REAL 3D distance between the two points (p1.DistanceTo(p2)), not
    just their X/Y difference - a line that is mostly vertical (varies
    mainly in Z, common when picking a reference line in a SECTION or
    ELEVATION view) used to be wrongly reported as "the same point"
    because the old check only ever looked at X and Y. The error message
    includes the raw picked coordinates so that if Trung ever sees this
    again, the popup itself (which he can screenshot, same as always)
    already carries the exact numbers needed to diagnose it - no
    separate DEBUG flag or console capture needed.
    """
    direction = p2 - p1
    distance = p1.DistanceTo(p2)
    if distance < 1e-9:
        return None, (
            "Those two points are the same point (or too close together) "
            "- pick two points that are clearly apart to define a "
            "direction. (P1: X={:.3f}, Y={:.3f}, Z={:.3f} | P2: X={:.3f}, "
            "Y={:.3f}, Z={:.3f} | distance={:.6f} ft)".format(
                p1.X, p1.Y, p1.Z, p2.X, p2.Y, p2.Z, distance))
    return direction, None


def _view_relative_angle_deg(direction, view):
    """Return the angle (degrees, 0-360) that `direction` (a 3D
    world-space vector) makes ON SCREEN in `view`'s own 2D coordinate
    system - i.e. as Trung would actually see and measure it while
    looking at that view, regardless of what kind of view it is.

    v12.3 replaces the old v8.0 approach (get a "world XY angle", then
    subtract the view's RightDirection angle - also computed from X/Y
    only). That approach silently assumed the view's own 2D drawing
    plane WAS the world XY plane, which is only true for a plan view (or
    a reflected ceiling plan). Trung's first real test of this tool from
    a SECTION view exposed the gap: a section view's 2D plane is
    RightDirection (horizontal, in world X/Y) crossed with UpDirection
    (world Z, vertical) - a VERTICAL plane, not the XY plane at all - so
    every calculation that only ever looked at X and Y was blind to the
    Z component that carries most or all of the direction in a section/
    elevation view, both for the "same point" check (fixed above in
    get_line_direction()) and for the angle itself.

    The general, correct fix: project `direction` onto the view's own
    RightDirection (screen +X) and UpDirection (screen +Y) using the dot
    product, and take the angle of THAT 2D projection. RightDirection,
    UpDirection, and ViewDirection always form a proper orthonormal
    basis for any Revit view - plan, section, elevation, or 3D - so this
    is not a special case per view type, it is simply what "the angle of
    this direction on screen" means, computed the general way instead of
    assuming the screen happens to be the XY plane.
    """
    right = view.RightDirection
    up = view.UpDirection
    rx = direction.DotProduct(right)
    ry = direction.DotProduct(up)
    angle_deg = math.degrees(math.atan2(ry, rx)) % 360.0
    if 90.0 < angle_deg < 270.0:
        angle_deg = (angle_deg + 180.0) % 360.0
    return angle_deg


def _get_angle_parameter(element):
    """Find the element's 'Angle' parameter - the exact field Trung sees
    and can edit directly in the Properties palette for a tag once
    Orientation = Model (v7.0). Tries the parameter's display name first
    (confirmed to exist from Trung's own screenshot), then a short list
    of BuiltInParameter fallbacks in case the display name ever differs
    on some category or a future Revit version. Returns None if nothing
    usable is found - caller must treat that as "try the next
    mechanism" (v11.0), not a silent guess.
    """
    param = element.LookupParameter(_ANGLE_PARAM_NAME)
    if param is not None and not param.IsReadOnly:
        return param
    for name in _ANGLE_PARAM_BIP_FALLBACKS:
        bip = getattr(BuiltInParameter, name, None)
        if bip is None:
            continue
        try:
            param = element.get_Parameter(bip)
        except Exception:
            param = None
        if param is not None and not param.IsReadOnly:
            return param
    return None


def _get_location_rotation_deg(element):
    """Try to read element's current rotation via Location.Rotation, in
    degrees normalized to [0, 360) (v11.0). This is the SAME approach
    that was confirmed UNAVAILABLE for IndependentTag in v5.0's testing
    - it is used here only as a fallback for element types (Text Note)
    that don't have an 'Angle' parameter, on the theory that a simpler,
    plain point-placed element may support it even though a tag with
    multiple orientation modes did not. NOT assumed to work - returns
    None (caller must raise a clear error, not guess) if unavailable.

    v11.1: Trung's first Text Note test hit exactly this None case
    (confirmed by the error message he got), and Text Notes turned out
    to have NO numeric angle field in Properties at all - only a
    click-and-drag curved-arrow rotate GRIP. That grip strongly suggests
    Revit rotates Text Notes via a relative transform under the hood
    too (same family as RotateElement), which still leaves open exactly
    WHY Location.Rotation wasn't usable for this particular Text Note -
    a real LocationPoint.Rotation is the normal, well-documented way to
    read a Text Note's angle in many public Revit API examples, so
    something specific to this Trung's file/this note is worth pinning
    down rather than guessing blindly again. This note also had
    Left/Right "Attachment" settings configured (visible in his
    screenshot) - a Text Note anchored between attachment points may
    not expose a plain LocationPoint the same way a simple free note
    does, which is one concrete lead. Added granular DEBUG_ANGLE
    reporting below (which exact sub-step returned nothing) instead of
    one blanket try/except, so the next test run gives real evidence
    instead of another guess.
    """
    try:
        loc = element.Location
    except Exception as ex:
        if DEBUG_ANGLE:
            print("  [pyNBT angle] Location fallback: element.Location "
                  "raised an exception: {}".format(ex))
        return None
    if loc is None:
        if DEBUG_ANGLE:
            print("  [pyNBT angle] Location fallback: element.Location "
                  "is None")
        return None
    rot = getattr(loc, "Rotation", None)
    if rot is None:
        if DEBUG_ANGLE:
            print("  [pyNBT angle] Location fallback: Location is of "
                  "type {} which has no usable 'Rotation' "
                  "value".format(type(loc).__name__))
        return None
    try:
        return math.degrees(rot) % 360.0
    except Exception as ex:
        if DEBUG_ANGLE:
            print("  [pyNBT angle] Location fallback: could not convert "
                  "Rotation value to degrees: {}".format(ex))
        return None


def _shortest_delta_deg(current_deg, target_deg):
    """Shortest signed turn (degrees, in (-180, 180]) to get FROM
    current_deg TO target_deg, both taken mod 360 (v11.0 - brought back
    for the Location.Rotation fallback path; the direct 'Angle' param
    path added in v7.0 needs no delta at all)."""
    diff = (target_deg - current_deg) % 360.0
    if diff > 180.0:
        diff -= 360.0
    return diff


def _get_or_create_textnote_angle_schema():
    """Return pyNBT's Extensible Storage schema for tracking a Text
    Note's last-applied angle (v12.0), creating it once per Revit
    session/document if it doesn't exist yet - same pattern v6.0 used
    for the (now removed) Tag angle schema."""
    schema = Schema.Lookup(_TEXTNOTE_ANGLE_SCHEMA_GUID)
    if schema is not None:
        return schema
    builder = SchemaBuilder(_TEXTNOTE_ANGLE_SCHEMA_GUID)
    builder.SetSchemaName(_TEXTNOTE_ANGLE_SCHEMA_NAME)
    builder.SetReadAccessLevel(AccessLevel.Public)
    builder.SetWriteAccessLevel(AccessLevel.Public)
    builder.AddSimpleField(_TEXTNOTE_ANGLE_FIELD_NAME, System.Int64)
    return builder.Finish()


def _get_stored_textnote_angle_deg(element):
    """Return the angle (degrees) pyNBT last applied to this Text Note
    via the v12.0 Extensible Storage mechanism, or 0.0 if this note was
    never aligned by the tool before - which matches a fresh, untouched
    note's real 0-degree state (the same assumption v6.0 made for Tags,
    and the same known trade-off: if Trung rotated this note BY HAND
    before ever running the tool on it, this will be wrong the first
    time, but correct on every run after that)."""
    schema = Schema.Lookup(_TEXTNOTE_ANGLE_SCHEMA_GUID)
    if schema is None:
        return 0.0
    entity = element.GetEntity(schema)
    if entity is None or not entity.IsValid():
        return 0.0
    field = schema.GetField(_TEXTNOTE_ANGLE_FIELD_NAME)
    milli_deg = entity.Get[System.Int64](field)
    return milli_deg / 1000.0


def _store_textnote_angle_deg(element, angle_deg):
    """Remember angle_deg (degrees, absolute) as the angle pyNBT just
    applied to this Text Note, so the NEXT run reads it back via
    _get_stored_textnote_angle_deg() instead of assuming 0 (v12.0)."""
    schema = _get_or_create_textnote_angle_schema()
    entity = Entity(schema)
    field = schema.GetField(_TEXTNOTE_ANGLE_FIELD_NAME)
    entity.Set[System.Int64](field, System.Int64(int(round(angle_deg * 1000.0))))
    element.SetEntity(entity)


def align_element_to_angle(doc, element, angle_deg):
    """Set a single element's (IndependentTag OR TextNote, v11.0)
    rotation to angle_deg (ABSOLUTE, degrees, already view-corrected by
    the caller). Must run inside an already-open Transaction.

    v1.9 through v6.0 all rotated tags with
    ElementTransformUtils.RotateElement, which only rotates BY a relative
    amount from the CURRENT rotation - there is no "set absolute angle"
    API on it. Every one of those versions had to somehow determine or
    remember the tag's current angle before rotating, and every approach
    eventually broke (see those version notes for the long history).

    v7.0 found a real, directly editable Revit parameter called "Angle"
    for tags with Orientation = Model, confirmed from Trung's own
    Properties palette screenshot - setting THAT parameter directly to
    the absolute target needs no RotateElement, no relative delta, no
    remembering or reading any "current" state at all.

    v11.0: Trung asked to also align Text Notes (merged into this same
    tool). Text Notes have no TagOrientation and, as far as could be
    confirmed, no "Angle" instance parameter the way tags have (that
    field only ever appeared once a tag's Orientation = Model - Text
    Notes have no such Orientation concept).

    v12.0: both the "Angle" parameter AND Location.Rotation are now
    confirmed dead ends for Text Note (see the v12.0 design note at the
    top of this file), so the mechanism order is now:
        1. The "Angle" parameter approach from v7.0/v8.0 - tried FIRST
           on ANY element (tag or Text Note). This is the ONLY path
           Tags ever use; it always succeeds for a valid tag.
        2. If not found AND the element is a Text Note - use pyNBT's
           own Extensible Storage memory (v12.0): read the angle the
           tool last applied (0.0 if never touched before), rotate by
           the shortest delta via RotateElement (using element.Coord as
           the axis origin, not Location.Point - see v11.1's findings),
           then store the new absolute angle back for next time.
        3. Otherwise (any other, currently unforeseen element type) -
           fall back to Location.Rotation as a last resort, raising a
           clear error (element type + ask for a Properties screenshot)
           if that also fails, rather than a silent wrong angle.
    A hand-rotated Tag is never a special case (mechanism 1 always
    lands on the absolute target regardless of prior state). A Text
    Note rotated BY HAND is a special case ONLY the very first time the
    tool ever touches it (see the v12.0 trade-off note at the top of
    this file) - every run after that is exact, since the tool then
    knows the angle it last set.
    """
    if isinstance(element, IndependentTag):
        t_switch = time.time()
        if element.TagOrientation != TagOrientation.AnyModelDirection:
            try:
                element.TagOrientation = TagOrientation.AnyModelDirection
            except AttributeError:
                raise Exception("Free tag rotation needs Revit 2022 or later.")
            doc.Regenerate()
        _log_timing("Ensure TagOrientation.AnyModelDirection (only "
                    "switches - and only regenerates - if it wasn't "
                    "already in that mode)", time.time() - t_switch)

    t_set = time.time()
    angle_param = _get_angle_parameter(element)
    if angle_param is not None:
        if DEBUG_ANGLE:
            try:
                before_deg = math.degrees(angle_param.AsDouble()) % 360.0
                before_text = "{:.3f}".format(before_deg)
            except Exception:
                before_text = "?"
            print("  [pyNBT angle] (Angle param) before={} deg, "
                  "target={:.3f} deg".format(before_text, angle_deg))
        angle_param.Set(math.radians(angle_deg))
        _log_timing("Set the 'Angle' parameter directly to the absolute "
                    "target (view-corrected as of v8.0 - no "
                    "RotateElement, no baseline tracking)",
                    time.time() - t_set)
        return

    if isinstance(element, TextNote):
        current_deg = _get_stored_textnote_angle_deg(element)
        delta_deg = _shortest_delta_deg(current_deg, angle_deg)
        if DEBUG_ANGLE:
            print("  [pyNBT angle] (Text Note Extensible Storage, v12.0) "
                  "stored={:.3f} deg, target={:.3f} deg, "
                  "delta={:.3f} deg".format(current_deg, angle_deg,
                                             delta_deg))
        if abs(delta_deg) > 1e-6:
            origin = element.Coord
            axis = Line.CreateBound(origin, origin + XYZ.BasisZ)
            ElementTransformUtils.RotateElement(doc, element.Id, axis,
                                                 math.radians(delta_deg))
        _store_textnote_angle_deg(element, angle_deg)
        _log_timing("Rotate Text Note by the shortest delta via pyNBT's "
                    "own Extensible Storage memory (v12.0 - used because "
                    "neither the 'Angle' parameter nor Location.Rotation "
                    "is available for Text Note)", time.time() - t_set)
        return

    current_deg = _get_location_rotation_deg(element)
    if current_deg is None:
        raise Exception(
            "Could not find an editable 'Angle' parameter or a usable "
            "Location.Rotation on this element to set its rotation. "
            "Please tell pyNBT the element type/category and Revit "
            "version, and send a Properties palette screenshot, so this "
            "can be investigated further.")
    delta_deg = _shortest_delta_deg(current_deg, angle_deg)
    if DEBUG_ANGLE:
        print("  [pyNBT angle] (Location.Rotation fallback) "
              "current={:.3f} deg, target={:.3f} deg, "
              "delta={:.3f} deg".format(current_deg, angle_deg, delta_deg))
    if abs(delta_deg) > 1e-6:
        origin = element.Location.Point
        axis = Line.CreateBound(origin, origin + XYZ.BasisZ)
        ElementTransformUtils.RotateElement(doc, element.Id, axis,
                                             math.radians(delta_deg))
    _log_timing("Rotate by the shortest delta via Location.Rotation "
                "fallback (v11.0 - used only when no 'Angle' parameter "
                "was found)", time.time() - t_set)


def align_many(doc, elements, direction):
    """Align every element in elements to direction (a 3D world-space
    vector, v12.3 - previously a plain world-XY angle), each converted
    to that element's OWN view's on-screen angle (see
    _view_relative_angle_deg()), all inside ONE Transaction - so the
    whole batch is still a single step in Trung's Undo history, exactly
    like a single-tag align always was.

    Per Trung's confirmed choice: an element that fails (e.g. no
    'Angle' parameter AND no usable Location.Rotation) is SKIPPED, not
    a hard stop for the whole batch - every other element still gets
    aligned. All failures are collected and returned so main() can
    report them together in one popup at the end (still silent if
    everything succeeded).
    """
    t = Transaction(doc, "pyNBT - {}".format(TOOL_NAME))
    t.Start()
    errors = []
    for element in elements:
        try:
            el_view = doc.GetElement(element.OwnerViewId)
            angle_deg = _view_relative_angle_deg(direction, el_view)
            if DEBUG_ANGLE:
                print("  [pyNBT angle] element={} view-relative "
                      "target={:.3f} deg".format(
                          _safe_element_text(element), angle_deg))
            align_element_to_angle(doc, element, angle_deg)
        except Exception as ex:
            errors.append("Error aligning {}: {}".format(
                _safe_element_text(element), str(ex)))
    if t.HasStarted():
        t.Commit()
    return errors


# ---------------------------------------------------------------------------
# Entry point - single-shot: pick element(s), click 2 points, rotate, done.
# No loop - run the tool again from the ribbon to align another batch.
# ---------------------------------------------------------------------------

def main():
    if DEBUG_TIMING:
        print("--- pyNBT {} v{} timing ---".format(TOOL_NAME, TOOL_VERSION))

    elements = get_elements_to_align(uidoc, doc)
    if not elements:
        return

    t_pick_pts = time.time()
    points = pick_line_points(uidoc, doc)
    _log_timing("Pick 2 line points (includes YOUR OWN mouse time - not "
                "a useful number to judge tool speed by)",
                time.time() - t_pick_pts)
    if points is None:
        return
    p1, p2 = points

    direction, err = get_line_direction(p1, p2)
    if direction is None:
        forms.alert(err, title=TOOL_NAME)
        return

    t_align = time.time()
    errors = align_many(doc, elements, direction)
    _log_timing("TOTAL align_many() for {} element(s) (everything after "
                "your last click)".format(len(elements)),
                time.time() - t_align)
    if errors:
        forms.alert("\n".join(errors), title=TOOL_NAME)


main()
