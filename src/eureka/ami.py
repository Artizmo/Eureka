"""Optional, loopback-only AMI feedback event consumer."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from collections.abc import Callable, Mapping

# The dialplan emits a correlation event immediately before opening AudioSocket.
_EVENT = "EurekaAudioSocket"
_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_MAX_FRAME = 16384
_MAX_LINE = 4096
_MAX_MAPPINGS = 128
_MAPPING_TTL = 30.0
_LOG = logging.getLogger(__name__)


class FeedbackCorrelator:
    """Associate the explicit dialplan UUID with a channel, never caller metadata."""

    def __init__(self):
        self._by_channel: dict[str, tuple[bytes, float]] = {}
        self._by_uuid: dict[bytes, str] = {}
        self._receivers: dict[bytes, Callable[[str], None]] = {}
        self._retired: dict[bytes, float] = {}

    def attach(self, call_id: bytes, receiver: Callable[[str], None]) -> None:
        self._expire()
        self._receivers[call_id] = receiver

    def detach(self, call_id: bytes) -> None:
        self._receivers.pop(call_id, None)
        self._retire(call_id)

    def _retire(self, call_id: bytes) -> None:
        self._remove_uuid(call_id)
        self._expire()
        if len(self._retired) >= _MAX_MAPPINGS:
            self._retired.pop(next(iter(self._retired)))
        self._retired[call_id] = time.monotonic()

    def clear(self) -> None:
        self._by_channel.clear()
        self._by_uuid.clear()

    def _remove_uuid(self, call_id: bytes) -> None:
        channel = self._by_uuid.pop(call_id, None)
        if channel is not None:
            self._by_channel.pop(channel, None)

    def _expire(self) -> None:
        now = time.monotonic()
        for call_id, retired in list(self._retired.items()):
            if now - retired >= _MAPPING_TTL:
                self._retired.pop(call_id, None)
        for channel, (call_id, created) in list(self._by_channel.items()):
            if call_id not in self._receivers and now - created >= _MAPPING_TTL:
                self._remove_uuid(call_id)

    def event(self, fields: Mapping[str, str]) -> None:
        self._expire()
        kind = fields.get("event", "").lower()
        channel = fields.get("uniqueid", "")
        if not _ID.fullmatch(channel):
            return
        if kind == "userevent" and fields.get("userevent") == _EVENT:
            if fields.get("eurekauniqueid") != channel:
                return
            raw = fields.get("eurekauuid", "")
            try:
                parsed = uuid.UUID(raw)
                if str(parsed) != raw.lower() or not parsed.int:
                    return
                call_id = parsed.bytes
            except (ValueError, AttributeError):
                return
            existing = self._by_channel.get(channel)
            reverse = self._by_uuid.get(call_id)
            if existing is not None or reverse is not None or call_id in self._retired:
                return  # Duplicates and conflicts cannot replace an association.
            if len(self._by_channel) >= _MAX_MAPPINGS:
                return
            self._by_channel[channel] = (call_id, time.monotonic())
            self._by_uuid[call_id] = channel
        elif kind == "hangup":
            entry = self._by_channel.get(channel)
            if entry is not None:
                self._retire(entry[0])
        elif kind == "dtmfend" and fields.get("direction", "").lower() == "received":
            digit = fields.get("digit", "")
            if digit not in ("1", "2"):
                return
            entry = self._by_channel.get(channel)
            if entry is not None:
                receiver = self._receivers.get(entry[0])
                if receiver is not None:
                    receiver(digit)


class AMIClient:
    """Bounded AMI stream; never logs server messages, credentials or events."""

    def __init__(self, username: str, secret: str, correlator: FeedbackCorrelator, *, port: int = 5038):
        for value in (username, secret):
            if not value or len(value) > 256 or any(c in value for c in "\r\n\x00"):
                raise ValueError("invalid AMI credentials")
        if not 1 <= port <= 65535:
            raise ValueError("invalid AMI port")
        self._username, self._secret = username, secret
        self._correlator = correlator
        self._port = port
        self._connected = False

    async def _frame(self, reader: asyncio.StreamReader) -> dict[str, str]:
        fields: dict[str, str] = {}
        size = 0
        while True:
            line = await reader.readuntil(b"\r\n")
            size += len(line)
            if len(line) > _MAX_LINE or size > _MAX_FRAME:
                raise ValueError("AMI frame too large")
            if line == b"\r\n":
                return fields
            key, sep, value = line[:-2].partition(b":")
            if not sep or not key or any(c < 33 or c > 126 for c in key):
                raise ValueError("invalid AMI frame")
            name = key.decode("ascii").lower()
            if name not in {"event", "userevent", "uniqueid", "eurekauniqueid", "eurekauuid",
                            "direction", "digit", "response"}:
                continue
            if name in fields:
                raise ValueError("duplicate AMI field")
            fields[name] = value.strip().decode("utf-8", errors="strict")

    async def _connection(self) -> None:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", self._port, limit=_MAX_LINE), 5)
        heartbeat: asyncio.Task | None = None
        try:
            banner = await asyncio.wait_for(reader.readuntil(b"\r\n"), 5)
            if not banner.startswith(b"Asterisk Call Manager/"):
                raise ValueError("invalid AMI banner")
            writer.write((f"Action: Login\r\nUsername: {self._username}\r\n"
                          f"Secret: {self._secret}\r\nEvents: on\r\n\r\n").encode())
            await asyncio.wait_for(writer.drain(), 5)
            response = await asyncio.wait_for(self._frame(reader), 5)
            if response.get("response", "").lower() != "success":
                raise ValueError("AMI login failed")
            self._connected = True
            _LOG.warning("AMI feedback connection established")
            async def keepalive() -> None:
                while True:
                    await asyncio.sleep(20)
                    writer.write(b"Action: Ping\r\nActionID: eureka-keepalive\r\n\r\n")
                    await asyncio.wait_for(writer.drain(), 5)

            heartbeat = asyncio.create_task(keepalive())
            while True:
                frame = await asyncio.wait_for(self._frame(reader), 45)
                self._correlator.event(frame)
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            self._correlator.clear()
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 5)
            except (ConnectionError, OSError, asyncio.TimeoutError):
                pass

    async def run(self) -> None:
        delay = 1.0
        unavailable_reported = False
        while True:
            started = time.monotonic()
            self._connected = False
            try:
                await self._connection()
            except (OSError, EOFError, ValueError, UnicodeError, asyncio.IncompleteReadError,
                    asyncio.LimitOverrunError, asyncio.TimeoutError):
                if self._connected or not unavailable_reported:
                    _LOG.warning("AMI feedback unavailable; retrying")
                unavailable_reported = True
            if time.monotonic() - started >= 30:
                delay = 1.0
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)


def configured_client(correlator: FeedbackCorrelator, env: Mapping[str, str] | None = None) -> AMIClient | None:
    env = os.environ if env is None else env
    enabled = env.get("EUREKA_AMI_ENABLED", "false").lower()
    if enabled == "false":
        return None
    if enabled != "true":
        raise ValueError("EUREKA_AMI_ENABLED must be true or false")
    return AMIClient(env.get("EUREKA_AMI_USERNAME", ""), env.get("EUREKA_AMI_SECRET", ""), correlator)
