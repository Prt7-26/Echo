#!/usr/bin/env python3
"""Tiny deterministic stand-in for `tui_gateway.entry`.

Speaks the exact wire protocol (frame shapes verified against the real gateway,
2026-06) so Echo's StdioSubprocessTransport + GatewayClient can be tested
end-to-end over real stdio pipes WITHOUT the heavy/nondeterministic real
backend. Used by LiveChecks when ECHO_APP_MOCK_GW points here.

Frames:
  event:    {"jsonrpc":"2.0","method":"event","params":{"type":..,"session_id":..,"payload":{..}}}
  response: {"jsonrpc":"2.0","id":N,"result":{..}}
"""
import json
import sys
import time


def emit(event, sid="mock", payload=None):
    params = {"type": event, "session_id": sid}
    if payload is not None:
        params["payload"] = payload
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "event", "params": params}) + "\n")
    sys.stdout.flush()


def respond(rid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()


def main():
    # Ready immediately (real gateway sends a skin payload; shape-compatible).
    emit("gateway.ready", payload={"skin": {"name": "echo"}})

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        if method == "session.list":
            respond(rid, {"sessions": [
                {"id": "mock1", "title": "Mock Session", "preview": "hello",
                 "message_count": 2, "started_at": 1781540000.0, "source": "mock"},
            ]})
        elif method == "session.create":
            respond(rid, {"session_id": "mock-new",
                          "info": {"model": "mock-model", "skills": {}, "tools": {}}})
        elif method == "session.resume":
            respond(rid, {"session_id": params.get("session_id", "mock1"),
                          "message_count": 1,
                          "messages": [{"role": "user", "text": "hi"},
                                       {"role": "assistant", "text": "hello there"}]})
        elif method == "prompt.submit":
            sid = params.get("session_id", "mock")
            respond(rid, {"ok": True})
            # Stream a tiny reply via events.
            emit("message.start", sid=sid)
            for chunk in ["Hello", ", ", "world", "."]:
                emit("message.delta", sid=sid, payload={"text": chunk})
                time.sleep(0.01)
            emit("message.complete", sid=sid, payload={
                "text": "Hello, world.", "status": "complete",
                "usage": {"input": 3, "output": 4, "total": 7, "calls": 1}})
        elif method == "session.interrupt":
            respond(rid, {"ok": True})
        elif method == "clarify.respond":
            respond(rid, {"ok": True})
        else:
            respond(rid, {"ok": True})

    return 0


if __name__ == "__main__":
    sys.exit(main())
