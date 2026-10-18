"""Markdown projection of chat messages, shared by the LLM inputs and the chat export.

Three rules keep this module honest:

- The UI contract (``views.message_display_text`` / ``normalize_message_type`` /
  ``resolve_role``) is not changed here. Markdown is a second projection for the
  models and for exports.
- Plain text bodies are never Markdown-escaped. Translation must keep bodies
  byte-identical: it preserves the source HTML and matches transcript rows against
  the job's ``annotate`` map to place ``text_hash`` markers, so it uses the
  ``text``/``keep`` profile where a row body equals ``message_display_text``.
- Card fields have a single source: ``cards.card_view`` (plus
  ``cards.product_card_image`` for the cover), never re-derived here.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Iterable, Literal, NamedTuple

from backend.app.shared.cards import card_view, product_card_image
from backend.app.shared.crm.views import (
    CrmMessage,
    CrmResolver,
    is_card_message,
    message_display_text,
)
from backend.app.shared.utils.crm_time import format_created_at

SpeakerStyle = Literal["role", "display"]
HtmlPolicy = Literal["keep", "strip"]
CardStyle = Literal["text", "markdown"]


@dataclass(frozen=True)
class MarkdownOptions:
    """How one transcript row is projected.

    ``speaker_style``: ``role`` is the agent vocabulary (系统 / 商家(我) / 买家),
    ``display`` is the export vocabulary (系统 / 我的机器人 / 我 / 联系人名).
    ``html``: ``strip`` turns markup into plain text with real line breaks;
    ``keep`` leaves the body untouched (translation fidelity).
    ``card_style``: ``markdown`` structures cards; ``text`` keeps the raw
    ``message_display_text`` body.
    """

    speaker_style: SpeakerStyle = "role"
    html: HtmlPolicy = "keep"
    card_style: CardStyle = "markdown"


class TranscriptLine(NamedTuple):
    timestamp: str
    speaker: str
    body: str


AGENT_OPTIONS = MarkdownOptions(speaker_style="role", html="strip", card_style="markdown")
TRANSLATION_OPTIONS = MarkdownOptions(speaker_style="role", html="keep", card_style="text")
EXPORT_OPTIONS = MarkdownOptions(speaker_style="display", html="keep", card_style="markdown")

_BREAK_TAGS = re.compile(r"<\s*/?\s*(?:br|p|div|li)\s*/?\s*>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")
_HORIZONTAL_SPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def html_to_text(text: str) -> str:
    """HTML → plain text, keeping ``<br>``/block boundaries as real line breaks.

    Folding all whitespace into single spaces (the old behaviour) destroyed the
    line structure of buyer messages; models read the transcript better with the
    breaks kept.
    """
    if not text:
        return ""
    value = html.unescape(text)
    value = _BREAK_TAGS.sub("\n", value)
    value = _ANY_TAG.sub("", value)
    value = _HORIZONTAL_SPACE.sub(" ", value)
    value = _BLANK_LINES.sub("\n\n", value)
    return value.strip()


def speaker_label(
    message: CrmMessage,
    *,
    resolver: CrmResolver,
    options: MarkdownOptions,
    display_name: str = "",
) -> str:
    if message.is_system:
        return "系统"
    if options.speaker_style == "display":
        if resolver.is_self(message.sender_id):
            return "我的机器人" if message.is_auto_reply else "我"
        return display_name or "买家"
    return "商家(我)" if resolver.is_self(message.sender_id) else "买家"


def card_markdown(message: CrmMessage) -> str:
    """Structured Markdown for one card message, or the raw label when unparsable."""
    view = card_view(message)
    if view is None:
        return message_display_text(message)
    summary = str(view.get("summary") or "").strip()
    title = str(view.get("title") or "").strip()
    lines: list[str] = []
    if title and summary:
        lines.append(f"**{title}**：{summary}")
    elif title or summary:
        lines.append(f"**{title}**" if title else summary)
    image = product_card_image(message)
    if image:
        lines.append(f"![{title or '产品图片'}]({image})")
    for detail in view.get("details") or []:
        value = str(detail.get("value") or "").strip()
        label = str(detail.get("label") or "").strip()
        # The summary already carries these (price/MOQ on a product card).
        if not value or (summary and value in summary):
            continue
        lines.append(f"- {label}：{value}")
    link = view.get("link") or {}
    href = str(link.get("href") or "").strip()
    if href:
        lines.append(f"- [{link.get('label') or '链接'}]({href})")
    return "\n".join(lines) if lines else message_display_text(message)


def message_body(message: CrmMessage, options: MarkdownOptions) -> str:
    """Markdown body of one message. Card fields come from ``card_view``."""
    if is_card_message(message) and options.card_style == "markdown":
        return card_markdown(message)
    text = message_display_text(message)
    return html_to_text(text) if options.html == "strip" else text


def transcript_lines(
    messages: Iterable[CrmMessage],
    *,
    resolver: CrmResolver,
    options: MarkdownOptions,
    limit: int | None = 30,
    display_name: str = "",
) -> list[TranscriptLine]:
    rows = list(messages)
    if limit is not None:
        rows = rows[-limit:]
    return [
        TranscriptLine(
            timestamp=format_created_at(message.created_at),
            speaker=speaker_label(message, resolver=resolver, options=options, display_name=display_name),
            body=message_body(message, options),
        )
        for message in rows
    ]


def transcript_markdown(
    messages: Iterable[CrmMessage],
    *,
    resolver: CrmResolver,
    options: MarkdownOptions,
    limit: int | None = 30,
    display_name: str = "",
) -> str:
    """One Markdown block per message: a bold ``[time] speaker`` label, then the body."""
    blocks: list[str] = []
    for line in transcript_lines(
        messages, resolver=resolver, options=options, limit=limit, display_name=display_name
    ):
        label = f"[{line.timestamp}] {line.speaker}" if line.timestamp else line.speaker
        body = line.body.strip()
        blocks.append(f"**{label}**\n\n{body}" if body else f"**{label}**")
    return "\n\n".join(blocks)


__all__ = [
    "AGENT_OPTIONS",
    "EXPORT_OPTIONS",
    "TRANSLATION_OPTIONS",
    "MarkdownOptions",
    "TranscriptLine",
    "card_markdown",
    "html_to_text",
    "message_body",
    "speaker_label",
    "transcript_lines",
    "transcript_markdown",
]
