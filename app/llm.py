"""
LLM provider abstraction.

Two providers:
- "stub": returns a deterministic answer built from the retrieved context.
  Useful for development without API keys, tests, and offline demos.
- "openai": uses the OpenAI Chat Completions API (also works with any
  OpenAI-compatible endpoint by setting OPENAI_BASE_URL).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


SYSTEM_PROMPT = """You are an order-guide assistant for an EV configurator.
You answer questions using ONLY the records provided in the context
(structured orders and/or PDF document chunks).
If the context doesn't contain enough information, say so explicitly.
When you cite a record, include its id and source. Keep answers concise."""


@dataclass
class LLMResponse:
    text: str
    used_model: str


def _format_context(hits: list[dict]) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        rid = h.get("id", h.get("order_id", "?"))
        src = h.get("source", "")
        blocks.append(
            f"[{i}] source={src} id={rid} score={h['score']}\n{h['document']}"
        )
    return "\n\n".join(blocks)


def _stub_answer(question: str, hits: list[dict]) -> LLMResponse:
    if not hits:
        return LLMResponse(
            text="I couldn't find any matching records.",
            used_model="stub",
        )
    lines = [f"Here are the {len(hits)} most relevant records I found:\n"]
    for h in hits:
        m = h["metadata"]
        rid = h.get("id", h.get("order_id", "?"))
        src = h.get("source", "")
        if src == "documents":
            page = m.get("page", "")
            page_str = f" p{page}" if page else ""
            source = m.get("source", "document")
            lines.append(
                f"- [{src}]{page_str} {rid} (score {h['score']}): from {source}"
            )
        else:
            # structured order
            model = m.get("model", "?")
            trim = m.get("trim", "")
            color = m.get("exterior_color", "")
            status = m.get("status", "?")
            msrp = m.get("msrp_usd", "?")
            region = m.get("customer_region", "?")
            lines.append(
                f"- [{src}] {rid} (score {h['score']}): {model} {trim} in {color}, "
                f"status={status}, MSRP ${msrp}, region {region}."
            )
    lines.append(
        "\n(Set LLM_PROVIDER=openai with OPENAI_API_KEY for a synthesized natural-language answer.)"
    )
    return LLMResponse(text="\n".join(lines), used_model="stub")


def _openai_answer(question: str, hits: list[dict]) -> LLMResponse:
    # Imported lazily so the stub path doesn't require httpx at runtime
    import httpx

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return LLMResponse(
            text="OPENAI_API_KEY is not set. Falling back to stub.\n\n"
            + _stub_answer(question, hits).text,
            used_model="stub-fallback",
        )

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    context = _format_context(hits)
    user_msg = f"Context (retrieved orders):\n{context}\n\nQuestion: {question}"

    r = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.2,
        },
        timeout=60.0,
    )
    r.raise_for_status()
    data = r.json()
    return LLMResponse(
        text=data["choices"][0]["message"]["content"].strip(),
        used_model=model,
    )


def answer(question: str, hits: list[dict]) -> LLMResponse:
    provider = os.environ.get("LLM_PROVIDER", "stub").lower()
    if provider == "openai":
        return _openai_answer(question, hits)
    return _stub_answer(question, hits)
