# Claude Code Message

English | [简体中文](README.zh-CN.md)

> **Disclaimer**: Claude Code Message is an unofficial community project, not affiliated with or endorsed by Anthropic. Claude and Claude Code are trademarks of Anthropic, PBC. It relies on undocumented internal behaviors that may change across Claude Code versions.

**A group chat for [Claude Code](https://code.claude.com) sessions, across machines.**

- Sessions on your other machines appear as ordinary peers named
  `<machine>-<session>`. Built-in `SendMessage` reaches them, and replies come
  back the same way.
- Every session **registers what it is working on**. The registration comes
  from the user's latest prompt, and agents can update it. New sessions get the
  roster at startup, so an agent can decide whom to @.
- `ccm broadcast` messages everyone; you can narrow it by machine or name.
- Every join, leave, task change, message, relay and broadcast goes into an
  **audit log** on each machine.
- It is one stdlib-only Python file plus a Claude Code plugin (a skill and
  hooks). The link runs over SSH, so there is no server and no open port.
- **Platform Support**: macOS (Darwin) and Linux (POSIX). Windows is supported via WSL (native Windows unsupported due to POSIX socket and path requirements).

```
 laptop (hub)                                server (Linux)
 ┌────────────┐ SendMessage ┌──────────┐     ┌──────────┐   inbox   ┌────────────┐
 │ web-ui     │ ──────────▶ │ mirror   │ ssh │ ccm      │ ────────▶ │ api        │
 │            │ ◀────────── │ server-  │ ──▶ │ serve    │ ◀──────── │            │
 └────────────┘   reply     │ api      │ ◀── │ mirror   │   reply   └────────────┘
                            └──────────┘     │ laptop-… │
                                             └──────────┘
```

## Quick start

```bash
# on the machine you work from (the hub)
git clone https://github.com/leecaa/claude-code-message.git ~/src/claude-code-message
python3 ~/src/claude-code-message/bin/ccm init --label laptop

# Ensure ~/.local/bin is in your PATH (e.g. export PATH="$HOME/.local/bin:$PATH")
# once per other machine; it only needs ssh + python3 + Claude Code, pair installs the rest
ccm pair server --remote-label server
ccm doctor
```

> **Note**: Keep `~/src/claude-code-message` in place (or re-run `ccm init` if moved), as `ccm init` registers this directory as the plugin source.

Then, in any Claude Code session: *"ask whoever is working on the API whether
they changed the auth endpoints"*. The agent reads the roster and @s the right
member.

| Command | |
|---|---|
| `ccm roster` | members, where they run, what they work on |
| `ccm send NAME TEXT [--as AGENT]` / `ccm broadcast TEXT [--as AGENT] [--node N] [--local] [--match RE]` | message from the CLI or from an agent's Bash |
| `ccm task [TEXT]` | show or set this session's task |
| `ccm agent serve` / `wait` / `status` / `stop` | bridge Antigravity, Codex & external agents as interactive peers |
| `ccm log [-f] [-n N] [--ev E,…]` | audit trail |
| `ccm status` / `ccm doctor` | health (counters survive reconnects) |
| `ccm pair` / `unpair` / `up` / `down` / `restart` | lifecycle |

## Receipts and bounded resend

Both `read_receipts` and `retry_enabled` default to `true`. They are independent:

```bash
ccm config set-read-receipts false
ccm config set-retry false
ccm config set-reliability '{"message_ttl":3600,"max_attempts":128}'
ccm receipts --json                 # sender-side facts, never message bodies
ccm receipts --inbox --json         # only the current owned receiving SID
ccm read-ack DELIVERY_ID            # explicit receiving-session declaration
ccm retry DELIVERY_ID               # only a safely pending original message
ccm retry DELIVERY_ID --inbox       # safe pre-write receiver retry
```

`queued` means a private SQLite transaction accepted the message. `peer_received`
means the paired CCM endpoint committed its inbox and dedup record. Neither means
Claude read it. `socket_written_unconfirmed` is a native socket handoff, not
queue admission. Native `delivered` is admission, not reading. The only read-style
fact is **`receiver_acknowledged`**: the actual ancestor-owned target session
explicitly declares it after a handoff. Model consumption, human reading and
human approval remain unknown; hooks, queries, idle and Stop never manufacture
that declaration.

New paired endpoints replay the same delivery ID with exponential backoff and
persistent receiver dedup. The defaults are 2–300 seconds, ±20% jitter, a
10-second ACK wait, 512 attempts and a fixed 24-hour TTL. A peer database identity
change pauses old queues. Targets are frozen SIDs, not names; `sid:SID` can address
a previously observed unique offline route. Broadcast accepts its frozen targets
in one transaction. No live worker means no automatic progress: the existing
`ccm up` daemon also supports local-only recovery with no peers.

An uncertain native handoff is deliberately **not injected again**. A crash after
dispatch reservation, partial write or post-write commit failure remains
`handoff_unknown`; this trades liveness for avoiding duplicate work. A legacy
peer is attempted once and remains unconfirmed, even after a later upgrade.
There is no end-to-end exactly-once or guaranteed eventual delivery claim.
Disabling retry stops new background timer/reconnect/restart attempts, while explicit
safe commands get one durable opportunity. Duplicate ingress never grants another
native handoff; reactive peer_received transport responses use persistent attempts,
lease and backoff even with retry off, not an unmetered ACK exception. First handoff
control facts and the read declaration's generation dependency remain independent
of automatic retry. Held/refused/expired messages are not unlocked.
Disabling read receipts on the receiver suppresses future disclosure, including
pending events; re-enabling does not replay suppressed declarations. IO already
started cannot be withdrawn.

The queue stores **raw message bodies**, unlike redacted audit previews: keep its
0700 directory and 0600 database private. Active bodies are bounded to 10,000 /
64 MiB; the total store/journal budget is 256 MiB with ACK headroom. Tombstones
cover the immutable expiry plus clock-skew and retention margins. Clock rollback,
corrupt configuration and incompatible schemas fail closed. Do not remove or
restore only part of reliability state to force a resend. See the
[architecture](docs/ARCHITECTURE.md) and [security policy](SECURITY.md).

## Message trust and first delivery

For an agent's `ccm send` / `ccm broadcast`, hooks capture its **current** permission
mode before the first send and subsequent sends. Native `SendMessage` declarations
are preserved. CCM does not label unknown senders as bypass or change
`crossSessionInbound`; matching classes deliver under Claude's default policy,
while mismatched/unknown classes may still require approval.

If you see “unidentified session” or “did not attest its permission mode”, see
[Message identity and troubleshooting](docs/MESSAGE-TRUST.md), including upgrade
steps and the limits of “trusted Claude” on a shared OS account.

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): design, lifecycles, audit
  schema, security (single source of truth)
