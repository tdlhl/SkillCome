# Spreadsheet Manipulation Skill (xlsx)

## Overview
This skill guides agents in manipulating Excel (.xlsx) spreadsheets using Python.

**Primary libraries**: `openpyxl` (structure-preserving read/write), `pandas` (data transformation).
Never use any other third-party libraries.

---

## Common Workflow

1. **Explore** the input file: list sheets, inspect headers, check dimensions.

**MANDATORY: Two-pass exploration before any logic.**
**Pass 1 (source):** Load the workbook *twice*: `(1) data_only=True` for computed values and `(2) default` for formulas/formatting. Print every non-empty cell in ALL source columns up to `ws.max_column` and rows up to `ws.max_row` with `repr(value)` and `type(value).__name__`. This catches datetime-vs-time-vs-string, hidden data in columns beyond the preview, and legend tables the task references.
**Pass 2 (target):** Print the full target range (header row + all data rows) to confirm dimensions and expected types.
Do NOT author any solution logic until both passes are printed. Do NOT infer ranges from a preview truncation.
2. **Write `solution.py`** with `INPUT_PATH` and `OUTPUT_PATH` defined at the top.
3. **Execute** `python solution.py` and verify the output file was created.
4. **Confirm** the target cells/range contain the expected values.

---

## Library Selection

| Use case | Library |
|----------|---------|
| Preserve formulas, formatting, named ranges | `openpyxl` |
| Bulk data transformation, aggregation, sorting | `pandas` → write back with `openpyxl` |
| Simple cell read/write | `openpyxl` |

**Warning**: `pandas.to_excel()` silently destroys existing formulas and named ranges.
When writing back to a spreadsheet that contains formulas, always use `openpyxl.save()`.

---

## solution.py Template

```python
import openpyxl
import pandas as pd

INPUT_PATH  = "..."   # set to the actual input path
OUTPUT_PATH = "..."   # set to the actual output path

wb = openpyxl.load_workbook(INPUT_PATH)
ws = wb.active  # or wb["SheetName"]

# --- perform manipulation ---

wb.save(OUTPUT_PATH)
```

---

## Output Requirements

- **Write computed values, not Excel formulas.** The evaluation checker reads the output with `openpyxl.load_workbook(..., data_only=True)`, which returns `None` for formula cells lacking a cached value. Always compute the result in Python (simulate VLOOKUP, INDEX/MATCH, SUM, date arithmetic, etc.) and write the **literal** value (int, float, str, `datetime.time`, `datetime.timedelta`) into the target cell. Never write a formula string (e.g. `"=VLOOKUP(...)"`) unless the task explicitly asks to *preserve* that formula in the output.

### Simulating existing formulas
When the preview shows a formula in a target cell (e.g. `=IF(AND($B$2>C4,$B$2<D4),"Select Period",...)`), do **not** parse the formula text. Instead, locate every operand cell referenced by the formula (`$B$2`, `C4`, `D4`, …), read their **raw values** via `openpyxl`, and replicate the logic in Python (`datetime` comparisons, numeric comparisons, string equality, etc.). This avoids fragile formula-parsing and naturally handles the `data_only=True` evaluation constraint.

- Save the result to `OUTPUT_PATH`.
- Do not hardcode row counts or column letters — iterate over actual rows in the workbook.
- Preserve sheets and cells not mentioned in the instruction.

---

## Cross-Sheet / Lookup Patterns

When matching values between sheets (INDEX/MATCH, VLOOKUP, retrieve-by-key):

1. **Build a dictionary** from the source sheet in one pass.
2. **Populate the target** by looking up each key. Avoid O(n²) nested loops.

### Key normalisation is mandatory

Excel performs implicit type coercion (int 1010 matches float 1010.0; " 1010  " matches 1010). Use a normaliser on *both* the source key and the search key:

```python
def norm_key(v):
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float)):
        return float(v)
    return v

lookup = {}
for row in range(2, ws_src.max_row + 1):
    k = norm_key(ws_src.cell(row=row, column=1).value)
    v = ws_src.cell(row=row, column=5).value
    if k is not None:
        lookup[k] = v

for row in range(2, ws_tgt.max_row + 1):
    k = norm_key(ws_tgt.cell(row=row, column=4).value)
    if k in lookup:
        tgt = ws_tgt.cell(row=row, column=7)
        raw = lookup[k]
        tgt.value = 0 if raw is None else raw  # see None→0 rule below
```

## Value Coercion: None → 0 and Time / Empty-Cell Semantics

openpyxl returns `None` for genuinely empty cells, but Excel formulas treat them as **0**. When replicating formula logic:

- **Coerce `None` to `0`** before writing the computed result; never skip the write. Apply to *every* cell in the output range, including cells whose source is blank.

