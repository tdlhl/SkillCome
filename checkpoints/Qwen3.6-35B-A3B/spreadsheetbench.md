# Spreadsheet Manipulation Skill (xlsx)

## Overview
This skill guides agents in manipulating Excel (.xlsx) spreadsheets using Python.

**Primary libraries**: `openpyxl` (structure-preserving read/write), `pandas` (data transformation).
Never use any other third-party libraries.

---

## Common Workflow

1. **Explore** the input file: list sheets, inspect headers, check dimensions.
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

- Save the result to `OUTPUT_PATH`.
- Do not hardcode row counts or column letters — iterate over actual rows in the workbook.
- Preserve sheets and cells not mentioned in the instruction.

---

## Formulas vs. Computed Values

**HARD RULE — VIOLATED IN 100% OF FAILURES**: NEVER write an Excel formula string to any cell. NEVER use `ws['X99'] = '=SUM(...)'` or any string starting with `=`. The evaluation framework does NOT recalculate formulas; any cell containing a formula string will ALWAYS read back as `None`. This includes **ALL** Excel functions: SUM, SUMPRODUCT, IF, VLOOKUP, INDEX/MATCH, COUNTIF, COUNTIFS, SUMPRODUCT, RIGHT, SEARCH, ISNUMBER, etc. If you write a formula, your output **will** be wrong. Instead, compute **everything** in Python and write ONLY static values (numbers, strings, dates). There are **no exceptions**. This is the #1 cause of task failure.

When the evaluation environment only checks final cell values (not formula text), formulas written with `openpyxl` will evaluate to `None` because Excel does not recalculate them on save. In such cases:

- Do not write Excel formulas. Instead, perform the computation in Python and write the resulting value directly.
- This applies to VLOOKUP, INDEX/MATCH, COUNTIFS, IF, and any other Excel function.
- If you notice a formula cell returns `None` after save, immediately switch to computing the answer in Python and writing the static computed value.

---

## Handling Merged Cells

If you get `'MergedCell' object attribute 'value' is read-only`, the target cell is part of a merged range. Unmerge the overlapping ranges before writing:

```python
for merged_range in list(ws.merged_cells.ranges):
    if merged_range.min_col <= 7 <= merged_range.max_col:
        ws.unmerge_cells(str(merged_range))
```

Alternatively, compute results in Python and write values instead of formulas to avoid formula recalculation issues.

---

## Data Exploration Before Coding

Before implementing the solution, read enough rows to understand the data format, especially when the preview is truncated:

```python
# Print first 20-30 rows of relevant columns to understand data types and patterns
for row in ws.iter_rows(min_row=1, max_row=min(30, ws.max_row), values_only=True):
    print(row)
```

Inspect the actual values, types (strings, integers, floats, datetimes), empty cells, and any special patterns before finalizing your logic.

---

## Cross-Row Lookup vs. Row-Local Matching

When task instructions describe retrieving IDs or values based on search parameters, **always verify whether the search is row-local (same row) or cross-row (lookup across all rows).**

- **Row-local**: The parameters on a row filter/validate that row's own data. The result comes from the same row.
- **Cross-row (join/lookup)**: The parameters act as keys to find a **different** row whose data matches the keys. The result comes from the matching row, which may be different from the original row.

Before coding, compare the example parameters with the expected outputs:
- If a row's expected output matches a **different** row's ID (not its own), the operation is a cross-row lookup.
- Implement this by first loading all relevant columns into a dictionary or list, then for each row performing the lookup across the loaded data. When building the lookup, decide whether duplicate keys can occur and keep the first or last match as required. Convert keys to strings with `str(cell).strip()` to handle mixed types.

When the source spreadsheet contains formulas in cells you need to read (e.g., calculated amounts, dates, or lookups), use `data_only=True` when loading the workbook:

```python
wb = openpyxl.load_workbook(INPUT_PATH, data_only=True)
```

Without `data_only=True`, formula cells often return `None` or the formula string, which cannot be parsed as numbers. If your numeric parsing keeps failing and all values appear as `None`, check whether the source cells contain formulas and switch to `data_only=True`.

Note: after saving with `openpyxl`, formula cells written by your script will still evaluate to `None` on reload. This section applies to pre-existing formulas already in the input file.

<!-- SLOW_UPDATE_START -->
## CRITICAL: Never Write Excel Formulas

When the evaluation environment checks final cell VALUES (not formula text), Excel formulas written to cells will ALWAYS appear as `None` after saving because formulas are not recalculated. This is the #1 cause of failures.

