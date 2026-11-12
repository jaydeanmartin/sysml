import type { Analysis } from "./types";

export const SNIPPETS: { label: string; title: string; text: string }[] = [
  {
    label: "+ Part def",
    title: "Part definition with attributes and a port",
    text: `    part def NewPart {
        attribute mass : MassValue;
        port power : PowerPort;
    }
`,
  },
  {
    label: "+ Port def",
    title: "Port definition",
    text: `    port def PowerPort {
        out attribute voltage : Real;
    }
`,
  },
  {
    label: "+ Connection",
    title: "Assembly with parts connected through ports",
    text: `    part def Assembly {
        part source : NewPart;
        part sink : NewPart;
        connect source.power to sink.power;
    }
`,
  },
  {
    label: "+ Instance",
    title: "Part usage (instance) with redefined values",
    text: `    part unit1 : NewPart {
        attribute :>> mass = 12.5 [SI::kg];
    }
`,
  },
  {
    label: "+ Action",
    title: "Action definition with a sequence of steps",
    text: `    action def Operate {
        attribute count : Real = 0;
        first start;
        then action prepare;
        then action execute { assign count := count + 1; }
        then action shutdown;
        then done;
    }
`,
  },
  {
    label: "+ States",
    title: "State machine with timed transitions",
    text: `    state def Modes {
        entry; then standby;
        state standby;
        state active;
        transition first standby accept after 5 [SI::s] then active;
        transition first active accept after 10 [SI::s] then standby;
    }
`,
  },
  {
    label: "+ Calc",
    title: "Calculation evaluated by the interpreter",
    text: `    calc def Power {
        in voltage : Real;
        in current : Real;
        return power : Real = voltage * current;
    }
`,
  },
];

export function requirementSnippet(r: {
  name: string;
  id?: string;
  text: string;
  subjectName: string;
  subjectType: string;
  constraint: string;
  /** a library requirement: abstract, with attributes (thresholds) left for usages and instances to set */
  abstract?: boolean;
  /** comma-separated attribute names, e.g. "actual, minimum" */
  thresholds?: string;
}): string {
  const id = r.id ? ` <'${r.id}'>` : "";
  const subject = r.subjectType ? `\n        subject ${r.subjectName || "subj"} : ${r.subjectType};` : "";
  const attrs = (r.thresholds || "")
    .split(",")
    .map((t) => t.trim())
    .filter((t) => /^[A-Za-z_]\w*$/.test(t))
    .map((t) => `\n        attribute ${t} : Real;`)
    .join("");
  const constraint = r.constraint ? `\n        require constraint { ${r.constraint} }` : "";
  return `    ${r.abstract ? "abstract " : ""}requirement def${id} ${r.name} {
        doc /* ${r.text.replace(/\*\//g, "* /")} */${subject}${attrs}${constraint}
    }
`;
}

const unitNote = (unit?: string | null) => (unit && quantityType(unit) === "Real" ? `  // ${unit}` : "");
const sysmlName = (s: string) => (/^[A-Za-z_]\w*$/.test(s) ? s : `'${s}'`);

function quantityType(unit?: string | null): string {
  switch ((unit || "").replace("SI::", "")) {
    case "kg":
    case "g":
      return "MassValue";
    case "m":
    case "km":
      return "LengthValue";
    case "s":
      return "DurationValue";
    case "K":
      return "TemperatureValue";
    case "W":
    case "kW":
      return "PowerValue";
    case "N":
      return "ForceValue";
    case "m/s":
      return "SpeedValue";
    case "m**2":
      return "AreaValue";
    default:
      return "Real";
  }
}

/** A calc def bound to a registered analysis through ToolExecution metadata. */
export function analysisBindingSnippet(a: Analysis): string {
  const cap = a.name.charAt(0).toUpperCase() + a.name.slice(1);
  const inputs = a.inputs.map(
    (v) => `        in ${sysmlName(v.name)} : ${quantityType(v.unit)}[1] { @ToolVariable { name = "${v.name}"; } }${unitNote(v.unit)}`
  );
  const [ret, ...outs] = a.outputs;
  const outputs = outs.map(
    (v) => `        out ${sysmlName(v.name)} : ${quantityType(v.unit)}[1] { @ToolVariable { name = "${v.name}"; } }${unitNote(v.unit)}`
  );
  const kind = a.outputs.length > 1 ? "action def" : "calc def";
  const retLine =
    a.outputs.length > 1
      ? `        out ${sysmlName(ret.name)} : ${quantityType(ret.unit)}[1] { @ToolVariable { name = "${ret.name}"; } }`
      : `        return ${sysmlName(ret.name)} : ${quantityType(ret.unit)}[1] { @ToolVariable { name = "${ret.name}"; } }`;
  return `    // ${a.description || a.name} — performed by the analysis service over REST
    // (needs 'private import AnalysisTooling::*;' in the package)
    ${kind} ${cap} {
        metadata ToolExecution { toolName = "${a.name}"; uri = "analysis://${a.name}"; }
${[...inputs, ...outputs, retLine].join("\n")}
    }
`;
}
