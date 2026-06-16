---
name: codex-chat-history-cleaner
description: Safely inspect and clean local Codex chat history, archived sessions, search-index remnants, and self-referential cleanup traces. Use when the user asks why deleted or archived Codex chats still appear in search, wants to delete local chat records, scrub current-session references to old chats, or prepare a privacy-preserving Codex history cleanup workflow.
---

# Codex Chat History Cleaner

## Attribution

Author: Robin

## Core Rule

Treat Codex chat cleanup as destructive local-state maintenance. Start with a dry run, identify every storage surface, explain counts and categories, and request explicit user approval before executing deletion or scrubbing.

Do not print private titles, usernames, full local paths, full thread IDs, transcript snippets, or sensitive search strings into the current conversation when the user's goal is search cleanup. Repeating sensitive matches can make the current session become the new search hit.

## Storage Surfaces

Check these local Codex locations under `CODEX_HOME` or `~/.codex`:

- `state_*.sqlite`: thread metadata; `threads.archived=1` means archived, not deleted.
- `sessions/**/rollout-*.jsonl`: normal chat transcript files.
- `archived_sessions/**/rollout-*.jsonl`: archived transcript files.
- `session_index.jsonl`: lightweight search/list index.
- `generated_images/` and related artifact directories: optional per-thread artifacts.
- `logs_*.sqlite`: diagnostic logs; inspect only if deleted sessions still appear after primary stores are clean.

## Workflow

1. Locate `CODEX_HOME`, usually `$HOME/.codex`.
2. Run one bundled-script dry run with selectors whenever possible. The script emits a privacy-safe count summary by default and tolerates `state_*.sqlite` files without a `threads` table by falling back to rollout files.
3. Choose selectors:
   - Use `--all-archived` for archived sessions.
   - Use `--ids THREAD_PREFIX` when thread IDs or unique prefixes are known; this can match database rows plus `sessions` and `archived_sessions` rollout filenames.
   - Use `--title-contains TEXT` sparingly; avoid echoing sensitive titles in chat. Title matching requires a usable `threads` table, because rollout files do not carry reliable title metadata.
   - Use `--all-except-current --current-thread-id THREAD_ID` only when the user asks to remove all historical search results while preserving the active thread; this can select database rows, non-current `sessions` rollout files, and archived rollout files.
4. Review only counts and categories from the dry run. Do not run extra schema or filesystem probes unless the script reports an actionable error.
5. Execute only after explicit approval by adding `--execute`.
6. Verify with the same dry-run command. Use direct SQLite count queries only if the script reports a database cleanup mismatch.
7. Every bundled-script run generates an HTML report. Give the user the report link/path from the `html_report` JSON field after the run.
8. Tell the user to restart Codex if UI search still displays stale cached results.

## Fast Path For Archived Cleanup

For "delete archived chats" requests, prefer this two-command flow:

```bash
python3 scripts/clean_codex_history.py --codex-home "$HOME/.codex" --all-archived --dry-run
```

After the user explicitly approves:

```bash
python3 scripts/clean_codex_history.py --codex-home "$HOME/.codex" --all-archived --execute
python3 scripts/clean_codex_history.py --codex-home "$HOME/.codex" --all-archived --dry-run
```

The script treats both database rows with `threads.archived=1` and `archived_sessions/**/rollout-*.jsonl` as archived cleanup targets. If a `state_*.sqlite` file has no `threads` table, continue with rollout-file cleanup instead of stopping to debug the database.

## Self-Referential Search Hits

If old titles still appear after deletion, inspect whether the current cleanup conversation contains command output or assistant text that repeated those old titles. In that case, scrub the current `rollout-*.jsonl` with `--scrub-file` and selectors.

Prefer ID selectors or local-only encoded patterns to avoid reintroducing sensitive terms into chat.

Example:

```bash
python3 scripts/clean_codex_history.py \
  --codex-home "$HOME/.codex" \
  --scrub-file "$HOME/.codex/sessions/YYYY/MM/DD/rollout-THREAD.jsonl" \
  --ids THREAD_PREFIX \
  --dry-run
```

Then execute with `--execute` after approval.

## Bundled Script

Use `scripts/clean_codex_history.py` for deterministic cleanup. It defaults to dry-run mode and creates timestamped backups before changing SQLite databases, index files, database-referenced transcript files, session transcript files, archived transcript files, or scrubbed transcript files unless `--no-backup` is passed.

The default JSON output is redacted: it reports counts, byte totals, skipped database categories, non-sensitive warnings, backup creation status, backup filenames, and HTML report location without titles, transcript snippets, or full thread IDs. Use `--show-sensitive` only when selected IDs or selected file paths are necessary, and avoid pasting that output into chat.

## HTML Run Reports

The script writes an HTML report for every run, including dry runs, execution runs, verification runs, and inventory-only runs. Reports default to:

```bash
$HOME/.codex/history_cleanup_reports/codex-history-cleanup-YYYYMMDD-HHMMSS-ffffff.html
```

The report contains operation mode, generation time, selector counts, session and archived-session statistics, database summaries, index cleanup statistics, warnings, backup status, and the redacted raw JSON summary. It must not include private titles, transcript snippets, or full thread IDs unless the operator explicitly used `--show-sensitive`.

Use `--report-dir PATH` to write reports somewhere else when the user wants a specific output location.

Useful commands:

```bash
# Inventory only
python3 scripts/clean_codex_history.py --codex-home "$HOME/.codex" --dry-run

# Delete archived records and files after approval
python3 scripts/clean_codex_history.py --codex-home "$HOME/.codex" --all-archived --execute

# Delete all non-current threads after approval
python3 scripts/clean_codex_history.py \
  --codex-home "$HOME/.codex" \
  --all-except-current \
  --current-thread-id THREAD_ID \
  --execute
```

## Privacy Before Sharing

Before publishing or sharing this skill, read `references/privacy.md`.

Do not include real usernames, absolute local paths, conversation titles, thread IDs, screenshots, database rows, generated images, auth files, or logs.
