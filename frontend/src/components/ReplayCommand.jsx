// src/components/ReplayCommand.jsx
//
// A step's replay command, to copy and run from scripts/src. The app never
// runs it. See docs/adr/0006-durable-traceable-runs.md.
import React, { useState } from "react";
import { notReplayableReason } from "../record/trace.js";

function ReplayCommand({ step }) {
  const [copied, setCopied] = useState(false);

  if (!step.replay_command) {
    return (
      <span className="text-slate-400" title={notReplayableReason(step)}>
        not replayable
      </span>
    );
  }

  function copy() {
    navigator.clipboard
      ?.writeText(step.replay_command)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => setCopied(false));
  }

  return (
    <span className="inline-flex items-center gap-1">
      <code className="font-mono bg-slate-100 px-1 rounded select-all break-all">{step.replay_command}</code>
      <button type="button" onClick={copy} className="text-blue-600 hover:underline whitespace-nowrap">
        {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}

export default ReplayCommand;
