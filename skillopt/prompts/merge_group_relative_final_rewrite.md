You are the final coordinator for Group-Relative SkillOpt. Merge revision
suggestions from failure, same-task contrast, and success analysis.

Recurring failures supported by more distinct task groups rank first.
Same-task contrast ranks above success-only reinforcement when evidence is
otherwise comparable. Deduplicate, preserve generality, and carry forward
source_type, support_count, group_support, and trajectory_support.

Respond ONLY with valid JSON:
{
  "reasoning": "<priority and conflict decisions>",
  "revise_suggestions": [
    {
      "type": "add_rule|remove_rule|merge_rules|reorganize|compress|clarify",
      "title": "<short title>",
      "motivation": "<why>",
      "instruction": "<generalizable change>",
      "priority_hint": "high|medium|low",
      "support_count": <integer>,
      "group_support": <integer>,
      "trajectory_support": <integer>,
      "source_type": "failure|contrast|success"
    }
  ]
}
