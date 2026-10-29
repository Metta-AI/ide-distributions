"""Run both research profiles inside the hosted Hermes image on this machine (Option C of
``docs/local-end-to-end.md``): the image CI uses, the checkout mounted read-only at ``/src``,
the state on a Docker volume, a multiplexing gateway and ``research serve`` as the seam.

    uv run python tools/local_stack.py up [--platform fake|real] [--seam-port 8650]
                                          [--gateway-port 8642] [--name ide-local]
                                          [--no-scenario] [--reset] [--timeout 600]
    uv run python tools/local_stack.py status
    uv run python tools/local_stack.py logs [--follow]
    uv run python tools/local_stack.py shell
    uv run python tools/local_stack.py scenario [--timeout 600]
    uv run python tools/local_stack.py down [--reset]

``up`` creates (or reuses) one container and one volume, writes two scripts into the volume
(``bootstrap.sh`` and ``as-profile.sh``, below) and runs the bootstrap inside the container:
install ``uv``, install and prepare both profiles, write the ``.env`` files, start the gateway
and the seam server. On a fresh volume it then runs the acceptance scenario unless
``--no-scenario`` is given. ``up`` on an existing container restarts the services without
reinstalling; ``--reset`` removes the container and the volume first.

Secrets come from the caller's environment and go into the container only: ``OPENROUTER_API_KEY``
(required on a fresh volume; later ``up`` runs keep the key already in the ``.env`` files) and,
with ``--platform real``, ``SOFTMAX_PLAYER_ID``, ``SOFTMAX_PLAYER_CREDENTIAL``, optionally
``COGAMES_API_URL`` and ``IDE_DEFAULT_COWORLD``. Hermes reads the ``.env`` files inside the
volume, so that is where the keys end up; nothing is written on this machine and nothing is
printed. Stdlib only; ``docker`` runs through subprocess.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
try:
    from research_core.client import ScriptedClient, SeamError
except ModuleNotFoundError:  # a plain `python3`, outside the workspace environment
    sys.path.insert(0, str(REPO / "core" / "src"))
    from research_core.client import ScriptedClient, SeamError

# The hosted Hermes runtime: the digest CI runs the hermes-marked tests in (ci.yml).
IMAGE = (
    "nousresearch/hermes-agent"
    "@sha256:9774f4f39a9bb8c2f68ce728ed5e99ddbad282163be56764afacf88ed952b784"
)
SOURCE = "/src"
DATA = "/opt/data"
STACK_DIR = f"{DATA}/local_stack"
HOST_HOME = f"{DATA}/host"
WORKSPACE = f"{DATA}/workspace"
UPSTREAM = f"{DATA}/upstream"
GATEWAY_LOG = f"{DATA}/gateway.log"
SERVE_LOG = f"{DATA}/serve.log"
API_KEY_FILE = f"{STACK_DIR}/api_key"
BOOTSTRAP = f"{STACK_DIR}/bootstrap.sh"
AS_PROFILE = f"{STACK_DIR}/as-profile.sh"
FAKE_PLATFORM_STATE = f"{WORKSPACE}/.state/fake_platform.json"
LABEL = "ide-local-stack"
FAKE_PLAYER_ID = "ply_canary"
REQUEST = "Submit the starter as my champion."
SUBMITTED = "version.submitted"
REAL_REQUIRED = ("SOFTMAX_PLAYER_ID", "SOFTMAX_PLAYER_CREDENTIAL")
DEFAULT_SERVER = "https://softmax.com/api"
# The Orchestrator commits in the workspace's code repository; the container has no identity.
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "IDE local",
    "GIT_AUTHOR_EMAIL": "ide-local@example.invalid",
    "GIT_COMMITTER_NAME": "IDE local",
    "GIT_COMMITTER_EMAIL": "ide-local@example.invalid",
}


class StackError(Exception):
    pass


# --- docker -----------------------------------------------------------------------------------


def docker(*argv: str, input: str | None = None, capture: bool = True) -> str:
    """Run one docker command; its stdout. A failure raises with the command and its output."""
    command = ["docker", *argv]
    result = subprocess.run(command, input=input, text=True, capture_output=capture)
    if result.returncode != 0:
        shown = " ".join(command)
        output = (result.stdout or "") + (result.stderr or "") if capture else ""
        raise StackError(f"`{shown}` failed with exit code {result.returncode}\n{output}".rstrip())
    return result.stdout or ""


def require_docker() -> None:
    try:
        docker("version", "--format", "{{.Server.Version}}")
    except (StackError, FileNotFoundError) as error:
        raise StackError(
            "Docker is not reachable; start OrbStack (`open -a OrbStack`) and retry.\n" + str(error)
        ) from None


def container_state(name: str) -> str | None:
    """``running``, ``exited``, ... or None when there is no such container."""
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Status}}", name], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def volume_exists(volume: str) -> bool:
    result = subprocess.run(["docker", "volume", "inspect", volume], capture_output=True)
    return result.returncode == 0


def exec_env_flags(names: tuple[str, ...]) -> list[str]:
    """``-e NAME`` for the caller's variables that are set: docker copies the value itself, so
    it never appears in a command line."""
    flags: list[str] = []
    for name in names:
        if os.environ.get(name):
            flags.extend(["-e", name])
    return flags


# --- the stack --------------------------------------------------------------------------------


@dataclass
class Settings:
    """What one stack is: fixed when its container is created, read back from its labels."""

    name: str
    volume: str
    seam_port: int
    gateway_port: int
    platform: str

    @property
    def seam_url(self) -> str:
        return f"http://127.0.0.1:{self.seam_port}"

    @property
    def gateway_url(self) -> str:
        return f"http://127.0.0.1:{self.gateway_port}"

    def labels(self) -> list[str]:
        values = {
            "volume": self.volume,
            "seam-port": self.seam_port,
            "gateway-port": self.gateway_port,
            "platform": self.platform,
        }
        flags: list[str] = []
        for key, value in values.items():
            flags.extend(["--label", f"{LABEL}.{key}={value}"])
        return flags

    @classmethod
    def from_container(cls, name: str) -> Settings:
        if container_state(name) is None:
            raise StackError(f"no container {name}; run `up` first")
        raw = docker("inspect", "--format", "{{json .Config.Labels}}", name)
        labels = json.loads(raw) or {}
        try:
            return cls(
                name=name,
                volume=labels[f"{LABEL}.volume"],
                seam_port=int(labels[f"{LABEL}.seam-port"]),
                gateway_port=int(labels[f"{LABEL}.gateway-port"]),
                platform=labels[f"{LABEL}.platform"],
            )
        except KeyError as error:
            raise StackError(f"container {name} was not created by this tool ({error})") from None


def create_container(settings: Settings) -> None:
    """One container from the image: the checkout read-only at /src, the volume at /opt/data,
    both ports published on this machine's loopback only, root as CI runs it, and ``sleep`` as
    the main process so the services can be started and restarted with ``docker exec``
    (``--init`` puts a reaper in front of it, so an exited service does not linger as a zombie
    that ``kill -0`` still finds)."""
    git_flags: list[str] = []
    for name, value in GIT_IDENTITY.items():
        git_flags.extend(["-e", f"{name}={value}"])
    docker(
        "run",
        "--detach",
        "--init",
        "--name",
        settings.name,
        "--user",
        "0",
        "--entrypoint",
        "sleep",
        *settings.labels(),
        "--volume",
        f"{REPO}:{SOURCE}:ro",
        "--volume",
        f"{settings.volume}:{DATA}",
        "--publish",
        f"127.0.0.1:{settings.seam_port}:{settings.seam_port}",
        "--publish",
        f"127.0.0.1:{settings.gateway_port}:{settings.gateway_port}",
        *git_flags,
        IMAGE,
        "infinity",
    )


def write_into_volume(name: str, path: str, text: str) -> None:
    docker("exec", "-i", name, "sh", "-c", f"mkdir -p {STACK_DIR} && cat > {path}", input=text)


def render(template: str, values: dict[str, str]) -> str:
    """Substitute ``@@NAME@@`` markers; every marker must be known."""
    text = template
    for key, value in values.items():
        text = text.replace(f"@@{key}@@", value)
    if "@@" in text:
        raise StackError("bootstrap template has an unknown marker")
    return text


def check_secrets(platform: str, fresh: bool, player: str | None = None) -> None:
    missing = []
    if fresh and not os.environ.get("OPENROUTER_API_KEY"):
        missing.append("OPENROUTER_API_KEY")
    if platform == "real":
        if not all(os.environ.get(name) for name in REAL_REQUIRED):
            player_from_local_login(player)
        missing.extend(name for name in REAL_REQUIRED if not os.environ.get(name))
    if missing:
        raise StackError(f"set {', '.join(missing)} in the environment (see the module docstring)")


def player_from_local_login(player: str | None) -> None:
    """Take the Player from this machine's ``softmax`` login when the environment has none.

    ``softmax player use <id>`` keeps a player session (a 24-hour token) per server in the
    CLI's ``credentials.yaml``; the active one is the default, ``--player`` picks another by its
    full id. The token goes into the process environment for the bootstrap only (docker
    copies it into the container's ``.env`` files, as hosting would).
    """
    try:
        from softmax_client import credentials
    except ImportError as error:
        raise StackError(
            "SOFTMAX_PLAYER_ID and SOFTMAX_PLAYER_CREDENTIAL are not set and the softmax client"
            " is not importable here; run through `uv run` from the repository root"
        ) from error
    server = (os.environ.get("COGAMES_API_URL") or DEFAULT_SERVER).rstrip("/")
    player_id = player or credentials.get_active_player_id(server=server)
    if not player_id:
        raise StackError(
            f"no active Player in the local softmax login for {server}; run"
            " `softmax player use <id>` (see `softmax player list`) or set SOFTMAX_PLAYER_ID"
            " and SOFTMAX_PLAYER_CREDENTIAL"
        )
    session = credentials.get_cached_player_session(server=server, player_id=player_id)
    if session is None:
        raise StackError(
            f"the local softmax login has no session for Player {player_id} on {server};"
            f" run `softmax player use {player_id}` and `up` again"
        )
    expires = getattr(session, "expires_at", None)
    if expires is not None and _expired(expires):
        raise StackError(
            f"the local player session for {player_id} expired at {expires}; run"
            f" `softmax player use {player_id}` to mint a new one, then `up` again"
        )
    credential = {"token": session.token}
    if expires is not None:
        credential["expires_at"] = str(expires)
    os.environ["SOFTMAX_PLAYER_ID"] = player_id
    os.environ["SOFTMAX_PLAYER_CREDENTIAL"] = json.dumps(credential)
    os.environ.setdefault("COGAMES_API_URL", server)
    print(f"Player from the local softmax login: {player_id} on {server} (session until {expires})")


def _expired(expires_at: object) -> bool:
    from datetime import UTC, datetime

    if isinstance(expires_at, datetime):
        value = expires_at
    else:
        text = str(expires_at).replace("Z", "+00:00")
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value <= datetime.now(UTC)


def bootstrap_values(settings: Settings) -> dict[str, str]:
    env = os.environ
    if settings.platform == "fake":
        return {
            "GATEWAY_PORT": str(settings.gateway_port),
            "SEAM_PORT": str(settings.seam_port),
            "PLATFORM": "fake",
            "PLAYER_ID": FAKE_PLAYER_ID,
            "COGAMES_API_URL": "",
            "DEFAULT_COWORLD": "",
        }
    return {
        "GATEWAY_PORT": str(settings.gateway_port),
        "SEAM_PORT": str(settings.seam_port),
        "PLATFORM": "real",
        "PLAYER_ID": env.get("SOFTMAX_PLAYER_ID", ""),
        "COGAMES_API_URL": env.get("COGAMES_API_URL") or DEFAULT_SERVER,
        "DEFAULT_COWORLD": env.get("IDE_DEFAULT_COWORLD", ""),
    }


def run_bootstrap(settings: Settings) -> None:
    """Write both scripts into the volume and run the bootstrap; its output streams through."""
    write_into_volume(settings.name, AS_PROFILE, AS_PROFILE_SCRIPT)
    bootstrap = render(BOOTSTRAP_SCRIPT, bootstrap_values(settings))
    write_into_volume(settings.name, BOOTSTRAP, bootstrap)
    secrets = exec_env_flags(("OPENROUTER_API_KEY", "SOFTMAX_PLAYER_CREDENTIAL"))
    docker("exec", *secrets, settings.name, "bash", BOOTSTRAP, "up", capture=False)


def in_profile(settings: Settings, *command: str, check: bool = True) -> str:
    """Run ``command`` inside the container with the Orchestrator profile's environment."""
    argv = ["docker", "exec", settings.name, "bash", AS_PROFILE, "orchestrator", *command]
    result = subprocess.run(argv, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise StackError(f"`{' '.join(argv)}` failed\n{result.stdout}{result.stderr}".rstrip())
    return result.stdout


def print_summary(settings: Settings) -> None:
    seam, name = settings.seam_url, settings.name
    header = "-H 'Content-Type: application/json'"
    open_body = json.dumps({"client": {"id": "curl", "kind": "web-ide"}, "actor": "user:local"})
    message = json.dumps(
        {
            "session_id": "<session_id>",
            "message": "What is the state of the workspace?",
            "actor": "user:local",
            "context": {"contract_version": 1, "on_screen": []},
        }
    )
    lines = [
        "",
        f"=== local stack {name} (platform {settings.platform}) ===",
        f"seam (research serve routes):  {seam}",
        f"gateway (Hermes API server):   {settings.gateway_url}  routes under /p/<profile>/",
        f"API key:                       {API_KEY_FILE} in volume {settings.volume}"
        " (API_SERVER_KEY, same in every .env)",
        f"                               docker exec {name} cat {API_KEY_FILE}",
        f"gateway log:                   {GATEWAY_LOG}      (tools/local_stack.py logs)",
        f"seam log:                      {SERVE_LOG}",
        f"profile homes:                 {HOST_HOME}/profiles",
        f"workspace (IDE_WORKSPACE):     {WORKSPACE}",
        f"checkout (read-only):          {SOURCE}",
        "",
        "talk to it:",
        f"  curl -s -X POST {seam}/session-open {header} -d '{open_body}'",
        f"  curl -s -X POST {seam}/message {header} -d '{message}'",
        f"  curl -s '{seam}/snapshot?store=workspace'",
        f"  curl -sN '{seam}/stream?store=workspace&after=0'",
        "  uv run python tools/local_stack.py scenario | status | logs | shell | down",
    ]
    print("\n".join(lines), flush=True)


# --- the scenario -----------------------------------------------------------------------------


def coworld_snapshot(client: ScriptedClient) -> dict | None:
    """The active Coworld's snapshot, or None while no Coworld is active (the seam answers 404)."""
    try:
        return client.snapshot("coworld")
    except SeamError as error:
        if error.status == 404:
            return None
        raise


def submitted_event(client: ScriptedClient, after: int) -> tuple[dict | None, int]:
    """Read the Coworld store's events after ``after``; the ``version.submitted`` event if one
    arrived, and the last seq seen."""
    try:
        events = client.events("coworld", after, idle=2.0, until=lambda e: e["kind"] == SUBMITTED)
    except SeamError as error:
        if error.status == 404:
            return None, after
        raise
    if events:
        after = events[-1]["seq"]
    found = next((event for event in events if event["kind"] == SUBMITTED), None)
    return found, after


def watcher_status(settings: Settings) -> dict:
    output = in_profile(settings, "research", "watcher", "status", check=False)
    try:
        return json.loads(output.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": output.strip()[-200:] or "no output"}


def progress_line(client: ScriptedClient, settings: Settings, elapsed: int) -> str:
    snapshot = coworld_snapshot(client)
    if snapshot is None:
        coworld = "no active Coworld yet"
    else:
        records = snapshot["records"]
        coworld = (
            f"Coworld {snapshot['coworld_id']}: {len(records.get('tasks', []))} tasks,"
            f" {len(records.get('versions', []))} versions, seq {snapshot['seq']}"
        )
    status = watcher_status(settings)
    watcher = (
        f"has_work={status.get('has_work')} waiting_on={status.get('waiting_on')}"
        f" heartbeat_age_s={status.get('heartbeat_age_s')}"
        if status.get("ok")
        else f"status unavailable ({status.get('error')})"
    )
    return f"[{elapsed:4d} s] {coworld}; watcher: {watcher}"


def fake_submissions(settings: Settings) -> dict[str, int]:
    """Submissions in the fake platform's state file, counted per Player id."""
    raw = docker("exec", settings.name, "cat", FAKE_PLATFORM_STATE)
    rows = json.loads(raw).get("submissions") or {}
    rows = list(rows.values()) if isinstance(rows, dict) else list(rows)
    counts: dict[str, int] = {}
    for row in rows:
        player = str(row.get("player_id"))
        counts[player] = counts.get(player, 0) + 1
    return counts


def run_scenario(settings: Settings, timeout_s: int) -> int:
    """Send the acceptance request through the seam and wait for ``version.submitted``."""
    client = ScriptedClient(settings.seam_url, timeout_s=60)
    before = coworld_snapshot(client)
    after = before["seq"] if before else 0
    print(f"scenario on {settings.seam_url} (platform {settings.platform}): {REQUEST!r}")
    session_id = client.open("local-stack", "web-ide")
    reply = client.say(REQUEST, {"contract_version": 1, "on_screen": []})
    print(f"session {session_id}; chat run {reply['run_id']} ({reply['delivered']})", flush=True)

    started = time.monotonic()
    last_report = started - 60
    found = None
    while time.monotonic() - started < timeout_s:
        found, after = submitted_event(client, after)
        if found:
            break
        if time.monotonic() - last_report >= 30:
            print(progress_line(client, settings, round(time.monotonic() - started)), flush=True)
            last_report = time.monotonic()
        time.sleep(5)
    elapsed = round(time.monotonic() - started)

    if found is None:
        print(f"no {SUBMITTED} after {elapsed} s; read {GATEWAY_LOG} (tools/local_stack.py logs)")
        print(progress_line(client, settings, elapsed))
        return 1
    print(f"{SUBMITTED} after {elapsed} s (seq {found['seq']}): {json.dumps(found)[:400]}")
    print(progress_line(client, settings, elapsed))
    if settings.platform == "fake":
        counts = fake_submissions(settings)
        print(f"submissions by Player: {counts}")
        if not counts.get(FAKE_PLAYER_ID):
            print(f"the submission was not made as the profile's Player {FAKE_PLAYER_ID}")
            return 1
    return 0


# --- the commands -----------------------------------------------------------------------------


def http_status(url: str, key: str | None = None) -> str:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8", "replace").strip()
            return f"{response.status} {body[:200]}"
    except urllib.error.HTTPError as error:
        return f"{error.code} {error.reason}"
    except (urllib.error.URLError, OSError) as error:
        return f"unreachable ({error})"


def cmd_up(args: argparse.Namespace) -> int:
    require_docker()
    name = args.name
    volume = f"{name}-stack"
    if args.reset:
        remove_stack(name, volume)
    fresh = not volume_exists(volume)
    state = container_state(name)
    if state is None:
        settings = Settings(name, volume, args.seam_port, args.gateway_port, args.platform)
        check_secrets(settings.platform, fresh, args.player)
        if fresh:
            docker("volume", "create", volume)
        print(f"creating container {name} from {IMAGE} (volume {volume})")
        create_container(settings)
    else:
        settings = Settings.from_container(name)
        check_secrets(settings.platform, fresh, args.player)
        requested = (args.seam_port, args.gateway_port, args.platform)
        if requested != (settings.seam_port, settings.gateway_port, settings.platform):
            print(
                f"container {name} exists with ports {settings.seam_port}/{settings.gateway_port}"
                f" and platform {settings.platform}; keeping those (use --reset to change)"
            )
        if state != "running":
            print(f"starting container {name} (was {state})")
            docker("start", name)
    started = time.monotonic()
    run_bootstrap(settings)
    print(f"bootstrap took {round(time.monotonic() - started)} s")
    print_summary(settings)
    if fresh and not args.no_scenario and settings.platform == "fake":
        print("\nfresh workspace: running the acceptance scenario (skip with --no-scenario)")
        return run_scenario(settings, args.timeout)
    if fresh and settings.platform == "real":
        print("\nreal platform: the submission scenario is never run on its own; talk to the")
        print("Orchestrator through the seam, or run `scenario --really` knowingly")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    require_docker()
    state = container_state(args.name)
    print(f"container {args.name}: {state or 'absent'}")
    if state is None:
        return 1
    settings = Settings.from_container(args.name)
    print(f"platform: {settings.platform}; volume: {settings.volume}")
    print(f"seam {settings.seam_url}/health: {http_status(settings.seam_url + '/health')}")
    if state != "running":
        return 1
    key = docker("exec", args.name, "cat", API_KEY_FILE).strip()
    capabilities = http_status(settings.gateway_url + "/v1/capabilities", key)
    print(f"gateway {settings.gateway_url}/v1/capabilities: {capabilities[:3]}")
    print(f"watcher: {json.dumps(watcher_status(settings))}")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    require_docker()
    Settings.from_container(args.name)
    tail = ["tail", "-n", str(args.lines)] + (["-f"] if args.follow else [])
    command = ["docker", "exec", args.name, *tail, GATEWAY_LOG, SERVE_LOG]
    try:
        return subprocess.call(command)
    except KeyboardInterrupt:
        return 130


def cmd_shell(args: argparse.Namespace) -> int:
    require_docker()
    Settings.from_container(args.name)
    command = ["docker", "exec", "-it", args.name, "bash", AS_PROFILE, "orchestrator", "bash"]
    if not sys.stdin.isatty():
        print("no TTY; run this yourself:\n  " + " ".join(command))
        return 0
    os.execvp("docker", command)


def cmd_scenario(args: argparse.Namespace) -> int:
    require_docker()
    settings = Settings.from_container(args.name)
    if container_state(args.name) != "running":
        raise StackError(f"container {args.name} is not running; run `up` first")
    if settings.platform == "real" and not args.really:
        raise StackError(
            "this stack is on the real platform: the scenario would upload and submit the"
            " starter policy to a real league as your Player; pass --really to do that"
        )
    return run_scenario(settings, args.timeout)


def remove_stack(name: str, volume: str) -> None:
    if container_state(name) is not None:
        print(f"removing container {name}")
        docker("rm", "--force", name)
    if volume_exists(volume):
        print(f"removing volume {volume}")
        docker("volume", "rm", volume)


def cmd_down(args: argparse.Namespace) -> int:
    require_docker()
    name = args.name
    state = container_state(name)
    if state == "running":
        print(f"stopping the seam server and the gateway in {name}")
        subprocess.run(["docker", "exec", name, "bash", BOOTSTRAP, "stop"], check=False)
        docker("stop", "--time", "30", name)
        print(f"container {name} stopped; its state is in volume {name}-stack")
    elif state is not None:
        print(f"container {name} is already {state}")
    else:
        print(f"no container {name}")
    if args.reset:
        remove_stack(name, f"{name}-stack")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run both research profiles inside the hosted Hermes image on this machine"
        " (Option C of docs/local-end-to-end.md)."
    )
    parser.add_argument("--name", default="ide-local", help="Container name (default ide-local)")
    commands = parser.add_subparsers(dest="command", required=True)

    up = commands.add_parser("up", help="Create or reuse the container and start the services")
    up.add_argument(
        "--platform",
        choices=("fake", "real"),
        default="real",
        help="real (the default: softmax.com as your Player) or fake (no credentials, no network)",
    )
    up.add_argument(
        "--player",
        default=None,
        help="A Player id from `softmax player list` (default: the local login's active one)",
    )
    up.add_argument("--seam-port", type=int, default=8650, help="research serve (default 8650)")
    up.add_argument("--gateway-port", type=int, default=8642, help="Hermes API (default 8642)")
    up.add_argument("--no-scenario", action="store_true", help="Leave a fresh workspace empty")
    up.add_argument("--reset", action="store_true", help="Remove the container and volume first")
    up.add_argument("--timeout", type=int, default=600, help="Scenario timeout in seconds")
    up.set_defaults(run=cmd_up)

    status = commands.add_parser("status", help="Container, seam, gateway and watcher state")
    status.set_defaults(run=cmd_status)

    logs = commands.add_parser("logs", help="Tail the gateway and seam logs")
    logs.add_argument("--follow", "-f", action="store_true")
    logs.add_argument("--lines", "-n", type=int, default=40)
    logs.set_defaults(run=cmd_logs)

    shell = commands.add_parser("shell", help="A shell with the Orchestrator's environment")
    shell.set_defaults(run=cmd_shell)

    scenario = commands.add_parser("scenario", help=f"Send {REQUEST!r} and wait for a submission")
    scenario.add_argument("--timeout", type=int, default=600, help="Seconds (default 600)")
    scenario.add_argument(
        "--really", action="store_true", help="On the real platform: yes, submit for real"
    )
    scenario.set_defaults(run=cmd_scenario)

    down = commands.add_parser("down", help="Stop the container; --reset removes it and the volume")
    down.add_argument("--reset", action="store_true")
    down.set_defaults(run=cmd_down)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with docker's output
    try:
        return args.run(args)
    except StackError as error:
        print(f"local_stack: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


# --- the scripts written into the volume -------------------------------------------------------

# Runs a command with one profile's environment, as the gateway gives it to that profile's turns.
AS_PROFILE_SCRIPT = f"""#!/bin/bash
# Written by tools/local_stack.py. Usage: as-profile.sh <role> <command...>
# HERMES_HOME is the profile home, the profile's .env is exported, and the workspace
# environment's bin comes first on PATH (so `research` and `python` are the workspace's).
set -euo pipefail
role=$1
shift
profile={HOST_HOME}/profiles/$role
set -a
. "$profile/.env"
set +a
export HERMES_HOME=$profile
export HERMES_REAL_HOME={HOST_HOME}
export PATH={WORKSPACE}/tools/venv/bin:{DATA}/.local/bin:$PATH
exec "$@"
"""

# The in-container setup. @@MARKERS@@ are substituted by ``render``; the secrets come from the
# environment ``docker exec -e`` passes, never from this file.
BOOTSTRAP_SCRIPT = f"""#!/bin/bash
# Written by tools/local_stack.py; runs inside the Hermes image as root.
#   bootstrap.sh up    install what is missing, rewrite the .env files, (re)start the services
#   bootstrap.sh stop  stop the seam server and the gateway
set -euo pipefail

GATEWAY_PORT=@@GATEWAY_PORT@@
SEAM_PORT=@@SEAM_PORT@@
PLATFORM=@@PLATFORM@@
PLAYER_ID=@@PLAYER_ID@@
COGAMES_API_URL=@@COGAMES_API_URL@@
DEFAULT_COWORLD=@@DEFAULT_COWORLD@@

DATA={DATA}
STACK_DIR={STACK_DIR}
HOST_HOME={HOST_HOME}
WORKSPACE={WORKSPACE}
UPSTREAM={UPSTREAM}
GATEWAY_LOG={GATEWAY_LOG}
SERVE_LOG={SERVE_LOG}
HERMES=/opt/hermes/.venv/bin/hermes
HERMES_PYTHON=/opt/hermes/.venv/bin/python
ORCHESTRATOR=$HOST_HOME/profiles/orchestrator
AS_PROFILE="bash {AS_PROFILE}"

# uv, its cache and the CPython 3.12 it fetches for the workspace environment stay on the volume.
export UV_CACHE_DIR=$DATA/uv-cache
export UV_PYTHON_INSTALL_DIR=$DATA/uv-python
export PATH=$DATA/.local/bin:$PATH

say() {{ echo "[local-stack ${{SECONDS}}s] $*"; }}

fail() {{ say "$*"; exit 1; }}

install_uv() {{
    if ! command -v uv > /dev/null; then
        say "installing uv into $DATA/.local/bin (the image ships none; CI uses astral-sh/setup-uv)"
        curl -LsSf https://astral.sh/uv/install.sh \\
            | env UV_INSTALL_DIR=$DATA/.local/bin UV_NO_MODIFY_PATH=1 sh
    fi
    # The agents' terminal is a login shell whose PATH has no $DATA/.local/bin; hosting's image
    # has uv on PATH, so put it where every shell finds it (the container layer, redone per up).
    ln -sf "$DATA/.local/bin/uv" /usr/local/bin/uv
}}

mark_checkout_safe() {{
    # /src belongs to the Mac user; git refuses another owner's repository without this.
    if ! git config --global --get-all safe.directory 2> /dev/null | grep -qx {SOURCE}; then
        git config --global --add safe.directory {SOURCE}
    fi
}}

api_key() {{
    # One random API_SERVER_KEY per volume, made on the first `up`.
    if [ ! -s "$STACK_DIR/api_key" ]; then
        "$HERMES_PYTHON" -c 'import secrets; print(secrets.token_hex(16))' > "$STACK_DIR/api_key"
        chmod 600 "$STACK_DIR/api_key"
    fi
    cat "$STACK_DIR/api_key"
}}

dotenv_value() {{ # dotenv_value <file> <name>: the value, or nothing
    if [ -f "$1" ]; then sed -n "s/^$2=//p" "$1" | head -n 1; fi
}}

model_key() {{
    # The caller's OPENROUTER_API_KEY wins; without one, the key of an earlier `up` is kept.
    if [ -n "${{OPENROUTER_API_KEY:-}}" ]; then echo "$OPENROUTER_API_KEY"; return; fi
    dotenv_value "$ORCHESTRATOR/.env" OPENROUTER_API_KEY
}}

player_credential() {{
    if [ -n "${{SOFTMAX_PLAYER_CREDENTIAL:-}}" ]; then echo "$SOFTMAX_PLAYER_CREDENTIAL"; return; fi
    dotenv_value "$ORCHESTRATOR/.env" SOFTMAX_PLAYER_CREDENTIAL | sed "s/^'//; s/'$//"
}}

env_lines() {{ # env_lines <API_SERVER_HOST>: what the host home's and every profile's .env share
    echo "API_SERVER_ENABLED=true"
    echo "API_SERVER_HOST=$1"
    echo "API_SERVER_PORT=$GATEWAY_PORT"
    echo "API_SERVER_KEY=$API_KEY"
    echo "OPENROUTER_API_KEY=$MODEL_KEY"
    echo "IDE_WORKSPACE=$WORKSPACE"
    echo "IDE_PLATFORM=$PLATFORM"
    echo "IDE_RESEARCHER_PROFILES=researcher"
    echo "SOFTMAX_PLAYER_ID=$PLAYER_ID"
    if [ "$PLATFORM" = real ]; then
        echo "SOFTMAX_PLAYER_CREDENTIAL='$PLAYER_CREDENTIAL'"
        if [ -n "$COGAMES_API_URL" ]; then echo "COGAMES_API_URL=$COGAMES_API_URL"; fi
        if [ -n "$DEFAULT_COWORLD" ]; then echo "IDE_DEFAULT_COWORLD=$DEFAULT_COWORLD"; fi
    fi
}}

write_env_files() {{
    # The gateway binds its API server from the host home's .env (0.0.0.0, so the published
    # port reaches it); the watcher and the seam read the profile's .env to find it (127.0.0.1).
    say "writing $HOST_HOME/.env and both profiles' .env (one API_SERVER_KEY everywhere)"
    {{ env_lines 0.0.0.0; echo "GATEWAY_MULTIPLEX_PROFILES=true"; }} > "$HOST_HOME/.env"
    for role in orchestrator researcher; do
        env_lines 127.0.0.1 > "$HOST_HOME/profiles/$role/.env"
    done
    chmod 600 "$HOST_HOME/.env" "$HOST_HOME"/profiles/*/.env
}}

install_profile() {{ # install_profile <role>
    if [ -d "$HOST_HOME/profiles/$1" ]; then say "profile $1 is installed"; return; fi
    say "installing profile $1 from {SOURCE}/$1"
    HERMES_HOME=$HOST_HOME "$HERMES" profile install "{SOURCE}/$1" --name "$1" --yes
}}

prepare_profile() {{ # prepare_profile <role> [prepare.py flags...]
    local role=$1
    shift
    say "preparing profile $role (the first run builds the workspace environment with uv sync)"
    $AS_PROFILE "$role" "$HERMES_PYTHON" "$HOST_HOME/profiles/$role/scripts/prepare.py" \\
        --source "{SOURCE}/$role" --hermes "$HERMES" "$@"
}}

configure_fake_platform() {{
    if [ "$PLATFORM" != fake ]; then return; fi
    say "pointing the fake platform at the fakegame fixture under $UPSTREAM"
    $AS_PROFILE orchestrator python \\
        {SOURCE}/tools/local_stack/configure_fake_platform.py "$UPSTREAM"
}}

service_pid() {{ # service_pid <service> <word in its command line>: the live pid, or nothing
    local pidfile=$STACK_DIR/$1.pid pid
    if [ ! -f "$pidfile" ]; then return; fi
    pid=$(cat "$pidfile")
    if [ -r "/proc/$pid/cmdline" ] && grep -aq "$2" "/proc/$pid/cmdline"; then echo "$pid"; fi
}}

stop_service() {{ # stop_service <service> <word in its command line>
    local pid
    pid=$(service_pid "$1" "$2")
    if [ -n "$pid" ]; then
        say "stopping $1 (pid $pid)"
        kill "$pid"
        for _ in $(seq 1 60); do
            if ! kill -0 "$pid" 2> /dev/null; then break; fi
            sleep 0.5
        done
        kill -9 "$pid" 2> /dev/null || true
    fi
    rm -f "$STACK_DIR/$1.pid"
}}

wait_for() {{ # wait_for <what> <pid> <log> <probe command...>
    local what=$1 pid=$2 log=$3
    shift 3
    for _ in $(seq 1 360); do
        if "$@" 2> /dev/null; then say "$what is ready (pid $pid)"; return; fi
        if ! kill -0 "$pid" 2> /dev/null; then
            tail -n 40 "$log"
            fail "$what exited; see $log"
        fi
        sleep 0.5
    done
    tail -n 40 "$log"
    fail "$what not ready within 180 s; see $log"
}}

start_gateway() {{
    stop_service gateway gateway
    say "starting the gateway on 0.0.0.0:$GATEWAY_PORT (log $GATEWAY_LOG)"
    mkdir -p /tmp/hermes-runtime
    (
        set -a
        . "$HOST_HOME/.env"
        set +a
        export HERMES_HOME=$HOST_HOME HERMES_REAL_HOME=$HOST_HOME
        # The image's own entrypoint drops to the `hermes` user; as root (CI's --user 0) the
        # gateway refuses to start without this. Everything on this volume is root's anyway.
        export HERMES_ALLOW_ROOT_GATEWAY=1
        setsid nohup "$HERMES" gateway run >> "$GATEWAY_LOG" 2>&1 < /dev/null &
        echo $! > "$STACK_DIR/gateway.pid"
    )
    wait_for "the gateway" "$(cat "$STACK_DIR/gateway.pid")" "$GATEWAY_LOG" \\
        curl -fsS -o /dev/null -H "Authorization: Bearer $API_KEY" \\
        "http://127.0.0.1:$GATEWAY_PORT/v1/capabilities"
}}

start_seam() {{
    stop_service seam serve
    say "starting research serve on 0.0.0.0:$SEAM_PORT (log $SERVE_LOG)"
    setsid nohup $AS_PROFILE orchestrator research serve --host 0.0.0.0 --port "$SEAM_PORT" \\
        >> "$SERVE_LOG" 2>&1 < /dev/null &
    echo $! > "$STACK_DIR/seam.pid"
    wait_for "the seam server" "$(cat "$STACK_DIR/seam.pid")" "$SERVE_LOG" \\
        curl -fsS -o /dev/null "http://127.0.0.1:$SEAM_PORT/health"
}}

up() {{
    mkdir -p "$STACK_DIR" "$WORKSPACE" "$UPSTREAM"
    install_uv
    mark_checkout_safe
    API_KEY=$(api_key)
    MODEL_KEY=$(model_key)
    if [ -z "$MODEL_KEY" ]; then
        fail "OPENROUTER_API_KEY is not set and no earlier key is in $ORCHESTRATOR/.env"
    fi
    PLAYER_CREDENTIAL=""
    if [ "$PLATFORM" = real ]; then
        PLAYER_CREDENTIAL=$(player_credential)
        if [ -z "$PLAYER_CREDENTIAL" ]; then fail "SOFTMAX_PLAYER_CREDENTIAL is not set"; fi
    fi
    install_profile orchestrator
    install_profile researcher
    write_env_files
    prepare_profile orchestrator --activate-supervisor
    prepare_profile researcher
    configure_fake_platform
    start_gateway
    start_seam
    say "up: seam http://127.0.0.1:$SEAM_PORT, gateway http://127.0.0.1:$GATEWAY_PORT"
}}

stop() {{
    stop_service seam serve
    stop_service gateway gateway
}}

case "${{1:-up}}" in
    up) up ;;
    stop) stop ;;
    *) fail "usage: bootstrap.sh [up|stop]" ;;
esac
"""


if __name__ == "__main__":
    sys.exit(main())
