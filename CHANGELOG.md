# Changelog

## 0.2.9 — 2026-10-02
- Fix invalid `from-mode="default"` envelopes: CLI sends use hook-attested `bypass`/`prompting` classes, or omit an unknown class.
- Refresh permission state at session startup, prompts, and before Bash/SendMessage; bind it to a live ancestor session and process generation instead of guessing from launch flags.
- Serialize registry publication before relaying messages, including a new sender's first message; reject stale process records.
- Normalize sender display names and percent-encode socket reply addresses while preserving native permission declarations and control frames.
- Add isolated first/repeated-send, broadcast, mode-transition, and subprocess/socket regression tests and document inbound-policy boundaries.

## 0.2.8
- Security: credential redaction now covers JSON fields, prefixed environment variables (e.g. `PGPASSWORD`), and HTTP Basic authorization headers.
- Security: native `SendMessage` summaries are redacted before writing to the audit log.
- Concurrency: cross-process roster updates are synchronized with file locking (`fcntl.flock`).
- Performance: negative task inference results are cached to avoid repeated 32MB transcript file scans.
- Performance: `SessionStart` hook avoids blocking on synchronous transcript inference.
- OSS hygiene & docs: added community disclaimer, platform requirements, PATH guidance, and `SECURITY.md`.

## 0.2.7
- Redaction also catches keys glued to CJK text.

## 0.2.6
- Credentials (API keys, tokens, passwords, URL credentials) are redacted from tasks and audit previews before they are stored or shared.

## 0.2.5
- Group chat: every session registers its current task (hooks), gets the roster at start, leaves on exit; stale entries are swept.
- `ccm roster`, `ccm send`, `ccm broadcast [--node|--local|--match]`, `ccm task`.
- Audit log (`ccm log`) on every node: join/leave/task, native sends, relays, CLI sends and broadcasts; all-time counters survive reconnects.
- Setup: `ccm init`, `ccm pair`, `ccm unpair`, `ccm doctor`; stable `~/.local/bin/ccm` shim that follows the installed plugin.
- Guard against both machines dialling each other.
- Session names in any language are kept in mirror names.

## 0.1.0
- Cross-machine SendMessage: remote sessions mirrored as native peers over an SSH stdio link.
