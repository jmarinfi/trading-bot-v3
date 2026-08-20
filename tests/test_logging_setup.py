import logging

import pytest

from src.logging_setup import setup_logging


@pytest.fixture
def restore_root_logger():
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in handlers_before:
        root.addHandler(handler)
    root.setLevel(level_before)


def test_log_line_written_to_file(tmp_path, restore_root_logger):
    log_file = tmp_path / "bot.log"
    setup_logging(log_file=log_file)

    logging.getLogger("test.logger").info("hola desde el test")

    content = log_file.read_text(encoding="utf-8")
    assert "hola desde el test" in content
    assert "INFO" in content
    assert "test.logger" in content


def test_second_call_does_not_duplicate_output(tmp_path, restore_root_logger):
    log_file = tmp_path / "bot.log"
    setup_logging(log_file=log_file)

    logging.getLogger("test.logger").info("primera")
    setup_logging(log_file=log_file)
    logging.getLogger("test.logger").info("segunda")

    content = log_file.read_text(encoding="utf-8")
    assert content.count("primera") == 1
    assert content.count("segunda") == 1


def test_second_call_keeps_handler_count_stable(tmp_path, restore_root_logger):
    root = logging.getLogger()
    setup_logging(log_file=tmp_path / "bot.log")
    count = len(root.handlers)

    setup_logging(log_file=tmp_path / "bot2.log")

    assert count == len(root.handlers) == 2


def test_creates_missing_parent_dirs(tmp_path, restore_root_logger):
    log_file = tmp_path / "data" / "bot.log"
    setup_logging(log_file=log_file)

    logging.getLogger("test.logger").info("con directorio anidado")

    assert log_file.is_file()
    assert "con directorio anidado" in log_file.read_text(encoding="utf-8")
