"""pytest plugin: the accommodations KruxMobileApp needs to run Krux's suite.

Load with ``-p krux_test_harness``, from the directory holding ``tools/``.

KruxMobileApp has no test suite of its own, so the app's ``src/krux`` is
checked by running Krux's. That works because ``src/krux`` is upstream plus a
recorded Android delta, and everything else -- ``tests/``, ``simulator/``,
``ur``/``urtypes``, the venv, the embit submodule -- can be held fixed. Any
difference between a baseline run and an app run is then attributable to the
krux package alone.

Four accommodations are needed, and all four are gaps in the CPython test
environment rather than claims about the app.

lcd gets real numbers
    The app's ``display.py`` reads ``lcd.font_size`` and ``lcd._height()``;
    upstream reads ``board.config``. A bare MagicMock leaks into module-level
    arithmetic (``FONT_HEIGHT``, ``TOTAL_LINES``) and every comparison against
    it raises TypeError. Upstream does not read these, so the values are inert
    on a baseline run. Set after the ``mp_modules`` fixture installs the mock,
    which is why this is a fixture hook and not a module-level constant.

settings.json is reset around every test
    The app's Android settings live in a real JSON file beside the process.
    Upstream keeps them behind a mocked ``sd:``/``flash:`` VFS that
    ``reset_krux_modules()`` throws away, so upstream tests never see state
    from the previous test. Without this, the app's run would drift for a
    reason that has nothing to do with the sync.

seeds.json is reset, and seeded from whatever the test injected
    The same reasoning, plus a second difference. Upstream's encrypted seeds
    are a dict read from the flash file through ``open``, so a test seeds them
    with ``patch("krux.encryption.open", mock_open(read_data=SEEDS_JSON))``.
    The app reads a real file through a ``JsonStore`` and never calls ``open``,
    so that patch did nothing: the store came up empty whatever the test
    injected, and the assertions about its contents failed against an empty
    store. The store is therefore taught to read the injection -- at the moment
    it is constructed, which is inside the test's own ``with`` block and so
    later than any fixture could have looked.

each test is interrupted if it overruns, and peak RSS is capped
    Three tests in the app tree reach ``CameraEntropy.capture()``, whose
    ``while True`` waits for a button press the fixture has already spent.
    ``unittest.mock`` records every call made inside that loop, so the hang is
    unbounded memory growth as well as a hang: left alone it reaches ~12.5 GB,
    which on a 15 GB host is not a slow run but the kernel OOM killer taking
    out unrelated processes.

    The runner deselects those three by name. The per-test SIGALRM timeout is
    the backstop, and it is the load-bearing one: a memory cap checked between
    tests cannot help when a single test allocates continuously, because the
    next test boundary never arrives. Set ``KRUX_TEST_TIMEOUT=0`` to disable
    the timeout and ``MEMCAP_MB=0`` to disable the cap.

The first three were originally applied by patching Krux's own ``conftest.py``.
That is not how it works any more: the patch depended on anchors in a file that
changes upstream, and a moved anchor meant a silently unpatched harness. Two
hooks reach the same state without the harness editing the code it is testing.
"""

import json
import os
import resource
import signal
import sys
import threading
import unittest.mock

import pytest

# --- lcd geometry ------------------------------------------------------------
#
# Same numbers the conftest patch used, so results are unchanged.

FONT_SIZE = 24
LCD_WIDTH = 480
LCD_HEIGHT = 800


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    """Give the lcd mock real dimensions once mp_modules has installed it."""
    outcome = yield
    if outcome.excinfo is not None:
        return
    if fixturedef.argname != "mp_modules":
        return
    lcd = sys.modules.get("lcd")
    if lcd is None:
        return
    lcd.font_size = FONT_SIZE
    lcd._width.return_value = LCD_WIDTH  # pylint: disable=protected-access
    lcd._height.return_value = LCD_HEIGHT  # pylint: disable=protected-access
    lcd.width.return_value = LCD_WIDTH
    lcd.height.return_value = LCD_HEIGHT


# --- settings.json -----------------------------------------------------------

SETTINGS_FILE = "settings.json"


@pytest.fixture(autouse=True)
def fresh_android_settings_file():
    """Start and end every test with no settings.json on disk.

    See the module docstring: upstream's mocked VFS resets per test and the
    app's real file would not.
    """
    path = os.path.join(os.getcwd(), SETTINGS_FILE)
    if os.path.exists(path):
        os.remove(path)
    yield
    # Guarded, not exists-then-remove: a test's own teardown can delete this
    # file first, and the check and the removal are not atomic. The check also
    # becomes wrong once another autouse fixture changes the teardown order, so
    # tolerate the file being gone rather than fail a test that already passed.
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


# --- seeds.json ----------------------------------------------------------------

# Where the app's MnemonicStorage keeps its encrypted seeds. Upstream keeps them
# in a dict built by reading the flash file through `open`, so a test seeds them
# with `patch("krux.encryption.open", mock_open(read_data=SEEDS_JSON))` -- inside
# the test body, in a `with` block. The app reads a real file through a JsonStore
# and never calls `open`, so that patch is inert: the store comes up empty
# whatever the test injected, and the assertions about its contents fail against
# an empty store.
#
# A fixture cannot help, because the patch is installed after every fixture has
# run. So the store itself is taught to read the injection: when `open` is a mock
# carrying `read_data`, the store is built from that instead of from the file.
# The tests need no change, and the two spellings of "the stored seeds" cannot
# drift apart.
#
# The file is relative, so it resolves against the process's working directory --
# the harness root, one level above. It is still reset per test, for the same
# reason settings.json is: upstream's storage is per-test by construction and the
# app's is a real file, so without a reset one test's mnemonics are the next
# test's.
SEEDS_FILE = "../seeds.json"


