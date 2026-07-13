"""A local stand-in for Google's `BidiGenerateContent` WebSocket endpoint,
scripted from a recorded JSON fixture (`tests/fixtures/gemini_live_replay/`).

This is what lets Phase 2's CI-blocking tests exercise `gemini_bridge.py`
deterministically without a real GEMINI_API_KEY or network access to Google
(Spec 04 §3: "contract tests against a Gemini Live fixture/replay harness
gate every merge... kept deliberately out of the PR-blocking path so vendor
flakiness never blocks a merge").

It is intentionally dumb: it doesn't validate the client's message shape
beyond dispatching on top-level key, it just replays the fixture's scripted
response list for each recognized incoming message type.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import websockets


class FakeGeminiLiveServer:
    def __init__(self, fixture_path: Path):
        self.fixture = json.loads(fixture_path.read_text())
        self.received_messages: list[dict] = []
        self.url: str = ""
        self._server: websockets.WebSocketServer | None = None

    async def __aenter__(self) -> "FakeGeminiLiveServer":
        self._server = await websockets.serve(self._handle, "localhost", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://localhost:{port}"
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, ws: websockets.WebSocketServerProtocol) -> None:
        responses = self.fixture["responses"]
        try:
            async for raw in ws:
                message = json.loads(raw)
                self.received_messages.append(message)

                if "setup" in message:
                    for scripted in responses.get("after_setup", []):
                        await ws.send(json.dumps(scripted))

                elif "clientContent" in message:
                    for scripted in responses.get("after_directive", []):
                        await ws.send(json.dumps(scripted))

                elif "realtimeInput" in message and "activityEnd" in message["realtimeInput"]:
                    for scripted in responses.get("after_activity_end", []):
                        await ws.send(json.dumps(scripted))

                # activityStart / audio chunks are recorded but not responded
                # to directly — Gemini's real reply only comes after
                # activityEnd.
        except websockets.exceptions.ConnectionClosed:
            # The client (gemini_bridge.py) or the test harness tearing
            # itself down mid-test is an expected, non-graceful close here —
            # not a bug to surface as a noisy traceback in test output.
            pass


class FakeGeminiLiveServerHandle:
    """Runs a FakeGeminiLiveServer on its own thread + event loop so it can
    be reached from a Starlette `TestClient`, which drives the FastAPI app
    (and therefore `gemini_bridge.py`'s real client connection) from its own
    separate portal thread/loop. Only the listening TCP port needs to be
    shared between them — same pattern as pointing a client at any other
    local network service."""

    def __init__(self, fixture_path: Path):
        self._fixture_path = fixture_path
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fake: FakeGeminiLiveServer | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self) -> None:
        async with FakeGeminiLiveServer(self._fixture_path) as fake:
            self._fake = fake
            self._stop_event = asyncio.Event()
            self._ready.set()
            await self._stop_event.wait()

    def start(self) -> "FakeGeminiLiveServerHandle":
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("fake Gemini Live server did not start in time")
        return self

    def stop(self) -> None:
        assert self._loop is not None and self._stop_event is not None
        self._loop.call_soon_threadsafe(self._stop_event.set)
        self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        assert self._fake is not None
        return self._fake.url

    @property
    def received_messages(self) -> list[dict]:
        assert self._fake is not None
        return self._fake.received_messages
