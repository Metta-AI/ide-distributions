"""Limits and step names the tool schemas (``plugin``, in the Hermes process) and the operations
(``program``, in the profile venv) share. Standard library only: the Hermes process imports it."""

POLICY_NAME_MAX = 64  # the platform's policy name limit (observatory_protocol stats.py)
# When a name is taken by someone else, the platform's own rule (policy_versions.py
# default_policy_names) is the name, a hyphen and the first 8 characters of the player id.
SUFFIX_LENGTH = 8
NAME_MAX = POLICY_NAME_MAX - SUFFIX_LENGTH - 1
MAX_EPISODES = 20
DEFAULT_EPISODES = 5
MAX_WAIT_SECONDS = (
    480  # practice_status waits at most this long, leaving room in the 840 s call for the download
)
# The steps of one user request, each a step on its task card; the Mind's step names (rungs) are
# fixed by the records schema (bodies.GoalStep), so each step is filed under the nearest one.
STEPS = {
    "understand": ("reflect", "Understand what you asked for"),
    "change": ("implement", "Change the program"),
    "upload": ("compile", "Save it as a new version"),
    "practice": ("tryout", "Play practice games"),
    "result": ("screen", "Read the results"),
    "release": ("submit", "Ask before releasing"),
}
STEP_STATUS = {"running": "running", "done": "ok", "failed": "failed", "skipped": "skipped"}
