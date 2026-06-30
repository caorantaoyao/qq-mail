#!/usr/bin/env python3
from __future__ import annotations
import base64
import email
import email.policy
import imaplib
import json
import os
import smtplib
import ssl
import sys
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from typing import Any


SERVER_NAME = "qq-mail-mcp"
SERVER_VERSION = "0.1.0"


def load_env_file() -> None:
    path = os.environ.get("QQ_MAIL_ENV_FILE")
    if not path:
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except FileNotFoundError:
        raise McpError(-32000, f"QQ_MAIL_ENV_FILE not found: {path}")


class McpError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise McpError(-32000, f"Missing required environment variable: {name}")
    return value


def imap_host() -> str:
    return os.environ.get("QQ_MAIL_IMAP_HOST", "imap.qq.com")


def smtp_host() -> str:
    return os.environ.get("QQ_MAIL_SMTP_HOST", "smtp.qq.com")


def imap_port() -> int:
    return int(os.environ.get("QQ_MAIL_IMAP_PORT", "993"))


def smtp_port() -> int:
    return int(os.environ.get("QQ_MAIL_SMTP_PORT", "465"))


def qq_user() -> str:
    return env("QQ_MAIL_USER")


def qq_auth_code() -> str:
    return env("QQ_MAIL_AUTH_CODE")


def connect_imap() -> imaplib.IMAP4_SSL:
    client = imaplib.IMAP4_SSL(imap_host(), imap_port())
    client.login(qq_user(), qq_auth_code())
    return client


def decode_text(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def ok_or_raise(status: str, payload: list[bytes] | tuple[Any, ...], action: str) -> list[bytes] | tuple[Any, ...]:
    if status != "OK":
        detail = payload[0].decode("utf-8", "replace") if payload else "unknown error"
        raise McpError(-32001, f"{action} failed: {detail}")
    return payload


def parse_message(raw: bytes, include_body: bool, include_attachments: bool) -> dict[str, Any]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    headers = {
        "subject": decode_text(msg.get("Subject")),
        "from": decode_text(msg.get("From")),
        "to": decode_text(msg.get("To")),
        "cc": decode_text(msg.get("Cc")),
        "date": decode_text(msg.get("Date")),
        "message_id": decode_text(msg.get("Message-ID")),
    }
    try:
        parsed_date = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
        if parsed_date:
            headers["date_iso"] = parsed_date.isoformat()
    except Exception:
        pass

    result: dict[str, Any] = {"headers": headers, "attachments": []}
    text_parts: list[str] = []
    html_parts: list[str] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        disposition = part.get_content_disposition()
        filename = decode_text(part.get_filename())
        payload = part.get_payload(decode=True) or b""

        if disposition == "attachment" or filename:
            attachment = {
                "filename": filename,
                "content_type": content_type,
                "size": len(payload),
            }
            if include_attachments:
                attachment["content_base64"] = base64.b64encode(payload).decode("ascii")
            result["attachments"].append(attachment)
            continue

        if include_body and content_type == "text/plain":
            text_parts.append(part.get_content())
        elif include_body and content_type == "text/html":
            html_parts.append(part.get_content())

    if include_body:
        result["body_text"] = "\n".join(text_parts).strip()
        result["body_html"] = "\n".join(html_parts).strip()

    return result


def tool_list_mailboxes(args: dict[str, Any]) -> dict[str, Any]:
    with connect_imap() as client:
        status, data = client.list()
        ok_or_raise(status, data, "list mailboxes")
    mailboxes = [line.decode("utf-8", "replace") for line in data if line]
    return {"mailboxes": mailboxes}


def tool_search_emails(args: dict[str, Any]) -> dict[str, Any]:
    mailbox = args.get("mailbox", "INBOX")
    query = args.get("query", "ALL")
    limit = int(args.get("limit", 20))
    fetch_headers = bool(args.get("fetch_headers", True))

    with connect_imap() as client:
        status, _ = client.select(mailbox)
        ok_or_raise(status, _, f"select {mailbox}")
        status, data = client.search(None, query)
        ok_or_raise(status, data, "search emails")
        ids = (data[0].decode("ascii").split() if data and data[0] else [])[-limit:]

        emails: list[dict[str, Any]] = []
        for uid in reversed(ids):
            item: dict[str, Any] = {"id": uid}
            if fetch_headers:
                status, fetched = client.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID)] FLAGS)")
                ok_or_raise(status, fetched, f"fetch headers for {uid}")
                raw_header = b""
                flags = ""
                for part in fetched:
                    if isinstance(part, tuple):
                        raw_header = part[1]
                        meta = part[0].decode("utf-8", "replace")
                        flags = meta[meta.find("FLAGS") :] if "FLAGS" in meta else ""
                parsed = parse_message(raw_header, include_body=False, include_attachments=False)
                item.update(parsed["headers"])
                item["flags"] = flags
            emails.append(item)
    return {"mailbox": mailbox, "query": query, "count": len(emails), "emails": emails}


