# Agent Bridge Architecture: Integrating External AI Agents into Claude Code Message

## 1. Overview

**Claude Code Message (CCM)** connects AI coding agent sessions across developer machines. While originally built for native Claude Code CLI sessions, CCM's underlying Unix Domain Socket (UDS) peer protocol and session registry can be bridged to support non-Claude autonomous agents—most notably **Google Antigravity (AGY)** and **OpenAI Codex**—as first-class citizens.

With the **CCM Agent Bridge (`ccm agent`)**, external agents can:
1. **Advertise themselves** on the CCM network with a valid name, live PID, and registered task (appearing in `ccm roster`).
2. **Actively receive messages** sent by Claude Code sessions or other peers via standard `SendMessage` or `ccm send`.
3. **Trigger reactive execution loops** in host runtimes (e.g., Antigravity's Reactive Background Task Wakeup or Codex's Lifecycle Hooks).
4. **Dispatch messages back** to Claude or remote peers with authenticated identity envelopes.

---

## 2. Core Protocol Mechanics

To participate in the CCM mesh, an agent endpoint must satisfy Claude Code's peer protocol contracts (as defined in [PROTOCOL.md](PROTOCOL.md)):

```
┌──────────────────────────────────────────┐
│   Claude Code / Remote Peer Session      │
└──────────────────────────────────────────┘
                     │
                     ▼ ccm send <agent-name> "..."
┌──────────────────────────────────────────┐
│       Unix Domain Socket (UDS)           │
│      /tmp/cc-socks/<pid>.sock (0600)     │
└──────────────────────────────────────────┘
                     │
                     ├─ Frame 1 (Auth): {"type":"auth","token":"<peerToken>"}
                     ├─ Frame 2 (Msg):  {"msgV":1,"msg_id":"...","type":"user",...}
                     ▼
┌──────────────────────────────────────────┐
│           CCM Agent Bridge               │
│   (Listens, validates, audits, dispatches)│
└──────────────────────────────────────────┘
       │                              │
       ▼                              ▼
┌──────────────┐              ┌──────────────┐
│ Antigravity  │              │    Codex     │
│ (Reactive BG)│              │ (Hooks/IPC)  │
└──────────────┘              └──────────────┘
```

### 2.1. Session Registry Record (`~/.claude/sessions/<pid>.json`)
Each active agent bridge endpoint writes a session JSON record with:
* `pid` *(int)*: Real living OS process ID.
* `name` *(str)*: Addressing target (e.g. `mac-antigravity`, `antigravity-worker`, `codex-agent`).
* `messagingSocketPath` *(str)*: UDS inbox path (mode `0600`).
* `procStart` *(str)*: Strict liveness verification token.
  * **macOS**: Must match `LC_ALL=C TZ=UTC ps -o lstart= -p <pid>`.
  * **Linux**: Clock ticks from field 22 of `/proc/<pid>/stat`.
* `peerProtocol`: `1`.
* `status`: `"idle"` or `"busy"`.
* `agent`: `"ccm-agent-<kind>"`.
* `ccmTask`: Current task string, automatically reflected in `ccm roster`.

### 2.2. Auth Key File (`~/.claude/sessions/<pid>.<hash>.key`)
Path is `<pid>.<sha256(path.resolve(sock))>.key` with mode `0600`.
Content:
```json
{
  "peerToken": "<32-hex-characters>",
  "procStart": "<UTC start time>",
  "pidDomain": "darwin"
}
```

### 2.3. Wire Protocol & Delivery
Every incoming message connection transmits two newline-delimited JSON frames:
1. **Auth Frame**: `{"type":"auth","token":"<peerToken>"}`
2. **Message Frame**:
   ```json
   {
     "msgV": 1,
     "msg_id": "939751c5-...",
     "type": "user",
     "priority": "next",
     "from": "uds:<sender_sock>",
     "message": {
       "role": "user",
       "content": "<cross-session-message from=\"uds:<sender_sock>\" from-name=\"mac任务汇总\" from-mode=\"bypass\">\nHello from Claude!\n</cross-session-message>"
     }
   }
   ```

---

## 3. Ingestion & Wakeup Strategies by Agent Runtime

AI agents operate in turn-based LLM execution loops. When a peer sends a message via CCM, the message must be ingested and wake up the agent without manual polling.

### 3.1. Google Antigravity (AGY) Integration

Antigravity natively includes a `<messaging>` reactive event subsystem:
> *"The system automatically resumes your execution when: A background task completes or sends you a notification, or a message arrives from a subagent or peer agent."*

There are two primary integration patterns for Antigravity:

