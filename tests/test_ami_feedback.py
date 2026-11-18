import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from eureka.ami import AMIClient, FeedbackCorrelator, configured_client
from eureka.telephony_daemon import AudioSocketServer, encode_frame
from test_phase3_transport import FakeConversation, build_service, read_frame


def association(call_id, uniqueid):
    return {"event": "UserEvent", "userevent": "EurekaAudioSocket", "eurekauuid": str(call_id),
            "eurekauniqueid": uniqueid, "uniqueid": uniqueid}


def dtmf(uniqueid, digit="1", direction="Received"):
    return {"event": "DTMFEnd", "uniqueid": uniqueid, "digit": digit, "direction": direction}


def frame(fields):
    return b"".join(f"{key}: {value}\r\n".encode() for key, value in fields.items()) + b"\r\n"


def test_correlation_conflicts_duplicates_hangup_and_expiry(monkeypatch):
    from eureka import ami
    clock = [10.0]
    monkeypatch.setattr(ami.time, "monotonic", lambda: clock[0])
    bus = FeedbackCorrelator()
    one, two, stale = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    received = []
    bus.event(association(one, "100.1"))  # May precede AudioSocket connection.
    bus.event(association(two, "100.2"))
    bus.attach(one.bytes, lambda digit: received.append((1, digit)))
    bus.attach(two.bytes, lambda digit: received.append((2, digit)))
    bus.event(association(two, "100.1"))
    bus.event(association(one, "100.2"))
    bus.event(association(one, "100.1"))
    for event in (dtmf("100.1", "3"), dtmf("100.1", "1", "Sent"), dtmf("100.3"),
                  {**association(stale, "100.3"), "eurekauniqueid": "100.4"}):
        bus.event(event)
    bus.event(dtmf("100.2", "2"))
    bus.event(dtmf("100.1", "1"))
    assert received == [(2, "2"), (1, "1")]
    bus.event({"event": "Hangup", "uniqueid": "100.1"})
    bus.event(association(one, "100.1"))
    bus.event(dtmf("100.1"))
    bus.detach(two.bytes)
    bus.event(association(two, "100.2"))
    bus.event(dtmf("100.2"))
    assert len(received) == 2
    bus.event(association(stale, "100.3"))
    clock[0] += 31
    bus.attach(stale.bytes, lambda digit: received.append((3, digit)))
    bus.event(dtmf("100.3"))
    assert len(received) == 2
    bus.clear()


def test_configuration_and_credentials():
    bus = FeedbackCorrelator()
    assert configured_client(bus, {}) is None
    assert configured_client(bus, {"EUREKA_AMI_ENABLED": "false", "EUREKA_AMI_SECRET": "ignored\n"}) is None
    for env in ({"EUREKA_AMI_ENABLED": "yes"}, {"EUREKA_AMI_ENABLED": "true"},
                {"EUREKA_AMI_ENABLED": "true", "EUREKA_AMI_USERNAME": "test\nAction: X", "EUREKA_AMI_SECRET": "x"}):
        with pytest.raises(ValueError):
            configured_client(bus, env)
    assert configured_client(bus, {"EUREKA_AMI_ENABLED": "true", "EUREKA_AMI_USERNAME": "user",
                                   "EUREKA_AMI_SECRET": "secret"}) is not None


def test_stream_parser_limits_and_privacy():
    async def exercise():
        client = AMIClient("user", "secret", FeedbackCorrelator())
        reader = asyncio.StreamReader()
        reader.feed_data(frame({"Event": "DTMFEnd", "CallerIDNum": "private-number", "Digit": "1"}))
        parsed = await client._frame(reader)
        assert parsed == {"event": "DTMFEnd", "digit": "1"}
        for data in (b"Event: X\r\nEvent: Y\r\n\r\n", b"not-a-header\r\n\r\n",
                     b"X: " + b"a" * 4100 + b"\r\n\r\n", b"X: " + b"a" * 1000 + b"\r\n"):
            reader = asyncio.StreamReader()
            reader.feed_data(data * (17 if data.startswith(b"X: ") and len(data) < 2000 else 1))
            reader.feed_eof()
            with pytest.raises((ValueError, asyncio.IncompleteReadError)):
                await client._frame(reader)
    asyncio.run(exercise())


