# Claude Code Message SOP

These procedures assume Claude Code, python3 ≥ 3.8 and ssh. Each step is a
command, followed by the output that shows it worked.

## 1. Add the first two machines (A dials B)

On **A** (the hub, the machine you work from most):

```bash
git clone https://github.com/leecaa/claude-code-message.git ~/src/claude-code-message
python3 ~/src/claude-code-message/bin/ccm init --label laptop       # ✓ node label / ✓ plugin 0.2.x / ✓ shim
ssh -o BatchMode=yes server true                                  # must return with no prompt
ccm pair server --remote-label server                             # ends with: ✓ link up: N member(s) on server visible here
ccm doctor                                                        # all good
```

B needs nothing beforehand: `pair` copies claude-code-message to `~/.local/share/claude-code-message`
on B and runs `ccm init` there.

## 2. Add another machine C

Pair it from the hub: `ccm pair C --remote-label c`. Links form a star around
the hub. Sessions on B and C each see the hub's sessions. To make B and C see
each other as well, pair C from B too. Keep one dialler per pair; `pair` refuses
to dial a machine that already dials you.

## 3. Daily use

| Task | Command |
|---|---|
| Health | `ccm status`, `ccm doctor` |
| Members | `ccm roster` |
| Traffic | `ccm log -n 100`, `ccm log -f` (live), `ccm log --ev broadcast` |
| Restart after an upgrade | `ccm restart` |

Existing sessions must reload plugins (`/reload-plugins`) or restart (`/exit`,
then `claude --resume <id>`) to load new hooks. Until then native messaging still
works, but CLI sends may lack the current permission snapshot. Do not terminate
someone else's active session to upgrade it.

### Sending from a plain terminal

`ccm send` / `ccm broadcast` run outside Claude sign as `<node>-cli` without a
permission declaration. Bypass recipients therefore **hold** these messages for
approval. For unattended coordination, send from inside a session with the hooks
loaded: the CLI identifies the registered ancestor and uses its current hook
metadata. It never guesses bypass from launch flags. See
[message trust](../../../docs/MESSAGE-TRUST.md) for mode parity and plan-mode limits.

## 4. Upgrade

```bash
cd ~/src/claude-code-message && git pull
python3 bin/ccm init                     # updates the plugin and the shim on this machine
ccm pair server --remote-label server    # re-ships the new version to the peer, restarts the link
```

When a link starts, the dialler also pushes its own `ccm` to
`~/.cache/claude-code-message/ccm.py` on the peer, so both ends of a link always run the
same version.

**Claude Code upgrades:** the peer protocol is internal. After upgrading
Claude Code, upgrade every machine and run `ccm doctor`. It warns when versions
differ. If a message fails to arrive, compare against `docs/PROTOCOL.md`.

## 5. Unpair / uninstall

```bash
ccm unpair server                     # stop dialling, remove its mirrors
ccm down                              # stop the daemon; all mirrors disappear at once
claude plugin uninstall claude-code-message@claude-code-message
rm ~/.local/bin/ccm; rm -rf ~/.local/state/claude-code-message ~/.config/claude-code-message
```

## 6. Recovery

| Situation | Action |
|---|---|
| Link flapping | `tail -50 ~/.local/state/claude-code-message/daemon.log`. The daemon reconnects with 1→60 s backoff; ssh problems show there. |
| Stale `server-*` names after a crash | `ccm down && ccm up`. `down` also kills orphan mirrors. |
| Roster shows departed sessions | These are swept automatically within 2 min of the session disappearing. |
| Audit log too big | It rotates at 20 MB to `audit.jsonl.1`. |

## Files

| Path | What |
|---|---|
| `~/.config/claude-code-message/config.json` | label, peers, `audit_preview_chars` (0 = do not store message text) |
| `~/.local/state/claude-code-message/audit.jsonl` | the audit trail (both machines keep their own) |
| `~/.local/state/claude-code-message/roster/` | one file per local session: its task |
| `~/.local/state/claude-code-message/{daemon.log,status.json,counters.json}` | daemon log, live status, all-time counters |
