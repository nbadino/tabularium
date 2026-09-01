"""Fixture globali: DB temporaneo isolato per ogni run di test.

Deve essere importato prima dei moduli app: imposta TABULARIUM_ROOT su una
directory temporanea così config.py e db.py puntano a un DB pulito.
"""
from __future__ import annotations

import os
import asyncio
from contextlib import contextmanager
import sys
import tempfile
from http.cookies import SimpleCookie
from pathlib import Path

# Temp root condivisa per tutta la sessione di test.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="tabularium-tests-"))
os.environ["TABULARIUM_ROOT"] = str(_TMP_ROOT)
# I test della suite storica sono single-user: la modalità auth va disattivata
# PRIMA dell'import dei moduli app (config.AUTH_MODE viene letto a import).
os.environ["TABULARIUM_AUTH"] = "off"


if sys.version_info >= (3, 12):
    # Starlette 1.6 + AnyIO 4 può bloccarsi nel portal usato da
    # ``starlette.testclient.TestClient`` prima ancora della prima richiesta
    # anche su Python 3.12/3.13. Usiamo lo stesso client ASGI diretto per
    # mantenere la suite sincrona senza introdurre un workaround nel runtime.
    # Manteniamo il contratto sincrono dei test, ma eseguiamo l'app in un loop
    # asyncio dedicato tramite ASGITransport. Questa è una compatibilità della
    # suite, non una patch del runtime di produzione.
    import anyio.to_thread
    import httpx2 as _httpx
    from fastapi import testclient as _fastapi_testclient

    async def _run_direct(func, *args, **kwargs):
        # These are AnyIO run_sync controls, not arguments for the wrapped
        # callable (notably ``open`` has a strict positional signature).
        for key in ("limiter", "abandon_on_cancel", "cancellable"):
            kwargs.pop(key, None)
        return func(*args, **kwargs)

    class _Py314TestClient:
        __test__ = False

        def __init__(self, app, *args, **kwargs):
            self.app = app
            self.base_url = kwargs.pop("base_url", "http://testserver")
            self.follow_redirects = kwargs.pop("follow_redirects", True)
            self.headers = kwargs.pop("headers", None)
            self.cookies = {}
            initial_cookies = kwargs.pop("cookies", None)
            if initial_cookies:
                self.cookies.update(dict(initial_cookies))

        async def _request(self, method, url, **kwargs):
            # Starlette delegates sync endpoints to this function. The normal
            # worker-thread path is the part that deadlocks under Python 3.14.
            anyio.to_thread.run_sync = _run_direct
            headers = dict(self.headers or {})
            if self.cookies:
                headers["cookie"] = "; ".join(
                    f"{key}={value}" for key, value in self.cookies.items()
                )
            async with self.app.router.lifespan_context(self.app):
                async with _httpx.AsyncClient(
                    transport=_httpx.ASGITransport(app=self.app),
                    base_url=self.base_url,
                    follow_redirects=self.follow_redirects,
                    headers=headers,
                ) as client:
                    response = await client.request(method, url, **kwargs)
                    for raw_cookie in response.headers.get_list("set-cookie"):
                        parsed = SimpleCookie()
                        parsed.load(raw_cookie)
                        self.cookies.update(
                            {key: morsel.value for key, morsel in parsed.items()}
                        )
                    return response

        def request(self, method, url, **kwargs):
            return asyncio.run(self._request(method, url, **kwargs))

        def get(self, url, **kwargs):
            return self.request("GET", url, **kwargs)

        def post(self, url, **kwargs):
            return self.request("POST", url, **kwargs)

        def put(self, url, **kwargs):
            return self.request("PUT", url, **kwargs)

        def patch(self, url, **kwargs):
            return self.request("PATCH", url, **kwargs)

        def delete(self, url, **kwargs):
            return self.request("DELETE", url, **kwargs)

        def options(self, url, **kwargs):
            return self.request("OPTIONS", url, **kwargs)

        @contextmanager
        def stream(self, method, url, **kwargs):
            # ASGITransport buffers the response in this compatibility client;
            # the returned Response still exposes ``iter_lines`` and keeps the
            # synchronous TestClient contract used by the SSE tests.
            yield self.request(method, url, **kwargs)

        def __enter__(self):
            return self

        def close(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            self.close()

    _fastapi_testclient.TestClient = _Py314TestClient
