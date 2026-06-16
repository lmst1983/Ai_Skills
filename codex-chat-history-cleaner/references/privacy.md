# Privacy Checklist

Use this checklist before sharing or publishing the skill.

- Replace real home directories with `$HOME`, `~`, or `/path/to/codex-home`.
- Replace real thread IDs with `THREAD_ID`, `THREAD_PREFIX`, or synthetic examples.
- Replace conversation titles with generic examples such as `old chat title`.
- Do not commit `auth.json`, `config.toml`, SQLite databases, `.jsonl` transcripts, generated images, screenshots, shell snapshots, or backups.
- Do not paste SQL query output that contains titles, paths, or user messages.
- Keep examples command-oriented and generic.
- Add a `.gitignore` if publishing from a workspace that may contain local Codex state.
- Review with local sensitive patterns before pushing or sharing.
