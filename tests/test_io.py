import json

import pytest

from datafab_io import atomic_write_json, backup_path, load_json_lenient


def test_lenient_loader_accepts_single_backslashes(tmp_path):
    f = tmp_path / "cfg.json"
    # "p" uses single backslashes (invalid JSON), "q" is properly escaped.
    f.write_text(r'{"p": "C:\Users\you\input.gds", "q": "C:\\ok\\x.gds"}', encoding="utf-8")
    data = load_json_lenient(f)
    assert data["p"] == r"C:\Users\you\input.gds"
    assert data["q"] == r"C:\ok\x.gds"


def test_atomic_write_and_backup(tmp_path):
    f = tmp_path / "db.json"
    atomic_write_json(f, {"v": 1}, backup=True)
    assert not backup_path(f).exists()  # nothing to back up yet
    atomic_write_json(f, {"v": 2}, backup=True)
    assert json.loads(f.read_text())["v"] == 2
    assert json.loads(backup_path(f).read_text())["v"] == 1
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_failed_write_keeps_original(tmp_path):
    f = tmp_path / "db.json"
    atomic_write_json(f, {"v": 1})
    with pytest.raises(TypeError):
        atomic_write_json(f, {"v": object()})  # not JSON serialisable
    assert json.loads(f.read_text()) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["db.json"]
