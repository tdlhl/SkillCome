"""Group-Relative SkillOpt extensions.

The original SkillOpt path remains the default.  These helpers are activated
only when ``group_rollout.enabled`` is set in a dedicated configuration.
"""

from skillopt.group_relative.reflect import run_group_contrast_reflect
from skillopt.group_relative.rollout import GroupedRollout, run_grouped_rollout

__all__ = [
    "GroupedRollout",
    "run_grouped_rollout",
    "run_group_contrast_reflect",
]
