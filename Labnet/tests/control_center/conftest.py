from __future__ import annotations

import pytest

from labnet.control_center import agent


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never start the installed Claude Code; agent tests pass a fake command."""

    monkeypatch.setattr(agent, "find_claude", lambda _configured: None)
