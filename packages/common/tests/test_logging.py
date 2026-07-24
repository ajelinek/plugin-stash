from shadetree_ai_plugins_common.logging import get_logger


def test_logger_writes_to_stderr_not_stdout(capsys):
    logger = get_logger("test-logger", to_file=False)
    logger.info("hello")
    captured = capsys.readouterr()
    assert "hello" in captured.err
    assert captured.out == ""
