import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import organizer as org  # noqa: E402


def make(folder: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        (folder / name).write_text(content, encoding="utf-8")


def test_sorts_by_category(tmp_path):
    make(tmp_path, {"отчёт.PDF": "a", "фото.jpg": "b", "song.mp3": "c", "x.unknown": "d"})
    org.apply(tmp_path, org.plan(tmp_path))
    assert (tmp_path / "Документы" / "отчёт.PDF").exists()
    assert (tmp_path / "Изображения" / "фото.jpg").exists()
    assert (tmp_path / "Аудио" / "song.mp3").exists()
    assert (tmp_path / "Другое" / "x.unknown").exists()


def test_no_overwrite(tmp_path):
    (tmp_path / "Документы").mkdir()
    make(tmp_path / "Документы", {"a.txt": "old"})
    make(tmp_path, {"a.txt": "new"})
    org.apply(tmp_path, org.plan(tmp_path))
    assert (tmp_path / "Документы" / "a.txt").read_text(encoding="utf-8") == "old"
    assert (tmp_path / "Документы" / "a (2).txt").read_text(encoding="utf-8") == "new"


def test_dedupe(tmp_path):
    make(tmp_path, {"a.jpg": "same", "b.jpg": "same", "c.jpg": "other"})
    org.apply(tmp_path, org.plan(tmp_path, dedupe=True))
    assert len(list((tmp_path / "Дубликаты").iterdir())) == 1
    assert len(list((tmp_path / "Изображения").iterdir())) == 2


def test_undo_restores_everything(tmp_path):
    make(tmp_path, {"a.pdf": "1", "b.zip": "2"})
    org.apply(tmp_path, org.plan(tmp_path, by_date=True))
    assert org.undo(tmp_path) == 2
    assert (tmp_path / "a.pdf").exists() and (tmp_path / "b.zip").exists()
    assert not (tmp_path / "Документы").exists()


def test_dry_run_changes_nothing(tmp_path):
    make(tmp_path, {"a.pdf": "1"})
    assert org.main([str(tmp_path), "--dry-run"]) == 0
    assert (tmp_path / "a.pdf").exists()


def test_log_written_during_moves_and_undo_after_crash(tmp_path, monkeypatch):
    import pytest
    make(tmp_path, {"a.txt": "1", "b.jpg": "2", "c.mp3": "3", "d.zip": "4"})
    moves = org.plan(tmp_path)
    real_move, calls = org.shutil.move, []

    def flaky_move(src, dst):
        calls.append(src)
        if len(calls) == 3:
            raise OSError("диск отвалился")
        return real_move(src, dst)

    monkeypatch.setattr(org.shutil, "move", flaky_move)
    with pytest.raises(OSError):
        org.apply(tmp_path, moves)
    monkeypatch.setattr(org.shutil, "move", real_move)

    import json
    history = json.loads((tmp_path / org.LOG_NAME).read_text(encoding="utf-8"))
    assert len(history) == 1 and len(history[0]["moves"]) == 2
    assert org.undo(tmp_path) == 2
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file() and p.name != org.LOG_NAME) == [
        "a.txt", "b.jpg", "c.mp3", "d.zip"]
    assert not any(p.is_file() for d in tmp_path.iterdir() if d.is_dir() for p in d.rglob("*"))
