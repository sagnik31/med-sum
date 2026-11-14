import React, { useRef, useState } from "react";

// Model output is untrusted: a document could carry instructions that make
// the model emit, say, an <img> pointing at another server with patient data
// in its URL. This policy lets the frame use its own inline styles and
// nothing else, so it cannot load images, fonts, styles or frames from
// anywhere. Placed before the document, the parser puts it in <head>.
const CONTENT_POLICY =
  '<meta http-equiv="Content-Security-Policy" ' +
  "content=\"default-src 'none'; style-src 'unsafe-inline'\">";

// Renders AI-generated HTML (a full document with its own <style>) in a
// sandboxed iframe: its CSS cannot leak into the app, and scripts in it
// cannot run (no allow-scripts). allow-same-origin only lets us measure
// the content to size the frame, and, with onCite, lets the app make the
// summary's source references (<cite data-document-id ...>) clickable:
// the listener runs in the app, not in the frame, and onCite gets only the
// element's data attributes to check.
function HtmlFrame({ html, title, onCite }) {
  const frameRef = useRef(null);
  const [height, setHeight] = useState(0);
  const onCiteRef = useRef(onCite);
  onCiteRef.current = onCite;

  function handleLoad() {
    const doc = frameRef.current?.contentDocument;
    if (!doc) return;
    const fit = () => setHeight(doc.documentElement.scrollHeight);
    fit();
    new ResizeObserver(fit).observe(doc.body);

    if (!onCite) return;
    for (const el of doc.querySelectorAll("cite[data-document-id]")) {
      el.setAttribute("role", "link");
      el.setAttribute("tabindex", "0");
      el.setAttribute("title", "Show this value on its document");
      el.style.cursor = "pointer";
      el.style.textDecoration = "underline";
    }
    const open = (event) => {
      const el = event.target.closest?.("cite[data-document-id]");
      if (!el) return;
      if (event.type === "keydown" && event.key !== "Enter") return;
      event.preventDefault();
      const { documentId, page, lines } = el.dataset;
      onCiteRef.current?.({ documentId, page, lines });
    };
    doc.addEventListener("click", open);
    doc.addEventListener("keydown", open);
  }

  return (
    <iframe
      ref={frameRef}
      title={title}
      srcDoc={CONTENT_POLICY + html}
      sandbox="allow-same-origin"
      onLoad={handleLoad}
      className="w-full border-0"
      style={{ height }}
    />
  );
}

export default HtmlFrame;
