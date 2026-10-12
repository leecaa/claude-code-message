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

CCM provides bounded persistent acceptance, deduplication and safe retransmission
within the existing daemon/SSH bridge. It does not provide isolation between OS-user
members, multi-hop routing, human-read proof or exactly-once native consumption.
Permission-mode parity remains authoritative. Native session inboxes are still
external, non-transactional sinks; uncertain handoffs pause rather than replay.
Pair the machines you need instead of routing through others.

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
| **daemon** | Holds a Node per configured peer, reconnects with 1–60 s link backoff, and also runs local-only recovery when configured with zero peers. No separate broker is started. |
| **serve** | The answering side over authenticated SSH stdio, using the single shipped Python file. Its pending durable state survives EOF but does not advance without a subsequently live worker. |
| **ReliabilityStore** | Private sqlite3 outbox/inbox/control ledger, stable node identity and database incarnation; BEGIN IMMEDIATE transactions, FULL synchronous, DELETE journal, bounded payload and total storage. |
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
| `ccm send NAME` / `ccm broadcast` | commit frozen targets to outbox, then wake a mirror or perform a local handoff; broadcast accepts all targets atomically | `send` / `broadcast` with per-target delivery IDs; queued is not reading |

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

## Reliable message boundaries

- Every target has a stable CCM `delivery_id`; original native `msg_id`, body and
  permission provenance are preserved. A broadcast has an additional group ID.
- Inbox identity is unique on `(origin_node_id, store_incarnation, delivery_id)`.
  Target SID is an immutable fingerprint field, not a second primary-key namespace.
  The native-ID mapping is unique in the same transaction. Changed content,
  source, target, expiry or receipt request is an ID conflict.
- `peer_received` is emitted only after the peer inbox/dedup/control transaction
  commits. It stops sender body replay, not receiver recovery. Lost ACK replay
  returns the existing durable fact without another native injection.
- Targets and reply routes use stable SIDs and verified current PID generations.
  A same-name replacement is not the old target. Missing original sender reply
  mirrors wait or expire; no fake online member or stronger permission is created.
- `native_dispatching` is committed before any native `sendall`. It cannot be
  re-leased because a timer expired. Dead-owner recovery, partial IO and post-write
  commit failure become `handoff_unknown`. State and its handoff/generation control
  fact commit together; dead-owner recovery atomically creates the unknown fact
  without IO. Only the same legitimate owner may certify a pre-write failure safe
  for another attempt. Database fencing cannot
  retract bytes already written to an external socket.
- Native admission, CCM transport and receiver declaration are independent facts.
  Native `held`, `delivered`, denied/refused/dropped/expired are not reading.
  A correctly correlated native status uses original message ID, peer/link and
  both session generations; unknown controls are forwarded unchanged, not retried.
- `receiver_acknowledged` requires an explicit `read-ack` from the actual
  ancestor-owned target SID/generation after handoff. There is no hook/idle/Stop
  heuristic or human-approval inference. Receipt and receipt_ack are idempotent
  control-plane events; receipt_ack terminates the chain, not ACK-of-ACK.

Known native admission controls may precede handoff facts. Their bounded durable
pending records retain authenticated peer/channel, original IDs and both generations;
handoff correlation applies only exact matches, with terminal facts never regressing.
A first native handoff has one explicit control opportunity even with retry off;
read-ack safely sends its generation dependency before its declaration. Explicit
remote retry uses a separate durable, owner/generation/peer-bound one-shot grant,
not a reset of ordinary native ingress. Both relay sides audit acceptance and
attempts; duplicates are distinct from native handoffs.

Configuration is validated strictly. Missing files have defaults; corrupt or
unreadable files pause acceptance/sending instead of restoring permissive defaults.
Privacy/retry updates first record a conservative durable pending revision, then
fsync the configuration and directory, then finish the revision. Interrupted
updates remain closed. Receiver privacy overrides requests, cancels unsent read
receipts and permanently suppresses their later replay. Already-started IO cannot
be recalled. Retry off covers background timer/reconnect/restart/body replays; current explicit
safe operations get one durable opportunity. A duplicate incoming body never grants
another native attempt. Its reactive peer_received response is a protocol exception,
not a new send grant: the same receipt ledger records attempts, lease and backoff,
allows only transport ACK facts, and remains bounded even when retry is off or an
earlier receipt_ack was received. It never revives background retry or disclosure.

Default message scheduling uses 2–300 seconds, ±20% jitter, ACK wait 10 seconds,
512 attempts and immutable TTL 24 hours. Offline checks do not count as attempts;
actual sends reserve the persistent budget before IO. Clock rollback pauses work.
Expired frames are rejected even after tombstone GC; tombstones remain through
expiry + 300-second skew allowance + 48-hour margin. Active bodies are bounded to
10,000/64 MiB. The 256 MiB database/journal budget includes metadata/control records
and a 4 MiB ACK reserve (never below the SQLite metadata page floor). Capacity
accounts for physical files, reusable freelist pages and projected journal/growth;
committed GC can safely compact an empty high-water file. Route cache metadata uses
ordinary budget, a 10,000-entry cap and 24-hour unreferenced retention; valid replay
tombstones are never removed for routes or new messages. A full store rejects new
acceptance rather than removing still-valid dedup records.

Node IDs and incarnations identify state, not authority. The existing paired
SSH/OS-account channel is the trust boundary; hello labels are not authentication.
Changed peer identity or rebuilt dedup state pauses old queues. Legacy sends are
one unconfirmed attempt and never become automatically replayable after upgrade.
Private 0700 state and 0600 DB/journal contain original bodies until retirement;
audit preview redaction does not apply to them. No worker means no progress, and
there is no unconditional eventual-delivery or exactly-once guarantee.

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
