import asyncio
import io
import json
import queue
from types import SimpleNamespace
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import rust_analyzer_mcp as bridge


class RustAnalyzerTests(unittest.TestCase):
    def test_utf8_response_framing(self):
        payload = json.dumps({"id": 1, "result": "café → 型"}, ensure_ascii=False).encode()
        wire = f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload
        session = object.__new__(bridge.Session)
        session.responses = queue.Queue()
        session.process = SimpleNamespace(stdout=io.BytesIO(wire))
        session.read()
        self.assertEqual(session.responses.get(), {"id": 1, "result": "café → 型"})
        self.assertIsInstance(session.responses.get(), EOFError)

    def test_rejects_outside_repository(self):
        with self.assertRaisesRegex(ValueError, "inside"):
            bridge.lookup_symbol("/tmp/not-authorized.rs", "anything")

    def test_real_mcp_symbol_lookup(self):
        async def run():
            params = StdioServerParameters(
                command="/opt/homebrew/bin/uv",
                args=["run", "--no-project", "--with", "mcp", "python", str(bridge.Path(bridge.__file__))],
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    self.assertEqual([tool.name for tool in tools.tools], ["lookup_symbol"])
                    self.assertTrue(tools.tools[0].annotations.read_only_hint)
                    result = await client.call_tool("lookup_symbol", {
                        "file_path": str(bridge.ROOT / "scripts/swift-rust-bridge/src/lib.rs"),
                        "symbol_name": "adeze_probe_rust_increment",
                    })
                    self.assertFalse(result.is_error, result)
                    data = json.loads(result.content[0].text)
                    self.assertEqual(data["backend"], "rust-analyzer")
                    self.assertEqual(data["symbols"][0]["line"], 11)
                    self.assertEqual(data["symbols"][0]["name"], "adeze_probe_rust_increment")
                    print(json.dumps(data, indent=2))
                    result = await client.call_tool("lookup_symbol", {
                        "file_path": str(bridge.ROOT / "math-audio/crates/math-iir-fir/src/iir/biquad_bank.rs"),
                        "symbol_name": "BiquadBank",
                    })
                    self.assertFalse(result.is_error, result)
                    data = json.loads(result.content[0].text)
                    self.assertEqual(data["symbols"][0]["line"], 38)
                    self.assertEqual(data["symbols"][0]["kind"], 23)
                    print(json.dumps(data, indent=2))
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
