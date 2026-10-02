# Claude Code Message 操作手册（SOP）

> 英文原版：[skills/claude-code-message/references/sop.md](../../../skills/claude-code-message/references/sop.md)。两者不一致时以英文版为准。

## 1. 接入前两台机器（A 主动连接 B）

在 **A**（hub，也就是你最常用的那台）上：

```bash
git clone https://github.com/leecaa/claude-code-message.git ~/src/claude-code-message
python3 ~/src/claude-code-message/bin/ccm init --label laptop    # 应看到 ✓ node label / ✓ plugin / ✓ shim
ssh -o BatchMode=yes server true                               # 必须不弹任何提示就返回
ccm pair server --remote-label server                          # 最后一行应为 ✓ link up: N member(s) on server visible here
ccm doctor                                                     # 应输出 all good
```

B 不需要预先准备。`pair` 会把 claude-code-message 复制到 B 的 `~/.local/share/claude-code-message`，并在 B 上执行 `ccm init`。

## 2. 再接入一台机器 C

在 hub 上运行 `ccm pair C --remote-label c`。连接是以 hub 为中心的星形：B 和 C 都能看到 hub 上的会话，但 B 和 C 彼此看不到。如果需要互相可见，在 B 上再 pair 一次 C。每一对机器只能由一边发起连接；如果对方已经在连接你，`pair` 会拒绝执行。

## 3. 日常使用

| 目的 | 命令 |
|---|---|
| 检查状态 | `ccm status`、`ccm doctor` |
| 查看成员 | `ccm roster` |
| 查看消息记录 | `ccm log -n 100`、`ccm log -f`（实时）、`ccm log --ev broadcast` |
| 升级后重启 | `ccm restart` |

已经在运行的会话需要执行 `/reload-plugins`，或退出后用 `claude --resume <id>` 恢复，才能加载新的 hooks。此前原生消息仍可收发，但 CLI 消息可能缺少当前权限快照。不要为了升级而终止他人的在途会话。

### 从普通终端发消息

在 Claude 会话之外运行 `ccm send` 或 `ccm broadcast` 时，发件人显示为 `<机器>-cli`，不声明权限类别。bypass 接收方会先扣下消息，等待用户批准。如果要无人值守协调，请在已加载 hooks 的会话内部发送：CLI 会识别真实的会话祖先进程，并使用 hooks 提供的当前权限模式，不通过启动参数猜测 bypass。权限对等规则及 plan 模式限制见 [消息信任说明](../../MESSAGE-TRUST.md)。

## 4. 升级

```bash
cd ~/src/claude-code-message && git pull
python3 bin/ccm init                         # 更新本机插件和 shim
ccm pair server --remote-label server        # 把新版本重新推送到对方并重建连接
```

每次建立连接时，发起方都会把自己的 `ccm` 推送到对方的 `~/.cache/claude-code-message/ccm.py`，所以连接两端的版本总是一致的。

**升级 Claude Code 之后**：这套协议是 Claude Code 内部使用的，没有公开承诺。请把所有机器都升级到同一版本，然后运行 `ccm doctor`。如果消息送不到，对照 `docs/PROTOCOL.md` 排查。

## 5. 解除配对与卸载

```bash
ccm unpair server
ccm down
claude plugin uninstall claude-code-message@claude-code-message
rm ~/.local/bin/ccm; rm -rf ~/.local/state/claude-code-message ~/.config/claude-code-message
```

## 6. 故障恢复

| 现象 | 处理 |
|---|---|
| 连接反复断开 | 查看 `~/.local/state/claude-code-message/daemon.log`。守护进程会自动重连，等待时间从 1 秒逐步退避到 60 秒 |
| 崩溃后残留 `server-*` 成员 | 运行 `ccm down && ccm up`，`down` 会顺带清理残留的镜像进程 |
| 名单里有已退出的会话 | 会话消失 2 分钟内会被自动清理 |
| 审计日志过大 | 超过 20 MB 会自动轮转为 `audit.jsonl.1` |

## 相关文件

| 路径 | 内容 |
|---|---|
| `~/.config/claude-code-message/config.json` | 本机 label、peers、`audit_preview_chars`（设为 0 表示日志不记录消息正文） |
| `~/.local/state/claude-code-message/audit.jsonl` | 审计日志（每台机器各一份） |
| `~/.local/state/claude-code-message/roster/` | 本机每个会话一个文件，记录它的任务 |
| `~/.local/state/claude-code-message/{daemon.log,status.json,counters.json}` | 守护进程日志、实时状态、累计计数 |
