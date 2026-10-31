"""The distribution owns profile preparation and the gateway on the platform's durable mount."""

import asyncio
import json
import os
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from control import serve
from inference import Availability
from inference import maintain as maintain_inference
from pydantic import BaseModel, ConfigDict, SecretStr


class Bootstrap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    platform_url: str
    credential: SecretStr


HERMES = "/opt/hermes/.venv/bin/hermes"
PYTHON = "/opt/hermes/.venv/bin/python"
RESEARCH = "/opt/workspace-env/bin/research"
SOURCE = Path("/opt/distribution")
HOME = Path("/workspace/host")
PROFILES = ("orchestrator", "researcher")


async def command(*args: str, env: dict[str, str]) -> None:
    process = await asyncio.create_subprocess_exec(*args, env=env)
    code = await process.wait()
    if code:
        raise RuntimeError(f"{args[0]} exited {code}")


class StartupTiming(BaseModel):
    event: Literal["workspace_startup"] = "workspace_startup"
    outcome: Literal["started", "passed"]
    workspace_id: str
    boot_id: str
    phase: str
    elapsed_seconds: float
    timestamp: datetime
    role: str | None = None


async def main() -> None:
    started = time.monotonic()
    boot_id = uuid4().hex
    inference = Availability()
    bootstrap = Bootstrap.model_validate_json(Path("/run/workspace/bootstrap.json").read_text())

    def report(
        phase: str,
        since: float,
        *,
        role: str | None = None,
        outcome: Literal["started", "passed"] = "passed",
    ) -> None:
        print(
            StartupTiming(
                workspace_id=bootstrap.workspace_id,
                boot_id=boot_id,
                phase=phase,
                outcome=outcome,
                elapsed_seconds=round(time.monotonic() - since, 3),
                timestamp=datetime.now(UTC),
                role=role,
            ).model_dump_json(exclude_none=True),
            flush=True,
        )

    HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    lease_file = HOME / ".inference-lease"
    lease_file.unlink(missing_ok=True)
    Path("/tmp/hermes-runtime").mkdir(exist_ok=True, mode=0o700)
    key_file = HOME / ".gateway-key"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(32))
        key_file.chmod(0o600)
    key = key_file.read_text()
    common = {
        **os.environ,
        "API_SERVER_ENABLED": "true",
        "API_SERVER_HOST": "127.0.0.1",
        "API_SERVER_PORT": "8642",
        "API_SERVER_KEY": key,
        "GATEWAY_MULTIPLEX_PROFILES": "true",
        "IDE_RESEARCHER_PROFILES": "researcher",
        "IDE_INFERENCE_LEASE_FILE": str(lease_file),
        "SOFTMAX_PLAYER_CREDENTIAL": bootstrap.credential.get_secret_value(),
        "COGAMES_API_URL": bootstrap.platform_url.removesuffix("/observatory"),
    }
    # Multiplexed gateway startup reads the host profile's dotenv after scoping its environment.
    host_dotenv = HOME / ".env"
    host_dotenv.write_text(
        "".join(
            f"{name}={json.dumps(common[name])}\n"
            for name in (
                "API_SERVER_ENABLED",
                "API_SERVER_HOST",
                "API_SERVER_PORT",
                "API_SERVER_KEY",
                "GATEWAY_MULTIPLEX_PROFILES",
            )
        )
    )
    host_dotenv.chmod(0o600)
    for role in PROFILES:
        phase_started = time.monotonic()
        report("profile_install", phase_started, role=role, outcome="started")
        home = HOME / "profiles" / role
        if home.exists():
            await command(HERMES, "profile", "update", role, "--yes", env=common)
        else:
            await command(
                HERMES,
                "profile",
                "install",
                str(SOURCE / role),
                "--name",
                role,
                "--yes",
                env=common,
            )
        report("profile_install", phase_started, role=role)
        profile_env = {**common, "HERMES_HOME": str(home)}
        # The watcher reads the gateway key from its profile dotenv, not its cron environment.
        dotenv = home / ".env"
        dotenv.write_text(
            "".join(
                f"{name}={json.dumps(value)}\n"
                for name, value in profile_env.items()
                if name
                in {
                    "API_SERVER_KEY",
                    "API_SERVER_HOST",
                    "API_SERVER_PORT",
                    "IDE_WORKSPACE",
                    "IDE_WORKSPACE_ENV",
                    "IDE_RESEARCHER_PROFILES",
                    "IDE_INFERENCE_LEASE_FILE",
                    "SOFTMAX_PLAYER_CREDENTIAL",
                    "COGAMES_API_URL",
                    "OPENROUTER_API_KEY",
                }
            )
        )
        dotenv.chmod(0o600)
        phase_started = time.monotonic()
        report("profile_prepare", phase_started, role=role, outcome="started")
        await command(
            PYTHON,
            str(home / "scripts/prepare.py"),
            "--source",
            str(SOURCE / role),
            "--hermes",
            HERMES,
            "--activate-supervisor",
            env=profile_env,
        )
        report("profile_prepare", phase_started, role=role)
    profile_env = {**common, "HERMES_HOME": str(HOME / "profiles/orchestrator")}
    phase_started = time.monotonic()
    report("gateway_ready", phase_started, outcome="started")
    gateway = await asyncio.create_subprocess_exec(HERMES, "gateway", "run", env=common)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8642", headers={"Authorization": f"Bearer {key}"}
    ) as client:
        async with asyncio.timeout(180):
            while True:
                if gateway.returncode is not None:
                    raise RuntimeError(f"Gateway exited {gateway.returncode}")
                result = (
                    await asyncio.gather(client.get("/v1/capabilities"), return_exceptions=True)
                )[0]
                if isinstance(result, httpx.Response) and result.is_success:
                    break
                await asyncio.sleep(1)
        report("gateway_ready", phase_started)
        phase_started = time.monotonic()
        report("application_wake", phase_started, outcome="started")
        await command(RESEARCH, "workspace", "wake", env=profile_env)
        report("application_wake", phase_started)
        phase_started = time.monotonic()
        report("application_ready", phase_started, outcome="started")
        seam = await asyncio.create_subprocess_exec(
            RESEARCH, "serve", "--host", "0.0.0.0", env=profile_env
        )
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8650") as seam_client:
            async with asyncio.timeout(30):
                while True:
                    result = (
                        await asyncio.gather(seam_client.get("/health"), return_exceptions=True)
                    )[0]
                    if isinstance(result, httpx.Response) and result.is_success:
                        break
                    await asyncio.sleep(1)
        report("application_ready", phase_started)
        report("startup_total", started)
        async with asyncio.TaskGroup() as group:
            group.create_task(
                maintain_inference(
                    platform_url=bootstrap.platform_url,
                    workspace_id=bootstrap.workspace_id,
                    credential=bootstrap.credential.get_secret_value(),
                    homes=(HOME, *(HOME / "profiles" / role for role in PROFILES)),
                    availability=inference,
                    lease_file=lease_file,
                )
            )
            control = group.create_task(
                serve(
                    platform_url=bootstrap.platform_url,
                    workspace_id=bootstrap.workspace_id,
                    credential=bootstrap.credential.get_secret_value(),
                    env=profile_env,
                    inference=inference,
                )
            )
            await asyncio.wait(
                [group.create_task(gateway.wait()), group.create_task(seam.wait()), control],
                return_when=asyncio.FIRST_COMPLETED,
            )
            raise RuntimeError("A distribution service exited")


if __name__ == "__main__":
    asyncio.run(main())
