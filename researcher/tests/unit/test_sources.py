"""``research source fetch``: public GitHub snapshots by commit, against a local repository
standing in for GitHub (``remote``)."""

import json
from pathlib import Path

import pytest

from ide_research_core import sources
from ide_research_core.io import gitops


@pytest.fixture
def upstream(tmp_path) -> Path:
    repo = tmp_path / "upstream"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    gitops.git(repo, "config", "user.email", "t@example.com")
    gitops.git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("first\n")
    (repo / "src").mkdir()
    (repo / "src" / "rules.py").write_text("DAMAGE = 10\n")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-q", "-m", "one")
    gitops.git(repo, "tag", "v1")
    (repo / "README.md").write_text("second\n")
    gitops.git(repo, "commit", "-q", "-am", "two")
    return repo


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "host" / "profiles" / "researcher-1"
    home.mkdir(parents=True)
    return {"HERMES_HOME": str(home)}


URL = "https://github.com/Metta-AI/example-game"


def test_fetch_snapshots_a_commit_once(upstream, env):
    data = sources.fetch(URL, None, env, remote=upstream.as_uri())
    assert data["commit"] == gitops.head(upstream)
    snapshot = Path(data["path"])
    assert snapshot.name == f"example-game@{data['commit'][:12]}"
    assert (snapshot / "README.md").read_text() == "second\n"
    record = json.loads(sources.record_path(snapshot).read_text())
    assert record["repo"] == URL and record["commit"] == data["commit"]
    assert sources.fetch(URL, None, env, remote=upstream.as_uri()) == data


def test_fetch_a_tag_and_diff_two_commits_in_the_cache(upstream, env):
    old = sources.fetch(URL + ".git", "v1", env, remote=upstream.as_uri())
    new = sources.fetch(URL, "main", env, remote=upstream.as_uri())
    assert old["git_dir"] == new["git_dir"] and old["commit"] != new["commit"]
    assert (Path(old["path"]) / "README.md").read_text() == "first\n"
    changed = gitops.git(
        Path(old["git_dir"]), "diff", "--name-only", old["commit"], new["commit"]
    )
    assert changed == "README.md"


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:Metta-AI/x.git",
        "http://github.com/Metta-AI/x",
        "https://gitlab.com/Metta-AI/x",
        "https://github.com/Metta-AI",
        "https://github.com/Metta-AI/x/tree/main",
        "https://github.com/../x",
        "https://user:pw@github.com/a/b",
    ],
)
def test_only_github_https_repository_urls(url):
    with pytest.raises(sources.SourceError) as error:
        sources.parse_repo(url)
    assert error.value.reason == "bad_arguments"


@pytest.mark.parametrize("ref", ["-x", "a..b", "main.lock", "a b", "x/"])
def test_refs_are_plain_names(env, ref):
    with pytest.raises(sources.SourceError):
        sources.fetch(URL, ref, env, remote="file:///nowhere")


def test_an_unreadable_repository_fails_with_the_private_hint(env, tmp_path):
    with pytest.raises(sources.SourceError) as error:
        sources.fetch(URL, None, env, remote=(tmp_path / "missing").as_uri())
    assert error.value.reason == "fetch_failed" and "Only public repositories" in str(error.value)


def test_a_fetched_tree_cannot_redirect_the_provenance_record(upstream, env):
    (upstream / ".source.json").symlink_to("src/rules.py")
    gitops.git(upstream, "add", "-A")
    gitops.git(upstream, "commit", "-q", "-m", "plant a link")
    data = sources.fetch(URL, None, env, remote=upstream.as_uri())
    assert (Path(data["path"]) / "src" / "rules.py").read_text() == "DAMAGE = 10\n"
    assert json.loads(sources.record_path(Path(data["path"])).read_text())["commit"] == data["commit"]
