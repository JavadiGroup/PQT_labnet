"""The safe-state watchdog for Automation device grants (``labnet-automation-watchdog``).

A small process next to the Control Center. If the Control Center dies or its
event loop hangs while a grant holds devices, the watchdog waits for the old
leases to lapse, takes them with its own Central user, applies the approved
protocol's safe state, and releases them. See ``core.py`` for the rules and
``control_center/automation_grants.py`` for the heartbeat it watches.
"""

from .config import WatchdogSettings
from .core import Watchdog

__all__ = ["Watchdog", "WatchdogSettings"]