def tool_get_email(args: dict[str, Any]) -> dict[str, Any]:
    mailbox = args.get("mailbox", "INBOX")
    msg_id = str(args["id"])
    mark_seen = bool(args.get("mark_seen", False))
    include_attachments = bool(args.get("include_attachments", False))
    fetch_op = "RFC822" if mark_seen else "BODY.PEEK[]"

    with connect_imap() as client:
        status, _ = client.select(mailbox)
        ok_or_raise(status, _, f"select {mailbox}")
        status, fetched = client.fetch(msg_id, f"({fetch_op})")
        ok_or_raise(status, fetched, f"fetch email {msg_id}")

    raw = b""
    for part in fetched:
        if isinstance(part, tuple):
            raw = part[1]
            break
    if not raw:
        raise McpError(-32002, f"Email not found: {msg_id}")
    parsed = parse_message(raw, include_body=True, include_attachments=include_attachments)
    parsed["id"] = msg_id
    parsed["mailbox"] = mailbox
    return parsed


def tool_send_email(args: dict[str, Any]) -> dict[str, Any]:
    msg = EmailMessage()
    msg["From"] = args.get("from", qq_user())
    msg["To"] = ", ".join(args["to"]) if isinstance(args["to"], list) else args["to"]
    if args.get("cc"):
        msg["Cc"] = ", ".join(args["cc"]) if isinstance(args["cc"], list) else args["cc"]
    if args.get("bcc"):
        msg["Bcc"] = ", ".join(args["bcc"]) if isinstance(args["bcc"], list) else args["bcc"]
    msg["Subject"] = args.get("subject", "")
    body = args.get("body", "")
    html = args.get("html")
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")

    for attachment in args.get("attachments", []):
        data = base64.b64decode(attachment["content_base64"])
        maintype, _, subtype = attachment.get("content_type", "application/octet-stream").partition("/")
        msg.add_attachment(
            data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=attachment.get("filename", "attachment"),
        )

    recipients: list[str] = []
    for key in ("to", "cc", "bcc"):
        value = args.get(key)
        if isinstance(value, list):
            recipients.extend(value)
        elif value:
            recipients.append(value)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host(), smtp_port(), context=context) as server:
        server.login(qq_user(), qq_auth_code())
        server.send_message(msg, from_addr=qq_user(), to_addrs=recipients)
    return {"sent": True, "recipients": recipients}


def tool_mark_email(args: dict[str, Any]) -> dict[str, Any]:
    mailbox = args.get("mailbox", "INBOX")
    msg_id = str(args["id"])
    seen = bool(args["seen"])
    op = "+FLAGS" if seen else "-FLAGS"
    with connect_imap() as client:
        status, _ = client.select(mailbox)
        ok_or_raise(status, _, f"select {mailbox}")
        status, data = client.store(msg_id, op, "\\Seen")
        ok_or_raise(status, data, f"mark email {msg_id}")
    return {"id": msg_id, "mailbox": mailbox, "seen": seen}


