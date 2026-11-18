"""Phase 2 AudioSocket transport, session admission, and paced echo support."""

from __future__ import annotations

import asyncio
import socket
import struct
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .interruption import InterruptionDetector
from .mvp import (
    FEEDBACK,
    GREETING,
    THINKING,
    INACTIVITY_GOODBYE,
    LIVE_UNAVAILABLE,
    MODEL_RETRY,
    REPEAT,
    UNAVAILABLE,
    Conversation,
    JsonOperationalLog,
    OperationalRecord,
    ProviderFailure,
    new_call_id,
)


UUID_FRAME = 0x01
AUDIO_FRAME = 0x10
HEADER_SIZE = 3
DEFAULT_MAX_AUDIO_PAYLOAD = 4096
AUDIO_INTERVAL_S = 0.020
THINKING_DELAY_S = 2.0


class ProtocolViolation(Exception):
    """A peer sent an invalid AudioSocket frame."""


class PeerHangup(Exception):
    """The peer closed the AudioSocket connection."""


@dataclass(frozen=True)
class Frame:
    frame_type: int
    payload: bytes

    def as_tuple(self) -> tuple[int, bytes]:
        return self.frame_type, self.payload


class FrameParser:
    """Incrementally parse validated AudioSocket frames from a stream."""

    def __init__(self, reader: asyncio.StreamReader, *, max_audio_payload: int = DEFAULT_MAX_AUDIO_PAYLOAD):
        self._reader = reader
        self._max_audio_payload = max_audio_payload
        self._first_frame = True

    async def read(self) -> tuple[int, bytes]:
        while True:
            try:
                header = await self._reader.readexactly(HEADER_SIZE)
                payload_length = int.from_bytes(header[1:], "big")
                if header[0] == AUDIO_FRAME and payload_length > self._max_audio_payload:
                    raise ProtocolViolation("audio payload exceeds configured maximum")
                payload = await self._reader.readexactly(payload_length)
            except asyncio.IncompleteReadError as exc:
                raise PeerHangup from exc

            frame_type = header[0]
            if self._first_frame:
                self._first_frame = False
                if frame_type != UUID_FRAME or payload_length != 16 or not any(payload):
                    raise ProtocolViolation("first frame must be a nonzero 16-byte UUID")
            elif frame_type == UUID_FRAME:
                raise ProtocolViolation("duplicate UUID frame")
            if frame_type == 0x00 and payload:
                raise ProtocolViolation("hangup frame must be empty")
            if frame_type == 0x03 and (len(payload) != 1 or payload not in b"0123456789*#ABCD"):
                raise ProtocolViolation("invalid DTMF frame")

            if frame_type == AUDIO_FRAME:
                if payload_length > self._max_audio_payload:
                    raise ProtocolViolation("audio payload exceeds configured maximum")
                if payload_length % 2:
                    raise ProtocolViolation("audio payload must contain complete slin16 samples")

            if frame_type in (0x00, UUID_FRAME, 0x03, AUDIO_FRAME, 0xFF):
                return frame_type, payload


class SessionManager:
    """Admit sessions by UUID while enforcing duplicate and capacity limits."""

    def __init__(self, *, max_concurrent_calls: int = 5):
        if max_concurrent_calls < 1:
            raise ValueError("max_concurrent_calls must be positive")
        self._max_concurrent_calls = max_concurrent_calls
        self._sessions: dict[bytes, Any] = {}
        self._lock = asyncio.Lock()

    async def register(self, call_id: bytes, session: Any) -> bool | str:
        async with self._lock:
            if call_id in self._sessions:
                await self._close(session)
                return False
            if len(self._sessions) >= self._max_concurrent_calls:
                await self._close(session)
                return "busy"
            self._sessions[call_id] = session
            return True

    async def get(self, call_id: bytes) -> Any | None:
        async with self._lock:
            return self._sessions.get(call_id)

    async def remove(self, call_id: bytes) -> None:
        async with self._lock:
            self._sessions.pop(call_id, None)

    @staticmethod
    async def _close(session: Any) -> None:
        close = getattr(session, "close", None)
        if close is None:
            return
        result = close()
        if isinstance(result, Awaitable):
            await result
        wait_closed = getattr(session, "wait_closed", None)
        if wait_closed is not None:
            result = wait_closed()
            if isinstance(result, Awaitable):
                await result


ConnectionHandler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


