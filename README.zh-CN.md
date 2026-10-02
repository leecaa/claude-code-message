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
| `ccm log [-f] [-n N] [--ev 事件,…]` | 审计日志 |
| `ccm status` / `ccm doctor` | 健康检查（计数器在断线重连后不清零） |
| `ccm pair` / `unpair` / `up` / `down` / `restart` | 生命周期管理 |

## 消息信任与首次发送

agent 通过 `ccm send` / `ccm broadcast` 发消息时，hooks 会在首次及后续发送前记录**当前**权限模式，不再通过进程启动参数猜测。原生 `SendMessage` 的权限声明则原样保留；新会话先公布身份再转发消息，不需要先发一条消息“预热”。

看到 `unidentified session` / `did not attest its permission mode` 时，应更新插件并重新加载 hooks 或重启会话。旧版 `from-mode="default"` 不符合协议；修复后只发送 `bypass` / `prompting`，无法确认时不声明权限类别。仅修改源码不会更新已安装插件或运行中的中继。

**可信身份不等于无条件授权**：默认策略下，权限类别相同的消息直接投递，不同类别或未知来源仍可能需要批准。CCM 不会伪装 bypass，也不自动设置全局 `crossSessionInbound="accept"`（该选项会放行所有入站消息，不能只筛出“由 Claude 发起”的消息）。普通 shell、无可验证祖先进程的后台进程，以及权限信息不足的 plan 模式，不会被冒充成 bypass 会话。plan 模式建议用原生 `SendMessage`。

详见 [消息身份、修复原理与验证边界](docs/MESSAGE-TRUST.md)。命令返回 `ok` 只证明 socket 写入成功，是否真正送达需要接收方确认。

## 文档

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：设计、生命周期、审计字段、安全（英文，单一事实来源）
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

许可证：MIT
