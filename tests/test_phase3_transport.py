import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from eureka.mvp import (  # noqa: E402
    FEEDBACK, GREETING, ApprovedKnowledge, Conversation, JsonOperationalLog, OperationalRecord, ProviderFailure, Recognition, ServiceConfig,
)
from eureka.telephony_daemon import (  # noqa: E402
    AudioSocketServer, EurekaAudioService, PhraseStore, SessionManager, encode_frame,
)


class FakeSTT:
    def __init__(self, text="goodbye"):
        self.text = text
        self.chunks = []

    async def recognize_stream(self, audio, *, sample_rate):
        assert sample_rate == 8000
        async for chunk in audio:
            self.chunks.append(chunk)
            if len(self.chunks) == 2:
                return Recognition(self.text, 0.99)


class FakeConversation:
    def __init__(self, text="goodbye"):
        self.stt = FakeSTT(text)
        self.last_transcript = ""
        self.resets = 0

    def reset(self):
        self.resets += 1
        self.last_transcript = ""

    async def reply_to_recognition(self, recognition):
        self.last_transcript = recognition.transcript
        return "goodbye", FEEDBACK

    async def render(self, text):
        raise AssertionError("fixed feedback must not use a provider")


def build_service(tmp_path, conversation, inactivity=0.3):
    phrase_dir = tmp_path / "phrases"
    phrase_dir.mkdir()
    for name in PhraseStore.REQUIRED:
        (phrase_dir / (name + ".slin")).write_bytes(b"\x01\x00" * 160)
    sessions = SessionManager(max_concurrent_calls=1)
    service = EurekaAudioService(conversation=conversation, phrases=PhraseStore(phrase_dir), sessions=sessions,
                                 logger=JsonOperationalLog(tmp_path / "logs"), max_call_seconds=2,
                                 inactivity_seconds=inactivity)
    return service, sessions


async def read_frame(reader):
    header = await asyncio.wait_for(reader.readexactly(3), 1)
    return header[0], await reader.readexactly(int.from_bytes(header[1:], "big"))


@pytest.mark.parametrize("stage", ["model", "tts"])
def test_thinking_during_slow_response_without_overlapping_answer(tmp_path, monkeypatch, stage):
    from eureka import telephony_daemon as transport

    async def exercise():
        if stage == "tts":
            monkeypatch.setattr(transport, "THINKING_DELAY_S", 0.05)
        started, release = asyncio.Event(), asyncio.Event()
        class SlowConversation(FakeConversation):
            async def reply_to_recognition(self, recognition):
                if stage == "model":
                    started.set()
                    await release.wait()
                return "answer", "answer"

            async def render(self, text):
                if stage == "tts":
                    started.set()
                    await release.wait()
                return b"\x03\x00" * 160

        service, _ = build_service(tmp_path, SlowConversation(), inactivity=4)
        service.max_call_seconds = 6
        service.phrases._phrases["thinking"] = b"\x02\x00" * 160 * 5
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            writer.write(encode_frame(1, uuid.uuid4().bytes))
            await writer.drain()
            await read_frame(reader)
            await asyncio.sleep(0.04)
            writer.write(encode_frame(0x10, b"\x00\x04" * 160) * 2)
            await writer.drain()
            await asyncio.wait_for(started.wait(), 1)
            started_at = asyncio.get_running_loop().time()
            if stage == "model":
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(reader.readexactly(1), 1.8)
            async def next_frame():
                header = await reader.readexactly(3)
                return header[0], await reader.readexactly(int.from_bytes(header[1:], "big"))
            assert await asyncio.wait_for(next_frame(), 1) == (0x10, b"\x02\x00" * 160)
            if stage == "model":
                assert asyncio.get_running_loop().time() - started_at >= 1.9
            release.set()
            for _ in range(4):
                assert await read_frame(reader) == (0x10, b"\x02\x00" * 160)
            assert await read_frame(reader) == (0x10, b"\x03\x00" * 160)
            writer.write(encode_frame(0))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()
    asyncio.run(exercise())


