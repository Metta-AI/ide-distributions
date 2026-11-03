"""Print the model tool-call results table at the end of a run (see ``harness.summary``)."""

from tests.model import harness


def pytest_terminal_summary(terminalreporter):
    lines = harness.summary()
    if lines:
        terminalreporter.section("model tool-call results")
        for line in lines:
            terminalreporter.write_line(line)
