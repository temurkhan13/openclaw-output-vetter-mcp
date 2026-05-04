"""MCP server — registers tools, resources, prompts; delegates to scanners."""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server import Server
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    Resource,
    TextContent,
    Tool,
)
from pydantic import AnyUrl, ValidationError

from openclaw_output_vetter_mcp.scanners import (
    find_swallowed_exceptions,
    review_transcript,
    verify_grounding,
)
from openclaw_output_vetter_mcp.types import Turn

logger = logging.getLogger(__name__)

SERVER_NAME = "openclaw-output-vetter"


# Demo-mode preset transcripts so a Claude Desktop user can try the server
# without authoring inputs themselves.
_DEMO_GROUNDED = {
    "question": "Where is the office?",
    "context": (
        "Pixelette Technologies is headquartered in London. "
        "We have a remote-first team with engineers in Pakistan + the UK + Canada. "
        "The London office is at 12 Old Brewery Mews."
    ),
    "answer": (
        "The Pixelette Technologies headquarters is in London. "
        "The team is remote-first with members across Pakistan, the UK, and Canada."
    ),
}

_DEMO_FABRICATED = {
    "question": "What's the company's funding?",
    "context": "Pixelette Technologies is a self-funded software studio.",
    "answer": (
        "Pixelette Technologies has raised $12M in Series A funding led by Sequoia Capital. "
        "The company has 47 full-time employees and recently expanded into APAC."
    ),
}

_DEMO_CODE_SWALLOWED = """
def fetch_user_records(api_url):
    try:
        response = requests.get(api_url, timeout=5)
        return response.json()
    except Exception:
        return {"records": [{"id": 1, "name": "sample"}], "total": 1}


def archive_logs(path):
    try:
        os.remove(path)
    except OSError:
        pass


def save_state(state):
    try:
        write_to_disk(state)
        return True
    except IOError as e:
        print(f"warning: save failed: {e}")
        return True
"""


