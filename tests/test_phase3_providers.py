import asyncio
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from eureka.mvp import FRAME_BYTES, ProviderFailure, Recognition
from eureka.providers import ProviderConfig, create_providers


def config(**kwargs):
    return ProviderConfig(stt_api_key="stt-secret", haiku_api_key="haiku-secret", tts_api_key="tts-secret",
                          haiku_model="anthropic/claude-haiku-4.5", tts_voice_id="voice1", **kwargs)


class Context:
    def __init__(self, value):
        self.value = value
        self.closed = False

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        self.closed = True


class FakeSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.ready = asyncio.Event()

    async def send_bytes(self, data):
        self.sent.append(data)
        self.ready.set()

    async def send_json(self, data):
        self.sent.append(data)
        self.ready.set()

    async def receive(self):
        return await self.incoming.get()

    def push(self, data):
        self.incoming.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(data)))


class Content:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        async def iterate():
            for chunk in self.chunks:
                yield chunk
        return iterate()

    def iter_chunked(self, size):
        return self.__aiter__()


class FakeSession:
    def __init__(self, *, ws=None, response=None):
        self.socket = ws
        self.response = response
        self.requests = []
        self.connection = None

    def ws_connect(self, url, **kwargs):
        self.requests.append((url, kwargs))
        self.connection = Context(self.socket)
        return self.connection

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        self.connection = Context(self.response)
        return self.connection


def use_session(monkeypatch, session):
    context = Context(session)
    monkeypatch.setattr("eureka.providers.aiohttp.ClientSession", lambda: context)
    return context


def result(text, confidence, *, final=True, endpoint=False):
    return {"type": "Results", "is_final": final, "speech_final": endpoint,
            "channel": {"alternatives": [{"transcript": text, "confidence": confidence}]}}


def sse(event):
    return [b"event: ignored\n", b"data: " + json.dumps(event).encode() + b"\n", b"\n"]


def run(coro):
    return asyncio.run(coro)


def test_configuration_validation_and_secrets():
    with pytest.raises(ValueError):
        ProviderConfig.from_env({})
    for model in ("anthropic/claude-sonnet-4.5", "claude-haiku-4-5-20251001", "other/claude-haiku-4.5"):
        with pytest.raises(ValueError):
            ProviderConfig("a", "b", "c", model, "voice")
    c = config()
    assert "secret" not in repr(c)
    providers = create_providers(c)
    assert providers.stt.config is providers.haiku.config is providers.tts.config


@pytest.mark.parametrize("missing", ["EUREKA_TTS_API_KEY", "EUREKA_TTS_VOICE_ID"])
def test_live_configuration_requires_elevenlabs_credentials(missing):
    env = {"EUREKA_STT_API_KEY": "stt", "EUREKA_HAIKU_API_KEY": "llm",
           "EUREKA_HAIKU_MODEL": "anthropic/claude-haiku-4.5",
           "EUREKA_TTS_API_KEY": "tts", "EUREKA_TTS_VOICE_ID": "voice"}
    del env[missing]
    with pytest.raises(ValueError):
        ProviderConfig.from_env(env)


def test_stt_streams_concurrently_and_stops_on_endpoint(monkeypatch):
    async def exercise():
        ws = FakeSocket()
        session = FakeSession(ws=ws)
        outer = use_session(monkeypatch, session)
        ended = asyncio.Event()
        async def frames():
            try:
                yield b"\x01\x00" * 160
                await asyncio.Event().wait()
            finally:
                ended.set()
        task = asyncio.create_task(create_providers(config()).stt.recognize_stream(frames(), sample_rate=8000))
        await asyncio.wait_for(ws.ready.wait(), 1)
        assert ws.sent == [b"\x01\x00" * 160]
        ws.push(result("ignore interim", 0.99, final=False))
        ws.push(result("hello", 0.92))
        ws.push(result("operator", 0.81, endpoint=True))
        assert await asyncio.wait_for(task, 1) == Recognition("hello operator", 0.81)
        assert ended.is_set() and outer.closed and session.connection.closed
        assert session.requests[0][1]["params"]["encoding"] == "linear16"
    run(exercise())


def test_stt_eof_drains_and_invalid_frame_fails(monkeypatch):
    async def exercise():
        ws = FakeSocket()
        use_session(monkeypatch, FakeSession(ws=ws))
        async def frames():
            yield b"\x00\x00"
        task = asyncio.create_task(create_providers(config()).stt.recognize_stream(frames(), sample_rate=8000))
        while len(ws.sent) < 2:
            await asyncio.sleep(0)
        assert ws.sent[-1] == {"type": "CloseStream"}
        ws.push(result("hi", 0.7))
        ws.push({"type": "Metadata"})
        assert await task == Recognition("hi", 0.7)
        async def bad():
            yield b"x"
        with pytest.raises(ProviderFailure):
            await create_providers(config()).stt.recognize_stream(bad(), sample_rate=8000)
    run(exercise())


