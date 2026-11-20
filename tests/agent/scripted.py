"""A scripted stand-in for the model: returns pre-written tool calls, one response per turn, and records what it saw."""

from __future__ import annotations

import json
from typing import Any

from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText


def call(name: str, **args: Any) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        type="function_call", name=name, arguments=json.dumps(args), call_id=f"call_{name}_{id(args)}", id=None
    )


def say(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        type="message",
        id="msg",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


class ScriptedModel(Model):
    def __init__(self, turns: list[list[Any]], tokens_per_turn: int = 100):
        self.turns = list(turns)
        self.tokens = tokens_per_turn
        self.seen_tool_outputs: list[str] = []
        self.instructions: str | None = None

    async def get_response(self, system_instructions, input, model_settings, tools, *args, **kwargs):
        self.instructions = system_instructions
        if isinstance(input, list):
            self.seen_tool_outputs = [str(i.get("output")) for i in input if i.get("type") == "function_call_output"]
        output = self.turns.pop(0) if self.turns else [say("nothing left to do")]
        usage = Usage(requests=1, input_tokens=self.tokens, output_tokens=0, total_tokens=self.tokens)
        return ModelResponse(output=output, usage=usage, response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError
