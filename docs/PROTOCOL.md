# Claude Code peer messaging protocol (as observed)

Reverse-engineered from Claude Code **2.1.282** (macOS arm64 and Linux x86_64),
by reading the bundled JS and capturing real frames with a fake peer. This is
not a public contract and may change between releases; Claude Code Message only relies on
the parts listed here.

## Session registry

Each interactive session writes `~/.claude/sessions/<pid>.json`:

| field | notes |
|---|---|
| `pid`, `sessionId`, `cwd`, `name`, `nameSource` | `name` is the address used by `SendMessage` |
| `messagingSocketPath` | the inbox; Unix socket |
| `procStart` | liveness token; must equal what Claude computes for `pid` |
| `pidDomain` | `darwin`, or `linux:<machine-id>:pid:[<pidns inode>]` |
| `peerProtocol` (1), `peerFeatures` | e.g. `notify_idle`, `reply_across_default_dirs` |
| `status` | `busy` / `idle`, shown by ListAgents |
| `kind` | `interactive`, `bg`, ... |

`procStart`:

- macOS: `LC_ALL=C TZ=UTC ps -o lstart= -p <pid>` (the UTC part matters — a
  local-time value makes the record look like a recycled pid and it is silently
  skipped).
- Linux: field 22 (`starttime`, clock ticks) of `/proc/<pid>/stat`.

Records whose pid is dead or whose `procStart` does not match are ignored, so a
registry entry is only visible while the owning process lives.

## Inbox socket

- macOS: `/tmp/cc-socks/<pid>.sock`; Linux: `$XDG_RUNTIME_DIR/cc-socks/<pid>.sock`.
  Directory must be mode 0700 and owned by the user; socket mode 0600.
- Liveness probe: connect and close without writing.

## Auth key

`~/.claude/sessions/<pid>.<sha256(path.resolve(sock))>.key`, mode 0600:

```json
{"peerToken":"<32 hex>","procStart":"...","pidDomain":"..."}
```

`path.resolve` does **not** follow symlinks (`/tmp` stays `/tmp` on macOS).
A sender reads the receiver's key file and proves itself with that token; only
the same OS user can read it.

## Wire format

One connection per message, newline-delimited JSON, sender writes then half-closes
(macOS: `end()` after ~150 ms):

```json
{"type":"auth","token":"<receiver peerToken>"}
{"msgV":1,"msg_id":"<uuid>","type":"user","priority":"next",
 "from":"uds:<sender sock>",
 "message":{"role":"user","content":"<cross-session-message from=\"uds:<sender sock>\" from-name=\"<name>\" from-mode=\"bypass\">\n<text>\n</cross-session-message>"}}
```

Other frame types (`"type":"control"`, file attachments) use the same
envelope. Limit is roughly 1 MB per line. The receiver:

- checks the token (peer or child token),
- applies **permission-class parity** (`bypass` / `prompting`), unless an explicit
  inbound policy overrides it; a mismatched class is held, and an undeclared
  sender is held by a bypass recipient,
- replies by connecting to the `from` address, which must be a local socket.

## Native status controls (not reading)

The observed native control shape uses top-level `type=control`,
`action=peer_message_status`, `status`, and optional `orig_msg_id`. The control's
own `msg_id` is **not** the original message ID. Observed admission states are
held, denied, expired, delivered, refused and dropped. Refusal may be encoded as
`status=expired,status_detail=refused`; direct refused is also accepted. Dropped
may include `drop_reason` and `dropped_msg_ids` containing original send IDs.
Reason contents and other transport options are not invented as wire fields.
`delivered` means queue admission (including release after a hold), not model
consumption. No consumed/read control has been established.

CCM correlates these facts only against an existing send, its authenticated paired
channel, original source/target SIDs and both PID generations. It does not infer a
SID from native `from`, change targets, or match a naked ID across all records.
Unknown actions/literals and native permission/control fields are forwarded
unchanged. Terminal denial/drop/refusal cannot be undone by late transport ACKs.

## CCM reliability envelope

This is a CCM protocol layered on the existing paired SSH channel, **not new
Claude native fields**. Hello advertises schema 1, persistent node ID and store
incarnation. The existing OS/SSH account authenticates the channel; label and UUID
are identity assertions, not additional authority.

`reliable_deliver` contains stable per-target delivery ID, origin node/incarnation,
fixed source/target SIDs and source generation, original native frame, creation
and expiry, receipt request and immutable fingerprint. Inbox acceptance and
native-ID mapping are one SQLite transaction. A conflicting ID cannot change its
target, body or lifetime. `receipt`/`receipt_ack` carry durable receive, native
handoff or explicit receiver declaration facts and are idempotent control-plane
traffic. They never become LLM messages or an ACK-of-ACK loop.

The local authenticated mirror also accepts private `ccm_local` wakeups for an
owned receipt or safe receiver retry. They are intercepted locally, never
forwarded to the native session. They do not alter native bodies or permission
classes. Legacy `deliver`/`ack` remain compatible, but a legacy socket-write ACK
is not promoted to a durable receive or read fact, nor replayed after an upgrade.

## Canonical identity envelope

In 2.1.282 the receiver recognizes a complete, canonical envelope, not arbitrary
XML. Optional attributes occur in order: `from`, `from-session`, `hop-chain`,
`from-name`, `from-mode`, `from-plugin`. `from-mode` is **only** `bypass` or
`prompting`, never the CLI's `default` or `bypassPermissions` spelling. An invalid
envelope loses its parsed display name and mode, even if the connecting PID is
verified and the socket write succeeded.

Reply addresses percent-encode characters outside `A-Za-z0-9:_/.\\-` (including
spaces, non-ASCII characters, `%`, and `~`). Names strip quotes/angle brackets and
Unicode Cc/Cf/Cs/Zl/Zp characters, trim whitespace, and retain at most 64 code points
plus an ellipsis. `from-session` is an optional navigation hint, not authentication.

CCM CLI sends take permission state from documented hook input; unknown state is
omitted rather than guessed. See [message trust](MESSAGE-TRUST.md) for the policy
matrix, startup-race fix, plan-mode limitation, and validation scope.

## How Claude Code Message uses this

For every live session on the peer machine, claude-code-message starts a small **mirror
process** locally. The mirror owns a real pid, so it can publish a valid
registry record (`agent: "claude-code-message"`, name `<peer-label>-<name>`), a key file
and an inbox socket. Local Claude therefore sends to it with native
`SendMessage`. The mirror checks the token and passes the frame over the SSH
link. The far side rewrites `from` / `from-name` to point at *its* mirror of the
sender, then delivers the frame to the real target with the target's own
token. Replies take the same path in reverse. `from-mode` is passed through
unchanged, so permission-mode parity still applies end to end.
