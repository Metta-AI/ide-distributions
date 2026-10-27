"""Fixtures for the Pudge Wars Basic tests (helpers live in tests/support/pudge_wars_basic.py).

No test reaches the network: every tool run (in-process or subprocess) sees
PUDGE_LIVE_ENGINE pointing at the recorded live-engine fixture, and COGAMES_API_URL at a
closed local port so an accidental lookup fails fast instead of calling softmax.com.

Native tests (`native_binaries`) run the real binaries. They are opt-in: they run when both
binaries for the fixture's commit are already built with current receipts
(`python3 games/pudge-wars-basic/tools/native.py build` and `... build --target pudge_league`),
or when PUDGE_NATIVE_TESTS=1, in which case the fixture prepares (network: github.com) and
builds them first. Otherwise they skip.
"""
import os
import shutil

import pytest
from support.pudge_wars_basic import FIXTURES, load_tools

LIVE_FIXTURE = FIXTURES / 'live_engine.json'


@pytest.fixture(scope='module', autouse=True)
def offline_engine():
    # Module scope: set for these tests only, restored before other directories' tests run.
    saved = {name: os.environ.get(name) for name in ('PUDGE_LIVE_ENGINE', 'COGAMES_API_URL')}
    os.environ['PUDGE_LIVE_ENGINE'] = str(LIVE_FIXTURE)
    os.environ['COGAMES_API_URL'] = 'http://127.0.0.1:9/api'
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(scope='module')
def tools(offline_engine):
    return load_tools()


@pytest.fixture(scope='module')
def native_binaries(tools):
    """{target: path} for pudge_headless and pudge_league at the fixture engine's commit."""
    forced = os.environ.get('PUDGE_NATIVE_TESTS') == '1'
    if shutil.which('nim') is None:
        pytest.skip('Nim 2.2.10 is not on PATH; native tests need it')
    commit = tools.native.default_commit()
    found = {}
    for target in tools.native.TARGETS:
        binary = tools.native.binary_path(commit, target)
        if not forced:
            if not binary.is_file() or not tools.native.source_dir(commit).is_dir():
                pytest.skip(f'{target} not built; run tools/native.py build --target {target} '
                            'or set PUDGE_NATIVE_TESTS=1')
            try:
                if tools.native.current_receipt(binary, tools.native.build_inputs(commit, target)) is None:
                    pytest.skip(f'{target} receipt is stale; rebuild or set PUDGE_NATIVE_TESTS=1')
            except tools.cli_contract.ToolError as error:
                pytest.skip(f'native sources are not usable: {error}')
        found[target] = tools.native.ensure(commit, target)[0]
    return found


@pytest.fixture(scope='module')
def native_binary(native_binaries):
    return native_binaries['pudge_headless']