def test_likely_slow_question_plays_thinking_before_model_request(tmp_path):
    from test_general_knowledge import general
    from test_phase3_conversation import Model

    async def exercise():
        started = asyncio.Event()
        class ObservedModel(Model):
            async def stream(self, **kwargs):
                started.set()
                yield "Plants use light."
        model = ObservedModel()
        convo = general(model)
        convo.stt = FakeSTT("Can you explain how plants use sunlight to make food and grow over time")
        service, _ = build_service(tmp_path, convo, inactivity=2)
        service.phrases._phrases["thinking"] = b"\x02\x00" * 160 * 3
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            writer.write(encode_frame(1, uuid.uuid4().bytes))
            await writer.drain()
            await read_frame(reader)
            await asyncio.sleep(.04)
            writer.write(encode_frame(0x10, b"\x00\x04" * 160) * 2)
            await writer.drain()
            for _ in range(3):
                assert await read_frame(reader) == (0x10, b"\x02\x00" * 160)
                assert not started.is_set()
            await asyncio.wait_for(started.wait(), 1)
            assert await read_frame(reader) == (0x10, bytes(320))
            writer.write(encode_frame(0))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()
    asyncio.run(exercise())


def test_fast_response_does_not_play_thinking(tmp_path, monkeypatch):
    from eureka import telephony_daemon as transport
    monkeypatch.setattr(transport, "THINKING_DELAY_S", 0.1)

    async def exercise():
        class FastConversation(FakeConversation):
            async def reply_to_recognition(self, recognition):
                return "answer", "answer"
            async def render(self, text):
                return b"\x03\x00" * 160
        service, _ = build_service(tmp_path, FastConversation(), inactivity=1)
        service.phrases._phrases["thinking"] = b"\x02\x00" * 160
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            writer.write(encode_frame(1, uuid.uuid4().bytes))
            await writer.drain()
            await read_frame(reader)
            await asyncio.sleep(0.04)
            writer.write(encode_frame(0x10, b"\x00\x04" * 160) * 2)
            await writer.drain()
            assert await read_frame(reader) == (0x10, b"\x03\x00" * 160)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(reader.readexactly(1), 0.15)
            writer.write(encode_frame(0))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()
    asyncio.run(exercise())


def test_thinking_waits_for_recognition_and_is_cancelled_on_hangup(tmp_path, monkeypatch):
    from eureka import telephony_daemon as transport
    monkeypatch.setattr(transport, "THINKING_DELAY_S", 0.05)

    async def exercise():
        recognition_release, model_started, model_cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class SlowSTT:
            async def recognize_stream(self, audio, *, sample_rate):
                await recognition_release.wait()
                return object()
        class SlowConversation(FakeConversation):
            def __init__(self):
                super().__init__()
                self.stt = SlowSTT()
            async def reply_to_recognition(self, recognition):
                model_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    model_cancelled.set()

        service, sessions = build_service(tmp_path, SlowConversation(), inactivity=1)
        service.phrases._phrases["thinking"] = b"\x02\x00" * 160 * 20
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            call_id = uuid.uuid4().bytes
            writer.write(encode_frame(1, call_id))
            await writer.drain()
            await read_frame(reader)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(reader.readexactly(1), 0.12)
            recognition_release.set()
            await asyncio.wait_for(model_started.wait(), 1)
            assert await read_frame(reader) == (0x10, b"\x02\x00" * 160)
            writer.write(encode_frame(0))
            await writer.drain()
            await asyncio.wait_for(reader.read(), 1)
            assert model_cancelled.is_set()
            assert await sessions.get(call_id) is None
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("rating", [1, 2])
def test_streaming_recognition_and_feedback(tmp_path, rating):
    async def exercise():
        conversation = FakeConversation()
        service, _ = build_service(tmp_path, conversation)
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            writer.write(encode_frame(1, uuid.uuid4().bytes))
            await writer.drain()
            await read_frame(reader)
            await asyncio.sleep(0.04)
            chunk = b"\x00\x04" * 160
            writer.write(encode_frame(0x10, chunk) * 2)
            await writer.drain()
            await read_frame(reader)
            writer.write(encode_frame(3, str(rating).encode()))
            await writer.drain()
            assert await read_frame(reader) == (0x00, b"")
            assert await asyncio.wait_for(reader.read(), 1) == b""
            writer.close()
            await writer.wait_closed()
            assert conversation.stt.chunks == [chunk, chunk]
            assert conversation.resets == 2
            record = json.loads(next((tmp_path / "logs").glob("*.jsonl")).read_text())
            assert record["rating"] == rating
            assert record["end_reason"] == "feedback"
            assert any(event.get("guardrail") == "goodbye" for event in record["events"])
        finally:
            await server.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("fail", [False, True])
