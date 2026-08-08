"""Graph query tests against a real (temp-file sqlite) database -- both
primitives are thin SQL wrappers, so these are effectively integration
tests of the traversal/filter logic against actual rows."""

from __future__ import annotations

from semi_intel.domain.enums import EntityType, RelationType
from semi_intel.domain.models import Entity, Relationship
from semi_intel.graph.queries import find_by_relation, related_entities
from semi_intel.repository.repositories import EntityRepository


def _build_chain(db_session):
    # Nova Lake --manufactured_by--> Intel
    # Nova Lake --uses_node--> 18A-P
    # 18A-P --used_by(reverse of uses_node from RibbonFET? keep simple)--
    # Arrow Lake --successor_of--> Nova Lake  (so Nova Lake has an incoming edge too)
    repo = EntityRepository(db_session)
    nova_lake = repo.add(Entity(type=EntityType.PRODUCT, name="Nova Lake"))
    intel = repo.add(Entity(type=EntityType.COMPANY, name="Intel"))
    node = repo.add(Entity(type=EntityType.FOUNDRY_NODE, name="18A-P"))
    arrow_lake = repo.add(Entity(type=EntityType.PRODUCT, name="Arrow Lake"))
    unrelated = repo.add(Entity(type=EntityType.PRODUCT, name="Totally Unrelated Product"))
    db_session.commit()

    db_session.add(Relationship(from_entity_id=nova_lake.id, to_entity_id=intel.id, relation_type=RelationType.MANUFACTURED_BY))
    db_session.add(Relationship(from_entity_id=nova_lake.id, to_entity_id=node.id, relation_type=RelationType.USES_NODE))
    db_session.add(Relationship(from_entity_id=arrow_lake.id, to_entity_id=nova_lake.id, relation_type=RelationType.SUCCESSOR_OF))
    db_session.commit()

    return {
        "nova_lake": nova_lake,
        "intel": intel,
        "node": node,
        "arrow_lake": arrow_lake,
        "unrelated": unrelated,
    }


def test_related_entities_depth_one_includes_direct_neighbors_both_directions(db_session):
    entities = _build_chain(db_session)
    nodes = related_entities(db_session, entities["nova_lake"].id, max_depth=1)
    names = {n.name for n in nodes}
    assert names == {"Intel", "18A-P", "Arrow Lake"}
    assert entities["unrelated"].name not in names


def test_related_entities_respects_depth_limit(db_session):
    entities = _build_chain(db_session)
    # Add a second hop: 18A-P --used_by chain via RibbonFET
    ribbonfet = Entity(type=EntityType.PACKAGING_TECH, name="RibbonFET")
    db_session.add(ribbonfet)
    db_session.commit()
    db_session.add(
        Relationship(from_entity_id=entities["node"].id, to_entity_id=ribbonfet.id, relation_type=RelationType.RELATED_TO)
    )
    db_session.commit()

    depth1 = related_entities(db_session, entities["nova_lake"].id, max_depth=1)
    assert "RibbonFET" not in {n.name for n in depth1}

    depth2 = related_entities(db_session, entities["nova_lake"].id, max_depth=2)
    names2 = {n.name for n in depth2}
    assert "RibbonFET" in names2
    ribbonfet_node = next(n for n in depth2 if n.name == "RibbonFET")
    assert ribbonfet_node.depth == 2


def test_related_entities_does_not_revisit_nodes(db_session):
    entities = _build_chain(db_session)
    nodes = related_entities(db_session, entities["nova_lake"].id, max_depth=3)
    entity_ids = [n.entity_id for n in nodes]
    assert len(entity_ids) == len(set(entity_ids))


def test_related_entities_for_unknown_entity_returns_empty(db_session):
    assert related_entities(db_session, 999, max_depth=2) == []


def test_find_by_relation_filters_by_target(db_session):
    entities = _build_chain(db_session)
    matches = find_by_relation(db_session, RelationType.USES_NODE, target_name="18A-P")
    assert len(matches) == 1
    assert matches[0].from_entity_name == "Nova Lake"


def test_find_by_relation_filters_by_source(db_session):
    _build_chain(db_session)
    matches = find_by_relation(db_session, RelationType.MANUFACTURED_BY, source_name="nova lake")  # case-insensitive
    assert len(matches) == 1
    assert matches[0].to_entity_name == "Intel"


def test_find_by_relation_returns_empty_for_no_match(db_session):
    _build_chain(db_session)
    matches = find_by_relation(db_session, RelationType.USES_PACKAGING)
    assert matches == []
