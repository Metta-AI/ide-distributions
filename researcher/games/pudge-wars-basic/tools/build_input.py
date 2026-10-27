"""Explicit caller-input rejection, distinct from a build or engine fault."""
from pathlib import Path


class InputRejected(ValueError):
    """The caller can correct this input and retry in a new build directory."""

    def __init__(self, message: str, *, path: Path | str, action: str):
        super().__init__(message)
        self.details = {'path': str(path), 'action': action}

    def to_dict(self):
        return {'reason': 'invalid_input', 'message': str(self), 'details': self.details}
