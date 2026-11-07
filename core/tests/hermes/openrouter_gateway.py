"""Run the gateway with OpenRouter's address pointing at the stub, so that Hermes builds the request
it builds for hosted workspaces (routing, reasoning, the output bound) and the stub receives it.

Hermes decides "this is OpenRouter" from the host name of the base URL and ignores a configured URL
for that provider, so a stub any other way would see a different request.

``STUB_RESULT_CHARS`` adds that much new text to each ``settings`` result, to make Hermes prune.
"""

import os
import runpy
import secrets
import socket

import hermes_constants

hermes_constants.OPENROUTER_BASE_URL = os.environ["STUB_OPENROUTER_URL"]
_resolve = socket.getaddrinfo


def _loopback_openrouter(host, *args, **kwargs):
    return _resolve("127.0.0.1" if host == "openrouter.ai" else host, *args, **kwargs)


socket.getaddrinfo = _loopback_openrouter


def _pad_settings_results() -> None:
    from hermes_cli import plugins

    context = plugins.PluginContext
    register_tool = context.register_tool
    result_chars = int(os.environ.get("STUB_RESULT_CHARS") or 0)

    def padded_register_tool(self, **kwargs):
        handler = kwargs["handler"]
        if kwargs.get("name") == "settings" and result_chars:
            kwargs["handler"] = lambda *args, **more: (
                handler(*args, **more) + "\n" + secrets.token_hex(result_chars // 2)
            )
        return register_tool(self, **kwargs)

    context.register_tool = padded_register_tool


_pad_settings_results()
runpy.run_module("gateway.run", run_name="__main__")
