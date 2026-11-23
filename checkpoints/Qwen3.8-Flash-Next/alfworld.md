# ALFWorld Embodied Agent Skill

## Overview
This skill guides agents operating in the ALFWorld text-based embodied environment.
The agent must complete household tasks by navigating rooms, interacting with objects,
and using appliances. Actions must be chosen from the admissible action list provided
at each step.

**Output format**: Always output `<think>...</think>` for reasoning, then `<action>...</action>` for the chosen action.

---

## Task Types

| Type | Goal | Key Steps |
|------|------|-----------|
| Pick & Place | Put object X in/on receptacle Y | Find X -> take X -> go to Y -> put X in/on Y |
| Pick Two & Place | Put two instances of X in/on Y | Find X1 -> take -> go to Y -> place X1 -> go search -> find X2 -> take -> go to Y -> place X2. Place the first item at the final destination immediately, then resume searching for the second. |

**Multi-instance memory**: If during initial search you spotted *two or more* instance IDs of the target at the same location, prioritize returning to that same spot for the second pickup instead of exploring unrelated rooms. A brief 1–2 step detour is acceptable, but the known multi-instance location should be your first re-visit target after placing item 1.
| Examine in Light | Examine object X under desklamp | Find X -> take X -> find desklamp -> use desklamp |
| Clean & Place | Clean object X and put in/on Y | Find X -> take X -> go to sink -> clean X -> go to Y -> put X |
| Heat & Place | Heat object X and put in/on Y | Find X -> take X -> go to microwave -> **open microwave** -> heat X with microwave -> go to Y -> put X |
| Cool & Place | Cool object X and put in/on Y | Find X -> take X -> go to fridge -> **open fridge** -> cool X with fridge -> go to Y -> put X |

---

## General Principles

1. **Decompose the task**: Parse the goal into ordered sub-goals (locate, acquire, transform, deliver). Complete each before moving to the next.
2. **Systematic exploration with a visited-set**: Maintain an explicit mental list "VISITED = {location1: fully-checked, location2: fully-checked, …}". A location is *fully-checked* once you have looked at every surface/item and opened every closed container at it. NEVER navigate to a location already in VISITED as fully-checked unless you have concrete reason (e.g. an object you dropped there). Open closed containers before judging them empty.

2b. **Sweep every numbered instance**: Treat each numbered variant of a location (cabinet 1, cabinet 2, cabinet 3 …; diningtable 1, diningtable 2 …; drawer 1, drawer 2 …) as a *separate* entry in VISITED that must be individually confirmed. If instance N yields nothing, go to instance N+1 on your very next opportunity instead of cycling back to other already-searched spots. The task target may reside on any numbered instance; skipping N+1 wastes steps and can make the object unfindable within the step limit.
3. **Grab immediately**: When a required object is visible and reachable, take it right away before moving elsewhere.

3b. **Exact name match**: Only pick up an object whose name in the observation exactly matches the target name in the task instruction. Similar-sounding names are different objects (e.g. 'cloth' ≠ 'towel', 'creditcard' ≠ 'card', 'mug' ≠ 'cup'). Picking up a wrong-name object wastes inventory space and steps — ignore it and keep searching.

3c. **Respect the single-carry limit**: You can hold only one object at a time. If you are holding the wrong item and see the correct target, drop the current item onto the nearest surface (or the current location), then immediately pick up the target. Do not try to multi-carry.
4. **Transform before placing**: If the task requires cleaning, heating, or cooling, perform the state change at the appropriate appliance before heading to the final destination.
5. **Direct delivery**: Once holding the transformed (or untransformed) goal object, navigate straight to the target receptacle and place it.

5b. **No verification at destination**: Upon arriving at the target receptacle, issue the placement action (`move X to Y`) immediately. Do NOT `look`, `examine`, or `inventory` at the destination first—these steps add zero information and risk triggering a loop penalty. Place first, confirm in the next observation if needed.
6. **Track progress**: Maintain an internal count of how many objects still need to be found and placed. Only stop searching when the count reaches zero.
7. **Avoid loops (consecutive AND cyclic)**: Never repeat the same action twice in a row. Detect CYCLES aggressively: if the set of locations you visited in your **last 5 steps** is identical to or a subset of locations you visited earlier in the episode, you are in a loop regardless of step-level repeats. The moment you detect this, your **very next action** MUST be `go to <location in an unvisited room>` selected from the admissible action list. If no unvisited-room target exists, `go to` a closed container you have not yet opened.
8. **Only choose admissible actions**: Always pick an action from the admissible action list. Do not invent actions.

---

## Common Mistakes to Avoid

