# STORY_ENGINE.md

The story engine is the platform's editorial edge: it turns the stream of
per-product change events into cross-OEM narratives. A single "new product"
ping is a lead; *three OEMs quietly listing the same unannounced CPU this
week* is a scoop. This is the layer that answers "what's the story?", not just
"what changed?".

## Design (matches every other layer)

Pure and rule-driven, exactly like diff and severity:
`detect(rows, rules) -> list[Story]` — same inputs, same stories, no I/O, no
clock beyond event timestamps. AI (if ever enabled) only *narrates* a finished
Story; it never decides one exists. Scoring is additive and fully explained,
never a fabricated number. Because it's pure over the recorded event stream,
stories are replayable and rule-tunable like everything else.

Pipeline position: runs in `run_all` AFTER all sources crawl and BEFORE the
final outbox drain, so a story can **demote** its constituent product pings.

## StoryRule (config, in radar.yaml)

```yaml
story_rules:
  - id: cross_oem_unseen_silicon
    title: "{n} OEMs listed the same previously-unseen part: {key}"
    match: {change_type: component_changed, unseen_component: true}
    group_by: new_value          # events grouped by this field's value = the shared key
    window: 7d                   # look-back
    min_distinct_manufacturers: 2 # fire when >= this many DISTINCT OEMs share the key
    base_score: 75
    per_extra_oem: 12            # +score per OEM beyond the minimum (capped at 100)
```

Add a new story pattern = add a YAML block. No code. `match` filters events
(same subset-match as severity rules, incl. meta flags like `unseen_component`,
`direction`); `group_by` picks the field whose value is the shared key
(`new_value`, `field`, or any meta key).

Shipped rules: cross-OEM unseen silicon (component_changed + new_product
variants) and simultaneous memory jumps. Tune or add freely.

## What fires, what doesn't

- Same OEM listing a chip on two products = ONE manufacturer → no story.
- Events outside `window` are ignored (rolling correlation, not all-time).
- A story re-fires ONLY when a NEW manufacturer joins the set (dedup key is
  rule + shared key + sorted OEM set). Three OEMs today, a fourth next week →
  a fresh story; the same three re-crawled → nothing.

## Delivery: one story, demote the parts

When a story fires, its constituent products' still-pending individual pings
are marked `demoted` in the outbox (not sent). You get ONE purple story embed
with the headline, the explainable score, and the linked evidence list —
instead of N separate "new product" pings. Calmer channel, higher signal
(DESIGN_REVIEW §7). Stories bypass the per-event severity threshold — they're
the high-value product by definition.

## Scoring (explainable, never fabricated)

`score = min(100, base_score + per_extra_oem * (distinct_oems - minimum))`,
with every contribution listed in `score_reasons` and shown in the embed and
dashboard. Deliberately NOT modelled: "traffic potential" / "expected article
value" — the system has no audience data, so inventing those would be the same
sin as AI hallucination (DESIGN_REVIEW §7). The score ranks urgency; the
judgement stays yours.

## Storage & dashboard

`stories` table (immutable rows, dedup_key UNIQUE). The dashboard's **Stories**
tab (first tab) shows them newest-first with evidence links and score. Runs
read-only like the rest of the dashboard.

## Testing

`tests/test_story.py`: windowing, distinct-OEM threshold, same-OEM-twice
guard, different-chips-don't-merge, score cap, embed evidence links, and the
end-to-end fire+demote+dedup path. Every future story mis-fire becomes a case.
