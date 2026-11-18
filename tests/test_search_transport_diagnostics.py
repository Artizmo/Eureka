import asyncio
import json
import uuid

import pytest

from test_phase3_transport import FakeConversation, build_service
from eureka.mvp import OperationalRecord


@pytest.mark.parametrize(('category', 'expected'), [
    ('outer_timeout', 'outer_timeout'), ('private arbitrary text', None),
])
def test_respond_search_failure_logging(tmp_path, category, expected):
    async def exercise():
        conversation = FakeConversation()
        conversation.last_search_failure = category
        service, _ = build_service(tmp_path, conversation)
        record = OperationalRecord(str(uuid.uuid4()), 0)
        frames = asyncio.Queue()
        for _ in range(2):
            frames.put_nowait((0x10, b'\x00\x04' * 160))
        await service._respond(frames, record)
        turn = next(event for event in record.events if event['event'] == 'turn')
        assert turn.get('search_failure') == expected
        service.logger.write(record)
        payload = json.loads(next((tmp_path / 'logs').glob('calls-*.jsonl')).read_text())
        saved = next(event for event in payload['events'] if event['event'] == 'turn')
        assert saved.get('search_failure') == expected
        assert 'private arbitrary text' not in json.dumps(payload)
    asyncio.run(exercise())
