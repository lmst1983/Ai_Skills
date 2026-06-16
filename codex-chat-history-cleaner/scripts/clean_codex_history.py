#!/usr/bin/env python3
"""Safely inspect and clean local Codex chat history stores."""

from __future__ import annotations

__author__ = "Robin"

import argparse
import html
import json
import shutil
import sqlite3
import tarfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class ThreadRow:
    id: str
    title: str
    archived: int
    rollout_path: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"))
    parser.add_argument("--dry-run", action="store_true", help="Inspect only. This is the default unless --execute is passed.")
    parser.add_argument("--execute", action="store_true", help="Actually delete or scrub matched records.")
    parser.add_argument("--all-archived", action="store_true", help="Select all archived threads.")
    parser.add_argument("--all-except-current", action="store_true", help="Select all threads except --current-thread-id.")
    parser.add_argument("--current-thread-id", help="Thread ID to preserve when using --all-except-current.")
    parser.add_argument("--ids", nargs="*", default=[], help="Thread IDs or unique prefixes to select.")
    parser.add_argument("--title-contains", nargs="*", default=[], help="Case-insensitive title substrings to select.")
    parser.add_argument("--scrub-file", help="Remove lines matching selected IDs or title substrings from this rollout JSONL file.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak backups before writes.")
    parser.add_argument("--show-sensitive", action="store_true", help="Include full paths and selected IDs in JSON output.")
    parser.add_argument("--report-dir", help="Directory for the generated HTML run report. Defaults to CODEX_HOME/history_cleanup_reports.")
    return parser.parse_args()


def state_dbs(codex_home: Path) -> list[Path]:
    return sorted(codex_home.glob("state_*.sqlite"))


def fetch_threads(db_path: Path) -> tuple[list[ThreadRow], Optional[str]]:
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
        if "threads" not in tables:
            return ([], "missing_threads_table")

        columns = {row[1] for row in conn.execute("pragma table_info(threads)")}
        required = {"id", "title", "archived", "rollout_path"}
        if not required.issubset(columns):
            return ([], "unsupported_threads_schema")

        order_columns = []
        if "updated_at_ms" in columns:
            order_columns.append("updated_at_ms desc")
        if "updated_at" in columns:
            order_columns.append("updated_at desc")
        order_sql = f" order by {', '.join(order_columns)}" if order_columns else ""
        rows = conn.execute(
            f"select id, title, archived, rollout_path from threads{order_sql}"
        ).fetchall()
    return ([ThreadRow(str(row[0]), str(row[1]), int(row[2]), str(row[3])) for row in rows], None)


def select_threads(rows: list[ThreadRow], args: argparse.Namespace) -> list[ThreadRow]:
    selected: list[ThreadRow] = []
    id_terms = [term.lower() for term in args.ids]
    title_terms = [term.lower() for term in args.title_contains]
    current_id = (args.current_thread_id or "").lower()

    for row in rows:
        row_id = row.id.lower()
        title = row.title.lower()
        match = False

        if args.all_archived and row.archived:
            match = True
        if args.all_except_current and row_id != current_id:
            match = True
        if id_terms and any(row_id.startswith(term) or row_id == term for term in id_terms):
            match = True
        if title_terms and any(term in title for term in title_terms):
            match = True

        if match:
            selected.append(row)

    return selected


def backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dst = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, dst)
    return dst


def backup_paths(paths: list[Path], codex_home: Path, label: str) -> Optional[Path]:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = codex_home / "history_cleanup_backups"
    backup_dir.mkdir(exist_ok=True)
    dst = backup_dir / f"{label}-{stamp}.tar.gz"
    with tarfile.open(dst, "w:gz") as archive:
        for path in existing:
            try:
                arcname = path.relative_to(codex_home)
            except ValueError:
                arcname = Path(path.name)
            archive.add(path, arcname=str(arcname))
    return dst


def delete_threads(db_path: Path, rows: list[ThreadRow]) -> int:
    ids = [row.id for row in rows]
    if not ids:
        return 0

    placeholders = ",".join("?" for _ in ids)
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"delete from threads where id in ({placeholders})", ids)
        conn.commit()
    return len(ids)


