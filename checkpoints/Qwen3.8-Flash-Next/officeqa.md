# OfficeQA Skill

## Retrieval Discipline
- Start by narrowing to the most likely candidate file before reading long passages.
- Prefer targeted search terms that name the exact entity, period, measure, or table concept from the question.
- After a promising match, read only a small surrounding span and verify it matches the requested year, basis, and unit.

## Evidence Discipline
- Extract the exact value from the retrieved text before doing any arithmetic.
- Keep track of each operand's period, unit, and semantic role so nearby proxy values are not mixed in.
- If the question asks for a transformed or derived quantity, compute only after confirming every operand.

## Final Answer Discipline
- Return the final answer only after one last consistency check against the retrieved evidence.
- Copy the final answer from a checked value, not from an unverified intermediate guess.
- Inside <answer>…</answer>, place ONLY the bare final value token (a number, a short date, or a brief noun phrase). Do NOT include unit labels (e.g. 'percentage points', 'dollars', 'million'), currency symbols ($, INR), sign prefixes (e.g. '+', '≈'), parenthetical derivations or rephrasings, source/table references, markdown formatting, or any explanatory prose within the tags. If the question says 'as a decimal' or gives a rounding instruction, supply just the rounded token (e.g. 0.068), nothing more.
- Match delimiters and punctuation exactly to the question's example: if it requests square brackets, emit them (e.g. [254689000]); if it shows a comma in the format (e.g. 57,615.04), keep the comma; if it says 'nearest whole number', emit digits only with no commas.
- All reasoning, evidence citations, and unit explanations belong outside the <answer> tags.

- **Minus-sign glyph**: Inside <answer>, use the Unicode MINUS SIGN U+2212 (−) for negative numbers—never the ASCII hyphen '-' or en-dash '–'. Copy the sign from the question's format example when one is shown.

- **Percent-sign glyph**: Emit a literal '%' inside <answer> ONLY when the question supplies an explicit format template that contains '%' (e.g., 'round to 27.67%' or a displayed example like '27.67%'). Phrases such as 'express as a percentage', 'as a percentage, rounded to …', or 'in percent' are scale instructions (multiply the ratio by 100); they do NOT add a display suffix. When no format template is given, strip '%', unit labels, currency symbols, and sign prefixes from <answer> tags.

## Arithmetic Precision Discipline
- For sample standard deviation, divide by (n−1); for population, divide by n. Confirm which the question asks.
- When a multi-step calculation requires pulling operands from two or more separate files or tables, collect ALL operands before performing any arithmetic, and double-check that each operand's row, column, and unit label match the question's specification.
- If two or more steps of arithmetic are involved, re-derive the final number from the raw operands rather than carrying a rounded intermediate forward.

## Step-Budget Discipline
- When approaching the last few tool calls, stop searching and commit your best answer inside <answer>…</answer> even if the evidence is not 100 % verified. An imperfect answer beats no answer.

## Wide-Table Navigation
- For tables with many rows or columns, issue **two or more** grep calls (e.g., one for the row entity, one for the table/concept keyword) to triangulate the exact region.
- Before extracting data rows, read the **header span** (column labels) to map each month/period to its column index, then pull values only from those indices.
- Avoid guessing column positions from grep snippets alone; confirm with a small `read` that includes the header row.

## Multi-File Series Assembly
- When the requested time span exceeds one file's coverage, grep **each** candidate file for the same table identifier (e.g., "FFO-3", "SN-1") and stitch the complete series before computing.
- Record which file supplied which year so the operand ledger is auditable.
- If the same year appears in more than one file, treat agreement as corroboration; flag any mismatch.

<!-- SLOW_UPDATE_START -->
CRITICAL — MINUS SIGN OVERRIDE: Inside <answer>, ALWAYS use ASCII hyphen-minus (U+002D '-'). Do NOT use Unicode minus (U+2212 '−') or en-dash. Any '−' encountered in retrieved text must be normalized to '-' before placing in the answer tag.

TRAILING-ZERO RULE: 'Rounded to the nearest hundredths place' means EXACTLY 2 digits after the decimal point at all times (7.60 not 7.6). 'Nearest thousandths' means exactly 3. 'Nearest whole number' means zero decimal digits. When a format example is shown, mirror its digit count exactly.

PERCENT-SIGN RULE: Include '%' inside <answer> when the question says 'what percentage' or 'express as a percentage/percent' or the format example contains '%'. Omit '%' for pure ratios, decimals, or when no percent language appears.

FULL-PRECISION ARITHMETIC: For multi-step calculations (std-dev, regression, CAGR, skewness), carry unrounded values through EVERY intermediate step. Round ONLY the single final number. For sample std-dev compute s = sqrt((n·Σx² − (Σx)²) / (n·(n−1))) as one expression; never round the mean or partial sums to fewer decimals mid-calculation.

REPORTING-STRUCTURE VERIFICATION: When a question specifies 'using the reporting structure in which X charged to Y', find the footnote/column header that explicitly defines the combined column. Cross-check the target year's value in BOTH candidate files. Prefer the file whose table structure names the combined basis. Do NOT manually sum split columns unless the footnote says to.

Gini: G = |x1−x2|/(x1+x2). Never ×2.
Arc elasticity: (V1−V0)/((V1+V0)/2). No logs, no /n.
Paths: Use Candidate File paths verbatim; never rewrite.
Grep retry on no-match: (a) shorter substring, (b) table ID (MY-2, FFO-3), (c) header keyword; 3 tries minimum.
Units: Read parenthetical label; convert BEFORE rounding (thousands→×1000).
NEVER emit empty <answer>. At tool limit, commit best number.
Image-only charts: state that the source table (e.g. FO-1, IFS-1) provides the data; extract from that table rather than guessing from narrative prose.
MONTHLY SERIES: For 'all months in YEAR' verify 12 values via header column mapping; cross-check sum against annual-total column.
<!-- SLOW_UPDATE_END -->
