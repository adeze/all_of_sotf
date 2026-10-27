"""Repository-scoped, read-only symbol lookup through rust-analyzer."""

import atexit
import json
from pathlib import Path
import queue
import subprocess
import threading
import time

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parents[1]
SERVER = MCPServer("rust-analyzer")
LOCK = threading.RLock()


class Session:
    def __init__(self, root: Path):
        self.root = root
        self.responses = queue.Queue()
        self.writes = threading.Lock()
        self.next_id = 0
        self.process = subprocess.Popen(
            [str(Path.home() / ".cargo/bin/rust-analyzer")],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        threading.Thread(target=self.read, daemon=True).start()
        try:
            self.request("initialize", {
                "processId": None,
                "rootUri": root.as_uri(),
                "workspaceFolders": [{"uri": root.as_uri(), "name": root.name}],
                "capabilities": {"textDocument": {"documentSymbol": {
                    "hierarchicalDocumentSymbolSupport": True,
                }}},
                "initializationOptions": {"checkOnSave": False},
            })
            self.notify("initialized", {})
        except Exception:
            self.close()
            raise

    def send(self, message):
        payload = json.dumps({"jsonrpc": "2.0", **message}).encode()
        with self.writes:
            self.process.stdin.write(
                f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload
            )
            self.process.stdin.flush()

    def read(self):
        try:
            while True:
                headers = {}
                while line := self.process.stdout.readline():
                    if line == b"\r\n":
                        break
                    key, value = line.decode().split(":", 1)
                    headers[key.lower()] = value.strip()
                if not headers:
                    raise EOFError("rust-analyzer closed its output")
                message = json.loads(self.process.stdout.read(int(headers["content-length"])))
                if "method" in message:
                    if "id" in message:
                        # No workspace edits are authorized through this read-only tool.
                        result = {"applied": False} if message["method"] == "workspace/applyEdit" else None
                        if message["method"] == "workspace/configuration":
                            result = [None] * len(message.get("params", {}).get("items", []))
                        self.send({"id": message["id"], "result": result})
                else:
                    self.responses.put(message)
        except Exception as error:
            self.responses.put(error)

    def notify(self, method, params):
        self.send({"method": method, "params": params})

    def request(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + 45
        while True:
            message = self.responses.get(timeout=max(0.01, deadline - time.monotonic()))
            if isinstance(message, Exception):
                raise message
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"{method}: {message['error']}")
            return message.get("result")

    def close(self):
        if self.process.poll() is None:
            try:
                self.request("shutdown", None)
                self.notify("exit", None)
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
                self.process.wait()


session = None


@SERVER.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False, open_world_hint=False,
))
def lookup_symbol(file_path: str, symbol_name: str) -> dict:
    """Find a Rust symbol in a repository file; return its LSP kind and source location."""
    global session
    path = Path(file_path).resolve()
    if not path.is_relative_to(ROOT) or path.suffix != ".rs" or not path.is_file():
        raise ValueError(f"Expected an existing .rs file inside {ROOT}")
    root = next((p for p in path.parents if (p / "Cargo.toml").is_file()), None)
    if root is None:
        raise ValueError("No Cargo.toml found for this Rust file")
    with LOCK:
        if session is None or session.root != root or session.process.poll() is not None:
            if session is not None:
                session.close()
            session = Session(root)
        uri = path.as_uri()
        session.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": "rust", "version": 1,
            "text": path.read_text(),
        }})
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    symbols = session.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}}) or []
                    pending = list(symbols)
                    matches = []
                    while pending:
                        symbol = pending.pop()
                        pending.extend(symbol.get("children", []))
                        if symbol["name"] == symbol_name:
                            position = symbol.get("selectionRange", symbol["range"])["start"]
                            matches.append({
                                "name": symbol["name"], "kind": symbol["kind"],
                                "file": str(path), "line": position["line"] + 1,
                                "column": position["character"] + 1,
                            })
                    if matches:
                        return {"backend": "rust-analyzer", "project": str(root), "symbols": matches}
                except RuntimeError as error:
                    if "-32801" not in str(error):
                        raise
                if time.monotonic() >= deadline:
                    raise ValueError(f"Symbol {symbol_name!r} not found in {path}")
                time.sleep(0.2)
        finally:
            session.notify("textDocument/didClose", {"textDocument": {"uri": uri}})


@atexit.register
def close_session():
    if session is not None:
        session.close()


if __name__ == "__main__":
    SERVER.run(transport="stdio")
