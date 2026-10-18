import DOMPurify from "isomorphic-dompurify";
import { marked } from "marked";

/**
 * How message-ish text becomes HTML for `dangerouslySetInnerHTML`.
 *
 * `markdown` is for text this app produces (prompt drafts, LLM replies);
 * `rich-text` is for text the buyer sent, which already carries markup and must
 * not be re-interpreted as Markdown (`- x`, `# y` stay literal).
 */
export type ContentKind = "markdown" | "rich-text";

const IMAGE_PLACEHOLDER = "[图片]";
const MARKDOWN_IMAGE_PATTERN = /!\[([^\]]*)\]\([^)]*\)/g;

// Union of what prompts (Markdown) and buyer messages (rich text) may contain.
// Images never render (privacy: they would trigger remote fetches) and the
// sanitizer allowlist is fixed here so every surface shares one policy.
const ALLOWED_TAGS = [
  "a", "b", "blockquote", "br", "code", "del", "div", "em", "h1", "h2", "h3",
  "h4", "h5", "h6", "hr", "i", "li", "ol", "p", "pre", "s", "span", "strong",
  "table", "tbody", "td", "th", "thead", "tr", "u", "ul",
];
const ALLOWED_ATTR = ["align", "href", "rel", "target", "title"];

DOMPurify.addHook("uponSanitizeElement", (node) => {
  const element = node as unknown as Pick<Element, "replaceWith" | "setAttribute" | "tagName" | "ownerDocument">;
  if (typeof element.tagName !== "string") return;
  if (element.tagName === "IMG") {
    element.replaceWith(element.ownerDocument.createTextNode(IMAGE_PLACEHOLDER));
    return;
  }
  if (element.tagName === "A") {
    element.setAttribute("target", "_blank");
    element.setAttribute("rel", "noopener noreferrer");
  }
});

export function renderContentHtml(raw: string | null | undefined, kind: ContentKind) {
  const source = raw ?? "";
  const html = kind === "markdown"
    ? marked.parse(source.replace(MARKDOWN_IMAGE_PATTERN, (_all, alt: string) => (alt ? `[图片：${alt}]` : "[图片]")), { async: false, gfm: true, breaks: true })
    : source;
  const sanitized = DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_UNKNOWN_PROTOCOLS: false,
  });
  return { __html: sanitized };
}