- **Preserve string vs. numeric type for computed outputs.** If the expected value has leading zeros or is inherently a fixed-width string (e.g. "014", "000", "A006"), write it as a **Python `str`**, not an `int`. A numeric `14` written where `"014"` is expected will fail the checker. When in doubt, check the type and value of a few filled input cells in the target column.
- A cell displaying `0:00:00` (zero-duration time) is stored as numeric `0`, read back by openpyxl as `datetime.time(0,0)` or `datetime.timedelta(0)`—**not** `""` or `None`. If a formula path (e.g. `IFERROR(...)`) would yield an empty string but the expected type is time, write `datetime.time(0, 0)` with a `hh:mm:ss` format string.
- Inspect actual expected types by reading a few input cells and checking `type(cell.value)` before deciding the output type.

---

## Text Matching Best Practices

For keyword search, partial match, or "contains" logic:

- Normalise to string + lowercase: `str(cell.value).lower()`.
- Use `in` for substring/partial matching: `if kw_lower in text_lower`.
- Multi-keyword tasks: `any(kw in text for kw in keywords_lower)`.
- Guard against `None`: `str(cell.value or '')`.
- Numeric prefixes/suffixes (e.g. "123-Core Design") still match via substring check.

<!-- SLOW_UPDATE_START -->
## Iterative Distribution / Balancing (REGRESSION GUARD)

When a task says "add units sequentially (1→5) to offset positive residual, subtract (5→1) for negative," implement EXACTLY:
- Positive residual: start at unit 1, add up to (max_i − current_i) to each unit in order 1,2,3,4,5 until residual is consumed.
- Negative residual: start at unit 5, subtract up to (current_i − min_i) in order 5,4,3,2,1.
- Clamp additions/subtractions to respect per-unit min/max constraints.
- Do NOT use proportional allocation. Sequential fill matters.
- After computing each row, print the final unit values and residual to verify the sum of changes equals the original residual.

## Row Deletion with Cascading Shift (CRITICAL)

When deleting N rows before EACH marker (e.g., 4 rows before each "Chassis"):
1. Collect all marker row indices BEFORE any deletion.
2. Process markers in ASCENDING original order. For the k-th marker at original row r_k:
   - Compute its CURRENT row after prior deletions: current_r_k = r_k − 4*k.
   - Delete 4 rows immediately above current_r_k.
3. After all deletions, markers must land at the final positions stated in the task.
4. NEVER delete rows using only original indices without adjusting for prior deletions.

## Range-Based (Bisect) Lookup, Not Exact-Match

When a legend/table maps a VALUE to a CATEGORY via ranges (e.g., "0–10% → Low, 10–20% → Medium"):
- Do NOT use dict with exact keys. Build a sorted list of (threshold, category).
- Use `bisect_right` or iterate thresholds descending to find the first threshold ≤ the value.
- After lookup, print the matched range and category for each target cell. If None, your thresholds list is incomplete or the value falls outside all defined ranges — assign the boundary category, never None.

## Formula Reference Drag / Step Increment

When simulating "drag this formula down" where the absolute reference shifts per row (e.g., $B$8 → $B$13 → $B$18):
1. From the task or first two example rows, compute the STEP = row_2_ref − row_1_ref.
2. For row offset i (0-based), the formula reference row = initial_ref_row + i × STEP.
3. Read that cell's value for each row independently. Do NOT reuse one fixed reference for all rows.
4. Print each row's resolved reference row and fetched value during execution to confirm alignment.

## Empty-String Guard on Numeric Parsing

Before calling `int()` or `float()` on a cell value, ALWAYS guard:
```python
def parse_int(v):
    if v is None or (isinstance(v, str) and v.strip() == ''):
        return None
    return int(v)
```
Never assume a cell in a numeric-looking column is actually numeric — it may be `''` or a space. Skip rows where required numeric fields parse to None.

## Lookup Returns None — Debug Protocol

If ALL target cells in a lookup yield None or False:
1. Print the FULL key you're searching for (repr + type) and the FULL list of keys in your dict.
2. Check: is the key stored as int but searched as str, or vice versa? Apply norm_key to BOTH sides.
3. Check: are you reading the correct column index? Print `ws.cell(row=r, column=col).value` for 3 sample rows.
4. Check: is the reference table in a DIFFERENT sheet or starting at a non-obvious row? Print `ws.max_row` and the first 3 non-empty cells of the key column.

## Retained Rules (Effective)

- Block-aware header preservation: keep per-block header text in place when sorting data within blocks.
- Composite strings: extract fixed prefix verbatim from the source cell; do not decompose segments. Use zfill for numeric padding.
- Post-write verification: re-open output and print every checked cell. If None where value expected, fix before finalizing.
<!-- SLOW_UPDATE_END -->
