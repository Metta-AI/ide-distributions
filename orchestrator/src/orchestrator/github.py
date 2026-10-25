"""Repository access through the owner's consented Softmax GitHub connection."""

import json
import os
import re
import subprocess
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

    def github_clone(self, repository: str):
        repository = repository.removeprefix("https://github.com/").rstrip("/").removesuffix(".git")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repository):
            raise ValueError("Use owner/repository or an HTTPS github.com repository URL")
        owner, name = repository.split("/")
        if name in {".", ".."}:
            raise ValueError("Invalid repository name")
        root = self.context.home / "repositories"
        root.mkdir(parents=True, exist_ok=True)
        checkout = root / f"{owner}-{name}-{uuid4().hex[:12]}"
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
        result = subprocess.run(
            [
                "git",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "credential.helper=",
                "clone",
                "--depth",
                "1",
                "--no-recurse-submodules",
                "--",
                url,
                str(checkout),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        if result.returncode:
            raise ValueError(
                "GitHub clone failed. Connect GitHub repository access in IDEa settings and "
                "verify repository and organization access. "
                + result.stderr.replace(self.token, "[redacted]")[-1000:]
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