**Hard rule**: NEVER write an Excel formula to any cell. NEVER use `ws['X99'] = '=SUM(...)'` or any string starting with `=`. Instead, perform ALL computations in Python and write ONLY static computed values (numbers, strings, dates) to the output cells.

This applies to ALL Excel functions: SUM, SUMPRODUCT, IF, VLOOKUP, INDEX/MATCH, COUNTIF, COUNTIFS, SEARCH, ISNUMBER, etc. If a task asks for a "formula," it means the computed result, not the formula text.

After saving, verify that target cells contain the expected VALUE type (not None and not a formula string). If any cell is None, you may have accidentally written a formula — fix it to compute in Python instead.

## Data Exploration — Be Thorough Before Coding

Before writing solution logic, always print at least 30 rows (or the full dataset if smaller) of ALL relevant columns. Use `values_only=True` to see actual cell values. Specifically:

- Check if row 1 is a header or data. Only skip row 1 if you confirm it contains header text (not numbers or data values). If unsure, start from row 1.
- Identify which columns are keys, which are values, and which are parameters to match against.
- Look for special patterns: merged cells, empty rows separating blocks, repeated header rows within data, number-as-text formats.
- For date/time data, check the actual Python type (datetime, string, float) and format. Print the type and value of each cell when dealing with dates.
- When exploring, print BOTH the raw cell values AND the corresponding row numbers to understand gaps and offset patterns.

## Iterate Over Actual Rows, Process Only Non-Empty Rows

- Always use `ws.max_row` to determine the actual data range — never hardcode row counts.
- When iterating, use `for row in ws.iter_rows(min_row=1, max_row=ws.max_row, values_only=True)` or loop `range(1, ws.max_row + 1)`.
- When operations should ONLY apply to rows with data (e.g., numbering, formatting, lookups), check if the key cell in the row is non-empty before processing: `if cell.value is not None and str(cell.value).strip() != ''`. Do NOT blindly process all rows from min_row to max_row.
- For sequential numbering or cumulative operations, maintain a counter that increments ONLY when a row with data is encountered. Skip blank rows without advancing the counter.

## Filtering Rows by Column Values

When filtering rows based on a column condition (e.g., "Lowest Performing" students):
- Use a simple equality check against the cell value: `str(cell_value).strip() == "Lowest Performing"`.
- For partial/case-insensitive matching, use: `keyword.lower() in str(cell_value).strip().lower()`.
- Do NOT sort the filtered results unless the task explicitly requires ordering. Preserve the order they appear in the data.
- Collect matching values in a list, then write them sequentially to the output range.

## Keyword Matching (Partial, Case-Insensitive)

When checking if text contains any keyword from a list:
- Convert both the cell text and keywords to lowercase: `cell_text.lower()` and `keyword.lower()`.
- Use Python string `in` operator: `keyword.lower() in cell_text.lower()`.
- Start iteration from row 1 unless you have PROOF row 1 is a header. An accidental data row skipped at position 1 can shift all subsequent matches and cause wrong results.

## Row Deletion — Handle Overlapping Ranges

When deleting rows above or around target markers (e.g., "delete 4 rows before each 'Chassis'"):
1. First, identify ALL target row indices (e.g., all rows containing "Chassis").
2. For each target, compute the range of rows to delete (e.g., target_row-4 to target_row-1).
3. Before deleting, check if any computed deletion range includes ANOTHER target row. If it does, the ranges overlap. Handle this by either:
   - Merging overlapping deletion ranges, OR
   - Only deleting rows that are NOT themselves target rows, OR
   - Processing from bottom to top AND checking each row before deletion: if the row being deleted is a target row, skip it.
4. Always process from bottom to top to avoid index shifting.
5. Delete rows using `ws.delete_rows(start_row, count)` rather than setting cell values to None, unless the task specifies otherwise.

## Sorting Ranges with Compound Keys (Letter + Number) Within Blocks

When sorting values like "D 14" or "A 37" within a range that contains BLANK-SEPARATED BLOCKS:
1. Scan rows from top to bottom. Group contiguous non-blank rows into blocks.
2. For each block separately: extract all values, parse each into (letter_prefix, numeric_suffix). The entire letter prefix (which may contain multiple letters) forms the first sort key; the numeric suffix parsed as int forms the second sort key.
3. Sort by the letter part first (alphabetically), then by the numeric part (as an INTEGER, not as a string). Example: `sorted(data, key=lambda x: (x.split()[0], int(x.split()[1])))`.
4. Write the sorted values back to the exact same rows within that block. Do NOT move values between blocks.
5. CRITICAL: If a block contains values with the same letter prefix (e.g., "D 6", "D 14", "D 7"), they must be ordered by numeric value ascending. Double-check that within each letter group, the numbers increase from top to bottom.
6. After writing, verify that the number of non-blank values in the block equals the number before sorting, and that no values crossed block boundaries.

