"""The distribution owns profile preparation and the gateway on the platform's durable mount."""

import asyncio
import json
import os
import secrets
from pathlib import Path

import httpx
from control import serve
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


async def main() -> None:
    bootstrap = Bootstrap.model_validate_json(Path("/run/workspace/bootstrap.json").read_text())
    HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
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
        "SOFTMAX_PLAYER_CREDENTIAL": bootstrap.credential.get_secret_value(),
        "COGAMES_API_URL": bootstrap.platform_url.removesuffix("/observatory"),
    }
    for role in PROFILES:
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
                    "SOFTMAX_PLAYER_CREDENTIAL",
                    "COGAMES_API_URL",
                    "OPENROUTER_API_KEY",
                }
            )
        )
        dotenv.chmod(0o600)
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
    profile_env = {**common, "HERMES_HOME": str(HOME / "profiles/orchestrator")}
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
        await command(RESEARCH, "workspace", "wake", env=profile_env)
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
        print("Distribution gateway and seam started", flush=True)
        async with asyncio.TaskGroup() as group:
            control = group.create_task(
                serve(
                    platform_url=bootstrap.platform_url,
                    workspace_id=bootstrap.workspace_id,
                    credential=bootstrap.credential.get_secret_value(),
                    env=profile_env,
                )
            )
            await asyncio.wait(
                [group.create_task(gateway.wait()), group.create_task(seam.wait()), control],
                return_when=asyncio.FIRST_COMPLETED,
            )
            raise RuntimeError("A distribution service exited")


if __name__ == "__main__":
    asyncio.run(main())