class AudioSocketServer:
    """An asyncio server restricted to a loopback TCP address."""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 9092, on_connection: ConnectionHandler):
        if host != "127.0.0.1":
            raise ValueError("AudioSocket must bind to 127.0.0.1")
        self.host = host
        self.port = port
        self._on_connection = on_connection
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._accept, self.host, self.port)
        sock = self._server.sockets[0]
        self.port = sock.getsockname()[1]

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        accepted_socket = writer.get_extra_info("socket")
        if accepted_socket is not None:
            accepted_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        await self._on_connection(reader, writer)


def encode_frame(frame_type: int, payload: bytes = b"") -> bytes:
    return bytes((frame_type,)) + len(payload).to_bytes(2, "big") + payload


async def echo(reader: asyncio.StreamReader, writer: Any) -> None:
    """Echo inbound audio using absolute 20 ms deadlines to prevent drift."""

    parser = FrameParser(reader)
    next_deadline: float | None = None
    loop = asyncio.get_running_loop()

    try:
        while True:
            frame_type, payload = await parser.read()
            if frame_type == 0x00:
                return
            if frame_type != AUDIO_FRAME:
                continue

            if next_deadline is None:
                next_deadline = loop.time()
            else:
                next_deadline += AUDIO_INTERVAL_S
                await asyncio.sleep(max(0, next_deadline - loop.time()))

            writer.write(encode_frame(AUDIO_FRAME, payload))
            await writer.drain()
    except PeerHangup:
        return


class PhraseStore:
    """Loads deployment-time pre-rendered, validated slin16 phrase files only."""

    REQUIRED = {"greeting": GREETING, "unavailable": UNAVAILABLE, "feedback": FEEDBACK, "thinking": THINKING}

    def __init__(self, directory: Path):
        self._phrases: dict[str, bytes] = {}
        for name in self.REQUIRED:
            path = directory / f"{name}.slin"
            if name == "thinking" and not path.exists():
                continue
            payload = path.read_bytes()
            if not payload or len(payload) % (160 * 2):
                raise ValueError(f"pre-rendered phrase {name} is not 20 ms slin16 audio")
            self._phrases[name] = payload

    def get(self, name: str) -> bytes:
        return self._phrases[name]

    def has(self, name: str) -> bool:
        return name in self._phrases

    def set_thinking(self, audio: bytes) -> None:
        self._phrases["thinking"] = audio