def safe_rollout_path(path_text: str, codex_home: Path) -> Optional[Path]:
    path = Path(path_text).expanduser()
    try:
        resolved_path = path.resolve(strict=False)
        resolved_home = codex_home.resolve(strict=False)
        resolved_path.relative_to(resolved_home)
    except ValueError:
        return None

    if not path.name.startswith("rollout-") or path.suffix != ".jsonl":
        return None
    return path


def remove_file(path_text: str, codex_home: Path) -> bool:
    path = safe_rollout_path(path_text, codex_home)
    if path is None:
        return False
    if path.exists() and path.is_file():
        path.unlink()
        return True
    return False


def selected_row_paths(rows: list[ThreadRow], codex_home: Path) -> list[Path]:
    paths = []
    for row in rows:
        if not row.rollout_path:
            continue
        path = safe_rollout_path(row.rollout_path, codex_home)
        if path is not None:
            paths.append(path)
    return paths


def rollout_id(path: Path) -> str:
    stem = path.stem
    return stem.removeprefix("rollout-")


def archived_rollout_files(codex_home: Path) -> list[Path]:
    archive_dir = codex_home / "archived_sessions"
    if not archive_dir.exists():
        return []
    return sorted(archive_dir.rglob("rollout-*.jsonl"))


def session_rollout_files(codex_home: Path) -> list[Path]:
    session_dir = codex_home / "sessions"
    if not session_dir.exists():
        return []
    return sorted(session_dir.rglob("rollout-*.jsonl"))


def select_archived_files(paths: list[Path], args: argparse.Namespace) -> list[Path]:
    selected: list[Path] = []
    id_terms = [term.lower() for term in args.ids]
    current_id = (args.current_thread_id or "").lower()

    for path in paths:
        file_id = rollout_id(path).lower()
        match = False

        if args.all_archived:
            match = True
        if args.all_except_current and file_id != current_id:
            match = True
        if id_terms and any(file_id.startswith(term) or file_id == term for term in id_terms):
            match = True

        if match:
            selected.append(path)

    return selected


def select_session_files(paths: list[Path], args: argparse.Namespace, exclude_paths: set[Path]) -> list[Path]:
    selected: list[Path] = []
    id_terms = [term.lower() for term in args.ids]
    current_id = (args.current_thread_id or "").lower()

    for path in paths:
        if path in exclude_paths:
            continue

        file_id = rollout_id(path).lower()
        match = False

        if args.all_except_current and file_id != current_id:
            match = True
        if id_terms and any(file_id.startswith(term) or file_id == term for term in id_terms):
            match = True

        if match:
            selected.append(path)

    return selected


