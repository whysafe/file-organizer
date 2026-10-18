"""Разбирает папку (например, «Загрузки») по категориям: документы, картинки, видео, архивы и т. д.

- Режим просмотра (--dry-run) показывает, что будет сделано, ничего не трогая
- Одинаковые имена не перезаписываются: добавляется « (2)», « (3)»
- Точные дубликаты (по SHA-256) можно отправить в папку «Дубликаты»
- Каждый запуск пишет журнал, по которому можно всё вернуть командой undo

Примеры:
    python organizer.py ~/Downloads --dry-run
    python organizer.py ~/Downloads --by-date --dedupe
    python organizer.py ~/Downloads --undo
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

CATEGORIES = {
    "Документы": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".xls", ".xlsx", ".csv", ".ppt", ".pptx", ".djvu", ".epub"},
    "Изображения": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".svg", ".tiff", ".psd", ".raw"},
    "Видео": {".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv"},
    "Аудио": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"},
    "Архивы": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Программы": {".exe", ".msi", ".dmg", ".pkg", ".deb", ".apk", ".appimage"},
    "Код": {".py", ".js", ".ts", ".html", ".css", ".json", ".sql", ".java", ".kt", ".cpp", ".c", ".go", ".php", ".sh", ".ipynb"},
}
OTHER = "Другое"
DUPES = "Дубликаты"
LOG_NAME = ".organizer-log.json"


def category_for(path: Path) -> str:
    ext = path.suffix.lower()
    return next((name for name, exts in CATEGORIES.items() if ext in exts), OTHER)


def free_name(target: Path, taken: set[Path]) -> Path:
    """Подбирает свободное имя: file.pdf → file (2).pdf, учитывая и диск, и уже запланированные переносы."""
    candidate, n = target, 2
    while candidate.exists() or candidate in taken:
        candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
        n += 1
    return candidate


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def plan(folder: Path, by_date: bool = False, dedupe: bool = False) -> list[tuple[Path, Path]]:
    managed = set(CATEGORIES) | {OTHER, DUPES}
    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".") and p.name != LOG_NAME)
    moves: list[tuple[Path, Path]] = []
    taken: set[Path] = set()
    seen_hashes: dict[str, Path] = {}
    if dedupe:  # уже разложенные файлы тоже участвуют в поиске дубликатов
        for sub in managed - {DUPES}:
            for p in (folder / sub).rglob("*") if (folder / sub).is_dir() else []:
                if p.is_file():
                    seen_hashes.setdefault(file_hash(p), p)
    for f in files:
        if dedupe:
            digest = file_hash(f)
            if digest in seen_hashes:
                dest = free_name(folder / DUPES / f.name, taken)
                moves.append((f, dest)); taken.add(dest)
                continue
            seen_hashes[digest] = f
        sub = folder / category_for(f)
        if by_date:
            sub = sub / datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m")
        dest = free_name(sub / f.name, taken)
        moves.append((f, dest)); taken.add(dest)
    return moves


def write_log(log: Path, history: list) -> None:
    # Пишем во временный файл и подменяем: журнал не останется обрезанным при сбое записи
    tmp = log.with_name(log.name + ".tmp")
    tmp.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(log)


def apply(folder: Path, moves: list[tuple[Path, Path]]) -> None:
    log = folder / LOG_NAME
    history = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
    done: list[list[str]] = []
    history.append({"at": datetime.now().isoformat(timespec="seconds"), "moves": done})
    for src, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        done.append([str(src), str(dst)])
        # Журнал обновляется после каждого переноса: если запуск оборвётся посередине,
        # undo всё равно вернёт уже перемещённые файлы
        write_log(log, history)


def undo(folder: Path) -> int:
    log = folder / LOG_NAME
    if not log.exists():
        return 0
    history = json.loads(log.read_text(encoding="utf-8"))
    if not history:
        return 0
    last = history.pop()
    restored = 0
    for src, dst in reversed(last["moves"]):
        if Path(dst).exists() and not Path(src).exists():
            shutil.move(dst, src)
            restored += 1
    for d in sorted({Path(dst).parent for _, dst in last["moves"]}, key=lambda p: len(p.parts), reverse=True):
        while d != folder and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    write_log(log, history)
    return restored


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Раскладывает файлы в папке по категориям")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="только показать план")
    ap.add_argument("--by-date", action="store_true", help="внутри категорий разложить по месяцам")
    ap.add_argument("--dedupe", action="store_true", help="точные копии в папку «Дубликаты»")
    ap.add_argument("--undo", action="store_true", help="отменить последний запуск")
    args = ap.parse_args(argv)
    folder = args.folder.expanduser().resolve()
    if not folder.is_dir():
        print(f"Папка не найдена: {folder}", file=sys.stderr)
        return 2
    if args.undo:
        print(f"Возвращено файлов: {undo(folder)}")
        return 0
    moves = plan(folder, args.by_date, args.dedupe)
    if not moves:
        print("Разбирать нечего: в корне папки нет файлов.")
        return 0
    stats = Counter(dst.relative_to(folder).parts[0] for _, dst in moves)
    for src, dst in moves[:30]:
        print(f"{src.name}  →  {dst.relative_to(folder)}")
    if len(moves) > 30:
        print(f"… и ещё {len(moves) - 30}")
    print("\nИтого: " + ", ".join(f"{k}: {v}" for k, v in stats.most_common()))
    if args.dry_run:
        print("Это пробный запуск, файлы не перемещены. Уберите --dry-run, чтобы применить.")
        return 0
    apply(folder, moves)
    print(f"Готово. Перемещено {len(moves)}. Отменить: python organizer.py \"{folder}\" --undo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
