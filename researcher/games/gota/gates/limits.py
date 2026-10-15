"""Live GotA ceilings at 801749e5; structured records consume their own storage."""
POLYWORLD_COMMIT = '801749e509e888b423ba44ee2188946c22e3ba85'
LEGACY = {'globals': 256, 'arrays': 32, 'array_cells': 4096, 'source_bytes': 65536,
          'routines': 64, 'static_instructions': 20000}
HARD = LEGACY | {'globals': 512, 'arrays': 128, 'array_cells': 262144, 'source_bytes': 65535}
HOST_STORAGE = {'globals': 78, 'arrays': 57, 'array_cells': 153920}
SOFT = {'instructions': 80000, 'work': 200000}
PRINT = {'print_bytes': 1024, 'print_events': 128}
