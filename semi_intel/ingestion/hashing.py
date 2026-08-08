"""One hashing rule, shared by manual CLI evidence entry and every ingestion
plugin, so "is this a duplicate?" means the same thing everywhere."""

from __future__ import annotations

import hashlib


def hash_content(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
