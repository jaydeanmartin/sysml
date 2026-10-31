// Lightweight SysML v2 syntax highlighter (keywords, comments, strings, units...).

const KEYWORDS = new Set(
  (
    "about abstract accept action actor after alias all allocate allocation analysis and as assert assign assume at " +
    "attribute bind binding by calc case comment concern connect connection constant constraint crosses decide def " +
    "default defined dependency derived do doc else end entry enum event exhibit exit expose false filter first flow " +
    "for fork frame from hastype if implies import in include individual inout interface istype item join language " +
    "library locale loop merge message meta metadata nonunique not null objective occurrence of or ordered out " +
    "package parallel part perform port portion private protected public redefines ref references render rendering " +
    "rep require requirement return satisfy send snapshot specializes stakeholder standard state subject subsets " +
    "succession terminate then timeslice to transition true until use variant variation verification verify via " +
    "view viewpoint when while xor"
  ).split(" ")
);

const TOKEN =
  /(\/\/[^\n]*)|(\/\*[\s\S]*?(?:\*\/|$))|("(?:[^"\\\n]|\\.)*"?)|('(?:[^'\\\n]|\\.)*'?)|(\[[^\]\n]*::[^\]\n]*\]|\[\s*SI[^\]\n]*\])|(\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(\b[A-Za-z_]\w*\b)|(:>>|:>|::>|~|::|=>|->)/g;

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Return highlighted HTML for the whole text (no line wrapping). */
export function highlight(text: string): string {
  let out = "";
  let last = 0;
  let prevWord = ""; // last identifier/keyword seen, to colour `doc` comments and declared names
  TOKEN.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TOKEN.exec(text))) {
    out += esc(text.slice(last, m.index));
    const tok = m[0];
    let cls = "";
    if (m[1]) cls = "c";
    else if (m[2]) cls = prevWord === "doc" || tok.startsWith("/**") ? "d" : "c";
    else if (m[3]) cls = "s";
    else if (m[4]) cls = "n";
    else if (m[5]) cls = "u";
    else if (m[6]) cls = "num";
    else if (m[7]) {
      if (KEYWORDS.has(tok)) cls = "k";
      else if (prevWord === "def" || prevWord === "package") cls = "t";
      else if (/^[A-Z]/.test(tok)) cls = "ty";
      prevWord = tok;
    } else if (m[8]) cls = "op";
    if (m[2] || m[3]) prevWord = "";
    out += cls ? `<span class="hl-${cls}">${esc(tok)}</span>` : esc(tok);
    last = m.index + tok.length;
  }
  out += esc(text.slice(last));
  // a trailing newline keeps the overlay as tall as the textarea
  return out + "\n";
}
