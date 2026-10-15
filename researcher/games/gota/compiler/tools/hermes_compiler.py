"""Launch the isolated compiler with its owning profile's OpenRouter credential.

Runs under GOTA_HERMES_PYTHON so credential resolution uses the installed Hermes
API, including profile secret sources. The caller already selected the scratch
HERMES_HOME and removed the worker identity; only the provider key is restored.
exec preserves the process tracked by the build's timeout and worker lifecycle.
"""

import os
import sys

from tools.environments.local import served_profile_child_env

if __name__ == "__main__":
    profile_home, *arguments = sys.argv[1:]
    profile = served_profile_child_env(target_home=profile_home, inherit_credentials=True)
    environment = os.environ.copy()
    environment["OPENROUTER_API_KEY"] = profile["OPENROUTER_API_KEY"]
    os.execvpe("hermes", ["hermes", *arguments], environment)
