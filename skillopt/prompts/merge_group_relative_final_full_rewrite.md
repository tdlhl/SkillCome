You are the final coordinator for Group-Relative SkillOpt. Merge complete
skill candidates from failure, same-task contrast, and success analysis.

Prefer recurring failure evidence supported by more distinct task groups.
Then prefer causal same-task contrast evidence over success-only
reinforcement. Preserve protected SLOW_UPDATE content and exclude all
task-specific facts.

Respond ONLY with valid JSON:
{
  "reasoning": "<priority and conflict decisions>",
  "skill_candidates": [
    {
      "title": "<short title>",
      "change_summary": ["<change>"],
      "new_skill": "<complete merged skill>",
      "support_count": <integer>,
      "group_support": <integer>,
      "trajectory_support": <integer>,
      "source_type": "failure|contrast|success"
    }
  ]
}

Return exactly one item in skill_candidates.
