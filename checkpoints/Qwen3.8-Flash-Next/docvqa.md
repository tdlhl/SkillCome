# DocVQA Skill

## Visual Evidence Discipline
- Read the document carefully before answering.
- Prefer the smallest exact text span that answers the question.
- When several nearby strings look similar, choose the one whose surrounding labels or layout best match the question.

- Use directional or positional cues in the question ('at the top', 'below the logo', 'bottom right corner', 'next to X') to first narrow the visual region, then read the text found there.

- For document-identity questions ('What type of form/document is this?', 'What is the title?'), read the main header or title text at the top of the page first; the document's own title line is the answer—do not paraphrase or reclassify it.

## Exact Answer Discipline
- Copy names, numbers, and dates exactly from the document whenever possible.

- In tables, agendas, or numbered lists, locate the specific row label, Roman numeral, or column header referenced by the question before extracting the paired value.
- For chart or diagram questions, read axis labels, legend entries, and individual bar/line values before comparing or selecting an answer.
- Prefer direct extraction over paraphrase. Preserve the document's exact formatting in compound values (phone/fax numbers, IDs, quoted taglines).
- Before finalizing, compare the answer against nearby alternatives and keep the best-supported exact span.

- When the question asks for the name or brand of a single entity, isolate the proper-noun brand token rather than copying an entire descriptive tagline, slogan, or location phrase that follows it on the same line or adjacent block.

## Span-Boundary Precision
- When a value includes a currency symbol, unit, percentage sign, or other integral prefix/suffix (e.g. '$', '%', '€'), include it in the answer span; do not strip it as mere formatting.
- When a visible label is printed adjacent to a value (e.g., 'Page 2', 'Total: $100', 'Date: 03/15/2024') and the question already names the value type ('What is the page number?'), return only the value portion—do not prepend or append the field label.
- After extracting, verify the span is neither shorter than the visible token-group that constitutes the complete answer nor wider by including adjacent label text or content from adjacent structural sub-lines. In structured form fields (addresses, multi-component identifiers), each visual line or labelled sub-field is a separate component; when the question targets one component (e.g., 'street address' or 'city/state/ZIP line'), return only that line—do not concatenate the entire multi-line block.

## Table Cross-Row / Multi-Item Matching
- For questions that reference multiple items (e.g., 'Actions #1 and 2', 'rows X and Y', 'both columns'), scan each referenced row or column, identify the value that appears in all of them, and return that common value exactly as printed.
- If the matched value is an abbreviation or initials in the document (e.g., 'DW', 'R. Smith'), reproduce it verbatim; do not expand or reformat it.

- When a field value is printed together with a parenthetical qualifier that forms part of the complete category designation (e.g., 'Public Information Issue (Transferred)', 'Form A (Revised)'), include the parenthetical text in the answer span; stripping it yields an incomplete, incorrect answer.