#### Pattern A: One-Shot / Background Task Wakeup (`ccm agent wait`)
Inside an Antigravity conversation, run a background command:
```bash
ccm agent wait mac-antigravity --timeout 3600
```
* Antigravity launches the task and continues or pauses asynchronously (`WaitMsBeforeAsync`).
* When Claude runs `ccm send mac-antigravity "Review ready"`, `ccm agent wait` receives the message frame, writes the payload to stdout, and exits with code 0.
* Antigravity's runtime detects task completion and triggers **Reactive Wakeup**, injecting the message into the next model turn automatically!

#### Pattern B: Daemon + Lifecycle Hooks (`hooks.json`)
Run a persistent agent bridge in the background:
```bash
ccm agent serve mac-antigravity --daemon --inbox ~/.cache/agent-ccm/antigravity-inbox
```
In `.agents/hooks.json` (or `~/.gemini/config/hooks.json`):
* Configure `PreInvocation` or `Stop` hooks.
* If unread messages exist in the inbox, the `Stop` hook returns:
  ```json
  {
    "decision": "continue",
    "reason": "[CCM Inbound Message from Claude]: <content>"
  }
  ```
  This forces the agent execution loop to continue and address the incoming message immediately.

---

### 3.2. OpenAI Codex Integration

Codex provides `~/.codex/hooks.json` and internal IPC endpoints.

#### Pattern A: Exec Adapter (`--exec`)
Run the bridge with a callback command:
```bash
ccm agent serve mac-codex --exec '/Users/mac/scripts/codex-inbox-handler.sh'
```
When a frame arrives, `ccm agent` passes the JSON payload via stdin to the handler script, which appends to Codex thread logs or dispatches via Codex CLI.

#### Pattern B: FIFO / File Queue
```bash
ccm agent serve mac-codex --fifo ~/.cache/codex-ccm.fifo
```

---

## 4. CLI Reference: `ccm agent`

The `ccm agent` suite manages external agent listeners and adapters.

### 4.1. `ccm agent serve`
Starts a persistent listener that binds the UDS socket, publishes the session registration, and dispatches received messages:
```bash
ccm agent serve <name> [options]
```

**Options**:
* `--kind <generic|antigravity|codex>`: Agent type tag (default: `generic`).
* `--task <text>`: Task description for `ccm roster`.
* `--fifo <path>`: Write incoming JSON messages to a named pipe or file.
* `--inbox <dir>`: Save incoming messages as individual `<msg_id>.json` files in `<dir>`.
* `--exec <command>`: Shell command to execute on each message (message passed on stdin).
* `--daemon`: Fork into the background and record PID in `~/.local/state/claude-code-message/agents/<name>.pid`.
* `--once`: Exit after receiving the first message.
* `--timeout <seconds>`: Automatically terminate if no message is received within `<seconds>`.

### 4.2. `ccm agent wait`
Convenient one-shot blocking command designed for background tasks:
```bash
ccm agent wait <name> [--timeout 60] [--task "Waiting for task handover"]
```
* Spins up a temporary listener for `<name>` (or pops from an existing daemon inbox).
* Blocks until a message is received (or timeout expires).
* Outputs the received message JSON to stdout and exits with status 0.
* On timeout, exits with status 2.

### 4.3. `ccm agent status`
Displays all active agent bridge listeners and their PID/socket details:
```bash
ccm agent status [name]
```

### 4.4. `ccm agent stop`
Gracefully terminates a running agent daemon:
```bash
ccm agent stop <name>
```

### 4.5. Outbound Identity & Ephemeral CLI Routing (`--as`)
When sending messages from scripts or external agent runtimes outside an interactive Claude session:
* `--as <name>` (or env `CCM_AS` / `CCM_AGENT_NAME`): Explicitly sends as a registered agent bridge (or custom name), binding the agent's real socket address (`from="uds:..."`), session ID, and display name into the XML envelope.
* **Auto-Binding**: If `--as` is omitted and the caller is not inside a Claude session, CCM automatically binds to the local agent bridge if exactly one is active on the node.
* **Smart `<node>-cli` Resolution**: When a peer replies to an ephemeral `<node>-cli` sender, CCM automatically aliases the delivery to the single active agent bridge on that node, preventing `unknown member` failures. If no bridge is running, it yields an actionable diagnostic error.

---

## 5. Security & Boundary Guardrails

1. **Permission Parity**: Inbound messages from Claude contain `from-mode="bypass"` or `from-mode="prompting"`. The bridge preserves permission modes and adheres to recipient trust boundaries.
2. **Credential Redaction**: All audit logs, task previews, and status entries pass through `redact()` before being written to disk or transmitted over SSH links.
3. **Clean Lifecycle Cleanup**: On SIGINT/SIGTERM or normal exit, all temporary sockets (`/tmp/cc-socks/<pid>.sock`), session records (`~/.claude/sessions/<pid>.json`), key files, and roster entries are automatically removed, preventing stale registry pollution.
