# 跨 Agent 桥接架构：将 Antigravity 与 Codex 等外部 Agent 接入 Claude Code Message

> **特性状态：探索性 / 可选阶段 (Experimental / Optional)**  
> **推荐生产级闭环架构**：不对称协作模式 —— **下行指令 (Claude ➔ AGY)** 采用 `tmux send-keys` 注入提示词拉起执行；**上行回传 (AGY ➔ Claude)** 采用成熟的原生 `ccm send` 单向高效回报。

## 1. 概述与架构分级

**Claude Code Message (CCM)** 本质上是一套跨主机的 AI Agent 对等会话协作网络。虽然最初针对 Claude Code 原生交互会话设计，但其底层的 Unix Domain Socket (UDS) 进程间通信（IPC）机制与会话发现注册表可以被通用化扩展，从而支持 **Google Antigravity (AGY)**、**OpenAI Codex** 等第三方自主 Agent 接入。

### 1.1. 架构分级与工程选型

经过多设备实机审计（涵盖消息投递、自动唤醒与结构化已读回执），跨 Agent 协同分为两个实践层级：

```
┌────────────────────────────────────────────────────────────────────────┐
│                   Tier 1: 推荐生产级闭环架构                            │
│                 (不对称确定性协作模式，当前稳定推荐)                       │
└────────────────────────────────────────────────────────────────────────┘
  下行指令 (Claude ➔ AGY):                                                
    Claude / 总控通过 tmux 会话注入提示词拉起执行：                       
    `tmux send-keys -t agy "执行 Task #X 提示词..." Enter`                
    ➔ 保证确定性上下文启动，避免空闲 Agent 无法无感唤醒的问题。          

  上行回传 (AGY ➔ Claude):                                                 
    AGY 完成任务后通过原生 CCM 单向回传：                                  
    `ccm send --as mac-agy "mac任务汇总" "Task #X 终态汇报: ..."`          
    ➔ 100% 可靠、零额外开销、原生写入 Claude Code 会话队列。              

──────────────────────────────────────────────────────────────────────────

┌────────────────────────────────────────────────────────────────────────┐
│                   Tier 2: 探索性全双工守护模式                          │
│                        (可选 / 实验阶段)                               │
└────────────────────────────────────────────────────────────────────────┘
  双向 IPC 模拟 (`ccm agent serve` / `wait` / Inbox 监听):                
    - 为外部 Agent 模拟 Claude 原生 UDS 会话注册表与收件箱。              
    - 依赖宿主环境的唤醒能力（如 AGY 子代理轮询、Stop Hook 或 wait 阻塞）。
    - 已知工程局限：由于 IDE / 终端宿主沙箱限制，无人干预下的自动唤醒与   
      结构化已读状态 (`receiver_acknowledged`) 闭环仍具不确定性，现阶段    
      定位于探索与实验特性。                                             
```

---

## 2. 核心协议与交付契约

外部 Agent 要接入 CCM 网状网络，必须完整遵守 CCM 的对等通信协议（参见 [PROTOCOL.md](../../PROTOCOL.md)）：

```
┌──────────────────────────────────────────┐
│   Claude Code / 远程节点会话              │
└──────────────────────────────────────────┘
                     │
                     ▼ ccm send <agent-name> "..."
┌──────────────────────────────────────────┐
│       Unix Domain Socket (UDS)           │
│      /tmp/cc-socks/<pid>.sock (0600)     │
└──────────────────────────────────────────┘
                     │
                     ├─ 帧 1 (鉴权): {"type":"auth","token":"<peerToken>"}
                     ├─ 帧 2 (消息): {"msgV":1,"msg_id":"...","type":"user",...}
                     ▼
┌──────────────────────────────────────────┐
│           CCM Agent Bridge 监听端        │
│   (接收、校验、审计、落盘与唤醒分发)      │
└──────────────────────────────────────────┘
       │                              │
       ▼                              ▼
┌──────────────┐              ┌──────────────┐
│ Antigravity  │              │    Codex     │
│ (响应式后台唤醒)│              │ (Hooks/IPC)  │
└──────────────┘              └──────────────┘
```

### 2.1. 会话注册表 (`~/.claude/sessions/<pid>.json`)
每个 Agent Bridge 监听端在上线时会写入真实的会话元数据：
* `pid` *(int)*：拥有该 Socket 的真实活跃操作系统进程 ID。
* `name` *(str)*：会话名称与寻址目标（如 `mac-antigravity`, `antigravity-worker`, `mac-codex`）。
* `messagingSocketPath` *(str)*：收件箱 UDS 路径，权限严格为 `0600`。
* `procStart` *(str)*：**活体校验核心令牌**。必须与操作系统内核给出的进程启动时间完全一致（macOS: `LC_ALL=C TZ=UTC ps -o lstart= -p <pid>`；Linux: `/proc/<pid>/stat` 字段 22）。
* `peerProtocol`：`1`。
* `status`：`"idle"` 或 `"busy"`。
* `agent`：`"ccm-agent-<kind>"`。
* `ccmTask`：当前工作任务描述，直接展示于 `ccm roster`。

### 2.2. 鉴权密钥文件 (`~/.claude/sessions/<pid>.<hash>.key`)
路径为 `<pid>.<sha256(path.resolve(sock))>.key`，权限 `0600`，记录随机生成的 `peerToken`。

