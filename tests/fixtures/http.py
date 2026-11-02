"""A fake HTTP layer: canned responses per route, and a record of every request made.

Pass ``FakeHttp().transport`` wherever production code accepts an ``httpx``
transport. A request no route answers fails the test.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

Responder = httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]


@dataclass(frozen=True)
class Call:
    """One request the code under test made."""

    method: str
    url: httpx.URL
    headers: httpx.Headers
    body: Any

    @property
    def path(self) -> str:
        """The URL path, without the query string."""
        return self.url.path


def json_response(
    status: int = 200, payload: object = None, headers: dict[str, str] | None = None
) -> httpx.Response:
    """A response with a JSON body."""
    return httpx.Response(status, json=payload, headers=headers)


class FakeHttp:
    """Routes keyed by method and URL (query string ignored). Each route answers with
    its responders in order and keeps repeating the last one."""

    def __init__(self) -> None:
        self._routes: list[tuple[str, str, list[Responder]]] = []
        self.calls: list[Call] = []

    def on(self, method: str, url: str, *responses: Responder) -> FakeHttp:
        """Answer ``method url`` with ``responses``, replacing any earlier route for it."""
        key = (method.upper(), url.rstrip("/"))
        self._routes = [r for r in self._routes if (r[0], r[1]) != key]
        self._routes.append((*key, list(responses)))
        return self

    @property
    def transport(self) -> httpx.MockTransport:
        """An ``httpx`` transport backed by these routes."""
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        content = request.read()
        body = json.loads(content) if content else None
        self.calls.append(Call(request.method, request.url, request.headers, body))
        bare = str(request.url.copy_with(query=None)).rstrip("/")
        for method, url, responders in self._routes:
            if method == request.method and url == bare:
                responder = responders.pop(0) if len(responders) > 1 else responders[0]
                if isinstance(responder, Exception):
                    raise responder
                if isinstance(responder, httpx.Response):
                    return httpx.Response(
                        responder.status_code,
                        headers=responder.headers,
                        content=responder.content,
                        request=request,
                    )
                return responder(request)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")
