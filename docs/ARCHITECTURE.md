# Claude Code Message architecture (SSOT)

This document is the single source of truth for Claude Code Message's design. The README
covers usage, `skills/claude-code-message/references/sop.md` covers procedures, and
`docs/PROTOCOL.md` covers the Claude Code internals it builds on.

## Goal

All Claude Code sessions on all paired machines form **one group chat**:

- any member can **@** any other member by name (native `SendMessage`),
- any member can **broadcast** to everyone,
- every member knows **who is here and what they work on**,
- every message leaves a **trace**.

Non-goals: access control between members, message persistence or replay, and
multi-hop routing. Permission-mode parity from Claude Code is kept as the only
gate. Messages are in-memory in each session, as they are natively. Pair the
machines you need instead of routing through others.

## Components

```
┌────────────── machine A ("laptop") ─────────────┐        ┌──────── machine B ("server")  ────────┐
│ Claude sessions ──hooks──▶ roster/<sid>.json        │        │                                        │
│      │  ▲                     │                     │  ssh   │                                        │
│      │  │ native UDS          ▼                     │ stdio  │                                        │
│      ▼  │           ccm daemon ── Node(dial) ◀──────┼────────┼──▶ Node(serve) ── mirrors of A's       │
│  mirrors of B's  ◀──spawns─┘   │                    │ JSONL  │         │         sessions            │
│  sessions (1 proc each)        ▼                    │        │         ▼                             │
│                     audit.jsonl, counters, status   │        │   audit.jsonl, counters               │
└─────────────────────────────────────────────────────┘        └────────────────────────────────────────┘
```

| Piece | Role |
|---|---|
| **mirror** | One process per remote session. It owns a pid, a registry record `~/.claude/sessions/<pid>.json` (name `<label>-<name>`, `agent: "claude-code-message"`, `ccmTask`), a key and an inbox socket, so local Claude treats it as a live peer. |
| **Node** | One end of a link, with the same code on both sides. Every 2 s it publishes the local sessions with their tasks, keeps the peer's mirrors in sync (spawn / update status and task / kill), relays frames, and rewrites `from` / `from-name` to the local mirror of the sender. |
| **daemon** | The dialling side. It holds a Node per configured peer, reconnects with 1–60 s backoff, and writes `status.json` and `counters.json`. |
| **serve** | The answering side, run by the dialler as `ssh host python3 ~/.cache/claude-code-message/ccm.py serve`, a copy pushed at every connect so both ends run one version. It needs no daemon. |
| **hooks** | Plugin hooks: SessionStart (register, capture current permission mode, ensure daemon, inject the briefing and roster), UserPromptSubmit (refresh mode and task), PreToolUse on Bash/SendMessage (refresh mode before sending), PostToolUse on SendMessage (audit native sends), SessionEnd (deregister). |
| **CLI** | `ccm`: setup (`init`, `pair`, `unpair`, `doctor`), daemon control, chat (`roster`, `send`, `broadcast`, `task`), audit (`log`). |

## Lifecycles

### Member (session)

| Event | Registration | Awareness | Cleanup |
|---|---|---|---|
| start / resume / clear / compact | SessionStart hook writes `roster/<sid>.json` (audit `join` on the first write) | the same hook injects the briefing and the current roster as `additionalContext` | — |
| user prompt | UserPromptSubmit sets `task` from the first line (≤140 chars; slash commands and wrapped system text are skipped) and audits `task` | peers see it within 2 s (status, roster, mirror `detail`) | — |
| agent refines its task | `ccm task "…"` (source `agent`) | same | — |
| exit | — | — | SessionEnd removes the roster entry (audit `leave`); peers drop its mirror when the next scan no longer lists it |
| crash / kill -9 | — | — | the daemon scan sweeps roster entries whose session has been gone for more than 120 s (audit `leave`, reason `swept`) |
| session predates the plugin | none; `task` is inferred from the transcript tail | shown in the roster | — |

### Link

| Event | Behaviour |
|---|---|
| connect | push ccm, run `serve`, exchange `hello` (version check), then `sessions` → mirrors spawn (audit `link_up`) |
| both sides dial | the serve side refuses with an `error` op; the dialler logs it and backs off (audit `link_refused`) |
| drop | the Node's reader hits EOF, kills all its mirrors and flushes counters (audit `link_down` with per-connection counts), then the daemon reconnects |
| daemon stop | SIGTERM kills mirrors; `ccm down` also kills orphan mirrors |

## Message paths

| Path | Transport | Audit events |
|---|---|---|
| SendMessage to a local member | native, the bridge is not involved | `native_send` (PostToolUse hook) |
| SendMessage to `server-x` | native → mirror → link → target's inbox | A: `native_send` and `relay_out`; B: `relay_in` |
| reply | reverse of the above; the `from` rewrite makes it a native reply | same, mirrored |
| `ccm send NAME` / `ccm broadcast` | ccm writes native frames straight to each inbox (local or mirror) | `send` / `broadcast` (per-target results) + `relay_*` for remote targets |

### Audit record

Each record is one JSON line in `~/.local/state/claude-code-message/audit.jsonl`:

```json
{"ts":"…","node":"laptop","ev":"relay_out","msg_id":"…","sender":"web-ui","to":"server-api","peer":"server","preview":"first 160 chars"}
```

Event types: `daemon_up/down`, `link_up/down/refused`, `pair/unpair`,
`join/leave/task`, `native_send`, `send`, `broadcast`, `relay_out`, `relay_in`
(with `ok`/`error`). `audit_preview_chars: 0` stores no message text. The file
rotates at 20 MB. Counters: `status.json` shows per-connection counts and
`counters.json` holds all-time counts per peer, so they survive reconnects.

## Security

| Concern | Answer |
|---|---|
| Transport | ssh (keys, BatchMode). No listening ports. |
| Who can inject into an inbox | only holders of the inbox's token, which is in a mode-0600 file of the same OS user, exactly as with native peers |
| Permission escalation | Native `from-mode` declarations are passed through. `ccm send` / `broadcast` use current hook metadata bound to the registered ancestor's session ID and process generation: `bypass` / `prompting`, or no declaration when unknown. No launch-flag inference or automatic inbound-policy override. See [message trust](MESSAGE-TRUST.md). |
| Prompt injection via peers | Claude Code wraps peer text in `<cross-session-message>` and its system prompt treats it as a teammate request. The briefing repeats that it is never user approval. |
| Access control between members | deliberately none: any member may message any member, and the audit log makes it accountable |

## Known limits

- The protocol is internal to Claude Code (tested on 2.1.282); keep all machines on one Claude Code version.
- No multi-hop routing: B and C see each other only if they are paired directly.
- `notify_when_idle` works for local members only (Claude Code limitation).
