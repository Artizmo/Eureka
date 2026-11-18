import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from eureka.mvp import JsonOperationalLog, ProviderFailure  # noqa: E402
from eureka.telephony_daemon import (  # noqa: E402
    AudioSocketServer, EurekaAudioService, FrameParser, PhraseStore, ProtocolViolation,
    SessionManager, encode_frame,
)


class FailingConversation:
    def __init__(self):
        self.audio = None

    async def reply_to_audio(self, audio):
        self.audio = audio
        raise ProviderFailure("unconfigured")


def run(coro):
    return asyncio.run(coro)


def make_service(tmp_path, greeting_frames=1):
    directory = tmp_path / "phrases"
    directory.mkdir()
    for name in PhraseStore.REQUIRED:
        (directory / f"{name}.slin").write_bytes(b"\x01\x00" * 160 * (greeting_frames if name == "greeting" else 1))
    conversation = FailingConversation()
    sessions = SessionManager(max_concurrent_calls=1)
    service = EurekaAudioService(conversation=conversation, phrases=PhraseStore(directory), sessions=sessions,
        logger=JsonOperationalLog(tmp_path / "logs"), max_call_seconds=3, inactivity_seconds=1)
    return service, sessions, conversation


async def read_frame(reader):
    header = await asyncio.wait_for(reader.readexactly(3), 1)
    return header[0], await reader.readexactly(int.from_bytes(header[1:], "big"))


@pytest.mark.parametrize("host", ["0.0.0.0", "::1", "127.0.0.2"])
def test_server_rejects_other_bind_addresses(host):
    with pytest.raises(ValueError):
        AudioSocketServer(host=host, on_connection=lambda *_: None)


def test_rejects_nil_uuid_and_oversize_header_without_waiting_for_payload():
    async def exercise():
        reader = asyncio.StreamReader()
        reader.feed_data(encode_frame(1, bytes(16)))
        with pytest.raises(ProtocolViolation):
            await FrameParser(reader).read()
        reader = asyncio.StreamReader()
        reader.feed_data(encode_frame(1, uuid.uuid4().bytes) + b"\x10\xff\xff")
        parser = FrameParser(reader)
        await parser.read()
        with pytest.raises(ProtocolViolation):
            await asyncio.wait_for(parser.read(), 0.1)
    run(exercise())


def test_socket_audio_greeting_and_failure_fallback(tmp_path):
    async def exercise():
        service, sessions, conversation = make_service(tmp_path)
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            call_id = uuid.uuid4().bytes
            writer.write(encode_frame(1, call_id))
            await writer.drain()
            assert await read_frame(reader) == (0x10, b"\x01\x00" * 160)
            await asyncio.sleep(0.04)
            audio = b"\x02\x00" * 160
            writer.write(encode_frame(0x10, audio) * 20)
            await writer.drain()
            assert await read_frame(reader) == (0x10, b"\x01\x00" * 160)
            assert await read_frame(reader) == (0x00, b"")
            assert await asyncio.wait_for(reader.read(), 1) == b""
            assert conversation.audio == audio * 20
            assert await sessions.get(call_id) is None
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()
        record = json.loads(next((tmp_path / "logs").glob("*.jsonl")).read_text())
        assert record["end_reason"] == "provider_failure"
    run(exercise())


@pytest.mark.parametrize("hangup", [True, False])
def test_hangup_or_socket_drop_interrupts_greeting(tmp_path, hangup):
    async def exercise():
        service, sessions, _ = make_service(tmp_path, greeting_frames=100)
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            reader, writer = await asyncio.open_connection(server.host, server.port)
            call_id = uuid.uuid4().bytes
            writer.write(encode_frame(1, call_id))
            await writer.drain()
            await read_frame(reader)
            if hangup:
                writer.write(encode_frame(0))
                await writer.drain()
                assert await asyncio.wait_for(reader.read(), 0.4) == b""
            else:
                writer.close()
                await writer.wait_closed()
            for _ in range(40):
                if await sessions.get(call_id) is None:
                    break
                await asyncio.sleep(0.01)
            assert await sessions.get(call_id) is None
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()
    run(exercise())


