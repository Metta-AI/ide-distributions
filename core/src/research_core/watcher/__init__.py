"""The watcher: the Orchestrator profile's model-less observation loop (plan entry 5).

``research serve`` runs it in the workspace environment every minute. It talks to Hermes only
through the runs API (:mod:`research_core.watcher.hermes_api`) and never imports the Hermes plugin.
"""
