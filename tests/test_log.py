import logging
import sys
import threading

import pytest

from understeer import device, log


@pytest.fixture
def session(tmp_path):
    """Session log in a temporary folder; restored afterwards."""
    hooks = sys.excepthook, threading.excepthook
    path = log.setup(tmp_path / "state" / "understeer.log")
    yield path
    for handler in list(log.log.handlers):
        log.log.removeHandler(handler)
        handler.close()
    log.log.propagate = True
    sys.excepthook, threading.excepthook = hooks


def text(path):
    for handler in log.log.handlers:
        handler.flush()
    return path.read_text()


def test_session_start_is_logged(session):
    assert session.exists()
    content = text(session)
    assert "Understeer" in content and "started" in content
    assert "kernel" in content


def test_previous_session_is_kept(tmp_path):
    path = tmp_path / "understeer.log"
    path.write_text("old session\n")
    (tmp_path / "understeer.previous.log").write_text("older session\n")
    try:
        log.setup(path)
        assert (tmp_path / "understeer.previous.log").read_text() == "old session\n"
        assert "old session" not in path.read_text()
    finally:
        for handler in list(log.log.handlers):
            log.log.removeHandler(handler)
            handler.close()
        log.log.propagate = True


def test_unwritable_folder(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert log.setup(blocker / "understeer.log") is None


def test_write_failure_reason_is_logged(session, tmp_path):
    # Writing into a folder fails like the driver rejecting a value would
    target = tmp_path / "FF"
    target.mkdir()
    assert device.write_values({str(target): 200}) == [str(target)]
    content = text(session)
    assert "Could not write FF=200" in content
    assert "WARNING" in content


def test_successful_writes_are_logged(session, tmp_path):
    (tmp_path / "SEN").write_text("900")
    assert device.write_values({str(tmp_path / "SEN"): 540}) == []
    assert "Wrote SEN=540" in text(session)


def test_failed_password_prompt_is_logged(session, monkeypatch):
    import subprocess

    def run(*args, **kwargs):
        raise subprocess.CalledProcessError(126, args[0], stderr="cancelled")
    monkeypatch.setattr(device.subprocess, "run", run)
    assert device.run_privileged("echo 1 > /x") is False
    content = text(session)
    assert "exit code 126" in content and "cancelled" in content


def test_crash_is_logged(session):
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    assert "RuntimeError: boom" in text(session)


def test_log_is_capped(tmp_path):
    handler = log._CappedFileHandler(tmp_path / "x.log", max_bytes=200)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("understeer-test-cap")
    logger.addHandler(handler)
    try:
        for i in range(100):
            logger.warning("line %d", i)
    finally:
        logger.removeHandler(handler)
        handler.close()
    content = (tmp_path / "x.log").read_text()
    assert len(content) < 400
    assert content.endswith("log full, further messages are dropped\n")


def test_describe_access(tmp_path):
    (tmp_path / "FF").write_text("70")
    assert "-rw" in device.describe_access(str(tmp_path))
    assert "not readable" in device.describe_access(str(tmp_path / "missing"))
