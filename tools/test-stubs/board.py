"""A placeholder `board` module, for test collection only.

Krux's suite installs the real per-device `board` in the m5stickv/amigo/dock/...
fixtures (tests/conftest.py), which then call reset_krux_modules() so krux is
re-imported against that device. Nothing provides `board` at *collection* time,
which is fine for upstream Krux because its settings modules do not import
board at module scope.

KruxMobileApp's Android modifications add `import board` to krux_settings.py
and settings.py, and test_firmware.py imports krux.settings at module scope, so
collection needs something named `board` to exist.

This module deliberately provides no attributes: reading one raises. If module
level code ever starts depending on the real device config, that should fail
loudly here rather than be papered over by a MagicMock's truthiness. Inside
tests the device fixtures replace this outright.
"""


def __getattr__(name):
    raise RuntimeError(
        "board.%s was read at import time. The per-device `board` is installed "
        "by the test fixtures; module level code must not depend on it." % name
    )
