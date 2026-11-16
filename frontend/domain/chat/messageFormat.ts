/** Detects elements in an inert fragment without rendering or loading its resources. */
export function containsHtmlElements(content: string): boolean {
  const template = document.createElement("template");
  template.innerHTML = content;
  return template.content.querySelector("*") !== null;
}