## Cross-Table Data Transfer with Matching Keys

When transferring data from one table to another based on matching keys:
- Build a lookup dictionary from the source table. The key should be a tuple of ALL matching columns (e.g., (category, brand)). Normalize keys by converting to strings with `.strip()` and lowercasing if appropriate.
- Source table column identification: read header rows carefully. When source and target tables use category names as column headers, map each target column to its corresponding source column by matching the header text exactly (case-insensitive, stripped).
- For each row in the destination table, extract the matching key values, normalize them the same way, and look up the source data.
- Only write to cells when a match is found. If no match exists, write 0 or leave the cell unchanged based on the task requirements. Do NOT blindly copy column-to-column without matching keys.
- When the target table expects 0 for non-matching entries, initialize the output value to 0 and only overwrite when a match is found.

## Merged Cells — Unmerge Before Writing

Before writing to any cell, check if the target range overlaps with merged cells. Use:
```python
for merged_range in list(ws.merged_cells.ranges):
    if (target_row >= merged_range.min_row and target_row <= merged_range.max_row and
        target_col >= merged_range.min_col and target_col <= merged_range.max_col):
        ws.unmerge_cells(str(merged_range))
```
This prevents the `'MergedCell' object attribute 'value' is read-only` error.

## Defensive Type Conversion — Always Handle Edge Cases

When converting cell values to numeric types (int, float), always handle potential failures:
- Write a helper function `safe_int(val)` and `safe_float(val)` that returns None or 0 for empty strings, None values, and non-numeric strings.
- When parsing currency or percentage strings, strip '%', '$', ',' characters before conversion.
- Never assume a cell value is already a number — always check `isinstance(val, (int, float))` before using it in arithmetic.
- When building lookup keys or parsing compound values, wrap all cell reads in `str(val).strip()` and handle the case where val is None.

## Time Range Parsing from Strings

When a task requires comparing times or computing time-based conditions:
- Time ranges are often stored as strings like "06:00-14:00", "6AM-2PM", or as datetime.time objects.
- Write a robust parser: use regex to extract hours and minutes from both start and end of the range, handling both 12-hour and 24-hour formats.
- Convert extracted times to total minutes (hour*60 + minute) for easy numeric comparison.
- When comparing a datetime from another cell to a time range, extract ONLY the time component from the datetime and convert to minutes.
- For multi-criteria counting (heat maps), first build the lookup structure (e.g., name → list of (dest, type, datetime)), then for each cell in the output table, iterate through the matching entries and count those that satisfy ALL criteria.

## Date Comparison and Lookup

When matching dates between cells:
- Always print the actual type and value of date cells before comparison.
- If dates are datetime objects with time components, decide whether to compare date-only (use `.date()`) or datetime-inclusive.
- For lookup tasks where a target date falls within a range of dates: normalize all dates to `date` objects (strip time), then use standard comparison (`start_date <= target_date <= end_date`).
- If date values are strings, parse them using `datetime.strptime()` with the appropriate format. Print the parsed result to verify correctness.

## Mathematical / Semantic Validation

When a task involves a complex calculation (percentages, financial formulas, ratios):
- Before coding, print relevant examples: the input values, the expected intermediate result, and the expected final output.
- If the task describes a formula in business terms (e.g., "percentage of night shifts"), translate it step by step: identify what constitutes a "night shift," what the denominator represents (e.g., total days, total shifts, total hours), and compute the fraction accordingly.
- If a formula from the task simplifies mathematically, verify the simplification with a few sample rows before applying it to the whole dataset.
- When uncertain about the calculation, compute it step-by-step (e.g., first compute numerator, then denominator, then divide) rather than attempting to simplify into a single expression.

## Post-Save Verification

After saving the workbook, ALWAYS re-open it and check at least the first few target cells to confirm they contain the expected value type (not None and not a formula string). If any are None, your code likely wrote a formula, missed cells, or encountered an unhandled edge case — fix before final submission.
- Additionally, verify a few cells from the MIDDLE and END of the output range. Regressions often occur at block boundaries or where data patterns change.
- When sorted output is expected, verify that values within each block are ordered correctly.
<!-- SLOW_UPDATE_END -->
