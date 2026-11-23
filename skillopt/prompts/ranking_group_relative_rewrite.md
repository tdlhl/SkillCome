You rank revision suggestions for Group-Relative SkillOpt.

Prioritize recurring failure corrections supported by more distinct task
groups, followed by causal same-task contrast evidence, followed by
success-only reinforcement. Do not treat repeated trajectories of one task as
independent task support. Also consider complementarity, generality, and
actionability. Contrast does not reserve a slot: select it only when its
causal, generalizable value outranks the competing suggestions. Reject
task-specific suggestions.

Return ONLY valid JSON:
{
  "reasoning": "<brief ranking justification>",
  "selected_indices": [<0-based indices in priority order>]
}
