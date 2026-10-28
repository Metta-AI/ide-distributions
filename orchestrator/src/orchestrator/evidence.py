"""On-demand platform evidence, retained as private files instead of filling the conversation."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoachingList(EvidenceInput):
    policy_version_id: UUID | None = None
    episode_id: UUID | None = None
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2048)


class CoachingRead(EvidenceInput):
    coaching_session_id: str = Field(pattern=r"^csn_[0-9a-f-]{36}$")


class CoachingAnalysisRead(CoachingRead):
    analysis_id: str = Field(pattern=r"^cna_[0-9a-f-]{36}$")


class PolicyList(EvidenceInput):
    player_id: str | None = Field(default=None, pattern=r"^ply_[0-9a-f-]{36}$")
    query: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2048)


class PolicyRead(EvidenceInput):
    policy_version_id: UUID


class EpisodeList(PolicyRead):
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2048)


class EpisodeRead(EvidenceInput):
    episode_id: UUID


class Recording(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    recording_url: AnyHttpUrl | None
    video_sha256: str | None
    video_bytes: int | None


class PolicyFile(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    download_url: AnyHttpUrl
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0, le=MAX_ARTIFACT_BYTES)


class EvidenceReceipt(BaseModel):
    path: str
    sha256: str
    size_bytes: int
    media_type: str
    source: str
    next_cursor: str | None = None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Evidence:
    def __init__(self, context):
        self.home = context.home
        credential = context.env["SOFTMAX_PLAYER_CREDENTIAL"]
        self.token = json.loads(credential)["token"] if credential.startswith("{") else credential
        self.base = context.env.get("COGAMES_API_URL", "https://softmax.com/api").rstrip("/")
        parsed = urlparse(self.base)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("Platform evidence requires HTTPS")

    def _get(self, path, *, max_bytes=MAX_JSON_BYTES):
        request = Request(
            f"{self.base}/observatory/v2/{path}",
            headers={"Authorization": f"Bearer {self.token}", "User-Agent": "autoresearch"},
        )
        with build_opener(_NoRedirect).open(request, timeout=60) as response:
            content = response.read(max_bytes + 1)
            cursor = response.headers.get("X-Next-Cursor")
        if len(content) > max_bytes:
            raise ValueError(f"Evidence exceeds the {max_bytes}-byte download limit")
        return content, cursor

    def _save(self, content, *, source, media_type, next_cursor=None):
        digest = hashlib.sha256(content).hexdigest()
        directory = self.home / "cache" / "evidence"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = directory / digest
        with TemporaryDirectory(dir=directory) as temporary:
            staged = Path(temporary) / digest
            staged.write_bytes(content)
            staged.chmod(0o600)
            staged.replace(target)
        return EvidenceReceipt(
            path=str(target),
            sha256=digest,
            size_bytes=len(content),
            media_type=media_type,
            source=source,
            next_cursor=next_cursor,
        ).model_dump()

    def _json(self, path):
        content, cursor = self._get(path)
        # Validate the response before publishing a readable, paginatable local file.
        content = (json.dumps(json.loads(content), indent=2, ensure_ascii=False) + "\n").encode()
        return self._save(content, source=path, media_type="application/json", next_cursor=cursor)

    def _download(self, url, *, digest, size, source, media_type):
        if url.scheme != "https" and not (
            url.scheme == "http"
            and url.host in {"localhost", "127.0.0.1", "::1"}
            and urlparse(self.base).scheme == "http"
        ):
            raise ValueError("Signed evidence downloads require HTTPS")
        if not 0 <= size <= MAX_ARTIFACT_BYTES:
            raise ValueError("Evidence exceeds the 256 MiB download limit")
        # The object store receives no platform bearer credential.
        request = Request(str(url), headers={"User-Agent": "autoresearch"})
        with urlopen(request, timeout=120) as response:
            content = response.read(size + 1)
        if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("Evidence size or SHA-256 verification failed")
        return self._save(content, source=source, media_type=media_type)

    def coaching_sessions(self, **args):
        request = CoachingList(**args)
        path = "coaching-sessions"
        if request.episode_id is not None:
            if request.policy_version_id is not None:
                raise ValueError("Filter by episode or policy version, not both")
            path = f"episodes/{request.episode_id}/coaching-sessions"
        query = request.model_dump(mode="json", exclude_none=True, exclude={"episode_id"})
        return self._json(f"{path}?{urlencode(query)}")

    def coaching_session(self, **args):
        request = CoachingRead(**args)
        return self._json(f"coaching-sessions/{request.coaching_session_id}")

    def coaching_analysis(self, **args):
        request = CoachingAnalysisRead(**args)
        return self._json(
            f"coaching-sessions/{request.coaching_session_id}/analyses/{request.analysis_id}"
        )

    def coaching_recording(self, **args):
        request = CoachingRead(**args)
        path = f"coaching-sessions/{request.coaching_session_id}"
        content, _ = self._get(path)
        recording = Recording.model_validate_json(content)
        if recording.recording_url is None:
            return {"status": "not_available", "source": path, "reason": "No completed recording"}
        if recording.video_sha256 is None or recording.video_bytes is None:
            raise ValueError("Recording metadata does not contain a size and SHA-256")
        return self._download(
            recording.recording_url,
            digest=recording.video_sha256,
            size=recording.video_bytes,
            source=path,
            media_type="video/webm",
        )

    def policy_versions(self, **args):
        request = PolicyList(**args)
        query = request.model_dump(mode="json", exclude_none=True, exclude={"query"})
        query["mine"] = "true"
        if request.query is not None:
            query["q"] = request.query
        return self._json(f"policy-versions?{urlencode(query)}")

    def policy_file(self, **args):
        request = PolicyRead(**args)
        path = f"policy-files/{request.policy_version_id}/download"
        content, _ = self._get(path)
        file = PolicyFile.model_validate_json(content)
        return self._download(
            file.download_url,
            digest=file.content_hash,
            size=file.size_bytes,
            source=path,
            media_type="application/octet-stream",
        )

    def policy_episodes(self, **args):
        request = EpisodeList(**args)
        query = request.model_dump(mode="json", exclude_none=True, exclude={"policy_version_id"})
        return self._json(
            f"policy-versions/{request.policy_version_id}/episodes?{urlencode(query)}"
        )

    def episode(self, **args):
        request = EpisodeRead(**args)
        return self._json(f"episodes/{request.episode_id}")

    def episode_replay(self, **args):
        request = EpisodeRead(**args)
        path = f"episodes/{request.episode_id}/bundle?include=replay,results,error_info"
        content, _ = self._get(path, max_bytes=MAX_ARTIFACT_BYTES)
        return self._save(content, source=path, media_type="application/zip")
