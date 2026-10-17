import DOMPurify from "isomorphic-dompurify";
import { marked } from "marked";

const IMAGE_PATTERN = /!\[([^\]]*)\]\([^)]*\)/g;

const ALLOWED_TAGS = [
  "a", "b", "blockquote", "br", "code", "del", "div", "em",
  "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "li", "ol", "p", "pre",
  "s", "span", "strong", "table", "tbody", "td", "th", "thead", "tr", "u", "ul",
];
const ALLOWED_ATTR = ["href", "title", "align"];

/**
 * Render one prompt draft as sanitized HTML for `dangerouslySetInnerHTML`.
 *
 * Prompts are Markdown-ish plain text; GFM plus hard line breaks matches how
 * numbered rules and protocol blocks are written. Images never render (privacy:
 * they would trigger remote fetches); a text placeholder is kept instead.
 * External links open in a new tab; the sanitizer allowlist is fixed here so
 * preview rendering stays independent from chat message sanitizing.
 */
export function renderPromptHtml(raw: string | null | undefined) {
  const source = (raw ?? "").replace(IMAGE_PATTERN, (_all, alt: string) => (alt ? `[图片：${alt}]` : "[图片]"));
  const html = marked.parse(source, { async: false, gfm: true, breaks: true });
  const sanitized = DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_UNKNOWN_PROTOCOLS: false,
    RETURN_DOM: true,
  });
  if (typeof sanitized === "string") return { __html: sanitized };
  const body = sanitized as unknown as HTMLElement;
  body.querySelectorAll("a").forEach((link) => {
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
  });
  return { __html: body.innerHTML };
}

export function promptCharCount(raw: string | null | undefined) {
  return (raw ?? "").length;
}

/**
 * Rough token estimate for a mixed CJK/latin/other prompt.
 *
 * Used only as an editor hint; real limits are enforced by the model context
 * window. CJK characters count as one token each, other text as ~4 chars each.
 */
export function estimatePromptTokens(raw: string | null | undefined) {
  const value = raw ?? "";
  const cjk = value.match(/[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]/g)?.length ?? 0;
  return cjk + Math.ceil((value.length - cjk) / 4);
}