def test_stt_propagates_audio_iterator_timeout_and_cleans_up(monkeypatch):
    async def exercise():
        ws = FakeSocket()
        session = FakeSession(ws=ws)
        outer = use_session(monkeypatch, session)
        timeout = asyncio.TimeoutError("call inactivity")

        async def frames():
            yield b"\0\0"
            raise timeout

        with pytest.raises(asyncio.TimeoutError) as caught:
            await create_providers(config()).stt.recognize_stream(frames(), sample_rate=8000)
        assert caught.value is timeout
        assert outer.closed and session.connection.closed
    run(exercise())


def test_stt_timeout_and_cancellation_cleanup(monkeypatch):
    async def exercise():
        ws = FakeSocket()
        session = FakeSession(ws=ws)
        outer = use_session(monkeypatch, session)
        async def frames():
            yield b"\0\0"
            await asyncio.Event().wait()
        task = asyncio.create_task(create_providers(config()).stt.recognize_stream(frames(), sample_rate=8000))
        await ws.ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert outer.closed and session.connection.closed
        with pytest.raises(ProviderFailure, match="STT provider failed") as caught:
            await create_providers(config(timeout_seconds=0.01)).stt.recognize_stream(frames(), sample_rate=8000)
        assert caught.value.category == 'timeout'
    run(exercise())


def test_stt_timeout_override_does_not_change_other_provider_timeout(monkeypatch):
    async def exercise():
        ws = FakeSocket()
        use_session(monkeypatch, FakeSession(ws=ws))
        async def frames():
            yield b'\0\0'
            await asyncio.Event().wait()
        providers = create_providers(config(timeout_seconds=0.01, stt_timeout_seconds=0.3))
        task = asyncio.create_task(providers.stt.recognize_stream(frames(), sample_rate=8000))
        await asyncio.wait_for(ws.ready.wait(), 1)
        await asyncio.sleep(.04)
        assert not task.done()
        ws.push(result('hello', 0.9, endpoint=True))
        assert await task == Recognition('hello', 0.9)
        assert providers.haiku.config.timeout_seconds == 0.01
    run(exercise())


@pytest.mark.parametrize('value', ['0', '-1', '301', 'nan', 'not-a-number'])
def test_invalid_stt_timeout_environment_is_rejected(value):
    env = {'EUREKA_STT_API_KEY': 'stt', 'EUREKA_HAIKU_API_KEY': 'llm',
           'EUREKA_HAIKU_MODEL': 'anthropic/claude-haiku-4.5',
           'EUREKA_TTS_API_KEY': 'tts', 'EUREKA_TTS_VOICE_ID': 'voice',
           'EUREKA_STT_TIMEOUT_SECONDS': value}
    with pytest.raises(ValueError):
        ProviderConfig.from_env(env)


def test_stt_timeout_diagnostic_counts_audio_and_results_without_content(monkeypatch):
    async def exercise():
        ws = FakeSocket()
        use_session(monkeypatch, FakeSession(ws=ws))
        async def frames():
            yield b'\0\0' * 160
            yield struct.pack('<160h', *([600] * 160))
            await asyncio.Event().wait()
        stt = create_providers(config(timeout_seconds=0.1)).stt
        task = asyncio.create_task(stt.recognize_stream(frames(), sample_rate=8000))
        while len(ws.sent) < 2:
            await asyncio.sleep(0)
        ws.push(result('private words', 0.9, final=False))
        with pytest.raises(ProviderFailure) as caught:
            await task
        assert caught.value.category == 'timeout'
        assert stt.last_diagnostic == {'stt_audio_frames': 2, 'stt_active_frames': 1,
            'stt_results': 1, 'stt_nonempty_results': 1, 'stt_final_results': 0}
        assert 'private words' not in str(stt.last_diagnostic)
    run(exercise())


def test_stt_network_failure_category_does_not_expose_error(monkeypatch):
    async def exercise():
        class BrokenSession:
            def ws_connect(self, *args, **kwargs):
                raise aiohttp.ClientConnectionError('private provider details')
        use_session(monkeypatch, BrokenSession())
        async def frames():
            yield b'\0\0'
        with pytest.raises(ProviderFailure) as caught:
            await create_providers(config()).stt.recognize_stream(frames(), sample_rate=8000)
        assert caught.value.category == 'network'
        assert 'private provider details' not in str(caught.value)
    run(exercise())


def choice(delta, finish_reason=None):
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}


