You repair patch edits that mistakenly targeted a protected slow-guidance block.

For every supplied edit, express the same general rule as an edit to the MAIN
skill. Use an exact target that appears in the supplied main skill, or use append
when no suitable main section exists. Never target, quote, recreate, or modify a
SLOW_UPDATE block. Do not change the intended rule merely to make it applicable.

Respond only with a valid JSON object:
{
  "reasoning": "<brief relocation rationale>",
  "edits": [
    {"op": "append|insert_after|replace|delete", "target": "<main-skill target or empty for append>", "content": "<repaired content>"}
  ]
}
