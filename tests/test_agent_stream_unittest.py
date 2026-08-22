# -*- coding: utf-8 -*-
"""Redis Agent事件只承担短期传输，不改变SQL最终结果契约。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from backend.agents import stream_events


class _SyncRedis:
    def __init__(self):
        self.added = []
        self.expired = []

    def xadd(self, key, fields, **options):
        self.added.append((key, fields, options))
        return "1-0"

    def expire(self, key, ttl):
        self.expired.append((key, ttl))


class _AsyncRedis:
    def __init__(self):
        self.called = False

    async def xread(self, _streams, **_options):
        if self.called:
            return []
        self.called = True
        return [[
            "stream",
            [
                ("1-0", {"event": json.dumps({"task_id": 7, "type": "phase"})}),
                ("2-0", {"event": json.dumps({"task_id": 7, "type": "task_completed"})}),
            ],
        ]]


class AgentStreamPublishTest(unittest.TestCase):
    def test_publish_uses_bounded_stream_and_ttl(self):
        client = _SyncRedis()
        with (
            patch.object(stream_events, "AGENT_STREAM_ENABLED", True),
            patch.object(stream_events, "_get_sync_client", return_value=client),
        ):
            emitted = stream_events.emit_agent_event(
                7,
                "text_delta",
                attempt=2,
                delta="测试",
            )

        self.assertTrue(emitted)
        key, fields, options = client.added[0]
        event = json.loads(fields["event"])
        self.assertTrue(key.endswith(":stream:task:7"))
        self.assertEqual(event["attempt"], 2)
        self.assertEqual(event["delta"], "测试")
        self.assertEqual(options["maxlen"], stream_events.AGENT_STREAM_MAX_EVENTS)
        self.assertEqual(client.expired[0][1], stream_events.AGENT_STREAM_TTL_SECONDS)

    def test_emitter_coalesces_small_text_chunks_before_next_event(self):
        emitter = stream_events.AgentStreamEmitter(7, 1)
        with patch.object(stream_events, "emit_agent_event", return_value=True) as publish:
            emitter.emit("text_delta", delta="短")
            emitter.emit("text_delta", delta="文本")
            self.assertEqual(publish.call_count, 0)
            emitter.emit("result_ready")

        self.assertEqual(publish.call_count, 2)
        self.assertEqual(publish.call_args_list[0].args, (7, "text_delta"))
        self.assertEqual(publish.call_args_list[0].kwargs["delta"], "短文本")
        self.assertEqual(publish.call_args_list[1].args, (7, "result_ready"))


class AgentStreamReadTest(unittest.IsolatedAsyncioTestCase):
    async def test_reader_preserves_ids_and_stops_on_terminal_event(self):
        with (
            patch.object(stream_events, "AGENT_STREAM_ENABLED", True),
            patch.object(stream_events, "_get_async_client", return_value=_AsyncRedis()),
        ):
            events = [event async for event in stream_events.iter_agent_events(7)]

        self.assertEqual([event["event_id"] for event in events], ["1-0", "2-0"])
        self.assertEqual(events[-1]["type"], "task_completed")


if __name__ == "__main__":
    unittest.main()
