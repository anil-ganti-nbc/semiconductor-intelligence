# Semiconductor Intelligence Platform 3.3.14 — Start here

This is a **private, populated operator checkpoint**. It contains historical
Signal Radar data imported from your legacy database.

## Open the platform

Keep this folder together, then double-click **`semintel.exe`**. The dashboard
will open in your browser. Leave the small application window running while
you use the dashboard; close it when you are finished.

After it opens, select **Signal Radar** in the left navigation. That is where
the imported posts, reconstructed candidates, RSS feeds, X accounts, and source
suggestions live. Select a candidate and use **View reports** to inspect every
report behind it. From there you can create a human-authored claim, save a
report as evidence, or manually promote the candidate.

Use **Claims & Evidence** to review claims and their supporting, weakening, or
contradicting evidence together. Editorial Inbox includes a ranked Radar review
shortlist even when no story has been promoted yet.

Signal Radar now opens on **Current** candidates from the last seven days.
Choose **Older** to review candidates whose independent reporting activity has
aged out, or **All ages** for the complete historical view. The adjacent window
selector supports 3, 7, 14, or 30 days. Aging only changes the view: it never
deletes, dismisses, or rescales a candidate.

The included database is **`semi_intel.db`**, in this same folder. The
configuration uses relative paths, so you may move the complete folder to a
different location without editing it.

## What has been carried over

- 80 RSS and X sources
- 5,211 historical posts
- 2,056 media records
- 2,020 collection-history records
- 106 source suggestions

Legacy derived stories and scores were deliberately not copied. Version 3.3.8
reanalyzed the historical posts using its current monitored-topic, matching,
clustering, independence, and scoring rules. This produced 350 current Signal
Radar candidates.

## Safety defaults

Automatic collection, X access, automatic promotion, scheduling, external
webhooks, Windows desktop alerts, media downloads, and OCR are all disabled.
Nothing will collect or deliver externally unless you explicitly enable it.

## Back up your database

Before making major changes, close the dashboard and run `semintel.exe backup`
from this folder. Verified backups are placed in the **`backups`** folder next
to `semi_intel.db`. Keep an additional copy of that folder somewhere safe.

The original legacy Signal Radar database was never modified.