def _injected_seeds():
    """The data a test patched into `krux.encryption.open`, or None.

    `mock_open` has no `read_data` attribute: the data is what the handle it
    returns gives back from `read()`, which is also how upstream consumes it.
    """
    encryption = sys.modules.get("krux.encryption")
    if encryption is None:
        return None
    opener = getattr(encryption, "open", None)
    if not isinstance(opener, unittest.mock.Mock):
        return None
    try:
        read_data = opener().read()
    except (TypeError, ValueError):
        return None
    if not isinstance(read_data, str):
        return None
    # Mirrors upstream's _load_mnemonics: a document that is not a dict is
    # corrupt storage, and upstream keeps it as-is for the caller to reject.
    try:
        return json.loads(read_data)
    except ValueError:
        return read_data


@pytest.fixture(autouse=True)
def fresh_android_seeds_file(mp_modules, monkeypatch):
    """Point the app's seeds store at whatever the test injected through `open`.

    Runs after `mp_modules`, which is what makes `krux.encryption` importable.
    The replacement delegates to the real JsonStore for everything except the
    initial read, so caching and flushing behave exactly as they do for the app.
    """
    from kivy.storage import jsonstore  # noqa: PLC0415

    real_store = jsonstore.JsonStore

    class Store(real_store):
        def __init__(self, filename=None, *args, **kwargs):
            # Read the injection here, not when the fixture was set up: the
            # test installs the patch inside its own body, so at fixture time
            # `open` is still the builtin. MnemonicStorage is constructed
            # inside the `with` block, which is what makes this the moment the
            # data is available.
            data = _injected_seeds()
            if data is not None and filename == SEEDS_FILE:
                with open(SEEDS_FILE, "w", encoding="utf8") as f:
                    json.dump(data, f)
            super().__init__(filename, *args, **kwargs)

    monkeypatch.setattr(jsonstore, "JsonStore", Store)
    # krux.encryption does `from kivy.storage.jsonstore import JsonStore`, so
    # patching the source module is not enough -- the name is already bound in
    # the importing module. It may not be imported yet, hence the lookup by name.
    encryption = sys.modules.get("krux.encryption")
    if encryption is not None and hasattr(encryption, "JsonStore"):
        monkeypatch.setattr(encryption, "JsonStore", Store)

    if os.path.exists(SEEDS_FILE):
        os.remove(SEEDS_FILE)
    yield
    # A relative path against a shared working directory, and the suite runs
    # with xdist, so another worker's file can appear between the check and the
    # removal. Tolerate its absence rather than failing a test that passed.
    try:
        os.remove(SEEDS_FILE)
    except FileNotFoundError:
        pass


# --- per-test timeout --------------------------------------------------------

DEFAULT_TIMEOUT_S = 60


class TestTimeout(Exception):
    """Raised in the test's own stack when it overruns."""


def _timeout_s():
    raw = os.environ.get("KRUX_TEST_TIMEOUT", str(DEFAULT_TIMEOUT_S))
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_S


def _alarm(_signum, _frame):
    raise TestTimeout(
        "test exceeded the %d s per-test limit; it is very likely one of the "
        "tests that reaches CameraEntropy.capture() with the fixture's button "
        "sequence already spent" % _timeout_s()
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Interrupt a test that overruns, instead of letting it eat the machine.

    This is the load-bearing safety net, and it is here rather than in the
    runner's deselect list because the memory cap cannot do this job: it only
    runs between tests, while a hung test allocates continuously and reaches
    ~12.5 GB -- more than this machine has -- before the next test boundary
    arrives. On a 15 GB host that is not a slow run, it is the kernel OOM
    killer taking out unrelated processes.

    SIGALRM interrupts the test at its next bytecode boundary, so a `while
    True` waiting on input is cancelled in seconds and reported by name.
    Krux's venv has no pytest-timeout, and adding one to Krux's dependencies to
    solve a problem in this app's harness would be the wrong direction.
    """
    seconds = _timeout_s()
    if seconds <= 0 or threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


# --- memory cap --------------------------------------------------------------


def _cap_mb():
    raw = os.environ.get("MEMCAP_MB", "1500")
    try:
        return int(raw)
    except ValueError:
        return 0


def peak_mb():
    """Peak resident set size of this process, in MB.

    ru_maxrss is in kilobytes on Linux. getrusage is used rather than reading
    /proc/self/statm because Krux's tests monkeypatch builtins.open to fake the
    storage files, so a plugin that opens /proc can be handed a settings.json
    and crash the run it exists to protect. getrusage touches no file.
    """
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Check memory after each test, so the culprit is named."""
    yield
    cap = _cap_mb()
    if cap <= 0:
        return
    try:
        peak = peak_mb()
    except Exception:  # pylint: disable=broad-except
        return
    if peak > cap:
        sys.stderr.write(
            "\n\nMEMORY CAP HIT: peak RSS %d MB exceeds the %d MB cap\n"
            "  during: %s\n"
            "  node:   %s\n" % (peak, cap, os.environ.get("PYTEST_CURRENT_TEST"), item.nodeid)
        )
        sys.stderr.flush()
        os._exit(3)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    if _cap_mb() <= 0:
        return
    try:
        sys.stderr.write(
            "\npeak RSS at end of session: %d MB (cap %d MB)\n" % (peak_mb(), _cap_mb())
        )
    except Exception:  # pylint: disable=broad-except
        pass
