import type { Catalog, CatalogChange, Located } from "../types";

export const NAME_RE = /^[A-Za-z_]\w*$/;

/** Shared by the Library and Configurator tabs. */
export interface CatalogPanelProps {
  projectId: string;
  catalog: Catalog | null;
  loading: boolean;
  error: string | null;
  /** the editor has unsaved edits (the catalog reflects saved versions) */
  dirty: boolean;
  /** save unsaved editor edits; false if that failed */
  ensureSaved(): Promise<boolean>;
  /** run a change of the model text (unsaved editor edits are saved first) */
  onChange(fn: () => Promise<CatalogChange>): Promise<CatalogChange | null>;
  /** open the element's model in the editor and select its text */
  onOpen(el: Located): void;
}

export const short = (qn: string | null | undefined) => (qn || "").split("::").pop() || "";

const UNIT_HINTS: Record<string, string> = {
  MassValue: "100 [SI::kg]",
  PowerValue: "1000 [SI::W]",
  LengthValue: "1.5 [SI::m]",
  DistanceValue: "1.5 [SI::m]",
  TimeValue: "10 [SI::s]",
  DurationValue: "10 [SI::s]",
  TemperatureValue: "293.15 [SI::K]",
  ThermodynamicTemperatureValue: "293.15 [SI::K]",
  AreaValue: "1.2 [SI::'m²']",
  VolumeValue: "0.05 [SI::'m³']",
  SpeedValue: "30 [SI::'m/s']",
  ForceValue: "500 [SI::N]",
  EnergyValue: "1000 [SI::J]",
  ElectricCurrentValue: "10 [SI::A]",
  Real: "0.5",
  Integer: "4",
  Natural: "4",
  Boolean: "true",
  String: '"text"',
};

/** Example SysML expression for an attribute type. */
export function placeholder(type: string | null): string {
  return (type && UNIT_HINTS[short(type)]) || "expression";
}

/** "a::b::c" written from inside package "a::x" -> "b::c" if visible by scoping (mirrors the server). */
export function ref(target: string, scope: string): string {
  const i = target.lastIndexOf("::");
  if (i < 0) return target;
  const parent = target.slice(0, i);
  return scope === parent || scope.startsWith(parent + "::") ? target.slice(i + 2) : target;
}

export function partText(head: string, lines: string[], doc: string): string {
  const inner = [...(doc.trim() ? [`doc /* ${doc.trim()} */`] : []), ...lines];
  return inner.length ? `${head} {\n${inner.map((l) => `    ${l}`).join("\n")}\n}` : `${head};`;
}

export function errorText(e: unknown): string {
  return (e as Error)?.message || String(e);
}
