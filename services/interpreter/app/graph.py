"""Condense the SysML v2 API JSON that OpenSysML exports into a diagram graph.

OpenSysML's ``-convert api-json`` writes every element of the model, including
the implied memberships, chains and parameter plumbing the language creates.
The frontend only needs the elements a modeler wrote and the relationships a
diagram draws, so this module keeps:

* nodes  - named, user-authored elements (packages, definitions, usages)
* edges  - typing, specialization, connections / interfaces / flows / bindings,
           successions, transitions, satisfy and verify relationships

Every node keeps its source location so the editor and the diagram can be
cross-highlighted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# API metatype -> short kind used by the frontend
NODE_KINDS: dict[str, str] = {
    "Package": "package",
    "LibraryPackage": "package",
    "PartDefinition": "part def",
    "PartUsage": "part",
    "PortDefinition": "port def",
    "PortUsage": "port",
    "AttributeDefinition": "attribute def",
    "AttributeUsage": "attribute",
    "EnumerationDefinition": "enum def",
    "EnumerationUsage": "enum",
    "ItemDefinition": "item def",
    "ItemUsage": "item",
    "OccurrenceDefinition": "occurrence def",
    "OccurrenceUsage": "occurrence",
    "ConnectionDefinition": "connection def",
    "InterfaceDefinition": "interface def",
    "FlowDefinition": "flow def",
    "AllocationDefinition": "allocation def",
    "RequirementDefinition": "requirement def",
    "RequirementUsage": "requirement",
    "ConcernDefinition": "concern def",
    "ConcernUsage": "concern",
    "ConstraintDefinition": "constraint def",
    "ConstraintUsage": "constraint",
    "CalculationDefinition": "calc def",
    "CalculationUsage": "calc",
    "ActionDefinition": "action def",
    "ActionUsage": "action",
    "PerformActionUsage": "perform",
    "SendActionUsage": "send",
    "AcceptActionUsage": "accept",
    "DecisionNode": "decide",
    "MergeNode": "merge",
    "ForkNode": "fork",
    "JoinNode": "join",
    "StateDefinition": "state def",
    "StateUsage": "state",
    "ExhibitStateUsage": "exhibit",
    "AnalysisCaseDefinition": "analysis def",
    "AnalysisCaseUsage": "analysis",
    "VerificationCaseDefinition": "verification def",
    "VerificationCaseUsage": "verification",
    "UseCaseDefinition": "use case def",
    "UseCaseUsage": "use case",
    "CaseDefinition": "case def",
    "CaseUsage": "case",
    "ViewDefinition": "view def",
    "ViewUsage": "view",
    "ViewpointDefinition": "viewpoint def",
    "MetadataDefinition": "metadata def",
    "ReferenceUsage": "ref",
}

# Connector-like usages drawn as edges between their two ends
CONNECTOR_KINDS: dict[str, str] = {
    "ConnectionUsage": "connection",
    "InterfaceUsage": "interface",
    "FlowConnectionUsage": "flow",
    "FlowUsage": "flow",
    "SuccessionFlowConnectionUsage": "flow",
    "SuccessionFlowUsage": "flow",
    "BindingConnectorAsUsage": "binding",
    "AllocationUsage": "allocation",
}

_TYPE_RE = re.compile(r"(?<![:>~]):(?![:>])\s*(~?\s*[A-Za-z_'][\w']*(?:::[\w']+)*)")
_DEFINED_BY_RE = re.compile(r"\bdefined\s+by\s+(~?[A-Za-z_'][\w']*(?:::[\w']+)*)")
_VALUE_RE = re.compile(r"(?<![<>=!:])(?::=|=)(?!=)\s*(.+?)\s*[;{]?\s*$")
_TRIGGER_RE = re.compile(r"\baccept\s+(.+?)(?=\s+(?:if|do|then)\b|;|$)")
_GUARD_RE = re.compile(r"\bif\s+(.+?)(?=\s+(?:do|then)\b|;|$)")
_TOOL_RE = re.compile(r"toolName\s*=\s*\"([^\"]+)\"")
_URI_RE = re.compile(r"uri\s*=\s*\"([^\"]+)\"")
_PKG_RE = re.compile(r"^\s*(?:(?:library|standard)\s+)*package\s+('[^']+'|[A-Za-z_]\w*)", re.M)
_MULT_RE = re.compile(r"\[\s*(\d+|\*)(?:\s*\.\.\s*(\d+|\*))?\s*\]")


@dataclass
class SourceFile:
    name: str
    text: str
    lines: list[str] = field(init=False)
    packages: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self.lines = self.text.splitlines()
        self.packages = {m.group(1).strip("'") for m in _PKG_RE.finditer(self.text)}

    def snippet(
        self,
        line: int | None,
        end_line: int | None,
        column: int | None = None,
        end_column: int | None = None,
        limit: int = 40,
    ) -> str | None:
        """Source text of [line:column, endLine:endColumn), 1-based."""
        if not line or line > len(self.lines):
            return None
        end_line = end_line or line
        truncated = end_line - line + 1 > limit
        end = min(end_line, line + limit - 1)
        chunk = self.lines[line - 1 : end]
        if not chunk:
            return None
        if end_column and not truncated:
            chunk[-1] = chunk[-1][: max(end_column - 1, 0)] if end_line == end else chunk[-1]
        if column:
            chunk[0] = chunk[0][column - 1 :]
        return "\n".join(chunk)


def _ref(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("@id")
    if isinstance(value, list) and value:
        return _ref(value[0])
    return None


def _refs(value: Any) -> list[str]:
    if isinstance(value, list):
        return [r for r in (_ref(v) for v in value) if r]
    r = _ref(value)
    return [r] if r else []


def _header(text: str | None) -> str:
    """The declaration part of an element's source text, before its body."""
    if not text:
        return ""
    t = text.strip()
    # cut at first body brace / semicolon that is not inside quotes or brackets
    depth = 0
    in_str = False
    for i, ch in enumerate(t):
        if ch == '"':
            in_str = not in_str
        elif in_str:
            continue
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and ch in "{;":
            return t[:i].strip()
    return t.split("\n", 1)[0].strip()


