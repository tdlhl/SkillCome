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
| Pick Two & Place | Put two instances of X in/on Y | Find X1 -> take -> place -> find X2 -> take -> place |
| Examine in Light | Examine object X under desklamp | Find X -> take X -> find desklamp -> use desklamp |
| Clean & Place | Clean object X and put in/on Y | Find X -> take X -> go to sink -> clean X -> go to Y -> put X |
| Heat & Place | Heat object X and put in/on Y | Find X -> take X -> go to microwave -> heat X -> go to Y -> put X |
| Cool & Place | Cool object X and put in/on Y | Find X -> take X -> go to fridge -> cool X -> go to Y -> put X |

---

## General Principles

1. **Decompose the task**: Parse the goal into ordered sub-goals (locate, acquire, transform, deliver). Complete each before moving to the next.
2. **Systematic exploration**: Search each surface and container exactly once before revisiting. Open closed containers (drawers, cabinets, fridge) before judging them empty.
3. **Grab immediately**: When a required object is visible and reachable, take it right away before moving elsewhere.
4. **Transform before placing**: If the task requires cleaning, heating, or cooling, perform the state change at the appropriate appliance before heading to the final destination.
5. **Direct delivery**: Once holding the transformed (or untransformed) goal object, navigate straight to the target receptacle and place it.
6. **Track progress**: Maintain an internal count of how many objects still need to be found and placed. Only stop searching when the count reaches zero.
7. **Avoid loops**: Never repeat the same action more than twice in a row. If stuck, move to a different unexplored location.
8. **Only choose admissible actions**: Always pick an action from the admissible action list. Do not invent actions.

---

## Common Mistakes to Avoid

- **Revisiting searched locations**: Keep track of which surfaces/containers have been checked; do not re-examine them.
- **Ignoring visible objects**: If the target object appears in the observation, pick it up immediately.
- **Skipping state changes**: Do not place an object at the destination without first cleaning/heating/cooling it when required.
- **Premature termination**: Do not stop the episode until all goal conditions are verified as met.
- **Action loops**: Repeatedly toggling or examining the same object wastes steps. Move on to new locations instead.

11. **Check task completion after every 'put' or 'move' action**: After placing an object at the target receptacle for transformation tasks (heat, cool, clean), immediately verify whether the goal condition is satisfied. If the task type requires a state change and you have just placed the transformed object at the correct location, the task is complete — *do not* pick it up again. Stop the episode.

- **Infinite heat/cool loops**: Do not pick up an object from the final receptacle and re-apply the same transformation. If you observe that the object is already at the correct location after the state change, the task is done.

10. **Never deposit goal objects at intermediate locations**: In any pick-and-place task, especially multi-object ones, never move a required object to a countertop, drawer, or any receptacle other than the single final destination specified in the task. Keep the object in your inventory until you arrive at the target, or if you must clear inventory, use only the designated target receptacle.

- **Placing objects in intermediate or wrong receptacles**: Never place a required object on a countertop, different cabinet, or wrong container. Always deliver directly to the single target receptacle specified in the goal. Dropping an object mid-task causes loss of its location and violates the goal condition.

<!-- SLOW_UPDATE_START -->
## Strategic Guidance for Task Execution

These instructions are critical requirements. Follow them strictly to avoid common failure modes.

### 1. EXACT Object Type Matching
When the goal specifies an object type, you MUST pick up ONLY objects whose name EXACTLY matches that type word. The ENTIRE object name must equal the goal's object type—no substitutions, no near matches. Specifically:
- "cloth" ≠ "towel", "handtowel", "rag"
- "cup" ≠ "mug", "goblet", "wine glass", "martini glass", "water glass"
- "knife" ≠ "butterknife", "butter knife"
- "cellphone" ≠ "phone", "smartphone"
- "pencil" ≠ "pen"
- "winebottle" ≠ "glassbottle", "bottle"
- "creditcard" ≠ "card"
- "pan" ≠ "skillet" (but if the goal says "skillet," a "pan" may be the correct target — check the task description carefully)

Before EVERY "take" action, read the object name exactly as it appears in the observation and compare it word-for-word against the goal's object type. If they are not identical, DO NOT pick it up. Continue searching.

### 2. Task Completion and Stopping
After EVERY action where you place, move, or put a goal object at the target receptacle:
- Verify: "Have ALL required objects been delivered to the target?"
- Count: For pick-two tasks, there must be exactly two correct objects at the destination. For single-object tasks, there must be exactly one.
- Transformation tasks (heat/cool/clean): The object must have undergone the transformation AND be at the destination.
- Once all conditions are met, STOP. Do not pick up objects from the target destination again. Do not re-apply transformations. Do not continue exploring.

### 3. Inventory Discipline
- When searching for goal objects, DO NOT carry non-goal items. If you accidentally pick up a wrong item, immediately go to the nearest surface or receptacle and place it there. Then continue your search with empty hands.
- If you are holding a non-goal item and spot a goal object in the observation, FIRST free your inventory (place the carried item somewhere), THEN go back and take the goal object.
- If you are holding a goal object, deliver it to the CORRECT target receptacle BEFORE searching for additional objects. Do not place it on an intermediate surface (countertop, dresser top, table). Go directly to the target receptacle specified in the goal.
- Do not search with a goal object in hand unless you are en route to the target receptacle.

