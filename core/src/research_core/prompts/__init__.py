"""The SOUL parts and their generator (design D.7).

``common/``, ``orchestrator/`` and ``researcher/`` hold Markdown parts, each starting with a
``<!-- role: ... -->`` marker. :mod:`research_core.prompts.build` assembles a role's SOUL from
them, fills the generated sections (vocabulary, records, tools) and checks the word budgets and
that no part of one role leaks into the other's prompt. ``tools/build_prompts.py`` is the CLI.
"""
