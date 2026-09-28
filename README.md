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
| `ccm send NAME TEXT` / `ccm broadcast TEXT [--node N] [--local] [--match RE]` | message from the CLI or from an agent's Bash |
| `ccm task [TEXT]` | show or set this session's task |
| `ccm log [-f] [-n N] [--ev E,…]` | audit trail |
| `ccm status` / `ccm doctor` | health (counters survive reconnects) |
| `ccm pair` / `unpair` / `up` / `down` / `restart` | lifecycle |

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): design, lifecycles, audit
  schema, security (single source of truth)
- [skills/claude-code-message/references/sop.md](skills/claude-code-message/references/sop.md):
  procedures for pairing, upgrading, recovery and uninstalling
- [docs/PROTOCOL.md](docs/PROTOCOL.md): the Claude Code peer protocol this
  builds on
- [SECURITY.md](SECURITY.md): security architecture, threat model, and vulnerability reporting
- 简体中文: [README.zh-CN.md](README.zh-CN.md),
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

`python3 tests/test_ccm.py`

License: MIT
