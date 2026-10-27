# Pudge Wars Basic test fixtures

Recorded, unmodified outputs of the real game (Coworld `pudge-wars-basic` 0.1.8,
`cow_c23845f0-444f-4d2f-864f-5764c58eda4a`, engine `Metta-AI/polyworld-pudge-wars` @
`cd8111a5ee4f2fdc38c027615b33421f6c7d2536`), collected 2026-10-26 (PDT). Tests check these
hashes before using a file.

| File | sha256 | Source |
| --- | --- | --- |
| `hosted_west_win.results.json` | `7c24d1017e176c9d451a2f3424a6db97296e3c1f6c7d33ddb5958299985399a6` | League `league_84ab9f1e-5fb8-4b62-a3ac-e49026bd12d0` episode `ereq_6a4387c9-4743-4fc7-bfd2-279e5e6212ae`, `coworld episode-results` (west 30-29, kill goal, platform seed 2002932588) |
| `hosted_east_win.results.json` | `cf41b0ec6730d6e3ac2cbb4ef121a4434999652802153616b2a308734aa31c3a` | Same league, episode `ereq_0040bb10-e1e5-4064-adf1-c1e5b317a0eb` (east 30-16, kill goal, seed 118013920) |
| `local_3v3_time_limit.results.json` | `1866d82ed631c0f80d989f6684e38d4f3db92d8f27fb0334d00b89026b9c30b4` | Native league host (`pwleague` via the game's `scripts/run_episode.py`): base:3 vs aggressive:3, seed 7, 3,000 ticks, time limit (west 12-4). `tools/match.py` reproduces it exactly. |
| `league_host_duel.results.json` | `8b1a45ef5704732bece53582b268780bcc8e691afe9a2459c61b943ab0a0d509` | `coworld run-episode` of the league image (`sha256:01caef1d…`), duel-1v1, base vs aggressive, seed 2026 (west 10-0 at tick 5526) |
| `league_host_duel.replay.gz` | `bb5809045d82ddd145abc9615afb38fe8b6d093ea2ef1497ba4919ce73739927` | The same episode's raw replay (sha256 `cd3d7bc8e12fd4668df23188d2681f2d483233eaeda818079b5d8062fa38aa45`), compressed with `gzip -9 -n` the way hosted replays are served |
| `live_engine.json` | `f9e0a255076c11cf7d603de42b8bec3b3280f8742ba70e7ae0d487f21d75619f` | The live-engine record `tools/engine.py` built from the public Observatory API (league `league_84ab9f1e…` → Coworld `cow_c23845f0…` 0.1.8) on 2026-10-26, without its fetch time. The test suite points `PUDGE_LIVE_ENGINE` at it so no test reaches the network. |