def test_provider_greeting_and_local_fallback_continue_call(tmp_path, fail):
    async def exercise():
        class GreetingConversation(FakeConversation):
            def __init__(self):
                super().__init__()
                self.texts = []

            async def render(self, text):
                self.texts.append(text)
                if text == GREETING and fail:
                    raise ProviderFailure("private response", category="http_other", http_status=402)
                return b"\x02\x00" * 160

        conversation = GreetingConversation()
        service, _ = build_service(tmp_path, conversation)
        service.provider_fixed_prompts = True
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            writer.write(encode_frame(1, uuid.uuid4().bytes))
            await writer.drain()
            assert await read_frame(reader) == (0x10, (b"\x01\x00" if fail else b"\x02\x00") * 160)
            await asyncio.sleep(0.04)
            writer.write(encode_frame(0x10, b"\x00\x04" * 160) * 2)
            await writer.drain()
            assert await read_frame(reader) == (0x10, b"\x02\x00" * 160)
            writer.write(encode_frame(3, b"1"))
            await writer.drain()
            assert await read_frame(reader) == (0x00, b"")
            assert await asyncio.wait_for(reader.read(), 1) == b""
            writer.close()
            await writer.wait_closed()
            assert conversation.texts == [GREETING, FEEDBACK]
            record_text = next((tmp_path / "logs").glob("*.jsonl")).read_text()
            record = json.loads(record_text)
            assert record["end_reason"] == "feedback"
            failures = [event for event in record["events"] if event.get("component") == "tts"]
            assert bool(failures) == fail
            assert "private response" not in record_text
        finally:
            await server.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("fail,rating", [(False, 1), (True, 2)])
def test_provider_feedback_or_fallback_accepts_rating_during_synthesis(tmp_path, fail, rating):
    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()
        class FeedbackConversation(FakeConversation):
            async def render(self, text):
                if text == GREETING:
                    return b"\x02\x00" * 160
                assert text == FEEDBACK
                started.set()
                await release.wait()
                if fail:
                    raise ProviderFailure("private response", category="http_other", http_status=402)
                return b"\x03\x00" * 160

        service, _ = build_service(tmp_path, FeedbackConversation())
        service.provider_fixed_prompts = True
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            writer.write(encode_frame(1, uuid.uuid4().bytes))
            await writer.drain()
            assert await read_frame(reader) == (0x10, b"\x02\x00" * 160)
            await asyncio.sleep(0.04)
            writer.write(encode_frame(0x10, b"\x00\x04" * 160) * 2)
            await writer.drain()
            await asyncio.wait_for(started.wait(), 1)
            writer.write(encode_frame(3, str(rating).encode()))
            await writer.drain()
            release.set()
            assert await read_frame(reader) == (0x10, (b"\x01\x00" if fail else b"\x03\x00") * 160)
            assert await read_frame(reader) == (0x00, b"")
            assert await asyncio.wait_for(reader.read(), 1) == b""
            writer.close()
            await writer.wait_closed()
            record_text = next((tmp_path / "logs").glob("*.jsonl")).read_text()
            record = json.loads(record_text)
            assert (record["rating"], record["end_reason"]) == (rating, "feedback")
            assert bool([event for event in record["events"] if event.get("component") == "tts"]) == fail
            assert "private response" not in record_text
        finally:
            await server.close()
    asyncio.run(exercise())


