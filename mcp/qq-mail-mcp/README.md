# QQ Mail MCP

本目录是一个本地 stdio MCP 服务，用 `QQ 邮箱` 的 IMAP/SMTP 能力操控邮箱。

## 能力

- `list_mailboxes`: 列出邮箱文件夹
- `search_emails`: 搜索邮件
- `get_email`: 读取邮件正文和附件元数据，可选返回附件 base64
- `send_email`: 发送邮件，支持 cc/bcc/html/base64 附件
- `mark_email`: 标记已读/未读
- `add_flags`: 添加 IMAP 标记
- `remove_flags`: 移除 IMAP 标记
- `copy_email`: 复制邮件到其他文件夹
- `move_email`: 移动邮件到其他文件夹
- `delete_email`: 删除邮件，可选 `expunge=true` 永久清理
- `append_email`: 追加邮件到指定文件夹，可用于草稿/归档

## 环境变量

不要把授权码提交进代码。运行前设置：

```bash
export QQ_MAIL_USER="caoran.taoyao@qq.com"
export QQ_MAIL_AUTH_CODE="你的 QQ 邮箱 SMTP/IMAP 授权码"
```

当前目录也提供了本地 `.env` 文件，可以这样加载：

```bash
set -a
source .env
set +a
```

可选覆盖：

```bash
export QQ_MAIL_IMAP_HOST="imap.qq.com"
export QQ_MAIL_IMAP_PORT="993"
export QQ_MAIL_SMTP_HOST="smtp.qq.com"
export QQ_MAIL_SMTP_PORT="465"
```

## Codex MCP 配置示例

把下面配置加入 Codex 的 MCP 配置文件。建议用环境变量注入授权码，不要明文写授权码。

```toml
[mcp_servers.qq_mail]
command = "python3"
args = ["/Users/bytedance/novels/qq-mail-mcp/server.py"]
env = { QQ_MAIL_USER = "caoran.taoyao@qq.com", QQ_MAIL_AUTH_CODE = "通过环境变量或安全配置注入" }
```

如果你的 Codex 配置不支持从 shell 环境继承 `QQ_MAIL_AUTH_CODE`，可以临时把授权码写进 `env`，但这会把密钥落盘；用完后应删除并重新生成 QQ 邮箱授权码。

## 本地自检

只验证 MCP 协议，不连接网络：

```bash
python3 scripts/smoke_mcp.py
```

真实邮箱连接测试：

```bash
export QQ_MAIL_USER="caoran.taoyao@qq.com"
export QQ_MAIL_AUTH_CODE="你的授权码"
python3 scripts/smoke_mcp.py --live
```

## 邮件“修改”的边界

IMAP 邮件本体通常不可原地编辑。实际可修改的是：

- 已读/未读状态
- 星标/删除等 flags
- 所在文件夹

如果要“改正文/主题”，可靠做法是创建新邮件或草稿，再删除旧邮件。
