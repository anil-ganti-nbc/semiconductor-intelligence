"""Two query primitives, both pure SQL joins under the hood:

- `related_entities`: breadth-first traversal from one entity, treating
  relationship edges as undirected ("related to" doesn't care about
  direction), up to a depth limit. Answers "show everything related to
  Nova Lake."
- `find_by_relation`: every edge of a given relation type, optionally
  filtered to a specific source or target entity name. Answers "show every
  product linked to LPDDR5X" (`relation_type=uses_memory, target="LPDDR5X"`)
  or "show every company working on N2" one hop at a time
  (`relation_type=uses_node, target="N2"` gets you the products; chase
  MANUFACTURED_BY from there for the companies).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import RelationType
from semi_intel.domain.models import Entity, Relationship


@dataclass(frozen=True)
class GraphNode:
    entity_id: int
    name: str
    type: str
    depth: int
    via_relation: Optional[str]
    from_entity_name: Optional[str]


def related_entities(session: Session, entity_id: int, max_depth: int = 2) -> List[GraphNode]:
    if max_depth < 1:
        return []

    visited = {entity_id}
    queue = deque([(entity_id, 0)])
    results: List[GraphNode] = []

    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue

        stmt = select(Relationship).where(
            (Relationship.from_entity_id == current_id) | (Relationship.to_entity_id == current_id)
        )
        current_entity = session.get(Entity, current_id)
        current_name = current_entity.name if current_entity else None

        for rel in session.scalars(stmt):
            neighbor_id = rel.to_entity_id if rel.from_entity_id == current_id else rel.from_entity_id
            if neighbor_id in visited:
                continue
            visited.add(neighbor_id)
            neighbor = session.get(Entity, neighbor_id)
            if neighbor is None:
                continue
            results.append(
                GraphNode(
                    entity_id=neighbor.id,
                    name=neighbor.name,
                    type=neighbor.type.value,
                    depth=depth + 1,
                    via_relation=rel.relation_type.value,
                    from_entity_name=current_name,
                )
            )
            queue.append((neighbor_id, depth + 1))

    return results


@dataclass(frozen=True)
class RelationMatch:
    from_entity_id: int
    from_entity_name: str
    relation_type: str
    to_entity_id: int
    to_entity_name: str


def find_by_relation(
    session: Session,
    relation_type: RelationType,
    target_name: Optional[str] = None,
    source_name: Optional[str] = None,
) -> List[RelationMatch]:
    stmt = select(Relationship).where(Relationship.relation_type == relation_type)
    matches: List[RelationMatch] = []

    for rel in session.scalars(stmt):
        from_entity = session.get(Entity, rel.from_entity_id)
        to_entity = session.get(Entity, rel.to_entity_id)
        if from_entity is None or to_entity is None:
            continue
        if target_name is not None and to_entity.name.lower() != target_name.lower():
            continue
        if source_name is not None and from_entity.name.lower() != source_name.lower():
            continue
        matches.append(
            RelationMatch(
                from_entity_id=from_entity.id,
                from_entity_name=from_entity.name,
                relation_type=rel.relation_type.value,
                to_entity_id=to_entity.id,
                to_entity_name=to_entity.name,
            )
        )

    return matches
