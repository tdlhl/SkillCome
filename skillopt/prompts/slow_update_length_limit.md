Rewrite an over-length slow-guidance block into a shorter complete replacement.

Preserve its intended actionable rules. Remove repetition and anything already
stated in the main skill. Obey the exact character limit in the user message.

Respond only with a valid JSON object:
{
  "reasoning": "<brief description of what was removed>",
  "slow_update_content": "<shortened replacement guidance>"
}
