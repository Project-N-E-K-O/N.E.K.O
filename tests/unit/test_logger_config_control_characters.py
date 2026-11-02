import io
import logging
import sys

import pytest

from utils.logger_config import RobustLoggerConfig


pytestmark = pytest.mark.unit


@pytest.fixture
def configured_logger(tmp_path, monkeypatch):
    from utils import logger_config

    console = io.StringIO()
    monkeypatch.setattr(sys, "stdout", console)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    monkeypatch.setenv("NEKO_LOG_CONSOLE_LEVEL", "DEBUG")
    monkeypatch.setattr(logger_config, "_get_application_root", lambda: tmp_path)
    monkeypatch.setattr(RobustLoggerConfig, "_cleanup_old_logs", lambda self: None)
    config = RobustLoggerConfig(
        app_name="ControlCharacterTest", service_name="Main", log_level=logging.DEBUG
    )
    logger = config.setup_logger("ControlCharacterTest.Main")
    try:
        yield logger, console
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def _file_output(logger, level):
    handler = next(
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.FileHandler) and handler.level == level
    )
    handler.flush()
    with open(handler.baseFilename, encoding="utf-8", newline="") as log_file:
        return log_file.read().replace("\r\n", "\n")


@pytest.mark.parametrize(
    "code", [code for code in [*range(32), *range(127, 160)] if code not in (9, 10)]
)
def test_control_characters_are_escaped_in_console_and_file(configured_logger, code):
    logger, console = configured_logger
    character = chr(code)
    logger.info("external=%s", "before" + character + "after")

    for output in (console.getvalue(), _file_output(logger, logging.INFO)):
        assert character not in output
        assert f"before\\x{code:02x}after" in output


@pytest.mark.parametrize("level", [logging.DEBUG, logging.INFO, logging.ERROR])
def test_terminal_sequences_are_escaped_in_every_sink(configured_logger, level):
    logger, console = configured_logger
    payload = "\x1b[2J\x1b]0;external title\x07\x1b]52;c;dGVzdA==\x1b\\\x9b31m"
    expected = r"\x1b[2J\x1b]0;external title\x07\x1b]52;c;dGVzdA==\x1b\\x9b31m"
    logger.log(level, "external=%s", payload)

    outputs = [console.getvalue(), _file_output(logger, level)]
    if level == logging.ERROR:
        outputs.append(_file_output(logger, logging.INFO))
    for output in outputs:
        assert payload not in output
        assert expected in output
        assert not any(character in output for character in ("\x07", "\x1b", "\x9b"))


def test_unicode_multiline_and_already_escaped_text_stay_readable(configured_logger):
    logger, console = configured_logger
    payload = "中文 🐱 café\n\tindented line; literal \\x07 and \\x1b[2J"
    logger.info("external=%s", payload)

    for output in (console.getvalue(), _file_output(logger, logging.INFO)):
        assert payload in output


def test_exception_chain_and_stack_info_are_escaped(configured_logger):
    logger, console = configured_logger
    try:
        try:
            raise ValueError("cause\x07")
        except ValueError as error:
            raise RuntimeError("upstream\x1b[2J\r\x08") from error
    except RuntimeError:
        record = logger.makeRecord(
            logger.name, logging.ERROR, __file__, 1, "request failed", (), sys.exc_info()
        )
    record.stack_info = "Stack (most recent call last):\n  external frame\x9b2J"
    logger.handle(record)

    for output in (
        console.getvalue(),
        _file_output(logger, logging.INFO),
        _file_output(logger, logging.ERROR),
    ):
        assert "Traceback (most recent call last):\n" in output
        assert r"ValueError: cause\x07" in output
        assert r"RuntimeError: upstream\x1b[2J\x0d\x08" in output
        assert "Stack (most recent call last):\n  external frame\\x9b2J" in output
        assert not any(character in output for character in ("\x07", "\x1b", "\r", "\x08", "\x9b"))


def test_logger_metadata_and_propagated_records_are_escaped(configured_logger):
    logger, console = configured_logger
    child = logging.getLogger(logger.name + ".external\x07\x1b[2J")
    child.info("message\x08")

    for output in (console.getvalue(), _file_output(logger, logging.INFO)):
        assert r".external\x07\x1b[2J" in output
        assert r"message\x08" in output
        assert not any(character in output for character in ("\x07", "\x1b", "\x08"))


def test_formatter_does_not_replace_message_arguments(configured_logger):
    logger, console = configured_logger
    payload = "external\x07\x1b[2J"
    record = logger.makeRecord(
        logger.name, logging.ERROR, __file__, 1, "response=%s", (payload,), None
    )
    logger.handle(record)

    assert record.msg == "response=%s"
    assert record.args == (payload,)
    assert record.getMessage() == "response=" + payload
    for output in (
        console.getvalue(),
        _file_output(logger, logging.INFO),
        _file_output(logger, logging.ERROR),
    ):
        assert r"response=external\x07\x1b[2J" in output
