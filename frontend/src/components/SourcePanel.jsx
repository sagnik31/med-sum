// src/components/SourcePanel.jsx
//
// One page of the document as the pipeline transcribed it, numbered, with
// the selected value's lines highlighted and scrolled into view. The text
// is shown as text (React escapes it), never as HTML.
import React, { useEffect, useRef } from "react";
import { formatCitation, sourceLines } from "../record/recordView.js";

function SourcePanel({ pages, page, citation, onPage }) {
  const citedRef = useRef(null);
  const shown = citation && citation.page === page ? citation : null;
  const src = shown
    ? sourceLines(pages, shown)
    : sourceLines(pages, { page, first_line: 0, last_line: -1, quote: "" });

  useEffect(() => {
    citedRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [shown?.page, shown?.first_line, shown?.last_line]);

  let firstCited = true;
  return (
    <section aria-label="Source page" className="border border-slate-200 rounded-md bg-slate-50">
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-slate-200">
        <h3 className="text-sm font-semibold text-slate-700">
          Source{shown ? `: ${formatCitation(shown)}` : `: page ${page}`}
        </h3>
        {pages.length > 1 && (
          <div className="flex gap-1" role="group" aria-label="Pages">
            {pages.map((p) => (
              <button
                key={p.page}
                type="button"
                onClick={() => onPage(p.page)}
                aria-pressed={p.page === page}
                className={`px-2 py-0.5 rounded text-xs border ${
                  p.page === page ? "bg-slate-900 text-white border-slate-900" : "bg-white"
                }`}
              >
                {p.page}
              </button>
            ))}
          </div>
        )}
      </div>

      {shown && !src.found && (
        <p className="px-3 py-2 text-sm text-red-700">
          This value points at lines that aren&apos;t on the page. Check the original document.
        </p>
      )}
      {shown && src.found && !src.matches && (
        <p className="px-3 py-2 text-sm text-amber-800">
          The quoted text differs from these lines. Check the original document.
        </p>
      )}

      <ol className="max-h-[32rem] overflow-y-auto font-mono text-xs leading-5 py-2">
        {src.lines.map((line) => {
          const ref = line.cited && firstCited ? citedRef : undefined;
          if (line.cited) firstCited = false;
          return (
            <li
              key={line.n}
              ref={ref}
              className={`flex gap-3 px-3 ${line.cited ? "bg-yellow-100" : ""}`}
              aria-current={line.cited ? "true" : undefined}
            >
              <span className="w-8 shrink-0 text-right text-slate-400 select-none">{line.n}</span>
              <span className="whitespace-pre-wrap break-words">{line.text || " "}</span>
            </li>
          );
        })}
        {src.lines.length === 0 && <li className="px-3 text-slate-500">This page has no text.</li>}
      </ol>
      <p className="px-3 py-2 border-t border-slate-200 text-xs text-slate-500">
        The page as the pipeline read it. Compare with the original document before relying on a
        value.
      </p>
    </section>
  );
}

export default SourcePanel;
