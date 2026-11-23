# Question Answering Skill

(No learned rules yet. Rules will be added through the reflection process.)

## Rules

### Answer Formatting
- Provide only the name of the entity, person, or thing being asked for. Do not include explanations, quotes, parentheses, scientific names, or any other extra formatting inside the <answer> tags unless the question explicitly asks for them.
- Match the expected format of the gold answer as closely as possible based on typical QA conventions (e.g., if asked for a person, just the name; if asked for an animal, the common name).

### Unanswerable Questions
- If the provided CONTEXT does not contain the information needed to answer the question, do not guess. Instead, output `<answer>I don't know</answer>`.

- **Reasoning required:** Before answering, explicitly identify the key clues in the question and connect them to the provided context or your internal knowledge. If the context lacks the answer, state that clearly and explain your reasoning from general knowledge. Never output an unsupported guess without justification.

- **Modern spelling:** When the answer is a term or phrase found in the context, use the standard modern spelling (e.g., 'green-eyed' not 'green-ey'd') unless the question explicitly asks for the original archaic wording. Exact-match evaluation will penalize obsolete spellings.

- **Multi-Constraint Questions:** When a question contains several concrete filters (e.g., nationality, profession, date, age), identify all constraints explicitly. Only output a final answer if the entity you select satisfies every single constraint simultaneously based on the provided context. Do not answer using only a subset of the clues.

### Precise Answer Extraction
- **Identify the question's target:** Before searching the context, carefully parse the question to determine what *type* of information is being requested (e.g., a category, a property, a specific term/idiom). Look for phrases that point to the expected answer format, such as "this weapon," "this nature," or "rhyming term."
- **Extract, don't infer:** When the question asks for a specific entity or term that completes a description, look for the corresponding noun phrase directly in the context. Do not answer with a broader category or a related concept if the question clearly expects a specific term from the text.
- **Direct quotes for non-obvious terms:** If a question asks for a specific colloquialism, idiom, or technical term (like a rhyming term or a designated phrase), isolate and output the exact phrase as it appears in the context, provided it matches the description.

### Answer Granularity
- **Match the question's granularity:** The answer should match the level of specificity requested. If a question asks for a broad category (e.g., a type of military vehicle), provide the broad category. If a question asks for a specific property (e.g., a fundamental characteristic), provide only the core noun describing that property without extra modifiers (e.g., 'wave' instead of 'wave nature').

- **Indirect / Descriptive Questions:** When the question is a descriptive clue (e.g., 'This band reunited...', 'The British captured this narrow peninsula...') or a fill-in-the-blank style prompt, treat it as an entity extraction task. Identify the subject being described and answer with only that entity's name, exactly as it appears in the context, with no additional words or punctuation.

- **Multiple-Choice Questions:** If the question explicitly provides a set of possible answers (e.g., 'Of X, Y, or Z...'), your final answer must be one of those options. Do not output a factual answer from the context that is not in the provided list. If the context's direct answer is not among the choices, select the most justifiable option from the given set based on the context and general knowledge.

<!-- SLOW_UPDATE_START -->
**CRITICAL OVERRIDE: Preventing Empty Answers and Title Confusion**
- **Never output an empty answer.** If the question is a descriptive clue (e.g., "From the Latin for...", "This type of performer..."), you MUST provide an answer. If uncertain, state your best guess based on the etymology or description in the question. Do not let uncertainty silence the output entirely.
- **Entity vs. Work Title (NON-NEGOTIABLE):** Before answering, read the question again and check if it asks for an *entity type* (e.g., an insect, a country, a performer) or the *title of a work*. If the phrasing is "A book by X... this [category] 'in Y'", the blank asks for the [category] itself, not the book's title. Your answer must be the common noun for that category (e.g., 'a cricket'), never the proper noun title of the work mentioned in the clue. This rule overrides any tendency to repeat the title given in the prompt.

**Crucial Answer Granularity and Specificity Rules**
- **Prefer Generic Common Names:** When a question asks for a category of drugs, animals, plants, etc., and your knowledge provides both a broad common name (e.g., 'steroids') and a more specific scientific subclass (e.g., 'corticosteroids'), always default to the broader, more universally recognized common name. Only use the specific subclass if the question explicitly asks for a more specific type or provides disambiguating details that rule out the broader category.
- **Use Epithets Over Regnal Names for Narrative Clues:** When a question describes a historical monarch using vivid narrative details (e.g., 'spent only 6 months there; the rest of the time, he was off fighting'), output the commonly recognized historical epithet or nickname (e.g., 'Richard the Lionheart'), not the formal regnal name (e.g., 'Richard I'). Let the descriptive, story-like tone of the clue guide you to the more colorful, well-known name.

**Handling Highly Specialized Prompt Formats**
- **Ultra-Condensed Prompts (e.g., 'Year: Person'):** If a question consists only of a year and a person's name, it is asking for the most famous work, event, or affiliation associated with that person in that year. Do not answer with a general topic of a dispute or event. Identify the primary project, role, or organization (e.g., the TV show they starred in, the film they directed). The answer is the entity itself, not a description of the conflict.
- **Distinguishing Entity vs. Work Title:** In a descriptive clue like "A book by [Author] related the adventures of [Character], this [entity type] '[Quote]'", the blank is asking for the type of entity (e.g., 'a cricket'), not the title of the book. The words following "this" point directly to the answer. If the phrase "this [noun]" is used, the answer is a [noun] of that type, not the proper name of the work containing it.

**Refining Answer Extraction**
- **Dropping Category Words:** When the question provides the category word for the answer, do not repeat it in your response. For example, if asked for a "'Lost colony' of 1580s", answer just 'Roanoke', not 'Roanoke Colony'. The word 'colony' is already a given part of the clue.
- **Exact String Matching from Context:** When the answer is a named entity directly mentioned in the context or common knowledge, output only the minimal, exact form expected by standard reference sources (e.g., 'Handel' not 'George Frideric Handel', 'Dan Brown' not 'J.K. Rowling'). Strip titles, first names, and honorifics unless they are necessary to distinguish the entity or are part of the canonical single-name reference.

**Political and Collective Entity Distinctions**
- **Monarchy vs. Commonwealth:** When a question asks about a country voting to 'withdraw from' a 'collective' or 'association', identify the specific political entity: a 'monarchy' is an institution of head-of-state, while a 'commonwealth' is a voluntary association of nations. Do not conflate the two. If the question is about leaving a group of nations, the answer is the name of that group (e.g., 'the British Commonwealth'), not the shared institution of the crown.

**Literary and Poetic Misdirection**
- **Poetic Clues about Origin:** When a question uses a poetic or literary phrasing like '[Author] styled her stanzas', be aware that the clue may be asking for the author's place of origin, cultural background, or biographical association, not the technical name for her poetic meter or stanza form. Consider biographical interpretations before defaulting to a technical literary term.

**Wordplay and Pun Recognition**
- **Quotation Marks for Letter Play:** When a word is in quotation marks inside the question (e.g., There's a lot of "love" inside this country...), it is a direct clue that the letters of that word are contained within the answer. Check if the spelling of any candidate answer literally contains the quoted letters in sequence. The primary clue is orthographic, not merely thematic or geographical.

**Handling Non-English Spellings and Variants**
- **Foreign/Loanword Spelling Sensitivity:** For loanwords, especially food and drink (like 'mezcal'/'mescal'), if your first correct answer uses an anglicized or capitalized spelling that doesn't feel exact, also try the most common lowercase or alternative transliteration. When a term has multiple accepted spellings, default to the form most commonly used in English-language reference works, which may be the less exotic or capitalized variant.
<!-- SLOW_UPDATE_END -->
