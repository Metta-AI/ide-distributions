"""The Softmax platform behind one interface: the real client and a fake for tests.

Everything here runs in the workspace environment (it needs the official ``coworld`` and
``softmax_client`` packages); the Hermes plugin never imports it directly.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from research_core.layout import Workspace
from research_core.platform.protocol import Platform


def from_env(env: Mapping[str, str] | None = None, workspace: Workspace | None = None) -> Platform:
    """``IDE_PLATFORM=fake`` selects the persistent fake; anything else is the real platform."""
    env = os.environ if env is None else env
    if env.get("IDE_PLATFORM", "real").lower() == "fake":
        from research_core import layout
        from research_core.platform.fake import FakePlatform

        ws = workspace or layout.from_env(env)
        return FakePlatform(ws.state / "fake_platform.json", env)
    from research_core.platform.real import RealPlatform

    return RealPlatform(env)
