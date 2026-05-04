"""Server protocol-wiring tests — tools / resources / prompts register + dispatch correctly."""
from __future__ import annotations

import json

import pytest

from openclaw_output_vetter_mcp.server import build_server


def test_build_server() -> None:
    server = build_server()
    assert server is not None
    assert server.name == "openclaw-output-vetter"


# ───────────── Tool registration ─────────────


async def test_list_tools_returns_three() -> None:
    from mcp.types import ListToolsRequest

    server = build_server()
    handler = server.request_handlers[ListToolsRequest]
    result = await handler(ListToolsRequest(method="tools/list"))
    names = {t.name for t in result.root.tools}
    expected = {
        "verify_response_grounding",
        "find_swallowed_exceptions",
        "review_transcript",
    }
    assert names == expected


async def test_tools_have_valid_schemas() -> None:
    from mcp.types import ListToolsRequest

    server = build_server()
    handler = server.request_handlers[ListToolsRequest]
    result = await handler(ListToolsRequest(method="tools/list"))
    for tool in result.root.tools:
        assert isinstance(tool.inputSchema, dict)
        assert tool.inputSchema.get("type") == "object"


# ───────────── verify_response_grounding ─────────────


async def test_call_tool_verify_grounding_clean() -> None:
    from mcp.types import CallToolRequest, CallToolRequestParams

    server = build_server()
    handler = server.request_handlers[CallToolRequest]
    result = await handler(
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="verify_response_grounding",
                arguments={
                    "question": "Where is the office?",
                    "context": "Pixelette Technologies is headquartered in London.",
                    "answer": "The Pixelette Technologies headquarters is in London.",
                },
            ),
        )
    )
    parsed = json.loads(result.root.content[0].text)
    assert parsed["verdict"] == "clean"
    assert parsed["grounded_count"] >= 1


async def test_call_tool_verify_grounding_fabricated() -> None:
    from mcp.types import CallToolRequest, CallToolRequestParams

    server = build_server()
    handler = server.request_handlers[CallToolRequest]
    result = await handler(
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="verify_response_grounding",
                arguments={
                    "question": "What's the funding?",
                    "context": "Pixelette is a self-funded software studio.",
                    "answer": (
                        "Pixelette has raised twelve million dollars in Series A funding from "
                        "Sequoia Capital. The company has forty-seven full-time employees."
                    ),
                },
            ),
        )
    )
    parsed = json.loads(result.root.content[0].text)
    assert parsed["verdict"] == "fabricated"


# ───────────── find_swallowed_exceptions ─────────────


async def test_call_tool_find_swallowed_exceptions_flags_mock() -> None:
    from mcp.types import CallToolRequest, CallToolRequestParams

    server = build_server()
    handler = server.request_handlers[CallToolRequest]
    result = await handler(
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="find_swallowed_exceptions",
                arguments={
                    "code": (
                        "def fetch():\n"
                        "    try:\n"
                        "        return real_call()\n"
                        "    except Exception:\n"
                        "        return {'id': 1, 'name': 'sample'}\n"
                    ),
                },
            ),
        )
    )
    parsed = json.loads(result.root.content[0].text)
    assert parsed["finding_count"] >= 1
    assert any(f["pattern"] == "mock-substitution" for f in parsed["findings"])


async def test_call_tool_find_swallowed_exceptions_clean() -> None:
    from mcp.types import CallToolRequest, CallToolRequestParams

    server = build_server()
    handler = server.request_handlers[CallToolRequest]
    result = await handler(
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="find_swallowed_exceptions",
                arguments={
                    "code": (
                        "def safe(x):\n"
                        "    try:\n"
                        "        return process(x)\n"
                        "    except ValueError as exc:\n"
                        "        raise RuntimeError(f'bad: {exc}') from exc\n"
                    ),
                },
            ),
        )
    )
    parsed = json.loads(result.root.content[0].text)
    assert parsed["verdict"] == "clean"
    assert parsed["finding_count"] == 0


# ───────────── review_transcript ─────────────


async def test_call_tool_review_transcript_flags_unverified_completion() -> None:
    from mcp.types import CallToolRequest, CallToolRequestParams

    server = build_server()
    handler = server.request_handlers[CallToolRequest]
    result = await handler(
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="review_transcript",
                arguments={
                    "transcript": [
                        {"role": "user", "text": "Please configure the gateway."},
                        {
                            "role": "assistant",
                            "text": "I've configured the gateway and verified everything works.",
                        },
                    ],
                },
            ),
        )
    )
    parsed = json.loads(result.root.content[0].text)
    assert parsed["issue_count"] >= 1
    assert any(i["issue_kind"] == "unverified-completion-claim" for i in parsed["issues"])


async def test_call_tool_review_transcript_handles_empty_list() -> None:
    from mcp.types import CallToolRequest, CallToolRequestParams

    server = build_server()
    handler = server.request_handlers[CallToolRequest]
    result = await handler(
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="review_transcript",
                arguments={"transcript": []},
            ),
        )
    )
    parsed = json.loads(result.root.content[0].text)
    assert parsed["verdict"] == "unverified"
    assert parsed["turn_count"] == 0


async def test_call_tool_unknown_returns_error() -> None:
    from mcp.types import CallToolRequest, CallToolRequestParams

    server = build_server()
    handler = server.request_handlers[CallToolRequest]
    result = await handler(
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="not_a_tool", arguments={}),
        )
    )
    parsed = json.loads(result.root.content[0].text)
    assert "error" in parsed


# ───────────── Resources ─────────────


async def test_list_resources_returns_three() -> None:
    from mcp.types import ListResourcesRequest

    server = build_server()
    handler = server.request_handlers[ListResourcesRequest]
    result = await handler(ListResourcesRequest(method="resources/list"))
    uris = {str(r.uri) for r in result.root.resources}
    assert {
        "vetter://demo/grounded",
        "vetter://demo/fabricated",
        "vetter://demo/swallowed-exceptions",
    } <= uris


# ───────────── Prompts ─────────────


async def test_list_prompts_returns_two() -> None:
    from mcp.types import ListPromptsRequest

    server = build_server()
    handler = server.request_handlers[ListPromptsRequest]
    result = await handler(ListPromptsRequest(method="prompts/list"))
    names = {p.name for p in result.root.prompts}
    assert names == {"verify-this-answer", "audit-this-code"}


@pytest.mark.parametrize("prompt_name", ["verify-this-answer", "audit-this-code"])
async def test_get_prompt_returns_walkthrough_text(prompt_name: str) -> None:
    from mcp.types import GetPromptRequest, GetPromptRequestParams

    server = build_server()
    handler = server.request_handlers[GetPromptRequest]
    result = await handler(
        GetPromptRequest(
            method="prompts/get",
            params=GetPromptRequestParams(name=prompt_name, arguments={}),
        )
    )
    text = result.root.messages[0].content.text
    assert len(text) > 50
    # Each prompt should reference at least one of the tools it walks through
    assert any(
        tool in text
        for tool in {"verify_response_grounding", "find_swallowed_exceptions"}
    )
