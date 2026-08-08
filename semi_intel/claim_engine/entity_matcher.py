"""Deterministic entity-mention detection.

No ML, no fuzzy matching: a known entity's name or one of its aliases has to
appear in the text as a whole word/phrase (case-insensitive). This answers
one narrow question -- which *already-known* entities does this text
mention? -- as a building block for claim-link suggestions. It does not
extract new entities from free text; that's a different, much harder
problem (real NLP/NER), deliberately out of scope here. See the roadmap:
this is the "rules first" half of claim detection, not the whole of it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Sequence

from semi_intel.domain.models import Entity

MIN_TERM_LENGTH = 3


@dataclass(frozen=True)
class EntityMention:
    entity_id: int
    entity_name: str
    matched_term: str


def _terms_for(entity: Entity) -> List[str]:
    terms = [entity.name]
    try:
        aliases = json.loads(entity.aliases or "[]")
    except (json.JSONDecodeError, TypeError):
        aliases = []
    terms.extend(a for a in aliases if isinstance(a, str))
    return [t for t in terms if len(t) >= MIN_TERM_LENGTH]


def find_entity_mentions(text: str, entities: Sequence[Entity]) -> List[EntityMention]:
    """One mention per entity at most -- we only need to know *whether* an
    entity is mentioned for scoring purposes, not how many times."""
    mentions: List[EntityMention] = []
    for entity in entities:
        for term in _terms_for(entity):
            pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
            if re.search(pattern, text, flags=re.IGNORECASE):
                mentions.append(
                    EntityMention(entity_id=entity.id, entity_name=entity.name, matched_term=term)
                )
                break
    return mentions
