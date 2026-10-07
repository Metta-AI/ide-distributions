"""Live unstructured hero ceilings: bots.nim:114-138 @5287c437."""
POLYWORLD_COMMIT = '5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd'
HARD = {'globals': 256, 'arrays': 32, 'array_cells': 4096, 'source_bytes': 65536,
        'routines': 64, 'static_instructions': 20000}
# 80% of live per-decision 100000 instructions / 250000 work units.
SOFT = {'instructions': 80000, 'work': 200000}
PRINT = {'print_bytes': 1024, 'print_events': 128}
