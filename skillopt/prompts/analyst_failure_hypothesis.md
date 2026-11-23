Also maintain one compact cross-step failure hypothesis.

Compare this minibatch with the supplied failure bank. Link an existing ID
only when the underlying failure mechanism is genuinely the same; otherwise
leave the ID empty. The mechanism must be one generalizable sentence. Do not
copy task details, answers, entities, formulas, or trajectories into it.

In addition to the normal failure-analysis fields, include:
{
  "failure_hypothesis": {
    "related_hypothesis_id": "<existing ID or empty>",
    "mechanism": "<one concise recurring failure mechanism>",
    "edit_indices": [<0-based indices of edits that address this mechanism>]
  }
}