### 2.3. 数据线传输格式 (Wire Protocol)
每个入站连接通过两行换行分隔的 JSON 帧完成交互：
1. **鉴权帧**：`{"type":"auth","token":"<peerToken>"}`
2. **消息帧**：
   ```json
   {
     "msgV": 1,
     "msg_id": "939751c5-...",
     "type": "user",
     "priority": "next",
     "from": "uds:<sender_sock>",
     "message": {
       "role": "user",
       "content": "<cross-session-message from=\"uds:<sender_sock>\" from-name=\"mac任务汇总\" from-mode=\"bypass\">\n你好，请进行验证\n</cross-session-message>"
     }
   }
   ```

---

## 3. 各 Agent 运行时的唤醒与摄入策略

### 3.1. Google Antigravity (AGY) 接入方式

Antigravity 原生具备 `<messaging>` 响应式唤醒机制：
> *“系统在以下情况自动恢复执行：后台任务完成或发送通知，或收到来自子 Agent / Peer Agent 的消息。”*

#### 模式 A：单次阻塞式 / 后台任务自动唤醒 (`ccm agent wait`)
在 Antigravity 交互中，直接下发后台命令：
```bash
ccm agent wait mac-antigravity --timeout 3600
```
* Antigravity 将该命令作为 Background Task 挂起执行。
* 当 Claude 执行 `ccm send mac-antigravity "内容"` 时，`ccm agent wait` 接收帧，打印 JSON 到 stdout 并以退出码 0 退出。
* Antigravity 检测到后台任务完成，**立即触发 Reactive Wakeup，将消息作为高优先级系统消息直接注入下一个推理 Turn**！实现真正的“即来即醒”！

#### 模式 B：常驻守护进程 + 钩子注入 (`hooks.json`)
启动常驻 Agent 监听器：
```bash
ccm agent serve mac-antigravity --daemon --inbox ~/.cache/agent-ccm/antigravity-inbox
```
在 `.agents/hooks.json` 中配置 `Stop` 或 `PreInvocation` 钩子：
* 当检测到 inbox 存在未读消息时，`Stop` 钩子返回：
  ```json
  {
    "decision": "continue",
    "reason": "[收到 CCM 消息]: <内容>"
  }
  ```
  拦截 Agent 停机并强行继续执行。

---

### 3.2. OpenAI Codex 接入方式

Codex 拥有 `~/.codex/hooks.json` 及内部 IPC 控制接口。

#### 模式 A：命令回调适配器 (`--exec`)
```bash
ccm agent serve mac-codex --exec '/Users/mac/scripts/codex-handler.sh'
```
收到消息时，Bridge 自动唤起脚本，通过 stdin 传入完整的 JSON 载荷，脚本可调用 Codex CLI 继续执行。

#### 模式 B：管道与文件队列 (`--fifo` / `--inbox`)
```bash
ccm agent serve mac-codex --fifo ~/.cache/codex-inbox.fifo
```

---

## 4. CLI 命令速查 (`ccm agent`)

### 4.1. `ccm agent serve`
启动常驻监听服务：
```bash
ccm agent serve <name> [选项]
```
- `--kind <generic|antigravity|codex>`：Agent 类型（默认 `generic`）。
- `--task <文本>`：展示在花名册中的任务。
- `--fifo <路径>`：写入收到的消息至命名管道或文件。
- `--inbox <目录>`：将收到的消息独立存储为 `<msg_id>.json`。
- `--exec <命令>`：收到消息后触发执行的 Shell 命令（消息通过 stdin 输入）。
- `--daemon`：后台守护进程运行。
- `--once`：收完一条消息后自动退出。
- `--timeout <秒数>`：超时自动退出。

### 4.2. `ccm agent wait`
专为 Agent 后台任务设计的单次阻塞命令：
```bash
ccm agent wait <name> [--timeout 60] [--task "等待任务派发"]
```
收到消息后以 JSON 输出到终端并以状态码 0 退出；超时退出码为 2。

### 4.3. `ccm agent status`
查看当前活跃的 Agent Bridge 监听器与 PID 信息：
```bash
ccm agent status [name]
```

### 4.4. `ccm agent stop`
平稳停止指定名称的 Agent 守护进程：
```bash
ccm agent stop <name>
```

### 4.5. 发件身份绑定与 `<node>-cli` 智能解析 (`--as`)
外部 Agent 或脚本在非 Claude 会话环境中调用 `ccm send` / `ccm broadcast` 时：
* `--as <name>`（或环境变量 `CCM_AS` / `CCM_AGENT_NAME`）：明确指定以特定 Agent Bridge 身份发信，自动绑定该 Agent 的真实 UDS 路径（`from="uds:..."`）、会话 ID 及名称到消息信封中。
* **单桥接自动绑定**：若未显式传参 `--as`，且调用者不具备 Claude 祖先进程，CCM 会在当前节点仅运行单个活跃 Agent Bridge 时自动推断并绑定该 Agent 身份。
* **`<node>-cli` 智能别名与诊断**：当对端会话依据信封默认标签回复 `<node>-cli` 时，CCM 会自动将其别名路由到该节点唯一的活跃 Agent Bridge；若无桥接运行，则输出清晰诊断提示而非生硬的 `unknown member` 报错。

---

## 5. 安全、脱敏与生命周期清理

1. **权限类别一致性 (Permission Parity)**：支持解析并传递 `from-mode="bypass"` / `prompting`，严格遵循双向信任边界。
2. **凭据安全脱敏 (Redaction)**：所有流经 Bridge 的预览文本、任务名均经过 `redact()` 过滤，杜绝 API Key 或个人 Token 泄漏到公共审计流。
3. **干净退出清理 (Clean Teardown)**：进程退出时自动销毁 Socket、Key 文件、Session 注册项及 Roster 记录，绝不残留无效死信。
