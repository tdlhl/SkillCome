# Question Answering Skill

(No learned rules yet. Rules will be added through the reflection process.)

## Answer Form and Entity Selection
- **Entity completeness vs. shortest convention:** Include modifiers only when they are NECESSARY to identify a smaller entity from a group (e.g., "Skywalker Ranch" vs. a generic ranch, "White Building"). However, when the entity has a universally recognized SHORT form, use that short form rather than elaborating: "Yale" not "Yale University", "Washington" not "George Washington" (when no other Washington is in play), just "unbearable" when the question asks for the single Kundera title word meaning 'intolerable'. Prefer the minimal string a native speaker would use in casual identification.
- **Single name form:** When context gives several co-located names for one entity (common name alongside a scientific or formal name), answer with the **single** name form that most naturally satisfies the question. Do NOT concatenate or parenthesise alternate names (write "adder", not "adder (Vipera berus)"). Prefer the simplest complete string.
- **No source artifacts or question-form:** Do NOT prefix answers in Jeopardy-question form ("What is…?"). Do not transplant quiz-show phrasing, repeat the question, or include unnecessary explanation inside the answer.
- **Granularity and grammatical-form match:** Match the specificity and grammatical form implied by the question. (a) **Number agreement:** if the question's syntax implies a plural ("this weapon" after describing multiple instances, or gold answers conventionally plural), use plural; do not silently switch to singular. (b) **Fill-in-the-blank minimalism:** when the question sentence already contains a head noun and asks you to supply only the distinguishing modifier (e.g., "not the X-nature but this ___ of light"), return just the modifier ("wave"), not the context bigram that redundantly repeats the head noun ("wave nature"). (c) **Article and determiner presence:** include a leading article ("a no-show") when the expected answer conventionally carries one.
- **Direct extraction (proper nouns & distinctive content):** When the answer is a proper name, title, or passage-specific term, copy it verbatim or trim to the essential noun phrase rather than paraphrasing. Rephrase only when the question demands a different form.
- **Common-noun synonym preference:** When the answer is a plain common noun (not a proper name), and the source uses a formal or literary synonym (e.g., "automobile" instead of "cars", "residence" instead of "home"), prefer the simplest, most everyday word that conveys the same meaning. Verbatim fidelity applies to proper nouns and distinctive/technical vocabulary; register-matching to everyday language applies to common nouns.

