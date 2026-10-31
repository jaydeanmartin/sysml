import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from "react";
import type { Diagnostic, Loc } from "../types";
import { highlight } from "./highlight";

export const LINE_HEIGHT = 20;
const PAD = 10;

export interface EditorHandle {
  insert(text: string, where?: "cursor" | "packageEnd"): void;
  reveal(loc: Loc): void;
  focus(): void;
}

interface Props {
  value: string;
  onChange(v: string): void;
  diagnostics: Diagnostic[];
  selected: Loc | null;
  activeLines?: Loc[];
  onCursor(line: number, column: number): void;
  onSave(): void;
  readOnly?: boolean;
}

export const Editor = forwardRef<EditorHandle, Props>(function Editor(
  { value, onChange, diagnostics, selected, activeLines = [], onCursor, onSave, readOnly },
  ref
) {
  const ta = useRef<HTMLTextAreaElement>(null);
  const pre = useRef<HTMLPreElement>(null);
  const gutter = useRef<HTMLDivElement>(null);
  const layer = useRef<HTMLDivElement>(null);

  const html = useMemo(() => highlight(value), [value]);
  const lineCount = useMemo(() => value.split("\n").length, [value]);
  const byLine = useMemo(() => {
    const m = new Map<number, Diagnostic[]>();
    for (const d of diagnostics) {
      const l = Math.max(1, Math.min(d.line || 1, lineCount));
      m.set(l, [...(m.get(l) || []), d]);
    }
    return m;
  }, [diagnostics, lineCount]);

  const syncScroll = () => {
    const t = ta.current;
    if (!t) return;
    if (pre.current) {
      pre.current.scrollTop = t.scrollTop;
      pre.current.scrollLeft = t.scrollLeft;
    }
    if (layer.current) {
      layer.current.style.transform = `translate(${-t.scrollLeft}px, ${-t.scrollTop}px)`;
    }
    if (gutter.current) gutter.current.scrollTop = t.scrollTop;
  };

  const offsetOf = (line: number, col: number) => {
    const lines = value.split("\n");
    let off = 0;
    for (let i = 0; i < Math.min(line - 1, lines.length); i++) off += lines[i].length + 1;
    return off + Math.max(0, col - 1);
  };

  useImperativeHandle(ref, () => ({
    insert(text, where = "cursor") {
      const t = ta.current;
      if (!t) return;
      let pos = t.selectionStart;
      if (where === "packageEnd") {
        // before the closing brace of the first top-level package
        const idx = value.lastIndexOf("}");
        pos = idx >= 0 ? idx : value.length;
        text = (value[pos - 1] === "\n" ? "" : "\n") + text;
      }
      const next = value.slice(0, pos) + text + value.slice(pos);
      onChange(next);
      requestAnimationFrame(() => {
        t.focus();
        t.selectionStart = t.selectionEnd = pos + text.length;
        const line = next.slice(0, pos).split("\n").length;
        t.scrollTop = Math.max(0, (line - 4) * LINE_HEIGHT);
        syncScroll();
      });
    },
    reveal(loc) {
      const t = ta.current;
      if (!t) return;
      t.focus({ preventScroll: true });
      t.selectionStart = offsetOf(loc.line, loc.column);
      t.selectionEnd = offsetOf(loc.endLine, loc.endColumn);
      const top = (loc.line - 1) * LINE_HEIGHT;
      if (top < t.scrollTop + LINE_HEIGHT || top > t.scrollTop + t.clientHeight - 3 * LINE_HEIGHT) {
        t.scrollTop = Math.max(0, top - t.clientHeight / 3);
      }
      syncScroll();
    },
    focus() {
      ta.current?.focus();
    },
  }));

  useEffect(syncScroll, [html]);

  const reportCursor = () => {
    const t = ta.current;
    if (!t) return;
    const before = value.slice(0, t.selectionStart);
    const line = before.split("\n").length;
    const col = t.selectionStart - before.lastIndexOf("\n");
    onCursor(line, col);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const t = e.currentTarget;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      onSave();
      return;
    }
    if (e.key === "Tab") {
      e.preventDefault();
      const s = t.selectionStart;
      const next = value.slice(0, s) + "    " + value.slice(t.selectionEnd);
      onChange(next);
      requestAnimationFrame(() => (t.selectionStart = t.selectionEnd = s + 4));
    } else if (e.key === "Enter") {
      // keep indentation, add one level after '{'
      e.preventDefault();
      const s = t.selectionStart;
      const lineStart = value.lastIndexOf("\n", s - 1) + 1;
      const indent = /^\s*/.exec(value.slice(lineStart, s))![0];
      const extra = value[s - 1] === "{" ? "    " : "";
      const ins = "\n" + indent + extra;
      const next = value.slice(0, s) + ins + value.slice(t.selectionEnd);
      onChange(next);
      requestAnimationFrame(() => (t.selectionStart = t.selectionEnd = s + ins.length));
    }
  };

  const band = (loc: Loc, cls: string, key: string) => (
    <div
      key={key}
      className={cls}
      style={{ top: PAD + (loc.line - 1) * LINE_HEIGHT, height: (loc.endLine - loc.line + 1) * LINE_HEIGHT }}
    />
  );

  return (
    <div className="editor" style={{ ["--lh" as string]: `${LINE_HEIGHT}px` }}>
      <div className="gutter" ref={gutter}>
        <div style={{ paddingTop: PAD, paddingBottom: 200 }}>
          {Array.from({ length: lineCount }, (_, i) => {
            const ds = byLine.get(i + 1);
            const sev = ds?.some((d) => d.severity === "error") ? "error" : ds ? "warning" : "";
            return (
              <div key={i} className={`gl ${sev}`} title={ds?.map((d) => d.message).join("\n")}>
                {i + 1}
              </div>
            );
          })}
        </div>
      </div>
      <div className="code">
        <div className="layer-clip">
          <div className="layer" ref={layer}>
            {selected && band(selected, "band sel", "sel")}
            {activeLines.map((l, i) => band(l, "band active", `a${i}`))}
            {[...byLine.entries()].map(([line, ds]) => (
              <div
                key={`d${line}`}
                className={`diag ${ds.some((d) => d.severity === "error") ? "error" : "warning"}`}
                style={{ top: PAD + (line - 1) * LINE_HEIGHT, left: `calc(${PAD}px + ${Math.max(1, ds[0].column) - 1}ch)` }}
                title={ds.map((d) => d.message).join("\n")}
              />
            ))}
          </div>
        </div>
        <pre className="hl" ref={pre} aria-hidden dangerouslySetInnerHTML={{ __html: html }} />
        <textarea
          ref={ta}
          value={value}
          spellCheck={false}
          readOnly={readOnly}
          wrap="off"
          onChange={(e) => onChange(e.target.value)}
          onScroll={syncScroll}
          onKeyDown={onKeyDown}
          onKeyUp={reportCursor}
          onClick={reportCursor}
          aria-label="SysML v2 source"
        />
      </div>
    </div>
  );
});