def build_server(backend_name: str = "default") -> Server:  # noqa: ARG001 — backend reserved for v1.1+
    """Construct a configured MCP server. `backend_name` reserved for future caching/storage backends."""
    server: Server = Server(SERVER_NAME)

    # ─────────────────────────── Tools ───────────────────────────

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="verify_response_grounding",
                description=(
                    "Check that every claim in `answer` has support in `context`. "
                    "Returns per-claim grounded/ungrounded + an overall verdict "
                    "(CLEAN / PARTIALLY_GROUNDED / FABRICATED). Use inline during "
                    "an agent conversation to flag hallucinated responses before they "
                    "become user-facing facts. Sub-second, local, no API key."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The user question (used for context-binding; v1.0 stores but doesn't use)",
                        },
                        "context": {
                            "type": "string",
                            "description": "Retrieval / source context the answer should be grounded in",
                        },
                        "answer": {
                            "type": "string",
                            "description": "The agent's response to verify",
                        },
                        "threshold": {
                            "type": "number",
                            "description": (
                                "Jaccard overlap threshold for `grounded` (0.0–1.0). "
                                "Default 0.30. Lower = more permissive, higher = stricter."
                            ),
                            "default": 0.30,
                        },
                    },
                    "required": ["question", "context", "answer"],
                },
            ),
            Tool(
                name="find_swallowed_exceptions",
                description=(
                    "Scan Python source code for try/except patterns that swallow "
                    "errors or substitute fabricated mock data — the silent-fake-success "
                    "pattern from the r/ClaudeAI thread. Flags pass-only handlers, "
                    "mock-substitution returns, silent log-and-return, and bare excepts. "
                    "Each finding includes a line number + severity + code excerpt."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Python source code to scan",
                        },
                    },
                    "required": ["code"],
                },
            ),
            Tool(
                name="review_transcript",
                description=(
                    "Multi-turn agent transcript review — flags unverified completion "
                    "claims (assistant says 'I've configured X' with no supporting tool "
                    "calls), cross-turn factual contradictions, and tool calls without "
                    "observable side effects. Pass an array of {role, text, tool_calls?} "
                    "objects."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "transcript": {
                            "type": "array",
                            "description": "List of turns. Each: {role, text, tool_calls?}",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "role": {"type": "string"},
                                    "text": {"type": "string"},
                                    "tool_calls": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": ["role", "text"],
                            },
                        },
                    },
                    "required": ["transcript"],
                },
            ),
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        logger.debug("call_tool name=%s args.keys=%s", name, list(arguments.keys()))

        if name == "verify_response_grounding":
            question = str(arguments.get("question", "")).strip()
            context = str(arguments.get("context", "")).strip()
            answer = str(arguments.get("answer", "")).strip()
            threshold = max(0.0, min(float(arguments.get("threshold", 0.30)), 1.0))
            result = verify_grounding(question, context, answer, threshold=threshold)
            return _serialize(result)

        if name == "find_swallowed_exceptions":
            code = str(arguments.get("code", ""))
            return _serialize(find_swallowed_exceptions(code))

        if name == "review_transcript":
            raw = arguments.get("transcript")
            if not isinstance(raw, list):
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"error": "transcript must be a list of turn objects"}),
                    )
                ]
            try:
                turns = [Turn.model_validate(item) for item in raw if isinstance(item, dict)]
            except ValidationError as exc:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"error": f"Invalid transcript turn: {exc}"}),
                    )
                ]
            return _serialize(review_transcript(turns))

        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

    # ──────────────────────── Resources ───────────────────────

    @server.list_resources()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri=AnyUrl("vetter://demo/grounded"),
                name="Demo: grounded answer",
                description="Sample input demonstrating a CLEAN grounding verdict",
                mimeType="application/json",
            ),
            Resource(
                uri=AnyUrl("vetter://demo/fabricated"),
                name="Demo: fabricated answer",
                description="Sample input demonstrating a FABRICATED grounding verdict",
                mimeType="application/json",
            ),
            Resource(
                uri=AnyUrl("vetter://demo/swallowed-exceptions"),
                name="Demo: swallowed-exception patterns",
                description="Sample Python code with mock-substitution + pass-only + log-and-return patterns",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()  # type: ignore[no-untyped-call, untyped-decorator]
    async def read_resource(uri: str) -> str:
        uri_s = str(uri)
        if uri_s == "vetter://demo/grounded":
            d = _DEMO_GROUNDED
            return verify_grounding(d["question"], d["context"], d["answer"]).model_dump_json(indent=2)
        if uri_s == "vetter://demo/fabricated":
            d = _DEMO_FABRICATED
            return verify_grounding(d["question"], d["context"], d["answer"]).model_dump_json(indent=2)
        if uri_s == "vetter://demo/swallowed-exceptions":
            return find_swallowed_exceptions(_DEMO_CODE_SWALLOWED).model_dump_json(indent=2)
        return json.dumps({"error": f"Unknown resource URI: {uri_s}"})

    # ───────────────────────── Prompts ────────────────────────

    @server.list_prompts()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_prompts() -> list[Prompt]:
        return [
            Prompt(
                name="verify-this-answer",
                description="Run a grounding check on the most recent assistant answer + flag hallucinations",
                arguments=[
                    PromptArgument(
                        name="threshold",
                        description="Jaccard overlap threshold for grounded (default 0.30)",
                        required=False,
                    ),
                ],
            ),
            Prompt(
                name="audit-this-code",
                description=(
                    "Run swallowed-exception detection on a code block and explain "
                    "each finding's risk + how to fix"
                ),
                arguments=[],
            ),
        ]

    @server.get_prompt()  # type: ignore[no-untyped-call, untyped-decorator]
    async def get_prompt(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
        arguments = arguments or {}
        if name == "verify-this-answer":
            threshold = arguments.get("threshold", "0.30")
            text = (
                f"Take the most recent assistant response (the `answer`), the user's "
                f"original question, and any retrieved context that should support the "
                f"answer. Call `verify_response_grounding(question=..., context=..., "
                f"answer=..., threshold={threshold})`. Then: (1) state the verdict "
                f"(CLEAN / PARTIALLY_GROUNDED / FABRICATED) and overall_grounding_score; "
                f"(2) for each ungrounded claim, name the claim verbatim + its overlap "
                f"score; (3) recommend either acknowledging uncertainty in the response, "
                f"retrieving additional context, or marking the claims as the agent's "
                f"opinion rather than fact. End with one specific corrective action — "
                f"do not output 'continue monitoring.'"
            )
            return GetPromptResult(
                description="Inline grounding-check walkthrough",
                messages=[
                    PromptMessage(role="user", content=TextContent(type="text", text=text)),
                ],
            )

        if name == "audit-this-code":
            text = (
                "Take the code block the user just provided (or the most recent code block "
                "in the conversation). Call `find_swallowed_exceptions(code=...)`. Then: "
                "(1) name each finding with line number + pattern + severity; "
                "(2) for mock-substitution patterns, quote the exact return value being "
                "fabricated and explain why the caller will be misled; "
                "(3) recommend a specific fix per finding — re-raise after logging, "
                "use a Result/Optional return type, or document why the swallow is "
                "intentional. End with a one-line ticket-ready verdict."
            )
            return GetPromptResult(
                description="Swallowed-exception audit walkthrough",
                messages=[
                    PromptMessage(role="user", content=TextContent(type="text", text=text)),
                ],
            )

        return GetPromptResult(
            description=f"Unknown prompt: {name}",
            messages=[
                PromptMessage(role="user", content=TextContent(type="text", text=f"Unknown prompt: {name}")),
            ],
        )

    return server


def _serialize(model: Any) -> list[TextContent]:
    return [TextContent(type="text", text=model.model_dump_json(indent=2))]
