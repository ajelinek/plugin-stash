from shadetree_ai_plugins_common.fsutil import check_path


def test_check_path_missing(tmp_path):
    result = check_path(str(tmp_path / "does-not-exist"))
    assert result["exists"] is False


def test_check_path_existing_file(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("hello")
    result = check_path(str(f))
    assert result["exists"] is True
    assert result["is_file"] is True
    assert result["is_dir"] is False
    assert result["size_bytes"] == 5
    assert result["readable"] is True


def test_check_path_existing_dir(tmp_path):
    result = check_path(str(tmp_path))
    assert result["exists"] is True
    assert result["is_dir"] is True
