"""Mapping of models onto SysML v2 API elements and the query language (no database needed)."""

import uuid

import pytest

from app import repo

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
    own = repo.own_elements(ELEMENTS, {"P"})
    assert [e["@id"] for e in own] == ["P_ns", "P_om", "P", "P__a_om", "P__a", "P__a_t"]


def test_to_api_maps_ids_to_stable_uuids_and_adds_text_and_description():
    own = repo.own_elements(ELEMENTS, {"P"})
    out = repo.to_api(own, PROJECT, MODEL, "M", "about M", "package P { part a; }")
    assert out == repo.to_api(own, PROJECT, MODEL, "M", "about M", "package P { part a; }")  # deterministic
    assert all(uuid.UUID(i) for i in out)
    ns = out[MODEL]
    assert ns["declaredName"] == "M" and ns["@type"] == "Namespace"
    text = out[repo.text_id(MODEL)]
    assert text["@type"] == "TextualRepresentation" and text["language"] == "sysml" and text["body"] == "package P { part a; }"
    assert out[repo.description_id(MODEL)]["body"] == "about M"
    assert {"@id": text["owningRelationship"]["@id"]} in ns["ownedRelationship"]
    part = next(p for p in out.values() if p.get("declaredName") == "a")
    typing = out[part["ownedRelationship"][0]["@id"]]
    assert typing["type"] == {"@id": LIB}  # library references keep their ids
    assert not any(k.startswith("sysx:") for p in out.values() for k in p)
    # the same element gets another id in another project
    assert set(repo.to_api(own, str(uuid.uuid4()), MODEL, "M", "", "")) - set(out)


def test_to_api_without_elements_still_stores_the_text():
    out = repo.to_api([], PROJECT, MODEL, "M", "", "package P { oops")
    assert out[MODEL]["@type"] == "Namespace"
    assert out[repo.text_id(MODEL)]["body"] == "package P { oops"


def test_to_api_creates_a_missing_root_namespace():
    # versions stored by earlier releases may lack the file's root Namespace
    out = repo.to_api(repo.own_elements(ELEMENTS, {"P"})[1:], PROJECT, MODEL, "M", "", "")
    ns = out[MODEL]
    member = out[ns["ownedRelationship"][0]["@id"]]
    assert out[member["ownedRelatedElement"][0]["@id"]]["declaredName"] == "P"


def test_changes_carry_only_what_changed():
    own = repo.own_elements(ELEMENTS, {"P"})
    v1 = repo.to_api(own, PROJECT, MODEL, "M", "", "package P { part a; }")
    v2 = repo.to_api(own, PROJECT, MODEL, "M", "", "package P {  part a; }")
    ch = repo.changes(v1, v2)
    assert [i for i, _ in ch] == [repo.text_id(MODEL)]
    gone = repo.changes(v1, {})
    assert len(gone) == len(v1) and all(p is None for _, p in gone)


def test_where_sql_translates_constraints():
    sql, params = repo.where_sql({
        "@type": "CompositeConstraint", "operator": "and", "constraint": [
            {"@type": "PrimitiveConstraint", "property": "@type", "operator": "instanceOf", "value": ["Usage"]},
            {"@type": "PrimitiveConstraint", "property": "declaredName", "operator": "=", "value": ["a", "b"], "inverse": True},
        ],
    })
    assert " AND " in sql and "NOT coalesce" in sql
    assert "PartUsage" in params[1] and "Usage" in params[1] and "Package" not in params[1]
    assert '"a"' in params and '["b"]' in params


def test_instance_of_follows_specializations():
    assert {"PartUsage", "ConnectionUsage", "FlowUsage"} <= repo.subtypes("Usage")
    assert "Package" in repo.subtypes("Element") and "Package" not in repo.subtypes("Type")


@pytest.mark.parametrize("bad", [
    {"@type": "PrimitiveConstraint", "property": "x", "operator": "like", "value": ["a"]},
    {"@type": "PrimitiveConstraint", "operator": "=", "value": ["a"]},
    {"@type": "CompositeConstraint", "operator": "xor", "constraint": []},
    {"@type": "Nope"},
])
def test_invalid_constraints_are_400(bad):
    with pytest.raises(repo.RepoError) as exc:
        repo.where_sql(bad)
    assert exc.value.status == 400


def test_parse_changes_assigns_ids():
    pid = str(uuid.uuid4())
    out = repo.parse_changes([
        {"@type": "DataVersion", "payload": {"@type": "PartDefinition", "declaredName": "X"}},
        {"@type": "DataVersion", "identity": {"@id": pid}, "payload": None},
    ])
    new_id, payload = out[0]
    assert uuid.UUID(new_id) and payload["@id"] == payload["elementId"] == new_id
    assert out[1] == (pid, None)
    assert repo.parse_changes([{"@type": "DataVersion", "identity": {"@id": pid}}]) == [(pid, None)]  # null omitted
    for bad in ([{"payload": None}], [{}], [{"payload": {"declaredName": "no type"}}], [{"identity": {"@id": "x"}, "payload": {"@type": "A"}}], {}):
        with pytest.raises(repo.RepoError):
            repo.parse_changes(bad)
