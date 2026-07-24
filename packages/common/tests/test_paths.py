from shadetree_ai_plugins_common.paths import state_dir


def test_state_dir_created(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = state_dir("demo")
    assert d.exists()
    assert d == tmp_path / ".shadetree-ai-plugins" / "demo"
