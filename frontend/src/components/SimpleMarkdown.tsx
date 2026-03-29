import React from "react";

/**
 * Lightweight markdown renderer — no external deps.
 * Handles: headers, bold, italic, inline code, code blocks, lists, and paragraphs.
 */

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderInline(text: string): React.ReactNode[] {
  // Process inline patterns: bold, italic, inline code
  const parts: React.ReactNode[] = [];
  // Regex: code(`), bold(**), bold(__), italic(*), italic(_)
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*]+\*)|(_[^_]+_)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = re.exec(text)) !== null) {
    if (match.index > last) {
      parts.push(text.slice(last, match.index));
    }
    const m = match[0];
    if (m.startsWith("`")) {
      parts.push(<code key={key++} className="md-inline-code">{m.slice(1, -1)}</code>);
    } else if (m.startsWith("**") || m.startsWith("__")) {
      parts.push(<strong key={key++}>{m.slice(2, -2)}</strong>);
    } else if (m.startsWith("*") || m.startsWith("_")) {
      parts.push(<em key={key++}>{m.slice(1, -1)}</em>);
    }
    last = match.index + m.length;
  }
  if (last < text.length) {
    parts.push(text.slice(last));
  }
  return parts.length ? parts : [text];
}

export default function SimpleMarkdown({ content }: { content: string }) {
  const lines = content.split("\n");
  const elements: React.ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    if (line.trimStart().startsWith("```")) {
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      elements.push(
        <pre key={key++} className="md-code-block">
          <code>{escapeHtml(codeLines.join("\n"))}</code>
        </pre>
      );
      continue;
    }

    // Headers
    const headerMatch = line.match(/^(#{1,4})\s+(.*)/);
    if (headerMatch) {
      const level = headerMatch[1].length as 1 | 2 | 3 | 4;
      const Tag = `h${level}` as keyof JSX.IntrinsicElements;
      elements.push(<Tag key={key++} className="md-header">{renderInline(headerMatch[2])}</Tag>);
      i++;
      continue;
    }

    // Unordered list items
    if (line.match(/^\s*[-*]\s+/)) {
      const items: React.ReactNode[] = [];
      while (i < lines.length && lines[i].match(/^\s*[-*]\s+/)) {
        const text = lines[i].replace(/^\s*[-*]\s+/, "");
        items.push(<li key={key++}>{renderInline(text)}</li>);
        i++;
      }
      elements.push(<ul key={key++} className="md-list">{items}</ul>);
      continue;
    }

    // Ordered list items
    if (line.match(/^\s*\d+\.\s+/)) {
      const items: React.ReactNode[] = [];
      while (i < lines.length && lines[i].match(/^\s*\d+\.\s+/)) {
        const text = lines[i].replace(/^\s*\d+\.\s+/, "");
        items.push(<li key={key++}>{renderInline(text)}</li>);
        i++;
      }
      elements.push(<ol key={key++} className="md-list">{items}</ol>);
      continue;
    }

    // Blank line
    if (!line.trim()) {
      i++;
      continue;
    }

    // Regular paragraph
    elements.push(<p key={key++} className="md-para">{renderInline(line)}</p>);
    i++;
  }

  return <div className="md-root">{elements}</div>;
}
