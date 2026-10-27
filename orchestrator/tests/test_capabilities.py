from orchestrator.capabilities import Capabilities


def test_discovery_reports_presence_without_executing_or_returning_secrets(context, tmp_path):
    executable = tmp_path / "coworld"
    marker = tmp_path / "executed"
    executable.write_text(f"#!/bin/sh\ntouch {marker}\n")
    executable.chmod(0o755)
    context.env.update(PATH=str(tmp_path), RESEARCH_CLI=str(executable), SOFTMAX_PLAYER_CREDENTIAL="cga_private")
    result = Capabilities(context).host_capabilities()
    assert result["executables"]["coworld"] is True
    assert result["executables"]["softmax"] is False
    assert result["research_cli_available"] is True
    assert not marker.exists()
    assert "cga_private" not in str(result)
    assert str(executable) not in str(result)
