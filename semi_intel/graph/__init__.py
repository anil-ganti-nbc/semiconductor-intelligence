"""Knowledge-graph queries over the existing `entities` + `relationships`
tables. Deliberately not a dedicated graph database -- see the architecture
discussion and models.py: at the scale this platform runs at, SQL joins over
two relational tables answer every graph query in the original spec
("show everything related to Nova Lake," "show every product linked to
LPDDR5X"). This module is where those queries live so the CLI and web
dashboard share one implementation instead of two.

Temporal graph queries ("what did this look like as of a date") are
deliberately out of scope here -- they'd need edge-level validity windows
this schema doesn't have yet, and nothing in the roadmap needs them before
a real use case shows up.
"""
