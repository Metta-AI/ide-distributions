"""Repository access through the owner's consented Softmax GitHub connection."""

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from .context import Context


class GitHub:
    def __init__(self, context: Context):
        self.context = context
        credential = context.env["SOFTMAX_PLAYER_CREDENTIAL"]
        self.token = json.loads(credential)["token"] if credential.startswith("{") else credential
        self.base = context.env.get("COGAMES_API_URL", "https://softmax.com/api").rstrip("/")
        parsed = urlparse(self.base)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("GitHub repository access requires HTTPS")

    def github_repositories(self, page: int = 1):
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 1000:
            raise ValueError("page must be between 1 and 1000")
        request = Request(
            f"{self.base}/github/repositories?page={page}",
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 SoftmaxIDEa/0.9"},
        )
        request.add_unredirected_header("Authorization", f"Bearer {self.token}")
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    def github_clone(self, repository: str, commit: str | None = None, public_first: bool = False):
        repository = repository.removeprefix("https://github.com/").rstrip("/").removesuffix(".git")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repository):
            raise ValueError("Use owner/repository or an HTTPS github.com repository URL")
        owner, name = repository.split("/")
        if name in {".", ".."}:
            raise ValueError("Invalid repository name")
        if commit is not None and not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Use a full lowercase Git commit SHA")
        root = self.context.home / "repositories"
        root.mkdir(parents=True, exist_ok=True)
        url = f"{self.base}/github/repositories/{repository}/git"
        # Git receives only the workspace credential, scoped to the broker URL. Provider
        # credentials remain on Softmax. Do not persist authorization in .git/config.
        env = {
            "PATH": self.context.env.get("PATH", os.defpath),
            "HOME": str(self.context.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": f"http.{url}.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Bearer {self.token}",
            "GIT_CONFIG_KEY_1": "http.followRedirects",
            "GIT_CONFIG_VALUE_1": "false",
            "GIT_CONFIG_KEY_2": "http.userAgent",
            "GIT_CONFIG_VALUE_2": "Mozilla/5.0 SoftmaxIDEa/0.9",
        }
        # Public source needs no GitHub connection. Authorization is scoped to the
        # broker URL even when the first attempt uses GitHub directly.
        sources = [f"https://github.com/{repository}.git", url] if public_first else [url]
        if public_first and "COWORLD_REPOSITORIES_DIR" in self.context.env:
            # Operator-configured local development mirror. The model cannot
            # supply a host path, and the requested commit must already exist.
            mirror = Path(self.context.env["COWORLD_REPOSITORIES_DIR"]) / repository
            origin = subprocess.check_output(
                ["git", "-C", str(mirror), "remote", "get-url", "origin"],
                env=env, text=True, timeout=10,
            ).strip().removesuffix(".git")
            if origin != f"https://github.com/{repository}":
                raise ValueError("Local Coworld repository origin does not match the selected source")
            sources = [str(mirror.resolve())]
        for source in sources:
            checkout = root / f"{owner}-{name}-{uuid4().hex[:12]}"
            result = subprocess.run(
                ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "credential.helper=",
                 "clone", "--depth", "1", "--no-recurse-submodules", "--", source, str(checkout)],
                env=env, capture_output=True, text=True, timeout=240, check=False,
            )
            if result.returncode == 0:
                break
        if result.returncode:
            raise ValueError(
                "GitHub clone failed. Connect GitHub repository access in IDEa settings and "
                "verify repository and organization access. "
                + result.stderr.replace(self.token, "[redacted]")[-1000:]
            )
        if commit is not None:
            # Later terminal fetches cannot reuse this ephemeral authorization. Resolve
            # the requested release here without persisting credentials in the checkout.
            result = subprocess.run(
                ["git", "-c", "credential.helper=", "-C", str(checkout),
                 "fetch", "--depth=1", "--no-tags", "origin", commit],
                env=env, capture_output=True, text=True, timeout=240, check=False,
            )
            if result.returncode:
                raise ValueError(
                    "GitHub could not fetch the requested commit. Verify that it belongs to "
                    "this authorized repository. "
                    + result.stderr.replace(self.token, "[redacted]")[-1000:]
                )
            subprocess.run(
                ["git", "-c", f"core.hooksPath={os.devnull}", "-C", str(checkout),
                 "checkout", "--detach", commit],
                env=env, capture_output=True, text=True, timeout=60, check=True,
            )
        commit = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], env=env, text=True, timeout=10
        ).strip()
        return {
            "repository": f"https://github.com/{repository}",
            "path": str(checkout),
            "commit": commit,
            "shallow": True,
            "next": "Use read_file and search_files on this checkout; cite paths and the commit.",
        }
