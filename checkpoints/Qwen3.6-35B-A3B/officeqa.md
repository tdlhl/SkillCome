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

## Table Verification
- Before extracting any value, read the full table title, column headers, and any footnotes to confirm it matches the exact entity, source, and period requested.
- If multiple similar tables exist (e.g., by series, by nationality, bank vs. nonbank claims), explicitly select the one the question names.
- After extracting, list each operand with its column label, unit, and period; verify they are the exact requested quantities before computing.

## Arithmetic Integrity
- After the final rounding, compare the rounded value against a quick sanity check (order-of-magnitude, sign).

## Retrieval Workflow
- **Single-target grep**: Execute a `grep` with the most specific entity phrase from the question (e.g., 'liquid fuel other than gasoline', 'seigniorage on coins') before any `read`.
- **Minimal span read**: After grep, `read` only the 15-30 lines around the match.
- **Full operand extraction**: List every required operand and its metadata (year, unit) from the verified span; never mix in adjacent rows unless they share the exact same label and period.
- **Compute after confirmed set**: Perform arithmetic only after the full operand set is checked; then copy the computed result into the final answer.

- When the question asks for a change in percentage points, compute each percentage separately from the original numerator and denominator before subtracting. Do not attempt a one-step shortcut.

## Format Verification (STRENGTHENED)
- Immediately after computing the final value, reread the question's formatting instructions word-for-word. Check for: percent sign required or prohibited, bracketed lists, decimal-place specifications, commas vs. plain numbers, leading zeros.
- If the question says 'only report the value with no percent sign' or 'express the result as a percentage, rounded to two decimal places' without a '%' symbol, strip the symbol. If it says 'enclosed square brackets with comma-separated values', output exactly `[a, b]`.
- For logarithmic or exponential computations, retain at least 8 significant digits through intermediate log/division steps. Perform the final rounding only once, then re-calculate with one extra digit to confirm the last decimal place is correct.

<!-- SLOW_UPDATE_START -->
## PRECISION AND ROUNDING — DO NOT REGRESS (CRITICAL)
- For CAGR calculations: compute (End/Start)^(1/n) using at least 8 decimal places for the ratio before exponentiation. Then subtract 1. Only round at the very end.
- For arc elasticity: compute the numerator and denominator with full precision separately, divide with full precision, only then round to 3 decimal places.
- After computing ANY multi-step value to the requested decimal places, IMMEDIATELY recalculate it using one additional decimal place of intermediate precision and confirm the last digit is correct. If the recalculated value rounds to a different last digit, use the more precise value.
- When a question asks for multiple values (e.g., CAGR, decay factor, arc elasticity), compute ALL of them before rounding ANY of them. Never use a rounded intermediate value (like a rounded CAGR) to compute a derived value (like decay factor). Decay factor = 1 + CAGR (using unrounded CAGR), then round the decay factor independently.
- For quarter-over-quarter or month-over-month percent changes in large numbers (like ESF total assets), carry the division to at least 8 decimal places before multiplying by 100. Then round to the requested precision.

## FORMAT VERIFICATION — CHECK BEFORE EVERY SUBMISSION
- Before calling the answer tool, reread the question's EXACT formatting instructions word-for-word.
- If the question says 'report your answer as a percent' or 'round to the nearest whole number' and the context implies a percentage, INCLUDE the percent sign in your answer (e.g., '69%' not '69').
- If the question says 'return in enclosed brackets as comma-separated values', output exactly '[a, b, c]' — including the brackets.
- If the question says 'only report the value with no percent sign', strip the % symbol.
- If the question says 'report your answer in millions of dollars', do NOT include units or commas — just the number.
- Check for: decimal places, percent signs, brackets, commas, word separators, value ordering, leading zeros.
- Write your answer out in your reasoning exactly as it will appear: 'My final answer will be: [X, Y, Z]' and visually check it against the format instructions.