def test_hangup_cancels_pending_provider_greeting(tmp_path):
    async def exercise():
        started = asyncio.Event()
        cancelled = asyncio.Event()
        class SlowConversation(FakeConversation):
            async def render(self, text):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        service, sessions = build_service(tmp_path, SlowConversation())
        service.provider_fixed_prompts = True
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            call_id = uuid.uuid4().bytes
            writer.write(encode_frame(1, call_id))
            await writer.drain()
            await asyncio.wait_for(started.wait(), 1)
            writer.write(encode_frame(0))
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 1) == b""
            assert cancelled.is_set()
            assert await sessions.get(call_id) is None
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("timeouts", [1, 2])
def test_active_stt_timeout_retries_once_instead_of_ending_call(tmp_path, timeouts):
    from eureka.mvp import REPEAT

    async def exercise():
        class TimedOutSTT:
            def __init__(self):
                self.calls = 0
                self.last_diagnostic = None
            async def recognize_stream(self, audio, *, sample_rate):
                self.calls += 1
                if self.calls <= timeouts:
                    self.last_diagnostic = {"stt_audio_frames": 100, "stt_active_frames": 3}
                    raise ProviderFailure("STT provider failed", category="timeout")
                return Recognition("goodbye", .99)
        class RetryConversation(FakeConversation):
            def __init__(self):
                super().__init__()
                self.stt = TimedOutSTT()
            async def render(self, text):
                assert text == REPEAT
                return b"\x02\x00" * 160
        convo = RetryConversation()
        service, _ = build_service(tmp_path, convo, inactivity=1)
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            writer.write(encode_frame(1, uuid.uuid4().bytes))
            await writer.drain()
            assert await read_frame(reader) == (0x10, b"\x01\x00" * 160)
            assert await read_frame(reader) == (0x10, b"\x02\x00" * 160)
            assert await read_frame(reader) == (0x10, b"\x01\x00" * 160)
            if timeouts == 1:
                writer.write(encode_frame(3, b"1"))
                await writer.drain()
            assert await read_frame(reader) == (0x00, b"")
            writer.close()
            await writer.wait_closed()
            record = json.loads(next((tmp_path / "logs").glob("*.jsonl")).read_text())
            assert record["end_reason"] == ("feedback" if timeouts == 1 else "provider_failure")
            assert convo.stt.calls == 2
            assert sum(e.get("failure_category") == "timeout" for e in record["events"]) == timeouts
        finally:
            await server.close()
    asyncio.run(exercise())


def test_conversation_failure_prompts_retry_without_ending_call(tmp_path):
    from eureka.mvp import MODEL_RETRY

    async def exercise():
        class ImmediateSTT:
            async def recognize_stream(self, audio, *, sample_rate):
                return Recognition("question", .99)
        class RetryConversation(FakeConversation):
            def __init__(self):
                super().__init__()
                self.stt = ImmediateSTT()
                self.calls = 0
            async def reply_to_recognition(self, recognition):
                self.calls += 1
                if self.calls == 1:
                    raise ProviderFailure("private detail", category="invalid_decision")
                return "goodbye", FEEDBACK
            async def render(self, text):
                assert text == MODEL_RETRY
                return b"\x02\x00" * 160
        convo = RetryConversation()
        service, _ = build_service(tmp_path, convo, inactivity=1)
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            writer.write(encode_frame(1, uuid.uuid4().bytes))
            await writer.drain()
            assert await read_frame(reader) == (0x10, b"\x01\x00" * 160)
            assert await read_frame(reader) == (0x10, b"\x02\x00" * 160)
            assert await read_frame(reader) == (0x10, b"\x01\x00" * 160)
            writer.write(encode_frame(3, b"1"))
            await writer.drain()
            assert await read_frame(reader) == (0, b"")
            writer.close()
            await writer.wait_closed()
            record = json.loads(next((tmp_path / "logs").glob("*.jsonl")).read_text())
            assert record["end_reason"] == "feedback"
            assert any(e.get("failure_category") == "invalid_decision" for e in record["events"])
            assert "private detail" not in json.dumps(record)
        finally:
            await server.close()
    asyncio.run(exercise())


