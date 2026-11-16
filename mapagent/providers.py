"""Model providers. The agent loop only sees Turn objects, so adding a provider is one class."""
import json
import os

from .agent import Call, Turn

DEFAULT_CLAUDE_MODEL = "claude-haiku-5-5"


class ClaudeProvider:
    """Anthropic Messages API with tool use. Needs ANTHROPIC_API_KEY in the environment."""

    def __init__(self, model=None, max_tokens=4096):  # thinking counts toward max_tokens on current models
        import anthropic
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()  # a pasted key often carries a trailing newline
        self.client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        self.model = model or os.environ.get("MAP_AGENT_CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)
        self.max_tokens = max_tokens

    def complete(self, system, messages, tool_specs):
        response = self.client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system,
            tools=tool_specs, messages=[self._convert(m) for m in messages])
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [Call(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"]
        usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        return Turn(text, calls, usage)

    @staticmethod
    def _convert(message):
        if message["role"] == "user":
            return {"role": "user", "content": message["text"]}
        if message["role"] == "assistant":
            content = [{"type": "text", "text": message["text"]}] if message["text"] else []
            content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.args} for c in message["calls"]]
            return {"role": "assistant", "content": content}
        return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]}
                                            for r in message["results"]]}


class OpenAIProvider:
    """OpenAI Chat Completions with function calling. Needs OPENAI_API_KEY and MAP_AGENT_OPENAI_MODEL."""

    def __init__(self, model=None):
        import openai
        self.client = openai.OpenAI()
        self.model = model or os.environ["MAP_AGENT_OPENAI_MODEL"]

    def complete(self, system, messages, tool_specs):
        response = self.client.chat.completions.create(
            model=self.model, tools=self.tools(tool_specs),
            messages=[{"role": "system", "content": system}, *(m for message in messages for m in self._convert(message))])
        message = response.choices[0].message
        calls = [Call(c.id, c.function.name, self._arguments(c.function.arguments)) for c in message.tool_calls or []]
        usage = ({"input_tokens": response.usage.prompt_tokens, "output_tokens": response.usage.completion_tokens}
                 if response.usage else {})
        return Turn(message.content or "", calls, usage)

    @staticmethod
    def tools(tool_specs):
        return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["input_schema"]}} for t in tool_specs]

    @staticmethod
    def _arguments(raw):
        """Unreadable arguments become an input the tool rejects, so the model sees the error."""
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {"unreadable_arguments": str(raw)[:200]}
        return parsed if isinstance(parsed, dict) else {"unreadable_arguments": str(raw)[:200]}

    @staticmethod
    def _convert(message):
        if message["role"] == "user":
            return [{"role": "user", "content": message["text"]}]
        if message["role"] == "assistant":
            return [{"role": "assistant", "content": message["text"] or None,
                     "tool_calls": [{"id": c.id, "type": "function",
                                     "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                                    for c in message["calls"]]}]
        return [{"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in message["results"]]


class ScriptedProvider:
    """Replays fixed turns. Each step is a Turn, or a function of the messages that returns one."""

    def __init__(self, steps):
        self.steps = list(steps)

    def complete(self, system, messages, tool_specs):
        step = self.steps.pop(0)
        return step(messages) if callable(step) else step


def get(name, model=None):
    if name == "claude":
        return ClaudeProvider(model)
    if name == "openai":
        return OpenAIProvider(model)
    raise ValueError(f"Unknown provider {name}. Available: claude, openai.")
