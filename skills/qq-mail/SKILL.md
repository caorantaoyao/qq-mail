---
name: qq-mail
description: "Use when the user wants to operate QQ Mail from Codex: read inbox, search messages, send email, inspect attachments, mark read or unread, move, copy, delete, or append draft/archive mail through the qq_mail MCP server."
---

# QQ Mail

Use the `qq_mail` MCP server for QQ Mail operations.

## Routing

Use this skill when the user asks to:

- 查 QQ 邮箱、收件箱、未读邮件、邮件内容或附件
- 用 QQ 邮箱发送、回复草稿式内容、抄送或密送邮件
- 搜索、标记已读/未读、移动、复制、删除邮件
- 管理 QQ 邮箱文件夹里的邮件

## Tool Map

- `list_mailboxes`: list folders/mailboxes.
- `search_emails`: search messages with IMAP query syntax such as `ALL`, `UNSEEN`, `FROM "name@example.com"`, or `SUBJECT "keyword"`.
- `get_email`: read a message by id from a mailbox.
- `send_email`: send a message through QQ Mail SMTP.
- `mark_email`: mark a message read or unread.
- `add_flags` / `remove_flags`: modify IMAP flags such as `\\Flagged`.
- `copy_email` / `move_email`: copy or move a message across folders.
- `delete_email`: mark deleted, optionally expunge.
- `append_email`: append a simple message to a mailbox, useful for drafts or archive copies.

## Safety

Before sending or permanently deleting mail, confirm the recipient/action unless the user has explicitly provided all details and clearly asked to execute immediately.

IMAP does not reliably support editing an existing email body or subject in place. To change content, create a new draft/archive copy and delete or move the old message when requested.