def clean_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    dirs = sorted((path for path in root.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True)
    for directory in dirs:
        try:
            directory.rmdir()
        except OSError:
            pass


def clean_session_index(codex_home: Path, selected_ids: set[str], execute: bool, no_backup: bool, show_sensitive: bool) -> dict[str, object]:
    index_path = codex_home / "session_index.jsonl"
    if not index_path.exists():
        return {"exists": False, "line_count": 0, "removed": 0}

    kept: list[str] = []
    removed = 0
    total = 0

    for line in index_path.read_text(encoding="utf-8", errors="replace").splitlines(True):
        total += 1
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue

        if str(payload.get("id", "")) in selected_ids or str(payload.get("thread_id", "")) in selected_ids:
            removed += 1
        else:
            kept.append(line)

    backup_path = None
    if execute and removed:
        if not no_backup:
            backup_path = backup(index_path)
        index_path.write_text("".join(kept), encoding="utf-8")

    report: dict[str, object] = {
        "exists": True,
        "line_count": total,
        "removed": removed,
        "backup_created": bool(backup_path),
    }
    if show_sensitive and backup_path:
        report["backup"] = str(backup_path)
    return report


def scrub_file(path: Path, selected_ids: set[str], title_terms: list[str], execute: bool, no_backup: bool, show_sensitive: bool) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(path)

    needles = set(selected_ids)
    needles.update(term for term in title_terms if term)
    kept: list[str] = []
    removed = 0
    total = 0

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines(True):
        total += 1
        hay = line.lower()
        if any(needle.lower() in hay for needle in needles):
            removed += 1
        else:
            kept.append(line)

    backup_path = None
    if execute and removed:
        if not no_backup:
            backup_path = backup(path)
        path.write_text("".join(kept), encoding="utf-8")

    report: dict[str, object] = {"line_count": total, "removed": removed, "backup_created": bool(backup_path)}
    if show_sensitive and backup_path:
        report["backup"] = str(backup_path)
    return report


def display_path(path: Path, show_sensitive: bool) -> str:
    return str(path) if show_sensitive else path.name


def selector_summary(args: argparse.Namespace) -> dict[str, object]:
    return {
        "all_archived": bool(args.all_archived),
        "all_except_current": bool(args.all_except_current),
        "ids_count": len(args.ids),
        "title_contains_count": len(args.title_contains),
        "scrub_file": bool(args.scrub_file),
        "no_backup": bool(args.no_backup),
        "show_sensitive": bool(args.show_sensitive),
    }


def report_rows(mapping: dict[str, object]) -> str:
    rows = []
    for key, value in mapping.items():
        if isinstance(value, (dict, list)):
            rendered = f"<pre>{html.escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre>"
        else:
            rendered = html.escape(str(value))
        rows.append(f"<tr><th>{html.escape(str(key))}</th><td>{rendered}</td></tr>")
    return "\n".join(rows)


def report_table(title: str, mapping: dict[str, object]) -> str:
    return f"""
    <section>
      <h2>{html.escape(title)}</h2>
      <table>
        {report_rows(mapping)}
      </table>
    </section>
    """


def write_html_report(summary: dict[str, object], args: argparse.Namespace, codex_home: Path) -> Path:
    report_dir = Path(args.report_dir).expanduser() if args.report_dir else codex_home / "history_cleanup_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    report_path = report_dir / f"codex-history-cleanup-{stamp}.html"

    report_summary = dict(summary)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    report_summary["generated_at"] = generated_at

    databases = report_summary.get("databases", [])
    database_sections = ""
    if isinstance(databases, list) and databases:
        database_sections = "\n".join(
            report_table(f"Database {index + 1}", item)
            for index, item in enumerate(databases)
            if isinstance(item, dict)
        )

    selectors = selector_summary(args)
    overview = {
        "mode": report_summary.get("mode"),
        "generated_at": generated_at,
        "skill_author": __author__,
        "codex_home": report_summary.get("codex_home"),
        "report_name": report_path.name,
    }
    warnings = report_summary.get("warnings", [])
    warning_html = ""
    if isinstance(warnings, list) and warnings:
        warning_html = "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in warnings) + "</ul>"
    else:
        warning_html = "<p>No warnings.</p>"

    raw_json = html.escape(json.dumps(report_summary, ensure_ascii=False, indent=2))
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Codex History Cleanup Report</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --text: #202124;
      --muted: #5f6368;
      --border: #d7d7d2;
      --accent: #246b5f;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #171816;
        --panel: #20211f;
        --text: #ececea;
        --muted: #b8bbb5;
        --border: #3b3d39;
        --accent: #7bc4b4;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(1040px, calc(100% - 32px));
      margin: 32px auto;
    }}
    header {{
      border-bottom: 1px solid var(--border);
      margin-bottom: 24px;
      padding-bottom: 16px;
    }}
    h1 {{ font-size: 28px; margin: 0 0 8px; }}
    h2 {{ font-size: 18px; margin: 0 0 12px; }}
    p {{ color: var(--muted); margin: 0; }}
    section {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin: 16px 0;
      padding: 16px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      border-top: 1px solid var(--border);
      padding: 9px 8px;
      text-align: left;
      vertical-align: top;
    }}
    tr:first-child th, tr:first-child td {{ border-top: 0; }}
    th {{
      width: 260px;
      color: var(--muted);
      font-weight: 600;
    }}
    code, pre {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }}
    pre {{
      overflow: auto;
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    .badge {{
      display: inline-block;
      border: 1px solid var(--accent);
      color: var(--accent);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      font-weight: 600;
    }}
    ul {{ margin: 0; padding-left: 20px; }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Codex History Cleanup Report</h1>
      <p><span class="badge">{html.escape(str(report_summary.get("mode")))}</span> Generated after this cleanup script run.</p>
    </header>
    {report_table("Overview", overview)}
    {report_table("Selectors", selectors)}
    {report_table("Sessions", report_summary.get("sessions", {}) if isinstance(report_summary.get("sessions"), dict) else {})}
    {report_table("Archived Sessions", report_summary.get("archived_sessions", {}) if isinstance(report_summary.get("archived_sessions"), dict) else {})}
    {report_table("Session Index", report_summary.get("session_index", {}) if isinstance(report_summary.get("session_index"), dict) else {})}
    {database_sections}
    <section>
      <h2>Warnings</h2>
      {warning_html}
    </section>
    <section>
      <h2>Raw Summary</h2>
      <pre>{raw_json}</pre>
    </section>
  </main>
</body>
</html>
"""
    report_path.write_text(document, encoding="utf-8")
    return report_path


def emit_summary(summary: dict[str, object], args: argparse.Namespace, codex_home: Path) -> None:
    report_path = write_html_report(summary, args, codex_home)
    report_resolved = report_path.resolve(strict=False)
    summary["html_report"] = {
        "created": True,
        "name": report_path.name,
        "path": str(report_resolved),
        "url": report_resolved.as_uri(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> int:
    args = parse_args()
    execute = bool(args.execute)

    if args.dry_run and args.execute:
        raise SystemExit("Use either --dry-run or --execute, not both.")
    if args.all_except_current and not args.current_thread_id:
        raise SystemExit("--all-except-current requires --current-thread-id.")

    codex_home = Path(args.codex_home).expanduser()
    dbs = state_dbs(codex_home)

    selectors_used = args.all_archived or args.all_except_current or args.ids or args.title_contains
    summary: dict[str, object] = {
        "mode": "execute" if execute else "dry-run",
        "codex_home": str(codex_home) if args.show_sensitive else "~/.codex",
        "databases": [],
    }
    warnings: list[str] = []

    all_selected_ids: set[str] = set()
    selected_by_db: list[tuple[Path, list[ThreadRow], list[ThreadRow], Optional[str]]] = []
    for db_path in dbs:
        rows, skipped_reason = fetch_threads(db_path)
        selected = select_threads(rows, args)
        selected_by_db.append((db_path, rows, selected, skipped_reason))
        all_selected_ids.update(row.id for row in selected)

    if args.title_contains and not any(rows for _, rows, _, _ in selected_by_db):
        warnings.append("title_contains_requires_threads_table")

    archived_files = archived_rollout_files(codex_home)
    selected_archived_files = select_archived_files(archived_files, args)
    archived_selected_ids = {rollout_id(path) for path in selected_archived_files}
    selected_archived_bytes = sum(path.stat().st_size for path in selected_archived_files if path.exists())
    all_selected_ids.update(archived_selected_ids)

    db_selected_paths = {
        path
        for _, _, selected, _ in selected_by_db
        for path in selected_row_paths(selected, codex_home)
    }
    session_files = session_rollout_files(codex_home)
    selected_session_files = select_session_files(session_files, args, db_selected_paths)
    session_selected_ids = {rollout_id(path) for path in selected_session_files}
    selected_session_bytes = sum(path.stat().st_size for path in selected_session_files if path.exists())
    all_selected_ids.update(session_selected_ids)

    if not selectors_used and not args.scrub_file:
        summary["state_database_count"] = len(dbs)
        summary["sessions"] = {
            "file_count": len(session_files),
            "bytes": sum(path.stat().st_size for path in session_files if path.exists()),
        }
        summary["archived_sessions"] = {
            "file_count": len(archived_files),
            "bytes": sum(path.stat().st_size for path in archived_files if path.exists()),
        }
        summary["session_index"] = clean_session_index(codex_home, set(), False, args.no_backup, args.show_sensitive)
        emit_summary(summary, args, codex_home)
        return 0

    for db_path, rows, selected, skipped_reason in selected_by_db:
        db_report: dict[str, object] = {
            "db": display_path(db_path, args.show_sensitive),
            "has_threads_table": skipped_reason is None,
            "thread_count": len(rows),
            "selected_count": len(selected),
        }
        if skipped_reason:
            db_report["skipped_reason"] = skipped_reason
        if args.show_sensitive:
            db_report["selected_ids"] = [row.id for row in selected]

        db_backup = None
        rollout_backup = None
        if execute and selected and not args.no_backup:
            db_backup = backup(db_path)
            rollout_backup = backup_paths(selected_row_paths(selected, codex_home), codex_home, "sessions-from-db")
        db_report["backup_created"] = bool(db_backup)
        db_report["rollout_backup_created"] = bool(rollout_backup)
        if args.show_sensitive and db_backup:
            db_report["backup"] = str(db_backup)
        if args.show_sensitive and rollout_backup:
            db_report["rollout_backup"] = str(rollout_backup)
        elif rollout_backup:
            db_report["rollout_backup_name"] = rollout_backup.name

        if execute and selected:
            db_report["removed_session_files"] = sum(1 for row in selected if remove_file(row.rollout_path, codex_home))
            db_report["removed_thread_rows"] = delete_threads(db_path, selected)

        summary["databases"].append(db_report)

    session_backup = None
    if execute and selected_session_files and not args.no_backup:
        session_backup = backup_paths(selected_session_files, codex_home, "sessions")
    removed_session_files = 0
    if execute and selected_session_files:
        for path in selected_session_files:
            if path.exists() and path.is_file():
                path.unlink()
                removed_session_files += 1
        clean_empty_dirs(codex_home / "sessions")

    session_report: dict[str, object] = {
        "file_count": len(session_files),
        "selected_count": len(selected_session_files),
        "selected_bytes": selected_session_bytes,
        "removed_files": removed_session_files,
        "backup_created": bool(session_backup),
    }
    if args.show_sensitive:
        session_report["selected_ids"] = sorted(session_selected_ids)
        session_report["selected_files"] = [str(path) for path in selected_session_files]
        if session_backup:
            session_report["backup"] = str(session_backup)
    elif session_backup:
        session_report["backup_name"] = session_backup.name
    summary["sessions"] = session_report

    archived_backup = None
    if execute and selected_archived_files and not args.no_backup:
        archived_backup = backup_paths(selected_archived_files, codex_home, "archived_sessions")
    removed_archived_files = 0
    if execute and selected_archived_files:
        for path in selected_archived_files:
            if path.exists() and path.is_file():
                path.unlink()
                removed_archived_files += 1
        clean_empty_dirs(codex_home / "archived_sessions")

    archived_report: dict[str, object] = {
        "file_count": len(archived_files),
        "selected_count": len(selected_archived_files),
        "selected_bytes": selected_archived_bytes,
        "removed_files": removed_archived_files,
        "backup_created": bool(archived_backup),
    }
    if args.show_sensitive:
        archived_report["selected_ids"] = sorted(archived_selected_ids)
        archived_report["selected_files"] = [str(path) for path in selected_archived_files]
        if archived_backup:
            archived_report["backup"] = str(archived_backup)
    elif archived_backup:
        archived_report["backup_name"] = archived_backup.name
    summary["archived_sessions"] = archived_report

    summary["session_index"] = clean_session_index(codex_home, all_selected_ids, execute, args.no_backup, args.show_sensitive)

    if args.scrub_file:
        scrub_path = Path(args.scrub_file).expanduser()
        scrub_report = scrub_file(scrub_path, all_selected_ids, args.title_contains, execute, args.no_backup, args.show_sensitive)
        scrub_report["file"] = str(scrub_path) if args.show_sensitive else scrub_path.name
        summary["scrub_file"] = scrub_report

    if warnings:
        summary["warnings"] = warnings

    emit_summary(summary, args, codex_home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