class GraphBuilder:
    def __init__(self, elements: list[dict[str, Any]], files: list[SourceFile]):
        self.elements = elements
        self.by_id: dict[str, dict[str, Any]] = {e["@id"]: e for e in elements if "@id" in e}
        self.files = files
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self._metadata_owned: set[str] = set()

    # ------------------------------------------------------------------ helpers
    def _file_for(self, el: dict[str, Any]) -> SourceFile | None:
        if len(self.files) == 1:
            return self.files[0]
        qn = el.get("qualifiedName") or ""
        root = qn.split("::", 1)[0].strip("'")
        if not root:
            # walk owners to find something with a qualified name
            cur = el
            for _ in range(50):
                owner = self.by_id.get(_ref(cur.get("owner")) or "")
                if not owner:
                    break
                if owner.get("qualifiedName"):
                    root = owner["qualifiedName"].split("::", 1)[0].strip("'")
                    break
                cur = owner
        for f in self.files:
            if root in f.packages:
                return f
        return self.files[0] if self.files else None

    def _loc(self, el: dict[str, Any]) -> dict[str, Any] | None:
        line = el.get("sysx:sourceLine")
        if not line:
            return None
        f = self._file_for(el)
        return {
            "file": f.name if f else None,
            "line": line,
            "column": el.get("sysx:sourceColumn") or 1,
            "endLine": el.get("sysx:sourceEndLine") or line,
            "endColumn": el.get("sysx:sourceEndColumn") or 1,
        }

    def _source_lines(self, el: dict[str, Any]) -> str | None:
        f = self._file_for(el)
        if not f:
            return None
        return f.snippet(
            el.get("sysx:sourceLine"),
            el.get("sysx:sourceEndLine"),
            el.get("sysx:sourceColumn"),
            el.get("sysx:sourceEndColumn"),
        )

    def _name(self, el: dict[str, Any]) -> str | None:
        return el.get("declaredName") or el.get("name")

    def _node_owner(self, el: dict[str, Any]) -> str | None:
        cur = _ref(el.get("owner"))
        for _ in range(100):
            if not cur:
                return None
            if cur in self.nodes:
                return cur
            parent = self.by_id.get(cur)
            if not parent:
                return None
            cur = _ref(parent.get("owner"))
        return None

    def _is_under_metadata(self, el: dict[str, Any]) -> bool:
        cur = el
        for _ in range(100):
            if cur.get("@type") in ("MetadataUsage", "MetadataFeature"):
                return True
            owner = self.by_id.get(_ref(cur.get("owner")) or "")
            if not owner:
                return False
            cur = owner
        return False

    def _resolve_end(self, end_id: str | None) -> list[str]:
        """Resolve a connector end to the feature path it references."""
        end = self.by_id.get(end_id or "")
        if not end:
            return []
        ref_sub = self.by_id.get(_ref(end.get("ownedReferenceSubsetting")) or "")
        target_id = _ref(ref_sub.get("referencedFeature")) if ref_sub else None
        if not target_id:
            # fall back to any subsetting
            for sid in _refs(end.get("ownedSubsetting")):
                s = self.by_id.get(sid)
                if s:
                    target_id = _ref(s.get("subsettedFeature"))
                    if target_id:
                        break
        target = self.by_id.get(target_id or "")
        if target and target.get("chainingFeature"):
            return _refs(target.get("chainingFeature"))
        return [target_id] if target_id else []

    # ------------------------------------------------------------------ build
    def build(self) -> dict[str, Any]:
        # 1. metadata (ToolExecution) -> annotate owners later
        tool_meta: dict[str, dict[str, str]] = {}
        for el in self.elements:
            if el.get("@type") == "MetadataUsage":
                src = self._source_lines(el) or el.get("sysx:sourceText") or ""
                tool = _TOOL_RE.search(src)
                if tool:
                    owner = _ref(el.get("owner"))
                    if owner:
                        uri = _URI_RE.search(src)
                        tool_meta[owner] = {"toolName": tool.group(1), "uri": uri.group(1) if uri else ""}

        # 2. nodes, in document order (elements are emitted owner-first)
        for el in self.elements:
            t = el.get("@type")
            if t not in NODE_KINDS:
                continue
            name = self._name(el)
            if not name or not el.get("sysx:sourceLine"):
                continue
            if t == "ReferenceUsage" and not el.get("direction") and not name:
                continue
            if self._is_under_metadata(el):
                continue
            self._add_node(el, tool_meta.get(el["@id"]))

        # 3. documentation -> owner node
        for el in self.elements:
            if el.get("@type") == "Documentation":
                owner = self._node_owner(el)
                if owner and el.get("body"):
                    prev = self.nodes[owner].get("doc")
                    body = el["body"].strip()
                    self.nodes[owner]["doc"] = f"{prev}\n{body}" if prev else body

        # 3b. requirement constraints and subjects
        for el in self.elements:
            rel = self.by_id.get(_ref(el.get("owningRelationship")) or "")
            if not rel:
                continue
            rt = rel.get("@type")
            if rt == "RequirementConstraintMembership" and el.get("@type") in ("ConstraintUsage", "AssertConstraintUsage"):
                owner = self._node_owner(el)
                text = " ".join((self._source_lines(el) or "").split())
                if owner and text:
                    kind = "assume" if rel.get("kind") == "assumption" else "require"
                    self.nodes[owner].setdefault("constraints", []).append({"kind": kind, "text": text})
            elif rt == "SubjectMembership" and el["@id"] in self.nodes:
                self.nodes[el["@id"]]["kind"] = "subject"

        # 4. relationships
        for el in self.elements:
            t = el.get("@type")
            if t in ("FeatureTyping", "ConjugatedPortTyping"):
                self._typing_edge(el)
            elif t == "Subclassification":
                self._edge("specializes", _ref(el.get("specific")), _ref(el.get("general")), el)
            elif t in CONNECTOR_KINDS and el.get("@id") not in self.nodes:
                self._connector_edge(el, CONNECTOR_KINDS[t])
            elif t == "SuccessionAsUsage":
                self._succession_edge(el)
            elif t == "TransitionUsage":
                self._transition_edge(el)
            elif t == "SatisfyRequirementUsage":
                self._satisfy_edge(el, "satisfy")
            elif t == "VerifyRequirementUsage" or (
                t == "RequirementUsage"
                and (
                    el.get("sysx:endForm") == "verify"
                    # `verify requirement r : ReqDef;` in a verification objective
                    or (self.by_id.get(_ref(el.get("owningRelationship")) or "") or {}).get("@type") == "RequirementVerificationMembership"
                )
            ):
                self._satisfy_edge(el, "verify")

        # Redefinition / subsetting is reported on the node, not drawn
        for el in self.elements:
            if el.get("@type") == "Redefinition":
                rid = _ref(el.get("redefiningFeature"))
                if rid in self.nodes:
                    self.nodes[rid]["redefines"] = _ref(el.get("redefinedFeature"))
            elif el.get("@type") == "Subsetting" and not el.get("isImplied"):
                sid = _ref(el.get("subsettingFeature"))
                if sid in self.nodes:
                    self.nodes[sid]["subsets"] = _ref(el.get("subsettedFeature"))

        # Metadata applied to an element (`#Component part def ...`) -> tag names
        for el in self.elements:
            if el.get("@type") != "MetadataUsage":
                continue
            owner = _ref(el.get("owner"))
            md = self.by_id.get(_ref(el.get("type")) or "")
            name = self._name(md) if md else None
            if owner in self.nodes and name:
                self.nodes[owner].setdefault("metadata", []).append(name)

        nodes = list(self.nodes.values())
        return {"nodes": nodes, "edges": self.edges}

    def _add_node(self, el: dict[str, Any], tool: dict[str, str] | None) -> None:
        t = el["@type"]
        header = _header(self._source_lines(el)) or _header(el.get("sysx:sourceText"))
        type_name = None
        is_def = t.endswith("Definition") or t in ("Package", "LibraryPackage")
        m = None if is_def else (_DEFINED_BY_RE.search(header) or _TYPE_RE.search(header))
        if m:
            type_name = re.sub(r"\s+", "", m.group(1))
        value = None
        vm = _VALUE_RE.search(header)
        if vm and t in ("AttributeUsage", "ReferenceUsage", "ItemUsage", "PartUsage", "EnumerationUsage"):
            value = vm.group(1).rstrip(";").strip()
        mult = None
        mm = _MULT_RE.search(header.split("=", 1)[0])
        if mm:
            mult = mm.group(0).replace(" ", "")
        kind = NODE_KINDS[t]
        if t == "ReferenceUsage" and el.get("direction"):
            kind = "param"
        if t == "ActionUsage" and el.get("sysx:subactionKind") in ("entry", "do", "exit"):
            kind = el["sysx:subactionKind"]
        node: dict[str, Any] = {
            "id": el["@id"],
            "kind": kind,
            "metatype": t,
            "name": self._name(el),
            "shortName": el.get("declaredShortName"),
            "qualifiedName": el.get("qualifiedName"),
            "owner": self._node_owner(el),
            "loc": self._loc(el),
            "typeName": type_name,
            "typeId": None,
            "conjugated": bool(type_name and type_name.startswith("~")),
            "direction": el.get("direction"),
            "value": value,
            "multiplicity": mult,
            "isAbstract": bool(el.get("isAbstract")),
            "header": header[:300],
            "redefines": _ref(el.get("redefines")),
        }
        if tool:
            node["tool"] = tool
        self.nodes[el["@id"]] = node

    def _edge(
        self,
        kind: str,
        source: str | None,
        target: str | None,
        el: dict[str, Any],
        **extra: Any,
    ) -> None:
        if not source or not target:
            return
        self.edges.append(
            {
                "id": f"{el.get('@id')}#{kind}#{len(self.edges)}",
                "kind": kind,
                "source": source,
                "target": target,
                **extra,
            }
        )

    def _typing_edge(self, el: dict[str, Any]) -> None:
        typed = _ref(el.get("typedFeature"))
        typ = _ref(el.get("conjugatedPortDefinition")) or _ref(el.get("type"))
        if typed not in self.nodes:
            return
        type_el = self.by_id.get(typ or "")
        if type_el and type_el.get("@type") == "ConjugatedPortDefinition":
            # the conjugated port def is implied; point at the original port def
            orig = _ref(type_el.get("originalPortDefinition")) or _ref(type_el.get("owner"))
            typ = orig or typ
            self.nodes[typed]["conjugated"] = True
        if typ in self.nodes:
            self.nodes[typed]["typeId"] = typ
            if not self.nodes[typed].get("typeName"):
                self.nodes[typed]["typeName"] = self.nodes[typ]["name"]
            self._edge("typing", typed, typ, el)

    def _context(self, el: dict[str, Any]) -> str | None:
        return self._node_owner(el)

    def _connector_edge(self, el: dict[str, Any], kind: str) -> None:
        ends = _refs(el.get("connectorEnd"))
        if len(ends) < 2:
            # flows declare source/target output features instead
            src = _ref(el.get("sourceOutputFeature"))
            tgt = _ref(el.get("targetInputFeature"))
            if not (src and tgt):
                return
            paths = [self._resolve_end(src), self._resolve_end(tgt)]
        else:
            paths = [self._resolve_end(ends[0]), self._resolve_end(ends[1])]
        if not paths[0] or not paths[1]:
            return
        name = self._name(el)
        header = _header(self._source_lines(el)) or _header(el.get("sysx:sourceText"))
        typ = None
        tm = _TYPE_RE.search(header.split(" connect ", 1)[0]) if header else None
        if tm:
            typ = tm.group(1)
        self._edge(
            kind,
            paths[0][-1],
            paths[1][-1],
            el,
            sourcePath=paths[0],
            targetPath=paths[1],
            context=self._context(el),
            label=name or typ,
            loc=self._loc(el),
        )

    def _succession_edge(self, el: dict[str, Any]) -> None:
        owner = _ref(el.get("owner"))
        owner_el = self.by_id.get(owner or "")
        if owner_el and owner_el.get("@type") == "TransitionUsage":
            return  # the transition draws itself
        ctx = self._context(el)
        if not ctx:
            return
        src = _ref(el.get("sourceFeature"))
        tgts = _refs(el.get("targetFeature"))
        if not src and not tgts:
            ends = _refs(el.get("connectorEnd"))
            if len(ends) == 2:
                s, t = self._resolve_end(ends[0]), self._resolve_end(ends[1])
                src, tgts = (s[-1] if s else None), ([t[-1]] if t else [])
        if src not in self.nodes:
            src = f"{ctx}::__start"
        for tgt in tgts:
            if tgt not in self.nodes:
                tgt = f"{ctx}::__done"
            self._edge("succession", src, tgt, el, context=ctx, loc=self._loc(el))

    def _transition_edge(self, el: dict[str, Any]) -> None:
        src = _ref(el.get("source"))
        tgt = _ref(el.get("target"))
        ctx = self._context(el)
        if src not in self.nodes or tgt not in self.nodes:
            return
        text = " ".join((self._source_lines(el) or "").split())
        label_parts = []
        tm = _TRIGGER_RE.search(text)
        if tm:
            label_parts.append(tm.group(1))
        gm = _GUARD_RE.search(text)
        if gm:
            label_parts.append(f"[{gm.group(1)}]")
        self._edge(
            "transition",
            src,
            tgt,
            el,
            context=ctx,
            label=" ".join(label_parts) or self._name(el),
            loc=self._loc(el),
        )

    def _satisfy_edge(self, el: dict[str, Any], kind: str) -> None:
        req = None
        rs = self.by_id.get(_ref(el.get("ownedReferenceSubsetting")) or "")
        if rs:
            req = _ref(rs.get("referencedFeature"))
        req = req or _ref(el.get("subsets")) or _ref(el.get("satisfiedRequirement"))
        if kind == "verify" and req not in self.nodes:
            # a verified requirement declared by its definition: the edge goes to the def
            req = next((r for r in _refs(el.get("type")) if r in self.nodes), req)
        if req not in self.nodes:
            # a nested requirement written as a chain, e.g. `satisfy parent.child by x`
            chain = self.by_id.get(req or "")
            if chain and chain.get("chainingFeature"):
                req = _refs(chain["chainingFeature"])[-1]
        by = _ref(el.get("subject")) or _ref(el.get("satisfyingFeature"))
        if kind == "verify":
            by = self._context(el)
        if req not in self.nodes:
            return
        if by not in self.nodes:
            # satisfying feature may be a chain such as `vehicle.engine`
            subj = self.by_id.get(by or "")
            if subj and subj.get("chainingFeature"):
                by = _refs(subj["chainingFeature"])[-1]
        if by not in self.nodes:
            return
        self._edge(kind, by, req, el, loc=self._loc(el))


def build_graph(elements: list[dict[str, Any]], files: list[SourceFile]) -> dict[str, Any]:
    return GraphBuilder(elements, files).build()


def summarize(graph: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for n in graph["nodes"]:
        counts[n["kind"]] = counts.get(n["kind"], 0) + 1
    return counts
