import DOMPurify from "isomorphic-dompurify";

const IMAGE_PLACEHOLDER = "[图片]";

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

/**
 * Sanitize message/translation text into safe HTML for `dangerouslySetInnerHTML`.
 *
 * Buyer messages may carry rich-text markup (`<br>`, links, bold...); translations
 * are plain text from the LLM. Both go through the same allowlist so a stray
 * `<3` or similar never disappears and executable markup never survives.
 * Images are unconditionally replaced with a text placeholder (policy: blocked).
 */
export function renderMessageHtml(raw: string | null | undefined) {
  const html = DOMPurify.sanitize(raw ?? "", {
    ALLOWED_TAGS: ["a", "b", "blockquote", "br", "div", "em", "i", "li", "ol", "p", "s", "span", "strong", "u", "ul"],
    ALLOWED_ATTR: ["href", "rel", "target", "title"],
    ALLOW_UNKNOWN_PROTOCOLS: false,
  });
  return { __html: html };
}