def test_haiku_streams_and_sends_context(monkeypatch):
    async def exercise():
        chunks = ([b": OPENROUTER PROCESSING\n", b"\n"]
                  + sse(choice({"role": "assistant", "content": "Hello"}))
                  + sse(choice({"content": " there."}))
                  + sse(choice({}, "stop"))
                  + sse({**choice({}, "stop"), "usage": {}})
                  + sse({"choices": [], "usage": {}})
                  + [b"data: [DONE]\n", b"\n"])
        session = FakeSession(response=SimpleNamespace(status=200, content=Content(chunks)))
        outer = use_session(monkeypatch, session)
        haiku = create_providers(config()).haiku
        assert [s async for s in haiku.stream(system_prompt="approved info", context=("old question", "old answer"), user_text="new question")] == ["Hello", " there."]
        url, request = session.requests[0]
        payload = request["json"]
        assert url == "https://openrouter.ai/api/v1/chat/completions"
        assert request["headers"]["Authorization"] == "Bearer haiku-secret"
        assert payload["stream"] and payload["model"] == "anthropic/claude-haiku-4.5"
        assert [m["role"] for m in payload["messages"]] == ["system", "user", "assistant", "user"]
        assert payload["messages"][0]["content"] == "approved info"
        assert outer.closed and session.connection.closed
    run(exercise())


@pytest.mark.parametrize("events,done", [
    ([choice({"content": "partial"})], False),
    ([choice({"content": "partial"}), choice({}, "stop")], False),
    ([choice({"tool_calls": [{"id": "call"}]})], True),
    ([choice({"refusal": "cannot answer"})], True),
    ([choice({"content": {"text": "not a string"}})], True),
    ([{"error": {"message": "haiku-secret private transcript"}}], True),
    ([{"error": {}}], True),
    ([choice({"content": "partial"}), choice({}, "length")], True),
    ([choice({"content": "partial"}), choice({}, "stop"), choice({"content": "extra"})], True),
])
def test_haiku_fails_closed_on_incomplete_or_bad_events(monkeypatch, events, done):
    async def exercise():
        chunks = [chunk for event in events for chunk in sse(event)]
        if done:
            chunks += [b"data: [DONE]\n", b"\n"]
        use_session(monkeypatch, FakeSession(response=SimpleNamespace(status=200, content=Content(chunks))))
        with pytest.raises(ProviderFailure) as error:
            _ = [s async for s in create_providers(config()).haiku.stream(system_prompt="prompt", context=(), user_text="question")]
        assert "secret" not in str(error.value) and "transcript" not in str(error.value)
    run(exercise())


def test_haiku_http_failure_closes_session(monkeypatch):
    async def exercise():
        session = FakeSession(response=SimpleNamespace(status=401, content=Content([])))
        outer = use_session(monkeypatch, session)
        with pytest.raises(ProviderFailure) as error:
            _ = [s async for s in create_providers(config()).haiku.stream(system_prompt="prompt", context=(), user_text="question")]
        assert "secret" not in str(error.value)
        assert (error.value.category, error.value.http_status) == ("http_401", 401)
        assert outer.closed and session.connection.closed
    run(exercise())


def test_haiku_incomplete_stream_has_safe_diagnostic(monkeypatch):
    async def exercise():
        chunks = sse(choice({"content": "partial"})) + sse(choice({}, "length")) + [b"data: [DONE]\n", b"\n"]
        use_session(monkeypatch, FakeSession(response=SimpleNamespace(status=200, content=Content(chunks))))
        with pytest.raises(ProviderFailure) as error:
            _ = [s async for s in create_providers(config()).haiku.stream(system_prompt="prompt", context=(), user_text="question")]
        assert error.value.category == "incomplete_response"
    run(exercise())


def test_tts_streams_frames_pads_and_limits_peaks(monkeypatch):
    async def exercise():
        raw = struct.pack("<161h", *([32767] * 160 + [1234]))
        session = FakeSession(response=SimpleNamespace(status=200, content=Content([raw[:101], raw[101:]])))
        use_session(monkeypatch, session)
        tts = create_providers(config()).tts
        frames = [frame async for frame in tts.synthesize_stream("checked text", sample_rate=8000)]
        assert len(frames) == 2 and all(len(f) == FRAME_BYTES for f in frames)
        assert max(struct.unpack("<160h", frames[0])) == 30000
        assert frames[1][:2] == struct.pack("<h", 1234) and frames[1][2:] == bytes(318)
        assert session.requests[0][1]["params"] == {"output_format": "pcm_8000"}
    run(exercise())


def test_tts_rejects_truncation_and_http_errors(monkeypatch):
    async def exercise():
        session = FakeSession(response=SimpleNamespace(status=200, content=Content([b"\x00"])))
        use_session(monkeypatch, session)
        with pytest.raises(ProviderFailure) as error:
            await create_providers(config()).tts.synthesize("checked", sample_rate=8000)
        assert error.value.category == "invalid_audio"
        session.response = SimpleNamespace(status=401, content=Content([]))
        with pytest.raises(ProviderFailure) as error:
            await create_providers(config()).tts.synthesize("checked", sample_rate=8000)
        assert error.value.category == "http_401" and error.value.http_status == 401
        assert "secret" not in str(error.value)
        session.response = SimpleNamespace(status=402, content=Content([]))
        with pytest.raises(ProviderFailure) as error:
            await create_providers(config()).tts.synthesize("checked", sample_rate=8000)
        assert error.value.category == "http_other" and error.value.http_status == 402
        with pytest.raises(ProviderFailure):
            await create_providers(config()).tts.synthesize("checked", sample_rate=16000)
    run(exercise())
