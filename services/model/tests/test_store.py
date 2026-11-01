"""Mapping of models onto the SysML v2 API (no server needed)."""

import uuid

from app import store

PROJECT = "c72cdf16-d683-4003-b05d-912f4c3695c7"
MODEL = "5b0a3c8e-2d43-4c2e-9a53-0d0f3a6b7e11"
LIB = "14c0aa22-5489-59b5-b438-ded26e83ba31"  # a standard library element (already a UUID)

# what OpenSysML's api-json gives for "package P { part a : ScalarValues::Real; }" (abridged)
ELEMENTS = [
    {"@type": "Namespace", "@id": "P_ns", "owningRelationship": None, "ownedRelationship": [{"@id": "P_om"}],
     "ownedMember": [{"@id": "P"}], "sysx:statedDefaults": True},
    {"@type": "OwningMembership", "@id": "P_om", "owningRelatedElement": {"@id": "P_ns"}, "ownedRelatedElement": [{"@id": "P"}]},
    {"@type": "Package", "@id": "P", "declaredName": "P", "owningRelationship": {"@id": "P_om"},
     "ownedRelationship": [{"@id": "P__a_om"}], "sysx:sourceLine": 1},
    {"@type": "OwningMembership", "@id": "P__a_om", "owningRelatedElement": {"@id": "P"}, "ownedRelatedElement": [{"@id": "P__a"}]},
    {"@type": "PartUsage", "@id": "P__a", "declaredName": "a", "owningRelationship": {"@id": "P__a_om"}, "ownedRelationship": [{"@id": "P__a_t"}]},
    {"@type": "FeatureTyping", "@id": "P__a_t", "owningRelatedElement": {"@id": "P__a"}, "type": {"@id": LIB}},
    # another file of the same parse
    {"@type": "Namespace", "@id": "Q_ns", "owningRelationship": None, "ownedRelationship": [{"@id": "Q_om"}], "ownedMember": [{"@id": "Q"}]},
    {"@type": "OwningMembership", "@id": "Q_om", "owningRelatedElement": {"@id": "Q_ns"}, "ownedRelatedElement": [{"@id": "Q"}]},
    {"@type": "Package", "@id": "Q", "declaredName": "Q", "owningRelationship": {"@id": "Q_om"}},
]


def test_own_elements_follow_ownership_from_the_files_root_namespace():
    own = store.own_elements(ELEMENTS, {"P"})
    assert [e["@id"] for e in own] == ["P_ns", "P_om", "P", "P__a_om", "P__a", "P__a_t"]


def test_to_api_maps_ids_to_stable_uuids_and_adds_text_and_description():
    own = store.own_elements(ELEMENTS, {"P"})
    out = store.to_api(own, PROJECT, MODEL, "M", "about M", "package P { part a; }")
    assert out == store.to_api(own, PROJECT, MODEL, "M", "about M", "package P { part a; }")  # deterministic
    assert all(uuid.UUID(i) for i in out)
    ns = out[MODEL]
    assert ns["declaredName"] == "M" and ns["@type"] == "Namespace"
    text = out[store.text_id(MODEL)]
    assert text["@type"] == "TextualRepresentation" and text["language"] == "sysml" and text["body"] == "package P { part a; }"
    assert out[store.description_id(MODEL)]["body"] == "about M"
    assert {"@id": text["owningRelationship"]["@id"]} in ns["ownedRelationship"]
    part = next(p for p in out.values() if p.get("declaredName") == "a")
    typing = out[part["ownedRelationship"][0]["@id"]]
    assert typing["type"] is None  # the API server holds no standard library elements to refer to
    assert not any(k.startswith("sysx:") for p in out.values() for k in p)
    # the same element gets another id in another project
    assert set(store.to_api(own, str(uuid.uuid4()), MODEL, "M", "", "")) - set(out)


def test_to_api_without_elements_still_stores_the_text():
    out = store.to_api([], PROJECT, MODEL, "M", "", "package P { oops")
    assert out[MODEL]["@type"] == "Namespace"
    assert out[store.text_id(MODEL)]["body"] == "package P { oops"


def test_to_api_creates_a_missing_root_namespace():
    # versions stored by earlier releases may lack the file's root Namespace
    out = store.to_api(store.own_elements(ELEMENTS, {"P"})[1:], PROJECT, MODEL, "M", "", "")
    ns = out[MODEL]
    member = out[ns["ownedRelationship"][0]["@id"]]
    assert out[member["ownedRelatedElement"][0]["@id"]]["declaredName"] == "P"


def test_prune_drops_references_the_project_cannot_resolve():
    payloads = {"a": {"@id": "a", "type": {"@id": "b"}, "member": [{"@id": "x"}, {"@id": "a"}]}}
    assert store.prune(payloads, {"b"}) == {"a": {"@id": "a", "type": {"@id": "b"}, "member": [{"@id": "a"}]}}
    assert store.prune(payloads, set())["a"]["type"] is None


def test_changes_carry_only_what_changed():
    own = store.own_elements(ELEMENTS, {"P"})
    v1 = store.to_api(own, PROJECT, MODEL, "M", "", "package P { part a; }")
    v2 = store.to_api(own, PROJECT, MODEL, "M", "", "package P {  part a; }")
    ch = store.changes(v1, v2)
    assert [c["identity"]["@id"] for c in ch] == [store.text_id(MODEL)]
    gone = store.changes(v1, {})
    assert len(gone) == len(v1) and all(c["payload"] is None for c in gone)


def test_commit_description_round_trips_trailers():
    d = store.describe("Add instance x\n\nwith details", {"Model-Id": MODEL, "Valid": "true", "Elements": 12, "Authored": None})
    c = store._parse_commit({"@id": "c1", "description": d, "created": "2026-01-01T00:00:00Z", "previousCommit": {"@id": "c0"}})
    assert c.message == "Add instance x\n\nwith details"
    assert c.trailers == {"Model-Id": MODEL, "Valid": "true", "Elements": "12"}
    assert c.model_id == MODEL and c.previous == "c0"
    plain = store._parse_commit({"@id": "c2", "description": "made elsewhere\n\nnot: trailers here"})
    assert plain.model_id is None and plain.message == "made elsewhere\n\nnot: trailers here"
