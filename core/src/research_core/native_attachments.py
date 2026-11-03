"""Run in Hermes's interpreter: stage files using its public attachment RPC.

The input and output are OpenAI user messages. Hermes owns file names, storage,
PDF rendering and document annotations; the profile supplies no file parser.
"""

import base64
import json
import sys
from pathlib import Path


def main() -> None:
    from gateway.platforms.base import CachedMedia
    from tui_gateway import server

    request = json.load(sys.stdin)
    workspace = Path(request["workspace"])
    workspace.mkdir(parents=True, exist_ok=True)

    def call(method, params):
        response = server.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        )
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]

    session = call("session.create", {"cwd": str(workspace)})["session_id"]
    content = []
    try:
        for part in request["message"]["content"]:
            if part["type"] not in {"file", "input_audio"}:
                content.append(part)
                continue
            file = (
                part["file"]
                if part["type"] == "file"
                else {
                    "filename": "audio." + part["input_audio"]["format"],
                    "file_data": "data:audio/"
                    + part["input_audio"]["format"]
                    + ";base64,"
                    + part["input_audio"]["data"],
                }
            )
            staged = call(
                "file.attach",
                {
                    "session_id": session,
                    "data_url": file["file_data"],
                    "name": file["filename"],
                },
            )
            path = Path(staged["path"])
            content.append(
                {
                    "type": "text",
                    "text": CachedMedia(
                        str(path), "application/octet-stream", "document", staged["name"]
                    ).context_note(),
                }
            )
            if path.suffix.lower() == ".pdf":
                pdf = call("pdf.attach", {"session_id": session, "path": str(path)})
                for page in pdf["pages"]:
                    data = base64.b64encode(Path(page["path"]).read_bytes()).decode()
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64," + data,
                            },
                        }
                    )
            elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                call("image.attach", {"session_id": session, "path": str(path)})
                mime = (
                    "image/jpeg"
                    if path.suffix.lower() in {".jpg", ".jpeg"}
                    else ("image/" + path.suffix[1:].lower())
                )
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:"
                            + mime
                            + ";base64,"
                            + base64.b64encode(path.read_bytes()).decode(),
                        },
                    }
                )
            elif path.suffix.lower() in {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"}:
                from tools.transcription_tools import transcribe_audio

                transcript = transcribe_audio(str(path), model=request["audio_model"])
                if not transcript["success"]:
                    raise RuntimeError(transcript)
                content.append({"type": "text", "text": transcript["transcript"]})
    finally:
        call("session.close", {"session_id": session})
    sys.__stdout__.write(json.dumps({**request["message"], "content": content}))


if __name__ == "__main__":
    main()
