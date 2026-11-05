"""Preview or delete only historical Coworld chats with no stored league binding."""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import quote

import httpx


async def main(execute):
    key = Path("/workspace/host/.gateway-key").read_text().strip()
    async with (
        httpx.AsyncClient(base_url="http://127.0.0.1:8650") as seam,
        httpx.AsyncClient(
            base_url="http://127.0.0.1:8642", headers={"Authorization": f"Bearer {key}"}
        ) as gateway,
    ):
        response = await seam.post("/cleanup-unbound-chats", json={})
        response.raise_for_status()
        selected = response.json()["items"]
        print(json.dumps({"execute": execute, "selected": selected}))
        if not execute:
            return
        removed = []
        for item in selected:
            root = f"/p/{quote(item['profile'], safe='')}"
            if item["run_id"] is not None:
                response = await gateway.post(
                    f"{root}/v1/runs/{quote(item['run_id'], safe='')}/stop", json={}
                )
                if response.status_code != 404:
                    response.raise_for_status()
            response = await gateway.delete(f"{root}/api/sessions/{quote(item['id'], safe='')}")
            if response.status_code == 409:
                print(json.dumps({"session_id": item["id"], "state": "active_retry_required"}))
                continue
            if response.status_code != 404:
                response.raise_for_status()
            response = await seam.post("/cleanup-unbound-chats", json={"execute_ids": [item["id"]]})
            response.raise_for_status()
            removed.append(item["id"])
        print(json.dumps({"deleted": removed}))


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--execute",
    action="store_true",
    help="Delete the previewed unbound chats and native transcripts.",
)
asyncio.run(main(parser.parse_args().execute))