def test_duplicate_uuid_does_not_remove_active_session(tmp_path):
    async def exercise():
        service, sessions, _ = make_service(tmp_path, greeting_frames=100)
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        try:
            first, writer = await asyncio.open_connection(server.host, server.port)
            call_id = uuid.uuid4().bytes
            writer.write(encode_frame(1, call_id))
            await writer.drain()
            await read_frame(first)
            second, duplicate = await asyncio.open_connection(server.host, server.port)
            duplicate.write(encode_frame(1, call_id))
            await duplicate.drain()
            assert await asyncio.wait_for(second.read(), 1) == b""
            assert await sessions.get(call_id) is not None
            duplicate.close()
            writer.close()
            await writer.wait_closed()
            for _ in range(40):
                if await sessions.get(call_id) is None:
                    break
                await asyncio.sleep(0.01)
            assert await sessions.get(call_id) is None
        finally:
            await server.close()
    run(exercise())


def test_service_entrypoint_serves_shipped_greeting(tmp_path, monkeypatch):
    from eureka import service as entrypoint

    root = Path(__file__).parents[1]
    monkeypatch.setenv("EUREKA_PHRASE_DIR", str(root / "phrases"))
    monkeypatch.setenv("EUREKA_KNOWLEDGE_PATH", str(root / "knowledge/approved-facts-v1.json"))
    monkeypatch.setenv("EUREKA_PROMPT_PATH", str(root / "prompts/system-v1.txt"))
    monkeypatch.setenv("EUREKA_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("EUREKA_PORT", "0")

    async def exercise():
        ready = asyncio.Event()
        servers = []

        class CapturingServer(AudioSocketServer):
            async def start(self):
                await super().start()
                servers.append(self)
                ready.set()

        monkeypatch.setattr(entrypoint, "AudioSocketServer", CapturingServer)
        task = asyncio.create_task(entrypoint.run())
        try:
            await asyncio.wait_for(ready.wait(), 1)
            reader, writer = await asyncio.open_connection("127.0.0.1", servers[0].port)
            writer.write(encode_frame(1, uuid.uuid4().bytes))
            await writer.drain()
            assert await read_frame(reader) == (0x10, (root / "phrases/greeting.slin").read_bytes()[:320])
            writer.write(encode_frame(0))
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 1) == b""
            writer.close()
            await writer.wait_closed()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    run(exercise())


