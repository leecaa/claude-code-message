# Claude Code Message

[English](README.md) | 简体中文

> **免责声明**：Claude Code Message 是由社区维护的非官方开源项目，与 Anthropic 没有任何从属或背书关系。Claude 与 Claude Code 均为 Anthropic, PBC 的商标。本项目依赖 Claude Code 内部未公开协议，相关行为可能随版本迭代发生变动。

**跨机器的 [Claude Code](https://code.claude.com) 会话群聊。**

- 其他机器上的会话会以 `<机器>-<会话名>` 的名字出现在本机，和本机会话一样。直接用内置的 `SendMessage` 就能发消息，对方也用同样的方式回复。
- 每个会话都会**登记自己在做什么**：默认取用户最新一条输入，agent 也可以自己更新。新会话启动时会拿到成员名单，据此决定要 @ 谁。
- `ccm broadcast` 可以群发给所有人，也可以按机器或名字筛选。
- 加入、退出、任务变更、单发、中转、群发都会写进每台机器各自的**审计日志**。
- 整个工具由一个纯 Python 标准库文件和一个 Claude Code 插件（skill + hooks）组成。机器之间走 SSH，不需要服务端，也不开放端口。
- **支持平台**：macOS (Darwin) 与 Linux (POSIX)。Windows 用户可通过 WSL 运行（因依赖 POSIX 套接字与路径规范，原生 Windows 暂不支持）。

## 快速开始

```bash
# 在你日常工作的机器（hub）上
git clone https://github.com/leecaa/claude-code-message.git ~/src/claude-code-message
python3 ~/src/claude-code-message/bin/ccm init --label laptop

# 请确保 ~/.local/bin 在 PATH 中（如在 ~/.zshrc 或 ~/.bashrc 中添加 export PATH="$HOME/.local/bin:$PATH"）
# 每台其他机器执行一次。对方只需要 ssh、python3 和 Claude Code，其余由 pair 自动安装
ccm pair server --remote-label server
ccm doctor
```

> **注意**：请保留 `~/src/claude-code-message` 目录（若移动路径需重新执行 `ccm init`），因 `ccm init` 会将该目录注册为本地插件源路径。

装好之后，在任意 Claude Code 会话里说：“问一下负责 API 的会话有没有改过鉴权接口”。agent 会查看成员名单，找到合适的会话去 @。

| 命令 | 说明 |
|---|---|
| `ccm roster` | 成员、所在机器、在做什么 |
| `ccm send 名字 内容` / `ccm broadcast 内容 [--node N] [--local] [--match 正则]` | 从命令行或 agent 的 Bash 发消息 |
| `ccm task [内容]` | 查看或设置当前会话的任务 |
| `ccm agent serve` / `wait` / `status` / `stop` | 将 Antigravity、Codex 及外部 Agent 桥接为对等成员 |
| `ccm log [-f] [-n N] [--ev 事件,…]` | 审计日志 |
| `ccm status` / `ccm doctor` | 健康检查（计数器在断线重连后不清零） |
| `ccm pair` / `unpair` / `up` / `down` / `restart` | 生命周期管理 |

## 回执与有界重发

`read_receipts=true`、`retry_enabled=true` 缺省开启，两个开关独立：

```bash
ccm config set-read-receipts false
ccm config set-retry false
ccm config set-reliability '{"message_ttl":3600,"max_attempts":128}'
ccm receipts --json              # 发送侧事实，不输出正文
ccm receipts --inbox --json      # 仅当前真正拥有的接收SID
ccm read-ack DELIVERY_ID         # 接收会话明确声明已确认
ccm retry DELIVERY_ID            # 原消息仍安全可发时的一次明确重试
ccm retry DELIVERY_ID --inbox     # 接收端仅写前失败的安全重试
```

`queued` 只表示本机SQLite事务接受；`peer_received` 只表示配对CCM端提交了
inbox与去重记录，都不表示Claude已读。`socket_written_unconfirmed` 是原生socket
交接，native `delivered` 只是准入队列。唯一read式事实叫 **`receiver_acknowledged`**：
实际祖先拥有的目标SID在交接后主动声明。模型消费、人类已读和用户批准仍unknown；
hook、查询、idle、Stop都不生成这种声明，也不解除held/refused等权限边界。

新端以同一delivery ID有界重放并持久去重，缺省退避2–300秒、±20% jitter、ACK等待
10秒、最多512次实际尝试、固定TTL24小时。节点／去重库身份变化暂停旧队列，不按
同名新SID改投；广播冻结目标后单事务接受。`sid:SID`可选已观察且唯一绑定的离线路由。
没有存活worker就不会自动前进；既有`ccm up`也支持零peers的本地恢复，不另建broker。

native dispatch预约后崩溃、部分写或写后commit失败都留`handoff_unknown`并暂停，
不能因超时／缺回执再次注入。旧端一次写出后永久unconfirmed，升级不追认可重放。
不承诺端到端exactly-once或保证最终送达。关闭retry停止后台timer/reconnect/restart，
当前明确send／安全retry使用独立持久的一次机会，不能重置普通ingress许可。重复正文
不新增native尝试；反应式peer_received仅为传输响应，仍受持久attempt／lease／退避约束，
不是无限无记账ACK。首次handoff控制事实及read声明的generation依赖不靠自动retry。
它不能撤回已经启动的IO。接收端关闭read优先，
取消未开始的披露；重新开启不补发被抑制声明。

队列保存**原始正文**，不同于脱敏audit：目录0700、DB/journal0600，须当作私有数据。
活动正文最多10000条／64MiB，总store与journal预算256MiB并预留ACK空间；tombstone
覆盖不可变expiry＋时钟偏差／保留余量。时钟回拨、损坏配置或不兼容schema fail closed。
不能删去重库或恢复部分状态强行重发。详见[架构](docs/ARCHITECTURE.md)及
[安全说明](SECURITY.md)。

## 消息信任与首次发送

agent 通过 `ccm send` / `ccm broadcast` 发消息时，hooks 会在首次及后续发送前记录**当前**权限模式，不再通过进程启动参数猜测。原生 `SendMessage` 的权限声明则原样保留；新会话先公布身份再转发消息，不需要先发一条消息“预热”。

看到 `unidentified session` / `did not attest its permission mode` 时，应更新插件并重新加载 hooks 或重启会话。旧版 `from-mode="default"` 不符合协议；修复后只发送 `bypass` / `prompting`，无法确认时不声明权限类别。仅修改源码不会更新已安装插件或运行中的中继。

**可信身份不等于无条件授权**：默认策略下，权限类别相同的消息直接投递，不同类别或未知来源仍可能需要批准。CCM 不会伪装 bypass，也不自动设置全局 `crossSessionInbound="accept"`（该选项会放行所有入站消息，不能只筛出“由 Claude 发起”的消息）。普通 shell、无可验证祖先进程的后台进程，以及权限信息不足的 plan 模式，不会被冒充成 bypass 会话。plan 模式建议用原生 `SendMessage`。

详见 [消息身份、修复原理与验证边界](docs/MESSAGE-TRUST.md)。旧版命令的 `ok` 只证明socket写入；新队列的 `queued` 只证明持久接受，实际交接与接收声明仍分别确认。

## 文档

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：设计、生命周期、审计字段、安全（英文，单一事实来源）
- [docs/i18n/zh-CN/AGENT-BRIDGE.md](docs/i18n/zh-CN/AGENT-BRIDGE.md)：Agent Bridge 架构规范，支持 Google Antigravity、OpenAI Codex 与外部自主 Agent
- [docs/i18n/zh-CN/SOP.md](docs/i18n/zh-CN/SOP.md)：安装、配对、升级、恢复、卸载的操作步骤
- [docs/PROTOCOL.md](docs/PROTOCOL.md)：Claude Code Message 依赖的 Claude Code 内部通信协议（英文）
- [SECURITY.md](SECURITY.md)：安全架构、威胁模型与漏洞披露流程

## 安全

- **连接与认证**：由 SSH 负责。
- **谁能投递消息**：收件 socket 只接受带有自己 token 的消息，而 token 只有同一个系统用户能读到，这和 Claude Code 本机会话之间的规则一样。
- **权限模式**：发件方的权限模式原样传给收件方。收件方模式不同时，会先扣下消息，等它的用户批准，这也和本机行为一致。
- **成员之间**：任何成员都可以给任何成员发消息，每条消息都有审计记录可查。详见 [SECURITY.md](SECURITY.md)。

## 注意

Claude Code Message 依赖 Claude Code 的**内部**协议（在 2.1.282 上测试通过）。请让所有机器保持同一个 Claude Code 版本，版本不一致时 `ccm doctor` 会提醒。消息只转发一跳：两台机器要互相看到，需要直接配对。

## 测试

`python3 -m unittest discover -s tests -v`

## 社区认可与交流

- 讨论支持：[LINUX DO](https://linux.do)

许可证：MIT