class EurekaAudioService:
    """One-call AudioSocket endpoint with safe conversation and deterministic cleanup."""

    def __init__(self, *, conversation: Conversation, phrases: PhraseStore, sessions: SessionManager,
                 logger: JsonOperationalLog, max_call_seconds: float, inactivity_seconds: float,
                 feedback_correlator: Any = None, provider_fixed_prompts: bool = False,
                 interruptions_enabled: bool = False):
        self.conversation = conversation
        self.phrases = phrases
        self.sessions = sessions
        self.logger = logger
        self.max_call_seconds = max_call_seconds
        self.inactivity_seconds = inactivity_seconds
        self.feedback_correlator = feedback_correlator
        self.provider_fixed_prompts = provider_fixed_prompts
        self.interruptions_enabled = interruptions_enabled

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        parser = FrameParser(reader)
        call_id: bytes | None = None
        record: OperationalRecord | None = None
        admitted = False
        signal_hangup = False
        receiver: asyncio.Task | None = None
        session: asyncio.Task | None = None
        frames: asyncio.Queue | None = None
        try:
            _, call_id = await asyncio.wait_for(parser.read(), timeout=self.inactivity_seconds)
            admitted = await self.sessions.register(call_id, writer) is True
            if not admitted:
                return
            record = OperationalRecord(call_id=new_call_id(call_id), started_at=time.time())
            reset = getattr(self.conversation, "reset", None)
            if reset is not None:
                reset()
            record.event("session_started")
            frames: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue(maxsize=100)
            feedback_enabled = asyncio.Event()
            ami_feedback: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            def receive_rating(digit: str) -> None:
                if feedback_enabled.is_set() and not ami_feedback.done():
                    ami_feedback.set_result(digit)
            if self.feedback_correlator is not None:
                self.feedback_correlator.attach(call_id, receive_rating)
            ended = asyncio.Event()
            class CallWriter:
                def write(self, data):
                    if ended.is_set():
                        raise PeerHangup
                    writer.write(data)

                async def drain(self):
                    if ended.is_set():
                        raise PeerHangup
                    await writer.drain()

            receiver = asyncio.create_task(self._receive(parser, frames, ended))
            session = asyncio.create_task(self._session(CallWriter(), frames, record, feedback_enabled, ami_feedback))
            done, _ = await asyncio.wait((receiver, session), timeout=self.max_call_seconds,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                ended.set()
                record.finish("max_duration")
                signal_hangup = True
            elif receiver in done:
                record.finish(receiver.result())
            else:
                session.result()
                signal_hangup = True
        except asyncio.TimeoutError:
            if record:
                record.finish("inactivity_timeout")
        except (PeerHangup, ConnectionError, asyncio.IncompleteReadError):
            if record:
                record.finish("socket_loss")
        except (ProtocolViolation, ValueError) as exc:
            if record:
                record.event("protocol_failure", detail=str(exc))
                record.finish("protocol_failure")
        finally:
            for task in (receiver, session):
                if task is not None:
                    task.cancel()
            obsolete = getattr(frames, "obsolete_operation", None)
            # Close promptly, but retain admission and shared state until a
            # cancellation-resistant obsolete provider has actually stopped.
            if obsolete is not None and not obsolete.done():
                ended.set()
                if signal_hangup:
                    try:
                        writer.write(encode_frame(0x00))
                        await asyncio.wait_for(writer.drain(), timeout=1)
                    except (ConnectionError, OSError, asyncio.TimeoutError):
                        pass
                    signal_hangup = False
                writer.close()
            await asyncio.gather(*(task for task in (receiver, session) if task is not None), return_exceptions=True)
            if obsolete is not None:
                await asyncio.gather(obsolete, return_exceptions=True)
            if signal_hangup:
                try:
                    writer.write(encode_frame(0x00))
                    await asyncio.wait_for(writer.drain(), timeout=1)
                except (ConnectionError, OSError, asyncio.TimeoutError):
                    pass
            if admitted and call_id is not None:
                if self.feedback_correlator is not None:
                    self.feedback_correlator.detach(call_id)
                reset = getattr(self.conversation, "reset", None)
                if reset is not None:
                    reset()
                await self.sessions.remove(call_id)
            try:
                if record is not None:
                    record.finish("closed")
                    self.logger.write(record)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass

    @staticmethod
    async def _receive(parser: FrameParser, frames: asyncio.Queue, ended: asyncio.Event | None = None) -> str:
        try:
            while True:
                frame_type, payload = await parser.read()
                if frame_type == 0x00:
                    return "hangup"
                if frame_type == 0xFF:
                    return "peer_error"
                if frames.full():
                    frames.get_nowait()
                frames.put_nowait((frame_type, payload))
        except PeerHangup:
            return "socket_loss"
        finally:
            if ended is not None:
                ended.set()

    @staticmethod
    def _discard_frames(frames: asyncio.Queue, *, keep_feedback: bool = False) -> None:
        feedback = []
        while not frames.empty():
            frame = frames.get_nowait()
            if keep_feedback and frame[0] == 0x03 and frame[1] in (b"1", b"2"):
                feedback.append(frame)
        for frame in feedback:
            frames.put_nowait(frame)

    async def _audio_stream(self, frames: asyncio.Queue):
        # Silence frames arrive continuously on some Asterisk channels; they must
        # not keep an otherwise inactive call alive indefinitely.
        deadline = asyncio.get_running_loop().time() + self.inactivity_seconds
        pending = getattr(frames, "interruption_audio", deque())
        frames.interruption_audio = deque()
        while pending:
            yield pending.popleft()
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            frame_type, payload = await asyncio.wait_for(frames.get(), timeout=remaining)
            if frame_type != AUDIO_FRAME or not payload:
                continue
            if any(abs(sample[0]) >= 500 for sample in struct.iter_unpack("<h", payload)):
                deadline = asyncio.get_running_loop().time() + self.inactivity_seconds
            yield payload

    async def _interruptible(self, work: Awaitable, frames: asyncio.Queue, *,
                             superseded: asyncio.Event | None = None):
        detector = frames.detector

        async def listen():
            while True:
                frame_type, payload = await frames.get()
                if frame_type == AUDIO_FRAME and payload:
                    captured = detector.feed(payload)
                    if captured is not None:
                        return captured

        operation = asyncio.create_task(work)
        listener = asyncio.create_task(listen())
        handed_off = False
        try:
            done, _ = await asyncio.wait((operation, listener), return_when=asyncio.FIRST_COMPLETED)
            if listener in done:
                captured = listener.result()
                # A completed protected prompt takes precedence over simultaneous speech.
                if operation in done and not operation.cancelled() and operation.exception() is None:
                    result = operation.result()
                    if (isinstance(result, tuple) and
                            (result[0] in {"repeat", "emergency", "unsafe", "goodbye", "unavailable"}
                             or result[1] == LIVE_UNAVAILABLE)):
                        return False, result
                frames.interruption_audio = deque(captured)
                if superseded is not None:
                    superseded.set()
                frames.obsolete_operation = operation
                handed_off = True
                if not operation.done():
                    operation.cancel()
                return True, None
            return False, operation.result()
        finally:
            tasks = (listener,) if handed_off else (operation, listener)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _session(self, writer: Any, frames: asyncio.Queue, record: OperationalRecord,
                       feedback_enabled: asyncio.Event | None = None,
                       ami_feedback: asyncio.Future[str] | None = None) -> None:
        try:
            await self._run_session(writer, frames, record, feedback_enabled, ami_feedback)
        finally:
            await self._stop_thinking(getattr(frames, "thinking_task", None))

    async def _run_session(self, writer: Any, frames: asyncio.Queue, record: OperationalRecord,
                           feedback_enabled: asyncio.Event | None,
                           ami_feedback: asyncio.Future[str] | None) -> None:
        await self._play_fixed(writer, "greeting", GREETING, record)
        self._discard_frames(frames)
        frames.before_slow_request = lambda: self._play_thinking_early(writer, frames)
        stt_timeout_retried = False
        while True:
            frames.processing_started = asyncio.Event()
            frames.thinking_task = asyncio.create_task(self._thinking(writer, frames))
            try:
                status, text = await self._respond(frames, record)
            except asyncio.TimeoutError:
                await self._stop_thinking(frames.thinking_task)
                await self._inactivity_goodbye(writer, record)
                record.finish("inactivity_timeout")
                return
            finish = getattr(self.conversation, "finish_delivery", None) if self.interruptions_enabled else None
            if status == "stt_timeout":
                if stt_timeout_retried:
                    status, text = "provider_failure", UNAVAILABLE
                else:
                    stt_timeout_retried = True
                    status, text = "repeat", REPEAT
            elif status != "provider_failure":
                stt_timeout_retried = False
            if status == "interrupted":
                await self._stop_thinking(frames.thinking_task)
                record.event("interrupted", interruption_phase="generation")
                continue
            if status == "provider_failure":
                await self._stop_thinking(frames.thinking_task)
                if finish is not None:
                    finish(interrupted=True)
                await self._play(writer, self.phrases.get("unavailable"))
                record.finish("provider_failure")
                return
            if status == "conversation_failure":
                if finish is not None:
                    finish(interrupted=True)
                    finish = None
                text = MODEL_RETRY
            protected = (status in {"repeat", "emergency", "unsafe", "goodbye", "unavailable", "conversation_failure"}
                         or text == LIVE_UNAVAILABLE)
            if not self.interruptions_enabled or protected:
                self._discard_frames(frames)
            if status == "goodbye" and feedback_enabled is not None:
                feedback_enabled.set()
            try:
                if status == "unavailable":
                    await self._stop_thinking(frames.thinking_task)
                    await self._play(writer, self.phrases.get("unavailable"))
                elif text == FEEDBACK:
                    await self._play_fixed(writer, "feedback", FEEDBACK, record, thinking=frames.thinking_task)
                elif not self.interruptions_enabled or protected:
                    await self._play_text(writer, text, thinking=frames.thinking_task)
                else:
                    if not hasattr(frames, "detector"):
                        frames.detector = InterruptionDetector()
                    superseded = asyncio.Event()
                    interrupted, _ = await self._interruptible(
                        self._play_answer(writer, text, frames.detector, superseded, thinking=frames.thinking_task), frames,
                        superseded=superseded)
                    if interrupted:
                        await self._stop_thinking(frames.thinking_task)
                        if finish is not None:
                            finish(interrupted=True)
                        record.event("interrupted", interruption_phase="playback")
                        continue
            except ProviderFailure as exc:
                await self._stop_thinking(frames.thinking_task)
                if finish is not None:
                    finish(interrupted=True)
                record.event("provider_failure", component="tts", failure_category=exc.category,
                             http_status=exc.http_status)
                await self._play(writer, self.phrases.get("unavailable"))
                record.finish("provider_failure")
                return
            if finish is not None:
                finish()
            if status == "unavailable":
                record.finish("recognition_failure")
                return
            if status == "goodbye":
                record.event("feedback_requested")
                self._discard_frames(frames, keep_feedback=True)
                deadline = asyncio.get_running_loop().time() + self.inactivity_seconds
                while True:
                    try:
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            raise asyncio.TimeoutError
                        if ami_feedback is None:
                            frame_type, payload = await asyncio.wait_for(frames.get(), timeout=remaining)
                        else:
                            frame_task = asyncio.create_task(frames.get())
                            try:
                                done, _ = await asyncio.wait((frame_task, ami_feedback), timeout=remaining,
                                                             return_when=asyncio.FIRST_COMPLETED)
                                if not done:
                                    raise asyncio.TimeoutError
                                if ami_feedback in done:
                                    frame_type, payload = 0x03, ami_feedback.result().encode("ascii")
                                else:
                                    frame_type, payload = frame_task.result()
                            finally:
                                if not frame_task.done():
                                    frame_task.cancel()
                                await asyncio.gather(frame_task, return_exceptions=True)
                    except asyncio.TimeoutError:
                        await self._inactivity_goodbye(writer, record)
                        record.finish("inactivity_timeout")
                        return
                    if frame_type == 0x03 and payload in (b"1", b"2"):
                        record.rating = int(payload)
                        record.event("feedback", rating=record.rating)
                        record.finish("feedback")
                        return
            if not self.interruptions_enabled or protected:
                self._discard_frames(frames)
            else:
                unfinished = frames.detector.unfinished()
                if unfinished:
                    frames.interruption_audio = deque(unfinished)

    async def _settle_interrupted(self, frames: asyncio.Queue) -> None:
        obsolete = getattr(frames, "obsolete_operation", None)
        if obsolete is None:
            return
        # Recognition can proceed concurrently, but shared conversation state
        # cannot be restored or reused until the obsolete operation has stopped.
        await asyncio.wait((obsolete,))
        await asyncio.gather(obsolete, return_exceptions=True)
        frames.obsolete_operation = None
        finish = getattr(self.conversation, "finish_delivery", None)
        if finish is not None:
            finish(interrupted=True)

    async def _respond(self, frames: asyncio.Queue, record: OperationalRecord) -> tuple[str, str]:
        start = time.monotonic()
        component = "stt"
        stt = None
        try:
            stt = getattr(self.conversation, "stt", None)
            have_recognition = True
            if hasattr(stt, "recognize_stream"):
                recognition = await stt.recognize_stream(self._audio_stream(frames), sample_rate=8000)
            else:
                # Compatibility with the buffered Phase 2 adapter.
                audio = bytearray()
                async for chunk in self._audio_stream(frames):
                    audio.extend(chunk)
                    if len(audio) >= 20 * 160 * 2:
                        break
                if not self.interruptions_enabled or not hasattr(stt, "recognize"):
                    if self.interruptions_enabled:
                        await self._settle_interrupted(frames)
                    if hasattr(frames, "processing_started"):
                        frames.processing_started.set()
                    status, text = await self.conversation.reply_to_audio(bytes(audio))
                    have_recognition = False
                else:
                    recognition = await stt.recognize(bytes(audio), sample_rate=8000)
            frames.interruption_audio = deque()
            if self.interruptions_enabled:
                await self._settle_interrupted(frames)
            if have_recognition:
                component = "conversation"
                if hasattr(frames, "processing_started"):
                    frames.processing_started.set()
                prompt_reply = getattr(self.conversation, "reply_to_recognition_with_prompt", None)
                before_request = getattr(frames, "before_slow_request", None)
                if prompt_reply is not None and before_request is not None:
                    reply = prompt_reply(recognition, before_request)
                else:
                    reply = self.conversation.reply_to_recognition(recognition)
                if self.interruptions_enabled:
                    begin = getattr(self.conversation, "begin_delivery", None)
                    if begin is not None:
                        begin()
                    frames.detector = InterruptionDetector()
                    interrupted, result = await self._interruptible(reply, frames)
                    if interrupted:
                        return "interrupted", ""
                    status, text = result
                else:
                    status, text = await reply
            record.event("turn", guardrail=status, transcript=self.conversation.last_transcript,
                         latency_ms=round((time.monotonic() - start) * 1000),
                         search_failure=getattr(self.conversation, "last_search_failure", None))
            return status, text
        except asyncio.TimeoutError:
            if self.interruptions_enabled:
                await self._settle_interrupted(frames)
            raise
        except Exception as exc:
            if self.interruptions_enabled:
                await self._settle_interrupted(frames)
            diagnostic = getattr(stt, "last_diagnostic", None) if component == "stt" else None
            if not isinstance(diagnostic, dict):
                diagnostic = {}
            record.event("provider_failure", component=component,
                         failure_category=exc.category if isinstance(exc, ProviderFailure) else None,
                         http_status=exc.http_status if isinstance(exc, ProviderFailure) else None,
                         latency_ms=round((time.monotonic() - start) * 1000),
                         **{key: diagnostic.get(key) for key in (
                             "stt_audio_frames", "stt_active_frames", "stt_results",
                             "stt_nonempty_results", "stt_final_results")})
            if (component == "stt" and isinstance(exc, ProviderFailure) and exc.category == "timeout"
                    and diagnostic.get("stt_active_frames", 0) > 0):
                return "stt_timeout", REPEAT
            if component == "conversation":
                return "conversation_failure", MODEL_RETRY
            return "provider_failure", UNAVAILABLE

    @staticmethod
    async def _stop_thinking(task: asyncio.Task | None, *, finish_playback: bool = False) -> None:
        if task is not None:
            if not task.done() and not (finish_playback and getattr(task, "playback_started", False)):
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _thinking(self, writer: Any, frames: asyncio.Queue) -> None:
        if not self.phrases.has("thinking"):
            return
        await frames.processing_started.wait()
        await asyncio.sleep(THINKING_DELAY_S)
        asyncio.current_task().playback_started = True
        await self._play_thinking(writer, frames)

    async def _play_thinking_early(self, writer: Any, frames: asyncio.Queue) -> None:
        if not self.phrases.has("thinking"):
            return
        task = frames.thinking_task
        already_started = getattr(task, "playback_started", False)
        await self._stop_thinking(task, finish_playback=True)
        if not already_started:
            await self._play_thinking(writer, frames)

    async def _play_thinking(self, writer: Any, frames: asyncio.Queue) -> None:
        class ReferencedWriter:
            def write(self, data):
                detector = getattr(frames, "detector", None)
                if detector is not None and data[0] == AUDIO_FRAME:
                    detector.played(data[3:])
                writer.write(data)

            async def drain(self):
                await writer.drain()

        await self._play(ReferencedWriter(), self.phrases.get("thinking"))

    async def _inactivity_goodbye(self, writer: Any, record: OperationalRecord) -> None:
        try:
            await self._play_text(writer, INACTIVITY_GOODBYE)
        except ProviderFailure as exc:
            record.event("provider_failure", component="tts", failure_category=exc.category,
                         http_status=exc.http_status)

    async def _play_fixed(self, writer: Any, name: str, text: str, record: OperationalRecord,
                          *, thinking: asyncio.Task | None = None) -> None:
        audio = self.phrases.get(name)
        if self.provider_fixed_prompts:
            try:
                audio = await self.conversation.render(text)
            except ProviderFailure as exc:
                record.event("provider_failure", component="tts", failure_category=exc.category,
                             http_status=exc.http_status)
        await self._stop_thinking(thinking, finish_playback=True)
        await self._play(writer, audio)

    async def _play_text(self, writer: Any, text: str, *, thinking: asyncio.Task | None = None) -> None:
        audio = await self.conversation.render(text)
        await self._stop_thinking(thinking, finish_playback=True)
        await self._play(writer, audio)

    async def _play_answer(self, writer: Any, text: str, detector: InterruptionDetector,
                           superseded: asyncio.Event, *, thinking: asyncio.Task | None = None) -> None:
        audio = await self.conversation.render(text)
        await self._stop_thinking(thinking, finish_playback=True)

        class ReferencedWriter:
            def write(self, data):
                if superseded.is_set():
                    raise asyncio.CancelledError
                if data[0] == AUDIO_FRAME:
                    detector.played(data[3:])
                writer.write(data)

            async def drain(self):
                await writer.drain()

        await self._play(ReferencedWriter(), audio)

    @staticmethod
    async def _play(writer: Any, audio: bytes) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        for start in range(0, len(audio), 160 * 2):
            writer.write(encode_frame(AUDIO_FRAME, audio[start : start + 160 * 2]))
            await writer.drain()
            deadline += AUDIO_INTERVAL_S
            await asyncio.sleep(max(0, deadline - loop.time()))
