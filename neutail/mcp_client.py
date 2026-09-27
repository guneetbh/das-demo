"""Internal MCP client — the same protocol `neutail/mcp_server.py` speaks
to external clients (Claude Desktop, etc.) now used for every INTERNAL
tool call too. Agents no longer call `runtime.invoke_tool()` directly;
they call `call_tool()` here, which round-trips through a real MCP
`ClientSession` connected to the same server via the SDK's own in-process
transport, `mcp.shared.memory.create_connected_server_and_client_session`.
Real protocol messages (JSON-RPC-shaped, real (de)serialization) — zero
sockets, zero subprocess. This is what makes "internal dispatch is
literally MCP" true without requiring a second OS process to be running
just for the demo to work at all.

Explicit init()/shutdown() rather than session_store.py/vector_store.py's
lazy-singleton pattern: the session's lifetime is an async context manager
wrapping a background task group (the server's own run loop) — that needs
a controlled enter/exit tied to the process's lifetime (a FastAPI lifespan,
or run_demo.py's own start/end), not something safe to lazily start on
first use and never close.

Concurrency note (from reading the SDK's shared/session.py): the one
shared ClientSession safely multiplexes many concurrent call_tool() calls
*as coroutines on the same event loop* — exactly the shape of many
concurrent FastAPI requests under uvicorn's single-worker model. It is
NOT safe to call from a second OS thread or a second event loop — never
wrap a call to this module in `asyncio.to_thread()` or a nested
`asyncio.run()`.
"""

import json
from contextlib import AsyncExitStack

from mcp import types
from mcp.shared.memory import create_connected_server_and_client_session

from neutail import policy
from neutail.mcp_server import mcp as _mcp_app

_stack: AsyncExitStack | None = None
_session = None


async def init() -> None:
    """Call once per process, before any call_tool() — a FastAPI lifespan
    startup hook, or the top of run_demo.py's async main(). Safe to call
    more than once; only the first call does anything."""
    global _stack, _session
    if _session is not None:
        return
    _stack = AsyncExitStack()
    _session = await _stack.enter_async_context(
        create_connected_server_and_client_session(_mcp_app)
    )


async def shutdown() -> None:
    """Call once per process, on the way out."""
    global _stack, _session
    if _stack is not None:
        await _stack.aclose()
    _stack = None
    _session = None


async def call_tool(caller: str, tool: str, **kwargs) -> dict:
    """Replaces what used to be a direct `runtime.invoke_tool(caller, tool,
    **kwargs)` call — same signature and semantics from the caller's point
    of view, but now a real MCP round trip (in-process, no network) to the
    same policy-checked, audit-logged handler, via mcp_server.py's tool
    registry.

    Exception remapping: mcp_server.py's tool wrappers catch PolicyDenied/
    LookupError/ValueError and return an explicit CallToolResult with
    structuredContent={"error_type": ..., "reason": ...} rather than
    letting FastMCP's default handling collapse the exception into an
    untyped error string. This reconstructs the original exception type
    here, so every existing `except policy.PolicyDenied` / `except
    LookupError` / `except ValueError` call site elsewhere in the codebase
    needs zero changes."""
    if _session is None:
        raise RuntimeError("mcp_client.init() must be called before call_tool()")

    result: types.CallToolResult = await _session.call_tool(tool, {"caller": caller, **kwargs})

    if result.isError:
        detail = result.structuredContent or {}
        error_type = detail.get("error_type")
        reason = detail.get("reason")
        fallback_text = result.content[0].text if result.content and hasattr(result.content[0], "text") else None
        if error_type == "PolicyDenied":
            raise policy.PolicyDenied(caller, tool, reason or fallback_text or "denied")
        if error_type == "LookupError":
            raise LookupError(reason or fallback_text or f"no such tool: '{tool}'")
        if error_type == "ValueError":
            raise ValueError(reason or fallback_text or "invalid input")
        # A tool NAME that isn't registered at all (as opposed to a
        # registered tool rejecting bad input) never reaches mcp_server.py's
        # _dispatch()/structuredContent handling — FastMCP's own dispatcher
        # rejects it first, with this exact message. Previously,
        # runtime.invoke_tool() raised LookupError for this same case
        # (contracts.get() on an unknown name) — restore that behavior so
        # api.py's `except LookupError -> 404` still fires for it instead
        # of falling through to an unhandled 500.
        if fallback_text and fallback_text.startswith("Unknown tool:"):
            raise LookupError(fallback_text)
        # Anything else unmapped — a genuine bug in the handler, not one of
        # the known cases. Surface it clearly rather than fail parsing it.
        raise RuntimeError(f"MCP tool '{tool}' failed: {fallback_text or reason or 'unknown error'}")

    if result.structuredContent is not None:
        return result.structuredContent
    if result.content and hasattr(result.content[0], "text"):
        return json.loads(result.content[0].text)
    return {}
