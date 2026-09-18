"""MCP client wrapper: launches the server over stdio, discovers tools, and logs every call.

Each review (or batch) opens one connection inside a single `async with` block, so the
server subprocess is started and shut down within one Streamlit script run.
"""

from __future__ import annotations

import json
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import Client, StdioServerParameters

from .config import MCP_CONNECT_TIMEOUT_S, MCP_TOOL_TIMEOUT_S, ROOT
from .schemas import ToolCallLog


class MCPToolError(RuntimeError):
    pass


def server_params(fault_inject: str | None = None) -> StdioServerParameters:
    env = {"PYTHONPATH": str(ROOT), "CG_FAULT_INJECT": fault_inject or ""}  # no API keys are passed to the server
    return StdioServerParameters(command=sys.executable, args=["-m", "campaignguard.mcp_server"], cwd=str(ROOT), env=env)


def _source_ids(payload: Any) -> list[str]:
    """Extract source IDs from a tool result for logging and citation validation."""
    ids: list[str] = []
    if not isinstance(payload, dict):
        return ids
    for s in payload.get("sources", []) or []:
        ids.append(s.get("source_id"))
    for r in payload.get("results", []) or []:
        ids.append(r.get("policy_id"))
    return [i for i in ids if i]


class MCPSession:
    def __init__(self, client: Client):
        self.client = client
        self.tools: list = []
        self.log: list[ToolCallLog] = []

    async def discover(self) -> list:
        result = await self.client.list_tools()
        self.tools = list(result.tools)
        return self.tools

    @property
    def tool_names(self) -> set[str]:
        return {t.name for t in self.tools}

    async def call(self, name: str, arguments: dict, requested_by: str = "orchestrator", retries: int = 1) -> dict:
        """Invoke a tool through the MCP protocol. Returns structured content or raises MCPToolError."""
        if name not in self.tool_names:
            self.log.append(ToolCallLog(tool=name, arguments=_safe_args(arguments), requested_by=requested_by,
                                        duration_ms=0.0, ok=False, error="tool not discovered"))
            raise MCPToolError(f"Tool '{name}' was not discovered on the MCP server")
        attempt = 0
        while True:
            t0 = time.perf_counter()
            try:
                res = await self.client.call_tool(name, arguments, read_timeout_seconds=MCP_TOOL_TIMEOUT_S)
                dur = (time.perf_counter() - t0) * 1000
                if res.is_error:
                    msg = " ".join(getattr(c, "text", "") for c in res.content)[:300]
                    self.log.append(ToolCallLog(tool=name, arguments=_safe_args(arguments), requested_by=requested_by,
                                                duration_ms=round(dur, 1), ok=False, error=msg))
                    raise MCPToolError(msg or f"{name} failed")  # tool/validation errors are not retried
                payload = res.structured_content
                if payload is None and res.content:
                    payload = json.loads(res.content[0].text)
                self.log.append(ToolCallLog(tool=name, arguments=_safe_args(arguments), requested_by=requested_by,
                                            duration_ms=round(dur, 1), ok=True, source_ids=_source_ids(payload)))
                return payload
            except MCPToolError:
                raise
            except Exception as exc:  # transport errors / timeouts: limited retry
                dur = (time.perf_counter() - t0) * 1000
                self.log.append(ToolCallLog(tool=name, arguments=_safe_args(arguments), requested_by=requested_by,
                                            duration_ms=round(dur, 1), ok=False, error=f"{type(exc).__name__}: {exc}"[:300]))
                if attempt >= retries:
                    raise MCPToolError(f"{name} failed after {attempt + 1} attempt(s): {type(exc).__name__}") from exc
                attempt += 1


def _safe_args(arguments: dict) -> dict:
    """Log validated argument shape without dumping full campaign bodies."""
    out = {}
    for k, v in (arguments or {}).items():
        if isinstance(v, dict) and "body" in v:
            v = {**v, "body": (v["body"][:80] + "…") if len(v["body"]) > 80 else v["body"]}
        out[k] = v
    return out


@asynccontextmanager
async def connect(fault_inject: str | None = None) -> AsyncIterator[MCPSession]:
    """fault_inject is for recovery tests/traces only (see mcp_server._maybe_fault)."""
    async with Client(server_params(fault_inject), read_timeout_seconds=MCP_CONNECT_TIMEOUT_S) as client:
        session = MCPSession(client)
        await session.discover()
        yield session