def tool_add_flags(args: dict[str, Any]) -> dict[str, Any]:
    return store_flags(args, "+FLAGS")


def tool_remove_flags(args: dict[str, Any]) -> dict[str, Any]:
    return store_flags(args, "-FLAGS")


def store_flags(args: dict[str, Any], op: str) -> dict[str, Any]:
    mailbox = args.get("mailbox", "INBOX")
    msg_id = str(args["id"])
    flags = args["flags"]
    if isinstance(flags, list):
        flag_value = "(" + " ".join(flags) + ")"
    else:
        flag_value = str(flags)
    with connect_imap() as client:
        status, _ = client.select(mailbox)
        ok_or_raise(status, _, f"select {mailbox}")
        status, data = client.store(msg_id, op, flag_value)
        ok_or_raise(status, data, f"store flags for {msg_id}")
    return {"id": msg_id, "mailbox": mailbox, "operation": op, "flags": flags}


def tool_move_email(args: dict[str, Any]) -> dict[str, Any]:
    source = args.get("source_mailbox", "INBOX")
    dest = args["dest_mailbox"]
    msg_id = str(args["id"])
    with connect_imap() as client:
        status, _ = client.select(source)
        ok_or_raise(status, _, f"select {source}")
        status, data = client.copy(msg_id, dest)
        ok_or_raise(status, data, f"copy email {msg_id} to {dest}")
        status, data = client.store(msg_id, "+FLAGS", "\\Deleted")
        ok_or_raise(status, data, f"mark email {msg_id} deleted")
        status, data = client.expunge()
        ok_or_raise(status, data, "expunge source mailbox")
    return {"id": msg_id, "from": source, "to": dest, "moved": True}


def tool_copy_email(args: dict[str, Any]) -> dict[str, Any]:
    source = args.get("source_mailbox", "INBOX")
    dest = args["dest_mailbox"]
    msg_id = str(args["id"])
    with connect_imap() as client:
        status, _ = client.select(source)
        ok_or_raise(status, _, f"select {source}")
        status, data = client.copy(msg_id, dest)
        ok_or_raise(status, data, f"copy email {msg_id} to {dest}")
    return {"id": msg_id, "from": source, "to": dest, "copied": True}


def tool_delete_email(args: dict[str, Any]) -> dict[str, Any]:
    mailbox = args.get("mailbox", "INBOX")
    msg_id = str(args["id"])
    expunge = bool(args.get("expunge", False))
    with connect_imap() as client:
        status, _ = client.select(mailbox)
        ok_or_raise(status, _, f"select {mailbox}")
        status, data = client.store(msg_id, "+FLAGS", "\\Deleted")
        ok_or_raise(status, data, f"delete email {msg_id}")
        if expunge:
            status, data = client.expunge()
            ok_or_raise(status, data, "expunge mailbox")
    return {"id": msg_id, "mailbox": mailbox, "deleted": True, "expunged": expunge}


def tool_append_email(args: dict[str, Any]) -> dict[str, Any]:
    mailbox = args.get("mailbox", "Drafts")
    msg = EmailMessage()
    msg["From"] = args.get("from", qq_user())
    msg["To"] = args.get("to", "")
    msg["Subject"] = args.get("subject", "")
    msg.set_content(args.get("body", ""))
    flags = args.get("flags")
    date_time = None
    with connect_imap() as client:
        status, data = client.append(mailbox, flags, date_time, msg.as_bytes())
        ok_or_raise(status, data, f"append email to {mailbox}")
    return {"mailbox": mailbox, "appended": True}