def test_model_failure_category_is_recorded_without_provider_details(tmp_path):
    from test_phase3_conversation import conversation

    async def exercise():
        class FailingModel:
            async def stream(self, **kwargs):
                raise ProviderFailure("private provider details", category="http_429", http_status=429)
                yield
        class STT:
            async def recognize_stream(self, audio, *, sample_rate):
                return Recognition("what can eureka do", 0.99)
        convo = conversation(FailingModel())
        convo.stt = STT()
        service, _ = build_service(tmp_path, convo)
        record = OperationalRecord(str(uuid.uuid4()), 0)
        assert (await service._respond(asyncio.Queue(), record))[0] == "conversation_failure"
        failure = record.events[-1]
        assert (failure["component"], failure["failure_category"], failure["http_status"]) == ("conversation", "http_429", 429)
        service.logger.write(record)
        assert "private provider details" not in next((tmp_path / "logs").glob("*.jsonl")).read_text()
    asyncio.run(exercise())


def test_tts_failure_records_safe_category(tmp_path):
    async def exercise():
        class FailedConversation(FakeConversation):
            async def render(self, text):
                raise ProviderFailure("rejected", category="http_other", http_status=402)

        service, _ = build_service(tmp_path, FailedConversation())
        async def respond(frames, record):
            return "answered", "approved answer"
        async def play(writer, audio):
            pass
        service._respond = respond
        service._play = play
        record = OperationalRecord(str(uuid.uuid4()), 0)
        await service._session(None, asyncio.Queue(), record)
        assert record.end_reason == "provider_failure"
        assert any(event.get("component") == "tts" and event.get("failure_category") == "http_other"
                   and event.get("http_status") == 402 for event in record.events)
    asyncio.run(exercise())


def test_complete_conversation_over_socket_with_mock_providers(tmp_path):
    async def exercise():
        fact = "Eureka is an AI operator."
        facts = tmp_path / "facts.json"
        facts.write_text(json.dumps({"version": "1", "facts": {"eureka": fact}}))
        class STT:
            def __init__(self):
                self.turn = 0
            async def recognize_stream(self, audio, *, sample_rate):
                async for _ in audio:
                    break
                self.turn += 1
                return Recognition("what can eureka do" if self.turn <= 2 else "goodbye", 0.99)
        class Haiku:
            def __init__(self):
                self.contexts = []
            async def stream(self, *, system_prompt, context, user_text):
                self.contexts.append(context)
                assert fact in system_prompt
                yield fact
        class TTS:
            def __init__(self):
                self.texts = []
            async def synthesize(self, text, *, sample_rate):
                self.texts.append(text)
                return b"\x02\x00" * 160
        model, speech = Haiku(), TTS()
        conversation = Conversation(stt=STT(), haiku=model, tts=speech, knowledge=ApprovedKnowledge(facts),
                                    system_prompt="approved", config=ServiceConfig(general_knowledge_enabled=False))
        service, _ = build_service(tmp_path, conversation)
        service.provider_fixed_prompts = True
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            writer.write(encode_frame(1, uuid.uuid4().bytes))
            await writer.drain()
            assert await read_frame(reader) == (0x10, b"\x02\x00" * 160)
            for _ in range(3):
                await asyncio.sleep(0.04)
                writer.write(encode_frame(0x10, b"\x00\x04" * 160))
                await writer.drain()
                await read_frame(reader)
            writer.write(encode_frame(3, b"1"))
            await writer.drain()
            assert await read_frame(reader) == (0x00, b"")
            assert await asyncio.wait_for(reader.read(), 1) == b""
            writer.close()
            await writer.wait_closed()
            assert speech.texts == [GREETING, fact, fact, FEEDBACK]
            assert len(model.contexts) == 2 and len(model.contexts[1]) == 2
            assert not conversation.context
        finally:
            await server.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("selection", [None, "legacy", "elevenlabs"])
