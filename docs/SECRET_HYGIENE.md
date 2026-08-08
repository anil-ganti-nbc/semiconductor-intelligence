# Secret & Operational Hygiene

## Core Directives
1. **Never print secrets**: Discord webhooks, X sessions, or API tokens must never leak to standard error, logs, diagnostic pipelines, or migration output.
2. **Never commit secrets**: Verified through robust `.gitignore` implementations targeting legacy paths and canonical folders. 

## Legacy Fallback and Migration
- A stale legacy file (e.g. `./x_session.json`) will be read *only* if the canonical user path is empty, allowing smooth transitions. 
- Warning deprecation traces will notify operators to safely `mv` their state across.
- **Migration is explicit**. The system does not attempt automatic deletion of old state.
- **Backups and Recovery**: Snapshot routines ignore destructive overwrites unless fully consented.

## Release Distribution 
Executables built via packages must route to `dist/` or similar isolated folders. Source layouts and binary release layouts are inherently partitioned. You must not drop active binaries directly beside core python package code unless deliberately developing locally. 

Operators are advised to frequently rotate X Sessions upon credential decay and run periodic backups of `%LOCALAPPDATA%` folders.

## Git Tracking Audit & Remediation
Never commit the database caches or scraping secrets. You must untrack any prior leakage via:
\git rm --cached x_session.json diagnostics/ .x_browser_profile/ semi_intel.db radar.db -r\
This is required before distribution build compilation.

## Migration Helper
A dedicated executable payload helper was not requested or engineered out-of-the-box. Migration explicitly involves manual copying across boundaries. Operators are strictly instructed to copy without overwriting new canonical structures natively generated on first process boot.

