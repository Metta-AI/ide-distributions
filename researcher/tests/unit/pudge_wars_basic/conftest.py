"""Fixtures for the Pudge Wars Basic tests (helpers live in tests/support/pudge_wars_basic.py).

Native tests (`native_binary`) run the real headless binary. They are opt-in: they run when
the binary for the pinned commit is already built with a current receipt
(`python3 games/pudge-wars-basic/tools/native.py build`), or when PUDGE_NATIVE_TESTS=1, in
which case the fixture prepares (network: github.com) and builds it first. Otherwise skip.
"""
import os
import shutil

import pytest
from support.pudge_wars_basic import load_tools


@pytest.fixture(scope='session')
def tools():
    return load_tools()


@pytest.fixture(scope='session')
def native_binary(tools):
    forced = os.environ.get('PUDGE_NATIVE_TESTS') == '1'
    if shutil.which('nim') is None:
        pytest.skip('Nim 2.2.10 is not on PATH; native tests need it')
    commit = tools.native.default_commit()
    binary = tools.native.binary_path(commit)
    if not forced:
        if not binary.is_file() or not tools.native.source_dir(commit).is_dir():
            pytest.skip('headless binary not built; run tools/native.py build or set PUDGE_NATIVE_TESTS=1')
        try:
            if tools.native.current_receipt(binary, tools.native.build_inputs(commit)) is None:
                pytest.skip('headless binary receipt is stale; rebuild or set PUDGE_NATIVE_TESTS=1')
        except tools.cli_contract.ToolError as error:
            pytest.skip(f'native sources are not usable: {error}')
    path, _ = tools.native.ensure(commit)
    return path
