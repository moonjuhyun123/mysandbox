"""Optional Claude API client for the plot and cast stages.

Nothing here runs unless ``[studio.llm] enabled = true`` in the settings: API calls cost money
and are the owner's decision. Credentials come from the environment (``ANTHROPIC_API_KEY`` or
an ``ant auth login`` profile), never from the settings file.
"""
from __future__ import annotations

import json
from typing import Any, Mapping


class LLMError(Exception):
    """The call could not produce a usable answer; the message is safe to show to the user."""


class ClaudeLLM:
    def __init__(self, model: str, *, effort: str = "high", max_tokens: int = 32000):
        try:
            import anthropic
        except ModuleNotFoundError as exc:
            raise LLMError("anthropic 패키지가 없습니다. `pip install -r requirements-llm.txt`로 설치하세요.") from exc
        self._sdk = anthropic
        self.name, self.effort, self.max_tokens = model, effort, max_tokens
        try:
            self.client = anthropic.Anthropic()
        except anthropic.AnthropicError as exc:
            raise LLMError(f"Claude API 인증 정보를 찾지 못했습니다: {exc}") from exc
        # The client is built without credentials too; the first request would then die with a
        # TypeError. Say it here, before any work is done.
        if all(getattr(self.client, name, None) is None for name in ("api_key", "auth_token", "credentials")):
            raise LLMError("Claude API 인증 정보가 없습니다. ANTHROPIC_API_KEY 환경 변수를 설정하거나 "
                           "`ant auth login`으로 로그인하세요. (키는 설정 파일에 적지 않습니다.)")

    def complete_json(self, system: str, user: str, schema: Mapping[str, Any]) -> dict[str, Any]:
        sdk = self._sdk
        try:
            # Streaming keeps long answers inside HTTP timeouts. The system prompt (rules + meme
            # index) is the same for every post, so it is cached. Server-side fallbacks let a
            # declined request be re-run on another model instead of failing the whole plot.
            with self.client.beta.messages.stream(
                model=self.name,
                max_tokens=self.max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": dict(schema)}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                message = stream.get_final_message()
        except sdk.AuthenticationError as exc:
            raise LLMError("Claude API 키가 올바르지 않습니다.") from exc
        except sdk.PermissionDeniedError as exc:
            raise LLMError(f"이 키로는 요청할 수 없습니다: {exc.message}") from exc
        except sdk.NotFoundError as exc:
            raise LLMError(f"모델 이름을 확인하세요({self.name}): {exc.message}") from exc
        except sdk.RateLimitError as exc:
            raise LLMError("요청 한도에 걸렸습니다. 잠시 뒤 다시 실행하세요.") from exc
        except sdk.APIStatusError as exc:
            raise LLMError(f"Claude API 오류({exc.status_code}): {exc.message}") from exc
        except sdk.APIConnectionError as exc:
            raise LLMError(f"Claude API에 연결하지 못했습니다: {exc}") from exc
        except _transport_errors() as exc:      # a connection dropped while the answer was streaming
            raise LLMError(f"답을 받는 중에 연결이 끊겼습니다. 다시 실행하세요: {exc}") from exc

        if message.stop_reason == "refusal":
            raise LLMError("모델이 이 요청을 거절했습니다. 다른 글을 고르세요.")
        if message.stop_reason == "max_tokens":
            raise LLMError("답이 길이 제한에서 잘렸습니다. [studio.llm] max_tokens를 늘리세요.")
        # After a decline in mid-answer the fallback model continues the partial text: the answer is
        # every text block in order, on both sides of the "fallback" block, not just the first one.
        text = "".join(block.text for block in message.content if block.type == "text")
        if not text.strip():
            raise LLMError("모델의 답에 글이 없습니다.")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"모델의 답이 JSON이 아닙니다: {exc}") from exc


def _transport_errors() -> tuple[type[BaseException], ...]:
    """The HTTP library's own error base: the SDK does not wrap errors raised while a stream is read."""
    try:
        import httpx2 as http                # anthropic 1.x and later
    except ModuleNotFoundError:
        try:
            import httpx as http             # anthropic 0.x
        except ModuleNotFoundError:
            return ()
    return (http.HTTPError,)


def make_llm(studio: Mapping[str, Any]) -> ClaudeLLM:
    cfg = studio.get("llm", {})
    if not cfg.get("enabled", False):
        raise LLMError(
            "LLM 호출이 꺼져 있습니다(유료). config/settings.toml의 [studio.llm] enabled = true로 켜거나, "
            "--prompt-only로 지시문을 받아 쓰던 채팅 LLM에 붙여 넣으세요.")
    return ClaudeLLM(cfg["model"], effort=cfg.get("effort", "high"), max_tokens=int(cfg.get("max_tokens", 32000)))
