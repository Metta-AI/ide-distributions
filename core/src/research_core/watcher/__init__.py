"""The watcher: the Orchestrator profile's model-less cron job (plan entry 5).

It runs in the workspace environment every minute, talks to Hermes only through the runs API
(:mod:`research_core.watcher.hermes_api`) and never imports the Hermes plugin.
"""
