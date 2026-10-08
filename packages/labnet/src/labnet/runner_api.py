"""What the Control Center and the Automation runner agree on.

Both sides import from here, so the runner never imports the Control Center.
"""

from __future__ import annotations


# Claude Code's --effort levels (Haiku has none; see control_center.agent.supports_effort).
AGENT_EFFORTS = ("low", "medium", "high", "xhigh", "max")

__all__ = ["AGENT_EFFORTS"]