- [docs/AGENT-BRIDGE.md](docs/AGENT-BRIDGE.md): Agent Bridge architecture for
  Google Antigravity, OpenAI Codex, and external autonomous agents
- [skills/claude-code-message/references/sop.md](skills/claude-code-message/references/sop.md):
  procedures for pairing, upgrading, recovery and uninstalling
- [docs/PROTOCOL.md](docs/PROTOCOL.md): the Claude Code peer protocol this
  builds on
- [SECURITY.md](SECURITY.md): security architecture, threat model, and vulnerability reporting
- 简体中文: [README.zh-CN.md](README.zh-CN.md),
  [docs/i18n/zh-CN/AGENT-BRIDGE.md](docs/i18n/zh-CN/AGENT-BRIDGE.md),
  [docs/i18n/zh-CN/SOP.md](docs/i18n/zh-CN/SOP.md)

## Security in one paragraph

SSH carries and authenticates the link. An inbox accepts only frames that
carry its token, which is readable only by your OS user; this is the same
rule as native peers. The permission mode of the sender is passed through, so a
session in a different mode holds bridged messages for its user's approval,
exactly as it does locally. Any member may message any member; the audit log
makes that accountable. See [SECURITY.md](SECURITY.md) for full details.

## Caveats

Claude Code Message relies on Claude Code's **internal** peer protocol, tested on
2.1.282. Keep all machines on the same Claude Code version; `ccm doctor`
warns you when they differ. Routing is single-hop: pair machines directly if
they need to see each other.

## Test

`python3 -m unittest discover -s tests -v`

## Community & Acknowledgements

- Discussion & Support: [LINUX DO](https://linux.do)

License: MIT

