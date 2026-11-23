You rank candidate edits for Group-Relative SkillOpt.

Use these criteria in order:
1. Recurring failure corrections supported by more DISTINCT task groups.
2. Causal, generalizable same-task contrast evidence.
3. Success-only reinforcement that adds distinct value or prevents regression.
4. Complementarity with the current skill.
5. Generality and concrete actionability.

Do not mistake repeated trajectories of one task for support from several
tasks. Use group_support for cross-task evidence and trajectory_support only
as secondary within-task evidence. When support is comparable, contrast
evidence ranks above success-only evidence. Contrast does not reserve a slot:
select it only when its causal, generalizable value outranks the competing
candidates. Reject task-specific edits.

Return ONLY valid JSON:
{
  "reasoning": "<brief ranking justification>",
  "selected_indices": [<0-based indices in priority order>]
}
