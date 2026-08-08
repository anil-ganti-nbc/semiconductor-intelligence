"""Generic repository base.

Keeps ORM-session mechanics out of the CLI and out of service logic, and gives
every entity type the same basic CRUD shape. Domain-specific query and
mutation methods live in subclasses in repositories.py.
"""

from __future__ import annotations

from typing import Generic, List, Optional, Type, TypeVar

from sqlalchemy.orm import Session

ModelT = TypeVar("ModelT")


class Repository(Generic[ModelT]):
    model: Type[ModelT]

    def __init__(self, session: Session):
        self.session = session

    def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        self.session.flush()
        return obj

    def get(self, id_: int) -> Optional[ModelT]:
        return self.session.get(self.model, id_)

    def list(self) -> List[ModelT]:
        return list(self.session.query(self.model).all())

    def delete(self, obj: ModelT) -> None:
        self.session.delete(obj)
        self.session.flush()
