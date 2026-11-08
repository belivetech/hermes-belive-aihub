"""Local fake Chat Completions endpoint for repro/stream_retries.sh. No key, no network beyond 127.0.0.1.

MODE: ok | error_frame (text, then an `error` frame, no [DONE]) | eof (text, then close, no
[DONE]) | tool_eof (half a tool call, then close) | tool_done_eof (first a complete `terminal` tool
call that appends a line to $TOOL_COUNTER, then, once a tool result is in the conversation, text
and a close without [DONE]) | tool_done_error (the same tool call, then text and an `error` frame). Every POST
appends one JSON line to LOG.
Usage: python3 fake_stream.py MODE LOG PORT"""
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
MODE, LOG, PORT = sys.argv[1], sys.argv[2], int(sys.argv[3])
def chunk(delta, finish=None):
    return "data: " + json.dumps({"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_GET(self):
        body = json.dumps({"object": "list", "data": [{"id": "m", "object": "model", "context_length": 131072}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        stream = bool(req.get("stream"))
        tool_results = sum(1 for m in req.get("messages", []) if m.get("role") == "tool")
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": round(time.time(), 2), "path": self.path, "stream": stream,
                                "msgs": len(req.get("messages", [])), "tools": len(req.get("tools") or []), "tool_results": tool_results}) + "\n")
        if not stream:
            # Non-streaming fallback: same failure in non-stream form.
            if MODE == "ok":
                body = json.dumps({"id": "c1", "object": "chat.completion", "created": 1, "model": "m",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
                self.send_response(200)
            else:
                body = json.dumps({"error": {"message": "upstream failed", "code": 502}}).encode()
                self.send_response(502)
            self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close"); self.end_headers()
        w = lambda s: (self.wfile.write(s.encode()), self.wfile.flush())
        if MODE == "ok":
            w(chunk({"role": "assistant", "content": "ok"})); w(chunk({}, "stop")); w("data: [DONE]\n\n"); return
        if MODE in ("tool_done_eof", "tool_done_error") and not tool_results:
            args = json.dumps({"command": "echo ran >> " + os.environ["TOOL_COUNTER"]})
            w(chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                "function": {"name": "terminal", "arguments": args}}]}))
            w(chunk({}, "tool_calls")); w("data: [DONE]\n\n"); return
        if MODE == "tool_eof":
            w(chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                "function": {"name": "terminal", "arguments": "{\"command\": \"echo hi"}}]}))
        else:
            w(chunk({"role": "assistant", "content": "Partial answer"}))
        if MODE in ("error_frame", "tool_done_error"):
            w("data: " + json.dumps({"error": {"message": "upstream stream failed", "code": 502}}) + "\n\n")
        # eof / tool_eof / error_frame: close without [DONE]
        self.close_connection = True
ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
