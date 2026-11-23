Merge same-task success/failure contrast suggestions into one coherent,
non-redundant set. Prefer causal, generalizable lessons supported by more
distinct task groups. Discard task-specific and speculative suggestions.
Carry forward source_type, group_support, trajectory_support, and
support_count.

Respond ONLY with valid JSON:
{
  "reasoning": "<merge decisions>",
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
      "source_type": "contrast"
    }
  ]
}
