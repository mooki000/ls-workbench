"""The registry under load: a busy database, and the transient errors SQLite gives when the machine is overloaded (a real session lost its
launch job to one: `backend_failed` from the registry, the run interrupted, every later call unavailable). Synthetic files only."""
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from src.errors import WorkbenchError
from src.registry import Registry


@pytest.fixture
def registry(tmp_path):
    root = tmp_path / "state"
    reg = Registry(root)
    reg.open()
    return reg


def write(reg):
    with reg._db(write=True) as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES ('probe', 'x')")


def test_a_write_waits_out_a_lock_held_for_longer_than_five_seconds(registry):
    # The wait for a busy database was 5 seconds; a stalled machine kept the lock longer and the write failed as `busy`, which the
    # launch job did not survive. Heavy disk load is exactly when a registry write is slow.
    holder = sqlite3.connect(registry.path, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    threading.Timer(6.5, lambda: holder.execute("COMMIT")).start()
    started = time.monotonic()
    write(registry)
    assert time.monotonic() - started >= 6.0
    holder.close()


class Flaky:
    """A connection whose first BEGIN fails the way an overloaded WAL database does (SQLITE_PROTOCOL: the WAL index lock was not taken)."""

    def __init__(self, real, failures, code):
        self.real, self.failures, self.code = real, failures, code

    def __getattr__(self, name):
        return getattr(self.real, name)

    @property
    def row_factory(self):
        return self.real.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self.real.row_factory = value

    def execute(self, sql, *args):
        if sql.startswith("BEGIN") and self.failures > 0:
            self.failures -= 1
            error = sqlite3.OperationalError("locking protocol")
            error.sqlite_errorcode = self.code
            raise error
        return self.real.execute(sql, *args)


@pytest.mark.parametrize("code", [15, 261, 517, 773, 5, 6])
def test_transient_sqlite_errors_are_retried_not_failed(registry, monkeypatch, code):
    real = sqlite3.connect
    attempts = []

    def connect(*args, **kwargs):
        attempts.append(1)
        return Flaky(real(*args, **kwargs), 2 if len(attempts) == 1 else 0, code)
    monkeypatch.setattr("src.registry.sqlite3.connect", connect)
    write(registry)
    assert len(attempts) >= 2


def test_an_error_that_is_not_transient_still_fails_without_a_retry(registry, monkeypatch):
    real = sqlite3.connect
    attempts = []

    def connect(*args, **kwargs):
        attempts.append(1)
        return Flaky(real(*args, **kwargs), 99, 10)    # SQLITE_IOERR
    monkeypatch.setattr("src.registry.sqlite3.connect", connect)
    with pytest.raises(WorkbenchError) as raised:
        write(registry)
    assert raised.value.envelope.code == "backend_failed" and len(attempts) == 1
