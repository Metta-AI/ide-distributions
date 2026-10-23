"""Extract ``git archive`` output with bounds, streaming, for content we did not write.

Fetched repositories and card-made game folders are extracted with the tar ``data`` filter
(no absolute paths, no links out of the tree, no device files) and within limits on the
number of entries, any one file's size and the total size, so a hostile or accidental
archive cannot exhaust memory or the shared disk.
"""

import subprocess
import tarfile
from collections.abc import Mapping
from pathlib import Path

MAX_ENTRIES = 100_000
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
TIMEOUT_S = 600


class ArchiveError(RuntimeError):
    pass


def extract_git_archive(
    git_args: list[str], target: Path, env: Mapping[str, str] | None = None
) -> None:
    """Run ``git <git_args>`` (an ``archive --format=tar`` command) and extract its output into
    ``target``. Raises ``ArchiveError`` when git fails or a limit is exceeded."""
    process = subprocess.Popen(
        ["git", *git_args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
    entries = total = 0
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as tar:
            for member in tar:
                entries += 1
                total += max(member.size, 0)
                if entries > MAX_ENTRIES:
                    raise ArchiveError(f"the archive has more than {MAX_ENTRIES} entries")
                if member.size > MAX_FILE_BYTES:
                    raise ArchiveError(f"{member.name} is larger than {MAX_FILE_BYTES} bytes")
                if total > MAX_TOTAL_BYTES:
                    raise ArchiveError(f"the archive is larger than {MAX_TOTAL_BYTES} bytes")
                tar.extract(member, target, filter="data")
    except tarfile.TarError as error:
        process.kill()
        stderr = process.communicate(timeout=TIMEOUT_S)[1].decode(errors="replace").strip()
        raise ArchiveError(f"git {' '.join(git_args[:2])} failed: {stderr or error}") from None
    except BaseException:
        process.kill()
        process.wait()
        raise
    stderr = process.communicate(timeout=TIMEOUT_S)[1]
    if process.returncode != 0:
        raise ArchiveError(f"git archive failed: {stderr.decode(errors='replace').strip()}")