def test_live_mode_wires_configured_providers(tmp_path, monkeypatch, selection):
    from types import SimpleNamespace
    from eureka import providers, service as entrypoint

    root = Path(__file__).parents[1]
    monkeypatch.setenv("EUREKA_PROVIDER_MODE", "live")
    monkeypatch.delenv("EUREKA_GENERAL_KNOWLEDGE_ENABLED", raising=False)
    monkeypatch.delenv("EUREKA_WEB_SEARCH_ENABLED", raising=False)
    if selection is None:
        monkeypatch.delenv("EUREKA_TTS_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("EUREKA_TTS_PROVIDER", selection)
    phrase_dir = tmp_path / "phrases"
    phrase_dir.mkdir()
    for name in PhraseStore.REQUIRED:
        (phrase_dir / (name + ".slin")).write_bytes(b"\x01\x00" * 160)
    monkeypatch.setenv("EUREKA_PHRASE_DIR", str(phrase_dir))
    monkeypatch.setenv("EUREKA_KNOWLEDGE_PATH", str(root / "knowledge/approved-facts-v1.json"))
    monkeypatch.setenv("EUREKA_PROMPT_PATH", str(root / "prompts/system-v1.txt"))
    monkeypatch.setenv("EUREKA_LOG_DIR", str(tmp_path / "logs"))
    stub = SimpleNamespace(stt=object(), haiku=object(), tts=object())
    monkeypatch.setattr(providers, "create_providers", lambda: stub)

    async def exercise():
        ready = asyncio.Event()
        captured = []
        class Server:
            def __init__(self, *, host, port, on_connection):
                captured.append(on_connection.__self__)
            async def start(self):
                ready.set()
            async def close(self):
                pass
        monkeypatch.setattr(entrypoint, "AudioSocketServer", Server)
        task = asyncio.create_task(entrypoint.run())
        try:
            await asyncio.wait_for(ready.wait(), 1)
            conversation = captured[0].conversation
            assert (conversation.stt, conversation.haiku, conversation.tts) == (stub.stt, stub.haiku, stub.tts)
            assert conversation.config.general_knowledge_enabled
            assert conversation.general_prompt
            assert captured[0].provider_fixed_prompts
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(exercise())


@pytest.mark.parametrize("fail", [False, True])
def test_missing_thinking_clip_uses_configured_voice_at_startup(tmp_path, monkeypatch, fail, caplog):
    from types import SimpleNamespace
    from eureka import providers, service as entrypoint
    from eureka.mvp import THINKING

    root = Path(__file__).parents[1]
    phrase_dir = tmp_path / "phrases"
    phrase_dir.mkdir()
    for name in ("greeting", "feedback", "unavailable"):
        (phrase_dir / (name + ".slin")).write_bytes(b"\x01\x00" * 160)
    monkeypatch.setenv("EUREKA_PROVIDER_MODE", "live")
    monkeypatch.setenv("EUREKA_PHRASE_DIR", str(phrase_dir))
    monkeypatch.setenv("EUREKA_KNOWLEDGE_PATH", str(root / "knowledge/approved-facts-v1.json"))
    monkeypatch.setenv("EUREKA_PROMPT_PATH", str(root / "prompts/system-v1.txt"))
    monkeypatch.setenv("EUREKA_LOG_DIR", str(tmp_path / "logs"))
    calls = []
    class TTS:
        async def synthesize(self, text, *, sample_rate):
            calls.append((text, sample_rate))
            if fail:
                raise ProviderFailure("private provider details")
            return b"\x02\x00" * 160
    monkeypatch.setattr(providers, "create_providers", lambda: SimpleNamespace(stt=object(), haiku=object(), tts=TTS()))
    captured = []
    class Server:
        def __init__(self, *, host, port, on_connection):
            captured.append(on_connection.__self__)
        async def start(self):
            raise RuntimeError("stop before listening")
        async def close(self):
            pass
    monkeypatch.setattr(entrypoint, "AudioSocketServer", Server)
    with pytest.raises(RuntimeError, match="stop before listening"):
        asyncio.run(entrypoint.run())
    assert calls == [(THINKING, 8000)]
    assert captured[0].phrases.has("thinking") is not fail
    if not fail:
        assert captured[0].phrases.get("thinking") == b"\x02\x00" * 160
    else:
        assert "Thinking prompt unavailable" in caplog.text
        assert "private provider details" not in caplog.text


@pytest.mark.parametrize("selection", ["kokoro", "piper", "unknown"])
def test_removed_tts_selection_fails_before_startup(monkeypatch, selection):
    from eureka import service as entrypoint

    monkeypatch.setenv("EUREKA_TTS_PROVIDER", selection)
    with pytest.raises(ValueError, match="EUREKA_TTS_PROVIDER"):
        asyncio.run(entrypoint.run())


@pytest.mark.parametrize("tts_failure", [False, True])
def test_continuous_silence_times_out(tmp_path, tts_failure):
    async def exercise():
        from eureka.mvp import INACTIVITY_GOODBYE
        class TimeoutConversation(FakeConversation):
            async def render(self, text):
                assert text == INACTIVITY_GOODBYE
                if tts_failure:
                    raise ProviderFailure("voice unavailable", category="network")
                return b"\x02\x00" * 160
        conversation = TimeoutConversation()
        class ConsumingSTT:
            async def recognize_stream(self, audio, *, sample_rate):
                async for _ in audio:
                    pass
        conversation.stt = ConsumingSTT()
        service, sessions = build_service(tmp_path, conversation, inactivity=0.15)
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            call_id = uuid.uuid4().bytes
            writer.write(encode_frame(1, call_id))
            await writer.drain()
            await read_frame(reader)
            await asyncio.sleep(0.04)
            # Keep the audio stream open with silence beyond the timeout.
            hangup_frame = asyncio.create_task(read_frame(reader))
            for _ in range(9):
                if hangup_frame.done():
                    break
                writer.write(encode_frame(0x10, bytes(320)))
                await writer.drain()
                await asyncio.sleep(0.025)
            if tts_failure:
                assert await hangup_frame == (0x00, b"")
            else:
                assert await hangup_frame == (0x10, b"\x02\x00" * 160)
                assert await read_frame(reader) == (0x00, b"")
            assert await asyncio.wait_for(reader.read(), 1) == b""
            assert await sessions.get(call_id) is None
            writer.close()
            await writer.wait_closed()
            record = json.loads(next((tmp_path / "logs").glob("*.jsonl")).read_text())
            assert record["end_reason"] == "inactivity_timeout"
        finally:
            await server.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("hangup", [True, False])
def test_hup_or_max_duration_cancels_pending_recognition(tmp_path, hangup):
    async def exercise():
        started, cancelled = asyncio.Event(), asyncio.Event()

        class BlockingSTT:
            async def recognize_stream(self, audio, *, sample_rate):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        conversation = FakeConversation()
        conversation.stt = BlockingSTT()
        service, sessions = build_service(tmp_path, conversation)
        service.max_call_seconds = 0.2
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            call_id = uuid.uuid4().bytes
            writer.write(encode_frame(1, call_id))
            await writer.drain()
            await read_frame(reader)
            await asyncio.wait_for(started.wait(), 1)
            if hangup:
                writer.write(encode_frame(0))
                await writer.drain()
            else:
                assert await read_frame(reader) == (0x00, b"")
            assert await asyncio.wait_for(reader.read(), 1) == b""
            await asyncio.wait_for(cancelled.wait(), 1)
            assert await sessions.get(call_id) is None
            writer.close()
            await writer.wait_closed()
            record = json.loads(next((tmp_path / "logs").glob("*.jsonl")).read_text())
            assert record["end_reason"] == ("hangup" if hangup else "max_duration")
            assert record["rating"] is None
        finally:
            await server.close()
    asyncio.run(exercise())
