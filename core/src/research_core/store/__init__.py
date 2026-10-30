"""The store library: SQLite per Coworld plus a small workspace store (design §5, Appendix B).

The store is fixed machinery: numbered migrations define its shape, every library write is one
``BEGIN IMMEDIATE`` transaction whose ``change_log`` rows carry a token, ``events`` is
append-only, and ``Store.open`` refuses a store whose schema differs from the release's
migrations. Everything here is stdlib only: the Hermes plugin imports it inside Hermes's own
interpreter.

Modules: ``db`` (Store, Tx), ``migrate`` (migrations, schema check, rebuild), ``events`` (Event
and the watcher's handled-events bookkeeping), ``actors``, ``errors``, ``registry`` (the field
registry) and ``_common`` (small helpers shared by the record modules).
"""
