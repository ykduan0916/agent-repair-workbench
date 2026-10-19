"""HTTP protocol/usage tests: no real requests or credentials."""

import asyncio
import json

import httpx
import pytest

from agent_workbench.model_gateway.gateway import (
    INPUT_BOUND,
    PROFILE,
    Config,
    DeepSeekAdapter,
    GatewayError,
    decode_reply,
)


def adapter(transport=None):
    return DeepSeekAdapter(
        Config("https://example.invalid", "deepseek-flash", "test-only", PROFILE),
        transport=transport,
    )


def response(**overrides):
    data = {
        "usage": {"prompt_tokens": 32, "completion_tokens": 16, "total_tokens": 48},
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {"name": "emit_decision", "arguments": '{"decision":{}}'},
                        }
                    ]
                },
            }
        ],
    }
    data.update(overrides)
    return json.dumps(data).encode()


def test_role_output_caps_and_profile():
    gateway = adapter()
    for role, expected in (("diagnosis", 4096), ("repair", 8192), ("verification", 4096)):
        prepared = gateway.prepare(role, [], 8192)
        assert prepared.output_reserved == expected
        assert prepared.input_reserved == INPUT_BOUND
        assert json.loads(prepared.body)["max_tokens"] == expected
    assert gateway.prepare("repair", [], 100).output_reserved == 100
    assert adapter().config_sha256 == gateway.config_sha256
    assert "test-only" not in repr(gateway._config)
    with pytest.raises(GatewayError, match="INPUT_BOUND_UNVERIFIED"):
        Config("https://example.invalid", "unknown", "test-only", PROFILE)
    with pytest.raises(GatewayError, match="INPUT_BOUND_UNVERIFIED"):
        Config("https://example.invalid", "deepseek-flash", "test-only", "guess")


@pytest.mark.parametrize(
    "url",
    [
        "file:///secret",
        "https://user:pass@example.com",
        "http://x/v2",
        "https://x?secret=a",
        "http://x/\n",
        "http://x#fragment",
    ],
)
def test_bad_configuration(url):
    with pytest.raises(GatewayError):
        Config(url, "deepseek-flash", "test-only", PROFILE)


def test_usage_and_truncation_are_independent():
    prepared = adapter().prepare("diagnosis", [], 8192)
    known = decode_reply(response(), prepared)
    assert known.usage_known and known.decision == b"{}"
    truncated = decode_reply(response(choices=[{"finish_reason": "length"}]), prepared)
    assert truncated.usage_known and truncated.error == "MODEL_OUTPUT_TRUNCATED"
    invalid = decode_reply(response(choices=[]), prepared)
    assert invalid.usage_known and invalid.decision is None


@pytest.mark.parametrize(
    "usage,error",
    [
        (None, "MODEL_USAGE_UNKNOWN"),
        ({"prompt_tokens": True, "completion_tokens": 0, "total_tokens": 1}, "MODEL_USAGE_UNKNOWN"),
        ({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 3}, "MODEL_USAGE_UNKNOWN"),
        ({"prompt_tokens": -1, "completion_tokens": 2, "total_tokens": 1}, "MODEL_USAGE_UNKNOWN"),
        (
            {
                "prompt_tokens": INPUT_BOUND + 1,
                "completion_tokens": 0,
                "total_tokens": INPUT_BOUND + 1,
            },
            "MODEL_USAGE_BOUND_VIOLATION",
        ),
        (
            {"prompt_tokens": 0, "completion_tokens": 4097, "total_tokens": 4097},
            "MODEL_USAGE_BOUND_VIOLATION",
        ),
    ],
)
def test_unknown_usage_keeps_entire_reservation(usage, error):
    reply = decode_reply(response(usage=usage), adapter().prepare("diagnosis", [], 8192))
    assert reply.error == error and not reply.usage_known


def test_duplicate_keys_and_response_size_rejected():
    prepared = adapter().prepare("diagnosis", [], 8192)
    assert not decode_reply(b'{"usage":{},"usage":{}}', prepared).usage_known
    assert decode_reply(b" " * 131073, prepared).error == "MODEL_RESPONSE_INVALID"
    with pytest.raises(GatewayError):
        adapter().prepare("repair", [{"role": "user", "content": "x" * 65536}], 8192)


def test_transport_does_not_follow_redirect_or_leak_body():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://leak.invalid"}, content=b"SECRET")

    gateway = adapter(httpx.MockTransport(handler))
    reply = asyncio.run(gateway.send(gateway.prepare("diagnosis", [], 8192), 1))
    assert len(calls) == 1 and reply.error == "MODEL_TRANSPORT_ERROR"
    assert "SECRET" not in repr(reply)


def test_real_http_client_decodes_one_structured_reply():
    gateway = adapter(httpx.MockTransport(lambda _: httpx.Response(200, content=response())))
    reply = asyncio.run(gateway.send(gateway.prepare("diagnosis", [], 8192), 1))
    assert reply.usage_known and reply.decision == b"{}"


def test_function_parameters_are_root_object_and_refs_resolve():
    from jsonschema import Draft202012Validator

    gateway = adapter()
    for role in ("diagnosis", "repair", "verification"):
        params = json.loads(gateway.prepare(role, [], 8192).body)["tools"][0]["function"][
            "parameters"
        ]
        assert params["type"] == "object"
        Draft202012Validator.check_schema(params)
        conclusion = {
            "action": "conclude",
            "assessment": "inconclusive",
            "reason_code": "evidence_missing",
            "evidence_refs": [],
        }
        if role == "diagnosis":
            Draft202012Validator(params).validate({"decision": conclusion})


@pytest.mark.parametrize(
    "args", ["{}", '{"decision":{},"extra":1}', '{"decision":{},"decision":{}}', '{"decision":1}']
)
def test_wrapper_does_not_weaken_role_boundary(args):
    obj = json.loads(response())
    obj["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = args
    reply = decode_reply(json.dumps(obj).encode(), adapter().prepare("diagnosis", [], 8192))
    expected = (
        "MODEL_DECISION_JSON_INVALID" if args.count('"decision"') == 2 else "MODEL_DECISION_INVALID"
    )
    assert reply.usage_known and reply.error == expected