def test_fake_ami_reconnect_clears_mapping_and_redacts(caplog):
    async def exercise():
        bus = FeedbackCorrelator()
        call = uuid.uuid4()
        received = []
        bus.attach(call.bytes, received.append)
        connections = 0
        ready = asyncio.Event()
        second = asyncio.Event()
        async def server(reader, writer):
            nonlocal connections
            connections += 1
            n = connections
            writer.write(b"Asterisk Call Manager/5.0\r\n")
            await writer.drain()
            login = await reader.readuntil(b"\r\n\r\n")
            assert b"Action: Login" in login
            writer.write(b"Response: Success\r\n\r\n")
            if n == 1:
                writer.write(frame(association(call, "11.1")) + frame(dtmf("11.1")))
                await writer.drain()
                ready.set()
                await asyncio.sleep(0.05)
                writer.close()
                await writer.wait_closed()
            else:
                writer.write(frame({**dtmf("11.1", "2"), "CallerIDNum": "private-number"}))
                await writer.drain()
                await asyncio.sleep(0.05)
                second.set()
                await reader.read()
                writer.close()
                await writer.wait_closed()
        listener = await asyncio.start_server(server, "127.0.0.1", 0)
        client = AMIClient("private-user", "private-secret", bus, port=listener.sockets[0].getsockname()[1])
        task = asyncio.create_task(client.run())
        try:
            await asyncio.wait_for(ready.wait(), 2)
            await asyncio.wait_for(second.wait(), 3)
            assert received == ["1"]
            assert "AMI feedback connection established" in caplog.text
            assert "AMI feedback unavailable; retrying" in caplog.text
            assert "private" not in caplog.text
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            listener.close()
            await listener.wait_closed()
    asyncio.run(exercise())


@pytest.mark.parametrize("rating", ["1", "2"])
def test_feedback_prompt_time_from_ami_with_audio_flood(tmp_path, rating):
    async def exercise():
        bus = FeedbackCorrelator()
        service, _ = build_service(tmp_path, FakeConversation(), inactivity=0.4)
        service.feedback_correlator = bus
        original_play = service._play
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0
        async def controlled_play(writer, audio):
            nonlocal calls
            calls += 1
            if calls == 2:
                entered.set()
                await release.wait()
            await original_play(writer, audio)
        service._play = controlled_play
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        call = uuid.uuid4()
        try:
            bus.event(association(call, "20.1"))
            reader, writer = await asyncio.open_connection(server.host, server.port)
            writer.write(encode_frame(1, call.bytes))
            await writer.drain()
            await read_frame(reader)
            bus.event(dtmf("20.1", "1" if rating == "2" else "2"))  # Before feedback.
            await asyncio.sleep(0.04)
            writer.write(encode_frame(0x10, b"\x00\x04" * 160) * 2)
            await writer.drain()
            await asyncio.wait_for(entered.wait(), 1)
            bus.event(dtmf("20.1", "9"))
            bus.event(dtmf("20.1", rating, "Sent"))
            bus.event(dtmf("20.1", rating))
            bus.event(dtmf("20.1", "1" if rating == "2" else "2"))
            writer.write(encode_frame(0x10, b"\x00\x04" * 160) * 150)
            await writer.drain()
            await asyncio.sleep(0.05)
            release.set()
            await read_frame(reader)
            assert await read_frame(reader) == (0, b"")
            assert await asyncio.wait_for(reader.read(), 1) == b""
            writer.close()
            await writer.wait_closed()
            record = json.loads(next((tmp_path / "logs").glob("*.jsonl")).read_text())
            assert record["rating"] == int(rating)
            assert record["end_reason"] == "feedback"
            assert "20.1" not in json.dumps(record)
        finally:
            await server.close()
    asyncio.run(exercise())


def test_feedback_timeout_detaches_and_invalid_digits_are_not_logged(tmp_path):
    async def exercise():
        bus = FeedbackCorrelator()
        service, _ = build_service(tmp_path, FakeConversation(), inactivity=0.15)
        service.feedback_correlator = bus
        server = AudioSocketServer(port=0, on_connection=service.handle)
        await server.start()
        call = uuid.uuid4()
        try:
            bus.event(association(call, "30.1"))
            reader, writer = await asyncio.open_connection(server.host, server.port)
            writer.write(encode_frame(1, call.bytes))
            await writer.drain()
            await read_frame(reader)
            await asyncio.sleep(0.04)
            writer.write(encode_frame(0x10, b"\x00\x04" * 160) * 2)
            await writer.drain()
            await read_frame(reader)
            bus.event({**dtmf("30.1", "9"), "calleridnum": "private-number"})
            assert await read_frame(reader) == (0, b"")
            assert await asyncio.wait_for(reader.read(), 1) == b""
            bus.event(dtmf("30.1", "1"))
            record = json.loads(next((tmp_path / "logs").glob("*.jsonl")).read_text())
            assert record["rating"] is None
            assert record["end_reason"] == "inactivity_timeout"
            assert "private-number" not in json.dumps(record) and "30.1" not in json.dumps(record)
            assert call.bytes not in bus._receivers and call.bytes not in bus._by_uuid
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()
    asyncio.run(exercise())


def test_fragmented_frame():
    async def exercise():
        reader = asyncio.StreamReader()
        client = AMIClient("user", "secret", FeedbackCorrelator())
        task = asyncio.create_task(client._frame(reader))
        for part in (b"Eve", b"nt: DTMFEnd\r", b"\nUniqueid: 5.1\r\n", b"Digit: 2\r\n\r", b"\n"):
            reader.feed_data(part)
            await asyncio.sleep(0)
        assert await task == {"event": "DTMFEnd", "uniqueid": "5.1", "digit": "2"}
    asyncio.run(exercise())
