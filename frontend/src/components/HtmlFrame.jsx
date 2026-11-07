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
// the content to size the frame.
function HtmlFrame({ html, title }) {
  const frameRef = useRef(null);
  const [height, setHeight] = useState(0);

  function handleLoad() {
    const doc = frameRef.current?.contentDocument;
    if (!doc) return;
    const fit = () => setHeight(doc.documentElement.scrollHeight);
    fit();
    new ResizeObserver(fit).observe(doc.body);
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
