Merge complete skill candidates derived from same-task success/failure
contrast. Preserve only causal, generalizable changes supported by the
trajectories. Exclude task-specific facts and preserve protected SLOW_UPDATE
content.

Respond ONLY with valid JSON:
{
  "reasoning": "<merge decisions>",
  "skill_candidates": [
    {
      "title": "<short title>",
      "change_summary": ["<change>"],
      "new_skill": "<complete merged skill>",
      "support_count": <integer>,
      "group_support": <integer>,
      "trajectory_support": <integer>,
      "source_type": "contrast"
    }
  ]
}

Return exactly one item in skill_candidates.
