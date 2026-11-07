"""Fakes for the BeLive AI Hub media tests: urllib responses and a recording gateway."""

import io
import json
from urllib.error import HTTPError

MEDIA_KEY = "media-test-key"


class Headers(dict):
    def get(self, name, default=None):
        return next((v for k, v in self.items() if k.lower() == name.lower()), default)


class Response:
    def __init__(self, status=200, body=b"", headers=None):
        self.status, self.headers = status, Headers(headers or {})
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Gateway:
    """A fake of ``urllib``'s opener that answers by method and path and records every request."""

    def __init__(self):
        self.routes, self.requests = [], []

    def on(self, method, path, *responses):
        self.routes.append([method, path, list(responses)])

    def calls(self, method, prefix, exact=False):
        return [r for r in self.requests if r["method"] == method
                and (r["path"] == prefix if exact else r["path"].startswith(prefix))]

    def open(self, request, timeout):
        url = request.full_url
        path = url.split("/api/v1", 1)[1] if "/api/v1" in url else url
        self.requests.append({
            "method": request.get_method(), "url": url, "path": path, "timeout": timeout,
            "headers": {k.lower(): v for k, v in request.header_items()}, "data": request.data})
        for method, route, queue in self.routes:
            if method == request.get_method() and (path == route or (route.endswith("*") and path.startswith(route[:-1]))):
                answer = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(answer, Exception):
                    raise answer
                if answer.status >= 300:
                    raise HTTPError(url, answer.status, "status", answer.headers, io.BytesIO(answer.body))
                return answer
        raise AssertionError(f"unexpected request {request.get_method()} {path}")


def http_error(status, message="refused"):
    return Response(status, {"error": {"message": message}})


def catalogue_row(model_id, route, **fields):
    row = {"id": model_id, "name": model_id, "endpoints": [{"route": route, "status": "ready"}]}
    row.update(fields)
    return row
