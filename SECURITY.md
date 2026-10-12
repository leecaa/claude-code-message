# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.3.x (unreleased source candidate) | :white_check_mark: |
| 0.2.x   | :white_check_mark: |
| < 0.2   | :x:                |

The 0.3.0 source candidate has not been released. Checkout changes do not
upgrade installed plugins or running bridges.

## Security Model

`claude-code-message` links Claude Code sessions across machines using standard system security primitives:

1. **Transport & Authentication**: The cross-machine communication runs entirely over SSH (`ssh -o BatchMode=yes`), relying on your existing key-based SSH infrastructure and access controls. No separate listening server ports are opened.
2. **Socket Security**: Unix domain sockets are created in per-user directories with restricted permissions (`0600`), accessible only by the local OS user.
3. **Session Token Verification**: Every message framed to an inbox socket requires the session's secret token, which is stored in files readable only by that same OS user.
4. **Credential Redaction**: Tasks, broadcast previews, and audit logs are filtered through a multi-pattern credential redactor (`redact()`) before storage or transmission to prevent leaking secrets, API keys, tokens, and passwords into agent context or logs.

5. **Permission Provenance**: Agent CLI sends use current hook metadata bound to the registered ancestor's session ID, PID, process start, and PID domain. Unknown classes are not promoted to bypass. Native declarations and the recipient's inbound controls remain in force. See [message trust](docs/MESSAGE-TRUST.md).

The OS user and paired SSH accounts are the trust boundary, not individual programs
running as that user. A readable peer token proves access to that account, not that
an LLM authored the text. `from`, names, and permission declarations are assertions;
`verifiedPeerPid` identifies the connecting process, which may be a relay. CCM does
not provide isolation from malicious same-user code and does not enable blanket
`crossSessionInbound="accept"` or treat peer messages as user approval.

## Durable state and receipts

The reliability database contains **raw original message bodies**, unlike redacted
audit previews. Its 0700 directory, 0600 DB/journal/identity files and the existing
OS-user boundary must be maintained. Do not publish, pair-tar or casually copy
this state, credentials, real session IDs or private transcripts. Payloads retire
on fixed expiry; dedup/control metadata remains through replay expiry plus the
clock and retention margin. Secure-delete/retirement is not a forensic SSD or
backup erasure guarantee.

Node/store-incarnation identifiers bind the existing authorized SSH channel; a
hello label or UUID does not authenticate an additional actor. A database rebuild,
restored partial state or changed peer identity pauses old replay and requires
reconciliation, not a reset of budgets or silent retarget. Current PID/SID/key
generations are checked before native IO. SQLite fencing cannot retract bytes
already written; dispatch uncertainty remains paused rather than injected again.

Config parsing and revision updates fail closed, including invalid string booleans,
unreadable files and interrupted privacy changes. Read receipts are explicit
receiver declarations, not proof of human reading or work approval. Receiver
privacy-off suppresses unsent declarations and their future replay; already-started
IO or disclosures already received cannot be recalled. Status exposes identifiers
and independent facts, never bodies, peer tokens or arbitrary reason strings.
Legacy peers and native queue admission remain visibly unconfirmed, not fabricated
consumption. Unknown native controls retain their original routing and permissions.

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it privately:

- **Preferred**: Open a private advisory draft via [GitHub Private Vulnerability Reporting](https://github.com/leecaa/claude-code-message/security/advisories/new).
- Please include steps to reproduce the issue, the affected version, and any relevant logs or configuration snippets (with credentials redacted).

Please do **not** open public GitHub issues or discussions for undisclosed security vulnerabilities.
