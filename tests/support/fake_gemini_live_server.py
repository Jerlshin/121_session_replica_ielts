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
import time
from pathlib import Path

import websockets


class FakeGeminiLiveServer:
    def __init__(self, fixture_path: Path, *, activity_end_delay_s: float = 0.0):
        self.fixture = json.loads(fixture_path.read_text())
        self.received_messages: list[dict] = []
        # Parallel to received_messages — time.monotonic() at the instant
        # each message was received, so timing-sensitive tests (e.g.
        # asserting a directive wasn't sent until a prior turn's
        # TurnComplete had actually gone out) don't have to guess.
        self.received_at: list[float] = []
        self.url: str = ""
        self._server: websockets.WebSocketServer | None = None
        # Simulates a slow Gemini reply to a candidate's activityEnd —
        # lets tests prove the orchestrator's directive dispatcher actually
        # waits for TurnComplete instead of racing it (see
        # test_full_exam_session_flow.py's turn-taking regression test).
        self._activity_end_delay_s = activity_end_delay_s

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
                self.received_at.append(time.monotonic())

                if "setup" in message:
                    for scripted in responses.get("after_setup", []):
                        await ws.send(json.dumps(scripted))

                elif "clientContent" in message:
                    for scripted in responses.get("after_directive", []):
                        await ws.send(json.dumps(scripted))

                elif "realtimeInput" in message and "activityEnd" in message["realtimeInput"]:
                    scripted_list = responses.get("after_activity_end", [])
                    if self._activity_end_delay_s:
                        # Fire-and-forget so this loop keeps reading (and
                        # timestamping) subsequent incoming messages right
                        # away — if the reply were awaited inline here, this
                        # single-coroutine `async for` wouldn't get back
                        # around to read/record the *next* message until
                        # after the sleep, making received_at reflect this
                        # server's own artificial delay instead of when the
                        # client actually sent that next message (which is
                        # exactly the thing turn-taking regression tests
                        # need to observe honestly).
                        asyncio.create_task(self._reply_after_delay(ws, scripted_list))
                    else:
                        for scripted in scripted_list:
                            await ws.send(json.dumps(scripted))

                # activityStart / audio chunks are recorded but not responded
                # to directly — Gemini's real reply only comes after
                # activityEnd.
        except websockets.exceptions.ConnectionClosed:
            # The client (gemini_bridge.py) or the test harness tearing
            # itself down mid-test is an expected, non-graceful close here —
            # not a bug to surface as a noisy traceback in test output.
            pass

    async def _reply_after_delay(self, ws, scripted_list: list[dict]) -> None:
        await asyncio.sleep(self._activity_end_delay_s)
        try:
            for scripted in scripted_list:
                await ws.send(json.dumps(scripted))
        except websockets.exceptions.ConnectionClosed:
            pass


class FakeGeminiLiveServerHandle:
    """Runs a FakeGeminiLiveServer on its own thread + event loop so it can
    be reached from a Starlette `TestClient`, which drives the FastAPI app
    (and therefore `gemini_bridge.py`'s real client connection) from its own
    separate portal thread/loop. Only the listening TCP port needs to be
    shared between them — same pattern as pointing a client at any other
    local network service."""

    def __init__(self, fixture_path: Path, *, activity_end_delay_s: float = 0.0):
        self._fixture_path = fixture_path
        self._activity_end_delay_s = activity_end_delay_s
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
        async with FakeGeminiLiveServer(
            self._fixture_path, activity_end_delay_s=self._activity_end_delay_s
        ) as fake:
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

    @property
    def received_at(self) -> list[float]:
        assert self._fake is not None
        return self._fake.received_at
