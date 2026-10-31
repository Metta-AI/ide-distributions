"""Whether a platform-managed inference lease permits starting model work."""

from datetime import UTC, datetime
from pathlib import Path


def available(lease_file: Path | None) -> bool:
    return lease_file is None or (
        lease_file.exists() and datetime.fromisoformat(lease_file.read_text()) > datetime.now(UTC)
    )
