import React, { useRef, useState } from "react";

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
      srcDoc={html}
      sandbox="allow-same-origin"
      onLoad={handleLoad}
      className="w-full border-0"
      style={{ height }}
    />
  );
}

export default HtmlFrame;
