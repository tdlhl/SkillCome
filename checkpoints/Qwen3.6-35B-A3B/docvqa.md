# DocVQA Skill

## Visual Evidence Discipline
- Read the document carefully before answering.
- Prefer the smallest exact text span that answers the question.
- When several nearby strings look similar, choose the one whose surrounding labels or layout best match the question.

## Exact Answer Discipline

- Never add or remove currency symbols, percent signs, measurement units, or other formatting characters that are part of the document's text.
- When the question asks for a number (page, amount, count, total), extract only the digits with any attached symbol or unit exactly as shown — do not prepend labels like "Page", "Item", or "No.".
- Copy names, numbers, and dates exactly from the document whenever possible.
- Prefer direct extraction over paraphrase.
- Before finalizing, compare the answer against nearby alternatives and keep the best-supported exact span.

- When extracting a text span directly from the document, preserve its exact casing—do not normalize to sentence case, lower case, or upper case unless the question explicitly asks for a transformation.

- Preserve straight quotation marks (" and ') exactly as they appear in the document. Do not replace them with typographic/curly quotes (“ ” ‘ ’) or change single quotes to double quotes or vice versa.