TOOLS: dict[str, dict[str, Any]] = {
    "list_mailboxes": {
        "description": "List QQ Mail IMAP mailboxes/folders.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_list_mailboxes,
    },
    "search_emails": {
        "description": "Search emails in a mailbox using IMAP search syntax, e.g. ALL, UNSEEN, FROM \"a@b.com\", SUBJECT \"text\".",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mailbox": {"type": "string", "default": "INBOX"},
                "query": {"type": "string", "default": "ALL"},
                "limit": {"type": "integer", "default": 20},
                "fetch_headers": {"type": "boolean", "default": True},
            },
        },
        "handler": tool_search_emails,
    },
    "get_email": {
        "description": "Read one email by message sequence id from a mailbox.",
        "inputSchema": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "mailbox": {"type": "string", "default": "INBOX"},
                "id": {"type": "string"},
                "mark_seen": {"type": "boolean", "default": False},
                "include_attachments": {"type": "boolean", "default": False},
            },
        },
        "handler": tool_get_email,
    },
    "send_email": {
        "description": "Send an email through QQ Mail SMTP. Attachments must be base64 encoded.",
        "inputSchema": {
            "type": "object",
            "required": ["to"],
            "properties": {
                "to": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                "cc": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                "bcc": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "html": {"type": "string"},
                "attachments": {"type": "array", "items": {"type": "object"}},
            },
        },
        "handler": tool_send_email,
    },
    "mark_email": {
        "description": "Mark an email as read or unread.",
        "inputSchema": {
            "type": "object",
            "required": ["id", "seen"],
            "properties": {
                "mailbox": {"type": "string", "default": "INBOX"},
                "id": {"type": "string"},
                "seen": {"type": "boolean"},
            },
        },
        "handler": tool_mark_email,
    },
    "add_flags": {
        "description": "Add IMAP flags to an email, e.g. \\\\Flagged.",
        "inputSchema": {
            "type": "object",
            "required": ["id", "flags"],
            "properties": {
                "mailbox": {"type": "string", "default": "INBOX"},
                "id": {"type": "string"},
                "flags": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            },
        },
        "handler": tool_add_flags,
    },
    "remove_flags": {
        "description": "Remove IMAP flags from an email.",
        "inputSchema": {
            "type": "object",
            "required": ["id", "flags"],
            "properties": {
                "mailbox": {"type": "string", "default": "INBOX"},
                "id": {"type": "string"},
                "flags": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            },
        },
        "handler": tool_remove_flags,
    },
    "move_email": {
        "description": "Move an email by copying to another mailbox and deleting from the source mailbox.",
        "inputSchema": {
            "type": "object",
            "required": ["id", "dest_mailbox"],
            "properties": {
                "source_mailbox": {"type": "string", "default": "INBOX"},
                "dest_mailbox": {"type": "string"},
                "id": {"type": "string"},
            },
        },
        "handler": tool_move_email,
    },
    "copy_email": {
        "description": "Copy an email to another mailbox.",
        "inputSchema": {
            "type": "object",
            "required": ["id", "dest_mailbox"],
            "properties": {
                "source_mailbox": {"type": "string", "default": "INBOX"},
                "dest_mailbox": {"type": "string"},
                "id": {"type": "string"},
            },
        },
        "handler": tool_copy_email,
    },
    "delete_email": {
        "description": "Mark an email as deleted. Set expunge=true to permanently remove deleted messages from the mailbox.",
        "inputSchema": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "mailbox": {"type": "string", "default": "INBOX"},
                "id": {"type": "string"},
                "expunge": {"type": "boolean", "default": False},
            },
        },
        "handler": tool_delete_email,
    },
    "append_email": {
        "description": "Append a simple email message into a mailbox, useful for drafts or archival copies.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mailbox": {"type": "string", "default": "Drafts"},
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "flags": {"type": "string"},
            },
        },
        "handler": tool_append_email,
    },
}


def content_text(data: Any) -> list[dict[str, str]]:
    return [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}]


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "notifications/initialized":
        return None

    try:
        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": name,
                        "description": item["description"],
                        "inputSchema": item["inputSchema"],
                    }
                    for name, item in TOOLS.items()
                ]
            }
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name not in TOOLS:
                raise McpError(-32601, f"Unknown tool: {name}")
            result = {"content": content_text(TOOLS[name]["handler"](args))}
        else:
            raise McpError(-32601, f"Unknown method: {method}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except McpError as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": exc.code, "message": exc.message}}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32099, "message": str(exc)}}


def main() -> None:
    load_env_file()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