### 4. Systematic Exploration Protocol
When you need to find an object, follow this exact protocol. In your thinking at each step, list the locations you have already CHECKED so you don't revisit them.

**Search order**:
1. Identify ALL reachable furniture and surfaces in the current room from admissible actions or observation. Include: desks, beds, shelves, drawers, dressers, cabinets, countertops, sidetables, diningtables, coffeetables, armchairs, sofas, chairs, benches, sinks, toiletries, bathtubs, towelholders, towel racks, and any other named furniture.
2. For each furniture item: go to it, then look at/examine it. If it's a container (drawer, cabinet, fridge, safe), OPEN it first, then examine contents.
3. In your thinking, maintain an explicit checklist. Example: "Checked: drawer 1 (watch), drawer 2 (empty), dresser 1 (alarmclock, cellphone, pen). NOT checked: drawer 3, cabinet 1, bed 1."
4. NEVER revisit a CHECKED location unless you placed an object there and need to retrieve it.
5. If you have exhaustively searched ALL furniture and containers in the current room and not found the target, navigate to another room and repeat.

**Termination condition**: If you have truly checked EVERY named location in EVERY accessible room exactly once, and the target object is not found, do NOT loop back to already-checked locations. Instead, re-read the goal carefully — you may have misunderstood the object type or target receptacle. Check your inventory in case you already have the object. If you are certain the object cannot be found, end the episode.

### 5. Receptacle Verification
- Before placing an object, verify that the target receptacle's name in the goal matches your intended destination exactly. The goal may specify "the bottom drawer of the television stand" — this is NOT the same as "drawer 1." Look for the specific named receptacle when navigating.
- **Surface vs. internal storage**: A "dresser" is a piece of furniture with both a top surface and internal drawers. If the goal says "put X in the top drawer of the dresser," you must OPEN a drawer and place the object INSIDE it — NOT on top of the dresser. Similarly: "cabinet" can mean the interior (requires opening) or the top surface. Read the goal's preposition carefully: "in" means inside a container; "on" means on a surface.
- If you are unsure which drawer is the "top drawer" or which shelf is the "middle shelf," examine nearby receptacles or use relative positioning. The correct drawer may be labeled explicitly in the admissible actions (e.g., "drawer 1" of "dresser 1"). If the goal says "top drawer," try all drawers of that furniture item starting from the lowest-numbered or first-listed.

### 6. Transformation Task Safeguards
For heat/cool/clean tasks:
- The task is complete the moment you place the TRANSFORMED object at the TARGET receptacle. Do NOT pick it up again. Do NOT re-heat/re-cool/re-clean it.
- After placing the object, check the goal condition. If satisfied, output nothing further — the episode is done.
- Do not pick up other objects of the same type from the target receptacle and transform them. One transformed object at the destination is sufficient.
- If the goal says "cup" but you find a "mug," or says "knife" but you find a "butterknife," do NOT use them as substitutes. Keep searching for the exact object type.

### 7. Multi-Object Task Tracking
For pick-two or any task requiring multiple objects:
- Maintain an explicit count in your thinking at all times: "I need to place N [object type]. I have placed X so far. I still need Y more."
- Update this count after every successful placement.
- Stop searching ONLY when X equals N. Not before.
- Ensure EACH placed object exactly matches the goal's object type.
- Track which specific object instances you've already placed (e.g., "keychain 1 placed, still need keychain 2").

### 8. Room-to-Room Navigation
- If a room's furniture has been fully explored and the target object is not found, navigate to another room. Use admissible actions to go to other named rooms (e.g., "go to bedroom 1", "go to kitchen 1", "go to bathroom 1").
- Do not loop within the same room checking the same locations repeatedly. If a location has been checked today, do not return to it unless you have new information.
- The first action in an episode should be a navigation action to a specific furniture item or room—NOT a "look" action. The "look" action often returns no useful information. Start by going to a named location from the admissible actions.

### 9. CRITICAL: Action Syntax Requirements
- Every step MUST contain BOTH a thinking block ( `...`) AND an action block (`<action>...</action>`). Never output thinking without an action.
- The "take" action REQUIRES the full syntax: `take <object> from <location>`. Example: `take keychain 2 from coffeetable 1`. Do NOT write "take keychain 2" alone — this will fail.
- If you observe "Nothing happens" after an action, it means the action syntax was invalid or the action is not currently admissible. Check the admissible actions list and re-format your action accordingly.
- Do not attempt "move" actions unless you are holding an object and the target location is admissible. The syntax is: `move <object> to <location>`.

### 10. Avoiding Infinite Search Loops
- If you have visited the same set of 4–5 locations more than twice each without finding the target object, STOP and re-evaluate. You are in a loop. Ask yourself: "Have I checked EVERY location in this room? Have I checked surfaces like countertops, beds, shelves — not just drawers and cabinets?"
- Objects are often found on OPEN surfaces (countertops, tables, shelves, beds), not just inside containers. Do not focus exclusively on drawers and cabinets.
- If you see a location with many objects listed (e.g., "countertop 1: apple, bread, lettuce, pan, saltshaker..."), examine ALL objects carefully. The target may be among them.
- If the task says "clean a knife" and you see "butterknife" on every surface, the environment may not contain an exact "knife." In that case, after exhaustive search, the "butterknife" may be the intended target — but only after you have checked EVERY other location and are certain no "knife" exists.
<!-- SLOW_UPDATE_END -->
