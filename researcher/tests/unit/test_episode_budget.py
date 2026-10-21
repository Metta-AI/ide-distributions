from concurrent.futures import ThreadPoolExecutor

import pytest

from ide_research_core.budget import EpisodeBudget
from ide_research_core.softmax_tool.protocol import Refused


def test_parallel_researchers_cannot_spend_the_same_allowance(tmp_path):
    budget = EpisodeBudget(tmp_path / "budget.db", 4)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(budget.reserve, f"request-{i}", {"num_episodes": 2}) for i in range(3)
        ]
    assert sum(f.exception() is None for f in futures) == 2
    refused = next(f.exception() for f in futures if f.exception() is not None)
    assert isinstance(refused, Refused)
    assert refused.reason == "episode_budget_exhausted"
    assert budget.lower(4)["reserved"] == 4


def test_restart_and_ambiguous_response_keep_the_reservation(tmp_path):
    path = tmp_path / "budget.db"
    EpisodeBudget(path, 2).reserve("request", {"num_episodes": 2})
    resumed = EpisodeBudget(path, 2)
    resumed.reserve("request", {"num_episodes": 2})
    with pytest.raises(Refused, match="budget exhausted"):
        resumed.reserve("another", {"num_episodes": 1})
    with pytest.raises(ValueError, match="different inputs"):
        resumed.reserve("request", {"num_episodes": 1})
    resumed.release_refused("request")
    resumed.reserve("another", {"num_episodes": 1})
    assert resumed.lower(1) == {"limit": 1, "reserved": 1, "remaining": 0}
    with pytest.raises(ValueError, match="never raise"):
        resumed.lower(2)


def test_restart_keeps_user_limit_but_operator_can_grant_more_without_losing_spend(tmp_path):
    budget = EpisodeBudget(tmp_path / "budget.db", 10)
    budget.configure()
    budget.reserve("first", {"num_episodes": 2})
    budget.lower(3)
    budget.configure()
    assert budget.lower(3)["remaining"] == 1
    granted = EpisodeBudget(budget.path, 20)
    granted.configure()
    assert granted.lower(20) == {"limit": 20, "reserved": 2, "remaining": 18}
