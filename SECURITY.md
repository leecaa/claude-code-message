# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |
| < 0.2   | :x:                |

## Security Model

`claude-code-message` links Claude Code sessions across machines using standard system security primitives:

1. **Transport & Authentication**: The cross-machine communication runs entirely over SSH (`ssh -o BatchMode=yes`), relying on your existing key-based SSH infrastructure and access controls. No separate listening server ports are opened.
2. **Socket Security**: Unix domain sockets are created in per-user directories with restricted permissions (`0600`), accessible only by the local OS user.
3. **Session Token Verification**: Every message framed to an inbox socket requires the session's secret token, which is stored in files readable only by that same OS user.
4. **Credential Redaction**: Tasks, broadcast previews, and audit logs are filtered through a multi-pattern credential redactor (`redact()`) before storage or transmission to prevent leaking secrets, API keys, tokens, and passwords into agent context or logs.

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it privately:

- **Preferred**: Open a private advisory draft via [GitHub Private Vulnerability Reporting](https://github.com/leecaa/claude-code-message/security/advisories/new).
- Please include steps to reproduce the issue, the affected version, and any relevant logs or configuration snippets (with credentials redacted).

Please do **not** open public GitHub issues or discussions for undisclosed security vulnerabilities.
