"""Shared code of the Softmax IDE research profiles.

The base package is stdlib only, because the Hermes plugin imports it inside Hermes's own
interpreter. Modules that need the Softmax client, PyYAML or numpy run in the workspace
environment and import those libraries lazily.
"""

__version__ = "0.1.0"
