import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from provider_client import (
    OpenAICompatibleClient,
    TextBlock,
    ToolUseBlock,
    from_openai_response,
    to_openai_messages,
    to_openai_tools,
)


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeHTTPClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


class ProviderClientTests(unittest.TestCase):
    def test_converts_anthropic_tool_schema_to_openai_function(self):
        converted = to_openai_tools([{
            "name": "bash",
            "description": "Run a command",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        }])

        self.assertEqual(converted[0]["type"], "function")
        self.assertEqual(converted[0]["function"]["name"], "bash")
        self.assertEqual(
            converted[0]["function"]["parameters"]["required"], ["command"]
        )

    def test_converts_tool_use_and_tool_result_message_pair(self):
        messages = [
            {"role": "user", "content": "List files"},
            {"role": "assistant", "content": [
                ToolUseBlock(
                    type="tool_use",
                    id="call_123",
                    name="bash",
                    input={"command": "dir"},
                )
            ]},
            {"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": "call_123",
                "content": "README.md",
            }]},
        ]

        converted = to_openai_messages("You are an agent.", messages)

        self.assertEqual(converted[0]["role"], "system")
        self.assertEqual(converted[2]["tool_calls"][0]["id"], "call_123")
        self.assertEqual(
            json.loads(converted[2]["tool_calls"][0]["function"]["arguments"]),
            {"command": "dir"},
        )
        self.assertEqual(converted[3], {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": "README.md",
        })

    def test_translates_openai_tool_call_to_anthropic_shaped_block(self):
        response = from_openai_response({
            "model": "deepseek-v4-flash",
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command":"dir"}',
                        },
                    }],
                },
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

        self.assertEqual(response.stop_reason, "tool_use")
        self.assertIsInstance(response.content[0], ToolUseBlock)
        self.assertEqual(response.content[0].input, {"command": "dir"})

    def test_messages_create_uses_chat_completions_endpoint(self):
        fake_http = FakeHTTPClient({
            "model": "deepseek-v4-flash",
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "Done", "tool_calls": None},
            }],
        })
        client = OpenAICompatibleClient(
            api_key="test-key",
            base_url="https://token.sensenova.cn/v1",
            http_client=fake_http,
        )

        response = client.messages.create(
            model="deepseek-v4-flash",
            system="You are an agent.",
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
            max_tokens=100,
        )

        self.assertEqual(
            fake_http.calls[0][0],
            "https://token.sensenova.cn/v1/chat/completions",
        )
        request_json = fake_http.calls[0][1]["json"]
        self.assertEqual(request_json["messages"][0]["role"], "system")
        self.assertNotIn("tools", request_json)
        self.assertEqual(response.stop_reason, "end_turn")
        self.assertIsInstance(response.content[0], TextBlock)
        self.assertEqual(response.content[0].text, "Done")


if __name__ == "__main__":
    unittest.main()