- **General-over-specific category choice:** When a drug/animal/process has both a broad umbrella term and a narrower technical sub-type (e.g., "steroids" vs. "corticosteroids," "fish" vs. "teleosts"), and the question does NOT explicitly request the sub-type, answer with the general/umbrella term that a layperson would use. When the broad term appears as a page/section heading (the referent of a clue's demonstrative "these/those") and the narrower term appears only in body text, the heading term is almost certainly the expected granularity. Only select the sub-type when the question's phrasing uniquely points to that level of specificity.
- **Crossword-clue "as in" parsing:** In clue-style questions of the form *"[definition/definition-word], as in [named work/title/document]"*, the named source is a **locator**, not the answer. The question asks for the specific word or short token **inside** that source satisfying the given definition. Output that token, not the full source title. E.g., *"Sadness, as in a Tennyson poem"* → the emotion word from the poem, not the poem's title.

- **Synonym-register match:** When context offers several near-synonyms (e.g., "automobile", "car", "vehicle"), choose the one whose register matches the question's phrasing. A casual Jeopardy clue ("making these") calls for the colloquial word ("cars"), not the formal/technical term lifted verbatim from a Wikipedia passage ("automobiles").

When the question is itself a description/clue (e.g., '2007: George Clooney is the fixer for a law firm', 'This band reunited to play Don\'t Stop'), the answer is simply the proper name/title being described. Output only that name—no title prefixes like 'The film is' or 'The band called'.

- **Jeopardy clue granularity:** A brief clue like "Akron, Ohio" or "Intolerable, as in a Milan Kundera title" is NOT asking you to describe or expand the clue entity. Parse what attribute is being requested (time zone, the single title-word, a category match) and output only that attribute. If the clue names a place, the expected answer may be an associated property (time zone, zip code, area code), not a description of the place itself. Match the expected answer TYPE: if the clue is a one-word synonym prompt, the answer is one word, not the full title or an essay definition.

## Multi-Constraint Verification
- When a question lists several qualifying attributes (category, date, age, title, count, location), write down **every** constraint before searching for an answer.
- After retrieving a candidate, explicitly check it against **each** constraint. A single mismatch (e.g., wrong age, wrong date) disqualifies the candidate—do NOT commit on a partial match.
- If the first retrieved candidate fails any constraint, continue searching within the same category for an entity that satisfies all of them before falling back to other categories or your own knowledge.
- If no candidate in the provided context satisfies all constraints, reason carefully from your own knowledge rather than forcing the closest context hit.

## Referent Resolution and Context Anchoring
- **Resolve the referent first:** Before scanning the context, identify what the question is actually asking for. Questions often use indirect references ("this play", "this creature", "he", "she") to point at an entity described only by attributes. Restate the question as "Which single entity is uniquely described by [attribute 1], [attribute 2], [attribute 3]?" then search for the passage satisfying ALL attributes simultaneously.
- **Do not anchor on the most prominent entity in context:** Provided context may discuss a similar-but-different topic sharing surface keywords. Focus on the specific clue constraints; an answer matching those constraints factually is correct even if context emphasises a different entity.
- **Cross-check the full combination:** Would an expert confirm this exact cluster of clues points to your answer? If any detail is mentioned in multiple passages, the correct passage is where the FULL attribute set co-occurs.

- **Corroboration across documents = commit signal:** If three or more independent passages in the context all name the same entity, treat that as sufficient evidence and commit. Do not keep searching for a 'better' candidate when the documents already agree.
- **Associative and thematic clues:** Questions may use wordplay, article titles, or family/professional connections (e.g., 'psychoanalyze' pointing to the Freud family, 'fixer' as a character archetype) rather than literal keywords. Recognize these thematic hints as valid constraints alongside explicit ones.

## Multi-Hop Linking
If the answer requires connecting two or more facts from different documents (e.g., person A is X's daughter AND person X is Y's sister → person A is Y's sister), explicitly chain those relationships in your reasoning before committing to an answer. Do not stop at the first document that partially matches.

## Explicit Answer-Option Constraints
- When the question enumerates a closed set of candidate answers ("Of X, Y, or Z…", "which one…"), treat that list as a **hard constraint** and as the *only* permissible answer vocabulary: select the option that is closest to factually correct from the listed choices rather than substituting a different value retrieved from context. The question's grammar overrides verbatim extraction.
- If context gives a number or string outside the listed options, do NOT report it; map it to the nearest listed option or, if the listed options are the only valid answers, pick the one that matches the best-supported reading of the source.

- **Strip genus/descriptive words from proper names:** When a place or entity is commonly referred to by its short name with an implicit or redundant classifier, drop the classifier: "Roanoke" not "Roanoke Colony" (the context already identifies it), "Cosmos" not "New York Cosmos"—especially when the qualifier ("New York City soccer team") is already in the question itself. Treat context titles that contain this same generic word as decomposable: the proper-name token is the answer; the generic token is already consumed by the question surface.
  - **Question-redundancy filter:** Before finalizing, check whether part of your candidate answer is just echoing words the question already supplied (e.g., question says "this New York City soccer team" → answer "Cosmos", not "New York Cosmos"). Add a conventional article ("the Cosmos") only if the bare name is normally preceded by one in everyday usage.

## Spelling Normalization (American English default)
- QA reference answers conventionally use **American English** spelling. When the retrieved source or context uses British/Commonwealth variants ("haemophilia", "colour", "centre", "theatre", "programme"), normalize to the American equivalent ("hemophilia", "color", "center", "theater", "program") before submitting—**unless the question itself uses the British form**. Scan all retrieved documents for the American variant before committing; do not let the higher-frequency British spelling in context override this default. This applies to the final answer string, not to your reasoning.

- **Ignore noise and stage-direction artifacts in the question:** Scraped Jeopardy/trivia text often contains TTS repetitions ("Ah-Ah-Ambrosia", "ah-ah-abundance") or parenthetical scene-setting ("(Sarah of the Clue Crew shows a press at…)"). Strip these before parsing: focus on the semantic content (the entity described, the attribute requested). The parenthetical is context, not the question. Do not let garbled tokens alter your reading of the genus, technique, or attribute being asked about.

<!-- SLOW_UPDATE_START -->
## Critical Parsing Rules (Priority Over All Other Heuristics)

### Literal string-wordplay takes absolute precedence
- When a clue uses spatial/containment language—"X is INSIDE Y," "there's a lot of X inside Y," "X is hidden in Y," "X within Y"—this is a LITERAL EMBEDDING puzzle on the string of Y, NOT a thematic or contextual association. Decompose candidate entity names letter-by-letter. Example: "love inside country bordering Croatia" → check each bordering country's spelling: S-LOVE-NIA. The answer is determined by the string match, not by which country appears most in the context documents. Ignore context-topic co-occurrence when a verifiable string puzzle has a unique solution.
- Similarly, "X of Y" or "X in Y's name" clues require checking the literal characters of Y. Do NOT substitute a thematically related entity from context.

### Jeopardy "YEAR: PERSON" format = named entity, NOT event/role/employer
- When the clue format is "YEAR: PERSON_NAME" (or "PERSON_NAME, YEAR"), the expected answer is almost always the TV SHOW, FILM, BAND, or ORGANIZATION the person is most associated with—NOT their employer (HBO), NOT their award, NOT their role. Think: "What entity is this person the star/member of?" The category header (if visible) overrides everything; if invisible, default to the person's primary named project, not secondary facts. Do NOT pad the answer with the award name or episode details—just name the entity.

### Descriptive historical-figure clues: match the formal-name convention
- When a clue describes a historical figure through multiple biographical incidents (battles, injuries, dates), the gold answer often expects the FULL formal name including title or epithet: "Admiral Horatio Nelson" or "Richard the Lionheart" rather than just "Richard I" or "Nelson." This applies to figures whose TITLES or EPITHETS are part of their canonical Jeopardy answer string. Mirror the convention in the context document headings. When in doubt, err toward "Title/Nickname Firstname Lastname" for military/royal/nobility figures described by deeds; use bare surname for pop-culture figures.

### Proper-name full designation vs. shortened generic
- When an entity's official/formal proper name differs from its colloquial short form AND the short form is ambiguous (could refer to multiple distinct entities), output the FULL official name. Example: the 1999 Australian referendum asked voters to leave "the British Commonwealth" (not just "the Commonwealth," which is ambiguous with Massachusetts, CIS, etc.). The rule here is: if a qualifier like "British," "United," or "Free" is part of the conventional proper name of an ORGANIZATION/NATION/COLLECTIVE, include it. This does NOT contradict 'entity completeness vs. shortest convention'—that rule applies when the short form is unambiguous ("Yale" always means the university). This rule applies when the short form could match multiple distinct real-world entities.

### Poetic/literary metonymy: place names encoding intoform names
- When a clue about a poet mentions their "style," "form," or how they "styled/named" their work, consider that the expected answer may be the GEOGRAPHIC PLACE associated with their poetic school rather than the form's technical name. Example: Sappho → "Sapphic stanza" is also called the "Lesbian" form because Sappho was from Lesbos. If the gold answer is a place, output the place name ("Lesbos"), not the adjective ("Sapphic," "Aeolic," "Lesbian"). Scan context for island/city/country references to the poet's origin.

### Year-specific factual claims: verify against context, not general pattern
- When a clue states "In YEAR [someone/something had a specific record/achievement]," do NOT substitute a famous entity who 'usually' fits that pattern from general knowledge. Check the retrieved context documents for entities explicitly linked to that YEAR. Example: "In 2004 he had 4 novels on the bestseller lists"—do not default to Tom Clancy (who had this reputation in the 1990s); check context for which author's multiple titles are documented for 2004 specifically. The year is a hard constraint that must be verified, not approximated by a well-known stereotype.

## Retained Proven Rules (Do NOT Override)
- General-over-specific: When a page/section heading uses an umbrella term ("steroids") and body text uses a narrower sub-type ("corticosteroids"), and the question's "these/those" demonstrative points to the heading, use the umbrella term.
- Question-redundancy filter: If the question surface already contains a generic/classifier word ("colony," "city," "team," "feast"), strip it from your answer. "Roanoke" not "Roanoke Colony." "Belshazzar" not "Belshazzar's Feast" when the question says "at his feast." The entity being pointed to by the possessive pronoun IS the answer.
- Context association is NEVER a substitute for constraint verification. A country/person appearing prominently in retrieved documents does not make it the answer unless it satisfies ALL stated constraints.
- Epithet/nickname retention for historical figures described by deeds (Richard the Lionheart, not Richard I).

## Anti-Patterns to Actively Guard Against
- Do NOT answer a wordplay/pattern clue with a context-associated entity that merely shares a keyword or topic. The wordplay clue's logic is self-contained; solve it on the string/attribute level before considering context.
- Do NOT over-elaborate Jeopardy answers. If the expected answer is "The Sopranos," do NOT output "the Emmy for Outstanding Lead Actor in a Drama Series for The Sopranos." Give the entity name, period.
- Do NOT let a context document mentioning Entity B in connection with Entity A redirect you away from a clean logical answer pointing to Entity A.
- Do NOT replace a year-specific answer with a stereotype-famous pattern. "Who usually has multiple books on lists?" is not the same as "Who did this in 2004?"
- Do NOT choose a shortened form when it creates ambiguity with other real-world entities. "the British Commonwealth" ≠ "the Commonwealth" in a referendum context.
<!-- SLOW_UPDATE_END -->
