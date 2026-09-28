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
- applies **permission-mode parity**: if `from-mode` differs from its own mode,
  it holds the message for its user's approval,
- replies by connecting to the `from` address, which must be a local socket.

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
