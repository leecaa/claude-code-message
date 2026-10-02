# Message identity, permission classes, and first delivery

## Why “unidentified session / did not attest its permission mode” appears

Claude Code checks inbound peer messages even in `bypassPermissions` mode.
`verified pid` identifies the process that opened the socket, not necessarily the
Claude that authored the message: for a bridge, it is the relay process.

In CCM 0.2.8, `ccm send` and `ccm broadcast` could emit `from-mode="default"`.
The observed Claude Code 2.1.282 envelope accepts only **`bypass` or `prompting`**.
An invalid value prevents the entire envelope from being recognized, losing its
parsed name and permission declaration. A CLI send without an identified source
also has no reply address, which appears as “an unidentified session”.

Two additional problems made first messages unreliable:

- Process arguments are not current permission state. Claude can change modes
  after startup, and launch flags may not remain in its process title.
- A sender could start after the relay's last two-second registry scan. Its first
  message then reached the peer before that peer had a mirror for replies.

## What the fix does

1. `SessionStart` and `UserPromptSubmit` save Claude's `permission_mode` hook input.
   A synchronous `PreToolUse` hook for `Bash|SendMessage` refreshes it before sends,
   including the first tool call when session registration was not ready at startup.
2. The snapshot is bound to the live session ID, PID, process start, and PID domain.
   It is written only by a descendant of the registered session. Old snapshots
   cannot attest a recycled PID or another session.
3. The CLI finds its actual registered ancestor, even when session environment
   variables are missing. Environment variables alone cannot select a sender.
4. `bypassPermissions` maps to `bypass`; `default`, `acceptEdits`, `auto`, and
   `dontAsk` map to `prompting`. Missing/unrecognized modes remain **undeclared**.
5. Socket addresses are percent-encoded and display names normalized for the
   canonical envelope. A valid session ID is included for provenance/navigation.
6. The relay serializes a fresh registry publication before forwarding a message.
   Native permission declarations, control fields, and message bodies are not
   upgraded or rewritten to bypass; only reply routing and display names change.

The implementation reuses Claude's documented hook contract and Python's standard
library. There is no public SDK for the internal UDS wire protocol; adding an auth
library or inventing a signed “trusted Claude” flag would not change the receiver's
policy or establish a stronger OS-user boundary.

## What is intentionally still held

With no explicit `crossSessionInbound` setting, Claude Code uses this policy:

| Sender's declared class | Prompting recipient | Bypass recipient |
| --- | --- | --- |
| `prompting` | Deliver | Hold |
| `bypass` | Hold | Deliver |
| Undeclared | Deliver | Hold |

This applies to the first and every subsequent message; no warm-up or first-use
allowlist is involved. Identity does **not** grant permission to do work that the
sending session was denied. `hold`/`refuse` policies remain authoritative.

Plan mode is deliberately undeclared by the CCM CLI: the hook's `permission_mode`
alone cannot tell whether interactive plan has bypass permissions available.
Use native `SendMessage` in that case; its own declaration is preserved by CCM.
An ordinary shell, a detached process without a registered ancestor, or a session
without functioning hooks does not get a fabricated bypass declaration.

Claude's official `crossSessionInbound: "accept"` option delivers all inbound
peer messages, not just messages proven to be Claude-authored. CCM does **not**
set it, change global permissions, approve dialogs, or patch Claude Code. The trust
boundary remains your local OS account and explicitly paired SSH accounts; neither
an envelope nor a shared-account token proves that a particular LLM wrote text.

## Updating and checking

- Update the source and installed plugin, then reload plugins or restart Claude
  so the new `PreToolUse` hook is registered. Editing a checkout alone does not
  update the installed plugin cache or running relays.
- Update/restart both ends of a bridge through your normal authorized deployment
  procedure. `ccm init`, `ccm restart`, and pairing can start SSH links; do not use
  them as local-only verification commands when remote operations are not intended.
- Test a new session's first `ccm send` and `ccm broadcast`, then another send after
  a mode change. Matching declared classes should deliver without a parity dialog.
- The CLI's `ok` is a socket-write result, **not proof of model delivery**. Confirm
  receipt in the destination; explicit inbound policy can still hold or drop it.
- Run all isolated tests with `python3 -m unittest discover -s tests -v`.
  The subprocess integration tests use temporary registries and local sockets,
  never real sessions, SSH, or model calls. `TMPDIR` can select their scratch root.

Verification of this change on Linux with Claude Code 2.1.282 included capturing
an authentic native `SendMessage` frame and delivering a newly generated CCM
bypass frame back to the same live session. The destination received the parsed
name, session ID, and bypass class without the original approval dialog. This is
local receiver verification, not a claim of a completed macOS/SSH rollout.

## References

- [Official cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging)
- [Official hook inputs](https://code.claude.com/docs/en/hooks)
- [Official permission modes](https://code.claude.com/docs/en/permission-modes)
- [Observed wire protocol](PROTOCOL.md)
