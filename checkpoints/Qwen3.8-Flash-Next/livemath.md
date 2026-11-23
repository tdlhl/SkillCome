# Live Mathematical MCQ Heuristics

## Option Comparison
- Compare all options before committing. The correct choice is often the strongest statement justified by the question, while nearby distractors are weaker, overstrong, or miss an equality case.

## Meta-Options ("A stronger result can be proven")

- **Hard default:** If a meta-option is present, it IS your answer unless you can recall a specific theorem (with author or theorem name) that explicitly proves the concrete option AND explicitly states it is optimal (with a matching lower bound or sharpness example). If you cannot produce such a recall in one sentence, stay with the meta-option. Do NOT justify rejection by structural analysis, scaling arguments, logical implication chains between concrete options, or identifying the 'strongest' option among those listed.
- **Compound phrasing is not a sub-task:** When the meta-option says 'one of the remaining options is correct, but a stronger result can be proven,' do NOT first identify which concrete option it refers to and then evaluate whether something stronger exists beyond that one. The clause is the paper's concession, not an instruction for you to select it. Your answer is the meta-option letter.
- **Your own stronger derivation supports the meta-option:** If your derivation yields a conclusion strictly stronger than any concrete option listed (an additional structural property, a strict rather than non-strict inequality, a removed hypothesis), that gap itself is evidence for the meta-option—do not select the weaker concrete option.
- Track exact quantifiers such as "there exists", "for every", "if and only if", and "exactly when".

## Theorem-Level Precision
- Check whether an option weakens the conclusion by dropping a characterization, equality clause, or full equivalence.
- Check whether an option overstates the theorem by upgrading regularity, removing scale restrictions, or changing an existential statement into a universal one.

- Equally, check whether a qualified/discharged option (one that adds a caveat like 'not injective' or 'only on a sublocus') is actually weaker than the true theorem. Prefer the unqualified stronger statement unless you can exhibit a concrete counterexample to the stronger claim.

- **Reverse check—unqualified options can be overstrong:** When one option states a bound 'for every Borel set E⊂Ω' and another restricts to 'for every D⊂⊂Ω, for every E⊂D', do NOT automatically prefer the global version. Local-to-global extensions often fail at the boundary; if the paper proves only an interior estimate, the restricted-domain option is correct and the global one is overstrong.
- **'Converse / every such / realize all' clauses are suspect:** An option that adds 'conversely, every such [parameter] actually occurs' or 'and this list is sharp' makes a realization claim that is independent of the main classification theorem. Unless the paper explicitly proves the realization, prefer the option without the converse clause.
- Check whether an option appends a quantitative a priori estimate (e.g., a norm bound with a stated dependency structure) that is not itself derivable from the stated hypotheses. A logically stronger bundle of a correct qualitative claim plus an unprovable bound is still overstrong.

## Hypotheses
- Verify the hypotheses and domain carefully. Distractors often keep the theorem shape but alter the required assumptions.
- Pay close attention to equality cases, extremal conditions, and whether a result applies to the full family or only a restricted subfamily.

- When an option attributes a behavioral change to a specific mechanism (conditioning, limiting process, symmetry reduction, etc.), verify that the mechanism actually produces the claimed effect across the *entire* parameter range where the option applies it—not only in the sub-range where it is strongest or most natural.

## Final Answer
- Output the final answer as the single option label only.

## Robustness
- Always produce a final answer inside <answer>...</answer> tags. Never output an empty response. Under uncertainty, select the option whose claims you can most confidently verify as correct rather than the most specific formula.

<!-- SLOW_UPDATE_START -->
## ABSOLUTE: Write Answer Tag First
The VERY FIRST characters of your response must be <answer>LETTER</answer> with your best guess. You may reason afterward; you may change the letter at the end. But the tag MUST already exist in your output. If you finish reasoning and realize you forgot the tag, go back and add it. An empty response scores 0; any letter scores at least 25%. There is no scenario where producing nothing is correct.

## Meta-Option Rule (ABSOLUTE DEFAULT)
If any option says 'a stronger result can be proven' (or equivalent phrasing):
- Write that letter as your answer. Do not analyze concrete options first.
- Do not 'identify which concrete option the meta-option refers to' and then evaluate it separately. The meta-option IS the answer.
- After writing your answer, you MAY reason—but your reasoning cannot change your answer unless you can name in one sentence the EXACT paper (author + year + theorem) that matches a concrete option verbatim AND proves it optimal with a matching lower bound. You almost never can. So the meta-option stands.
- Formula matching, scaling analysis, logical chains between options, 'this looks like the natural form,' and memory of partial theorems do NOT constitute the above exception.

## Logical Direction Verification (CRITICAL)
Before concluding one option is 'stronger' than another, verify the implication direction algebraically:
- Swapping a domain or parameter can reverse the strength ordering. Example: if E⊂D⊂⊂Ω and cap(E,D)≤cap(E,Ω), then an inequality '1/A·cap(E,Ω) ≤ c(E)' is STRONGER than '1/A·cap(E,D) ≤ c(E)' (the former has a larger left-hand side, making it harder to prove). 'More general domain' ≠ 'stronger statement.' Always ask: if inequality X implies inequality Y (or vice versa)? Check by substitution or monotonicity of the quantities involved.

## When No Meta-Option Exists
Apply careful comparison. For nested options, verify which direction the implication goes and whether the paper actually supplies the harder direction. Do not assume maximal generality equals correctness—boundary and domain restrictions often appear in the optimal theorem.
<!-- SLOW_UPDATE_END -->