@pytest.fixture
def pre_render(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("pre_render", Path(__file__).parents[1] / "tools/pre_render_phrases.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(PhraseStore, "REQUIRED", {**PhraseStore.REQUIRED, "thinking": "One moment, please."})
    return module


def test_pre_render_uses_configured_tts_for_all_phrases(tmp_path, monkeypatch, pre_render):
    config = object()
    calls = []
    monkeypatch.setattr(pre_render.ProviderConfig, "from_env", lambda: config)

    class FakeTTS:
        def __init__(self, actual_config):
            assert actual_config is config

        async def synthesize(self, text, *, sample_rate):
            calls.append((text, sample_rate))
            return bytes([len(calls), 0]) * 160

    monkeypatch.setattr(pre_render, "ElevenLabsTTS", FakeTTS)
    directory = tmp_path / "phrases"
    run(pre_render.render(directory))
    assert calls == [(text, 8000) for text in PhraseStore.REQUIRED.values()]
    for index, name in enumerate(PhraseStore.REQUIRED, 1):
        assert (directory / f"{name}.slin").read_bytes() == bytes([index, 0]) * 160
    PhraseStore(directory)
    assert sorted(path.name for path in directory.iterdir()) == sorted(f"{name}.slin" for name in PhraseStore.REQUIRED)


@pytest.mark.parametrize("failure", ["exception", "empty", "unaligned", "oversized"])
def test_pre_render_failure_preserves_existing_clips(tmp_path, monkeypatch, pre_render, failure):
    directory = tmp_path / "phrases"
    directory.mkdir()
    original = b"\x01\x00" * 160
    for name in PhraseStore.REQUIRED:
        (directory / f"{name}.slin").write_bytes(original)
    monkeypatch.setattr(pre_render.ProviderConfig, "from_env", lambda: object())

    class FakeTTS:
        def __init__(self, config):
            self.calls = 0

        async def synthesize(self, text, *, sample_rate):
            self.calls += 1
            if self.calls == 2:
                if failure == "exception":
                    raise RuntimeError("provider failed")
                return {"empty": b"", "unaligned": b"\x00", "oversized": bytes(8000 * 2 * 60 + 320)}[failure]
            return b"\x02\x00" * 160

    monkeypatch.setattr(pre_render, "ElevenLabsTTS", FakeTTS)
    with pytest.raises((RuntimeError, ValueError)):
        run(pre_render.render(directory))
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == {
        f"{name}.slin": original for name in PhraseStore.REQUIRED
    }


def test_pre_render_staging_failure_preserves_existing_clips(tmp_path, monkeypatch, pre_render):
    directory = tmp_path / "phrases"
    directory.mkdir()
    original = b"\x01\x00" * 160
    for name in PhraseStore.REQUIRED:
        (directory / f"{name}.slin").write_bytes(original)
    monkeypatch.setattr(pre_render.ProviderConfig, "from_env", lambda: object())

    class FakeTTS:
        def __init__(self, config):
            pass

        async def synthesize(self, text, *, sample_rate):
            return b"\x02\x00" * 160

    monkeypatch.setattr(pre_render, "ElevenLabsTTS", FakeTTS)
    real_fchmod = pre_render.os.fchmod
    calls = 0

    def fail_second_stage(fd, mode):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("staging failed")
        real_fchmod(fd, mode)

    monkeypatch.setattr(pre_render.os, "fchmod", fail_second_stage)
    with pytest.raises(OSError):
        run(pre_render.render(directory))
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == {
        f"{name}.slin": original for name in PhraseStore.REQUIRED
    }


def test_pre_render_validate_only_never_loads_provider(tmp_path, monkeypatch, capsys, pre_render):
    for name in PhraseStore.REQUIRED:
        (tmp_path / f"{name}.slin").write_bytes(bytes(320))
    monkeypatch.setattr(pre_render.ProviderConfig, "from_env", lambda: pytest.fail("provider configuration accessed"))
    monkeypatch.setattr(sys, "argv", ["pre_render_phrases.py", str(tmp_path), "--validate-only"])
    pre_render.main()
    output = capsys.readouterr().out
    assert all(f"{name}.slin" in output for name in PhraseStore.REQUIRED)
    (tmp_path / "thinking.slin").write_bytes(b"")
    with pytest.raises(SystemExit) as exc:
        pre_render.main()
    assert exc.value.code == 1


def test_pre_render_cli_hides_failure_details(tmp_path, monkeypatch, capsys, pre_render):
    def fail():
        raise ValueError("secret-provider-key")

    monkeypatch.setattr(pre_render.ProviderConfig, "from_env", fail)
    monkeypatch.setattr(sys, "argv", ["pre_render_phrases.py", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        pre_render.main()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "secret-provider-key" not in captured.out + captured.err


@pytest.mark.parametrize("option", ["--espeak", "--sox"])
def test_pre_render_rejects_obsolete_renderer_options(tmp_path, monkeypatch, pre_render, option):
    monkeypatch.setattr(sys, "argv", ["pre_render_phrases.py", str(tmp_path), option, "fake"])
    with pytest.raises(SystemExit) as exc:
        pre_render.main()
    assert exc.value.code == 2