## ANTI-SILENCE PROTOCOL — NEVER EXIT WITHOUT SUBMITTING
- If you have extracted ANY relevant data and performed ANY computation, you MUST call the answer tool with your best result. A partial, uncertain, or approximately-correct answer is ALWAYS better than no answer.
- If you have been searching for more than 15 steps without clearly identifying the right value, STOP and produce your best estimate based on what you have found, with a brief note.
- If you find data but are unsure about which table version or reporting basis to use: PICK ONE explicitly (state your assumption), compute with it, and submit the result. Do not loop trying to find confirmation.
- If you catch yourself thinking 'I need to verify this one more time' — you don't. Submit now.
- SET A MENTAL LIMIT: after 10 read/grep operations total, you must have enough data to produce an answer. If not, submit your best partial result.

## CHART AND GRAPH DATA EXTRACTION (UPDATED)
- When a question references a chart, graph, or visual figure, locate the data table that UNDERLIES that chart. Charts in Treasury Bulletins are typically accompanied by nearby tables with the actual numerical values.
- If the chart text mentions specific data points (e.g., axis labels, legend entries), grep for those exact numbers or labels to find the source table.
- For questions about 'local maxima' or 'peaks' in line plots: you must find the underlying numerical data series. Scan broadly (100+ lines) around the chart location for any table with time series data. Count peaks from the NUMBERS, not from ASCII art or text descriptions of the chart.
- If you cannot find the underlying data table for a chart, GREP for the chart title or nearby section headers to locate any related data.
- If no numerical data table exists in the file for the chart, state 'Cannot determine: chart data not available in numerical form' and submit that as your answer — do not guess based on visual text descriptions.

## CORRECT TABLE AND BULLETIN SELECTION
- When a question references data from a specific historical period and asks for a specific reporting structure (e.g., 'using the reporting structure in which Air Force expenditures were still charged to Army appropriations'), identify WHICH bulletin from your candidate files dates to the period when that reporting structure was in effect. The bulletin CLOSEST in time to the reporting change is usually the correct one.
- Read the footnotes of candidate tables carefully. If one bulletin's footnote says 'Department of the Army expenditures, including expenditures for the Department of the Air Force' and another bulletin separates them, USE THE ONE WITH THE COMBINED REPORTING that matches the question's explicit instruction.
- When multiple bulletins cover overlapping years, prefer the bulletin that was published closest to the end of the period in question (it will have the most final/revised figures).

## COMPLEX MULTI-FILE TASKS — COMPLETION CHECKLIST
- For tasks requiring data from multiple files:
  1. Write out the complete list of required data points (e.g., 'FY1945 surplus, FY1946 surplus, ..., FY1959 surplus').
  2. As you extract each data point, CHECK IT OFF your list.
  3. If you cannot find a specific data point after 3 search attempts, mark it as 'UNAVAILABLE' and proceed. Do not let one missing value block the entire computation.
  4. If you have at least 70% of the required values, compute with what you have and note which values are missing or estimated.
  5. For standard deviation, skewness, or other statistical calculations: if you are missing 1-2 values out of 15+, estimate them by interpolating from adjacent years and note this in your reasoning.

## EXCHANGE RATE AND CONVERSION TASKS
- If a question requires an exchange rate (e.g., USD to INR) that is not in the candidate files, search the candidate files for any table with foreign exchange rates, currency values, or 'foreign currency' references. Look in sections labeled 'International Financial Statistics' or 'Foreign Currency'.
- If the exchange rate is truly not available, output the dollar amount with a note: 'USD [value]; exchange rate to INR not available in provided files.' Do NOT abandon the trajectory — submit the partial result.

## SUMMARY: YOUR MANDATORY EXIT CHECKLIST
Before you end ANY trajectory, confirm:
- [ ] I have called the answer tool (or will call it in the next step).
- [ ] My answer matches ALL format requirements from the question (%, brackets, decimals, commas, word order).
- [ ] I recalculated multi-step values with extra precision and confirmed the last decimal place.
- [ ] If I could not find all data, I still submitted my best partial result with a note.
If you cannot check all four boxes, FIX THE PROBLEM and then submit.
<!-- SLOW_UPDATE_END -->
