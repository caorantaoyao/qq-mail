#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server.py"


def rpc(proc: subprocess.Popen, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("MCP server returned no response")
    response = json.loads(line)
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Connect to QQ Mail and list mailboxes.")
    args = parser.parse_args()

    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        init = rpc(proc, "initialize", {"protocolVersion": "2024-11-05"}, 1)
        tools = rpc(proc, "tools/list", {}, 2)
        names = [tool["name"] for tool in tools["tools"]]
        expected = {"list_mailboxes", "search_emails", "get_email", "send_email", "delete_email"}
        missing = sorted(expected.difference(names))
        if missing:
            raise RuntimeError(f"Missing expected tools: {missing}")
        print(json.dumps({"initialized": init["serverInfo"], "tool_count": len(names)}, ensure_ascii=False))

        if args.live:
            result = rpc(proc, "tools/call", {"name": "list_mailboxes", "arguments": {}}, 3)
            print(result["content"][0]["text"])
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