- **Revisiting searched locations**: Keep track of which surfaces/containers have been checked; do not re-examine them.
- **Ignoring visible objects**: If the target object appears in the observation, pick it up immediately.
- **Skipping state changes**: Do not place an object at the destination without first cleaning/heating/cooling it when required.
- **Premature termination**: Do not stop the episode until all goal conditions are verified as met.
- **Action loops**: Repeatedly toggling or examining the same object wastes steps. Move on to new locations instead.

## Exploration Escalation When Stuck

If you have cycled through 3+ locations without finding progress (e.g. no new object appears, no placement succeeds), immediately switch to a **different room or zone** rather than rotating among the same neighbouring spots. Use the admissible "go to" actions to reach locations you have never visited. Do not waste actions on `look`, `examine`, or `inventory` at the destination when you already know what is there — those steps do not advance the goal; a new `go to` does.

## Step-Budget Awareness
ALFWorld episodes grant roughly 50 steps. Budget them: reserve the last ~10 steps for the final pickup → transform → place sequence. If by step 30 you have not yet found the target object, stop re-checking explored locations entirely and dedicate every remaining step to visiting a **new** room or opening a **new** closed container. A step spent revisiting a checked location is a step that will not be available to complete the task.

<!-- SLOW_UPDATE_START -->
MANDATORY PRE-ACTION GATE (follow before EVERY `take`):
Am I at the task's FINAL DESTINATION? YES → I am looking at my own delivered object. NEVER take it again. Go find a new location. NO → Check the name matches the target EXACTLY (see EXACT MATCH below). Then proceed.

EXACT NAME MATCH — NON-NEGOTIABLE:
Compare the full noun token before any `take`. These are DIFFERENT objects: knife ≠ butterknife, plate ≠ pan, spoon ≠ ladle ≠ spatula, cup ≠ mug, cloth ≠ towel, saltshaker ≠ peppershaker (wait: saltshaker IS saltshaker — peppershaker ≠ saltshaker), card ≠ creditcard, bowl × tray. If the observation says 'pan 2' and the task says 'plate', DO NOT TAKE THE PAN. Moving a pan to the countertop does not satisfy 'put the plate on the counter'. When uncertain, read the noun character-by-character against the task instruction.

POST-PLACEMENT SEARCH (pick-two tasks):
After placing item 1, first action MUST be `go to` a location NOT yet visited. Revisit item 1's source ONLY if 2+ instances were visible there. Otherwise that source is depleted. If you revisit any location and the target is not visible, mark it DONE permanently.

HARD ROOM-ROTATION RULE (fixes the #1 failure mode):
If you have been at a location TWICE and saw no target object, that location is PERMANENTLY CLOSED. At step 10, if the target noun has not appeared in any 'You arrive at' or 'On the … you see' line, you MUST move to a room you have ZERO visits. At step 20, if still unseen, leave the current room cluster entirely (e.g. leave kitchen → go to bedroom/bathroom/living room). You may NOT revisit any of the 5 most-recent locations. Consult the admissible-action list and select a `go to` whose room name you have never typed. This is not a suggestion — cycling among 5 locations for 40 steps guarantees failure.

EXHAUSTION COMMIT (step 25+):
Every remaining step must be a `go to` targeting a room or numbered container you have NEVER opened. Open every closed cabinet, drawer, and container on first arrival. Do NOT `look`, `examine`, or `inventory` at confirmed-empty locations. If you cannot find the target after opening all containers, it may be on a furniture surface in a room whose name appears in the admissible `go to` list but never appeared in your history.

SWEET SPOT TARGETING:
Kitchen utensils/food → countertops, diningtables (check ALL numbered tables: 1, 2, 3, 4), fridge, cabinets, sinkbasin. Cooking items (plate, bowl, pan, spatula) → check ALL diningtable instances AND countertop instances AND stoveburners AND every cabinet number. Electronics → desks, dressers, sidetables, bed. The target may be on the HIGHEST-numbered instance (diningtable 3, cabinet 5). Do not stop at instance 2 if instance 3 exists in your admissible list.

PRESERVE WHAT WORKS:
Sweep every numbered instance (drawer 1-4, cabinet 1-5, shelf 1-3, diningtable 1-3) as separate VISITED entries. Next instance = NEXT step. Multi-instance memory: 2+ target IDs at one spot → return there for 2nd pickup. Step-budget: reserve ~10 steps for transform (open appliance → use → close) + place 1–2 items. Examine-in-light: find desklamp first 5 steps, then target, then lamp.

LOOP KILL (hard stop):
After 3 `go to` actions landing on locations already seen with identical contents, STOP. Pick the admissible `go to` whose room-name string is NOT in your visit history. If none exists, `open` a numbered container at fresh ground. An unopened container yields new info; a re-read does not. Counting: track your last-6-location set. If it overlaps ≥ 80% with an earlier 6-location window, break out immediately.
<!-- SLOW_UPDATE_END -->
