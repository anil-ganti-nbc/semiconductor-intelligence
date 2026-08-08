# Runtime Layout

## Application Boundaries
- **Semi Intel**: `%LOCALAPPDATA%\SemiIntel\`
- **OEM Radar**: `%LOCALAPPDATA%\OEMRadar\`
These two applications share no common operational states, runtime directories, or secrets.

## Extensibility and Precedence Order
1. Explicit CLI arguments (highest)
2. Environment Variables (e.g. `SEMINTEL_HOME`)
3. Canonical Application Platform Path (e.g., `%LOCALAPPDATA%`, `~/.local/share`)
4. Legacy working-directory paths (emits a DeprecationWarning)
5. Initialization default (lowest)

## Subdirectory Typings
We maintain explicit folder types to ensure backups, logs, and temp scopes avoid collision.

**Semi Intel:**
- `config/`: Operator configurations and webhook setups.
- `secrets/`: Reserved for eventual token abstractions.
- `browser/`: `.x_browser_profile` caches and `x_session.json`
- `data/`: `semi_intel.db`
- `diagnostics/`: Bundled operational telemetry
- `logs/`: Flat logs 
- `exports/`: End user generated exports
- `backups/`: Confirmed verifiable snapshot backups

**OEM Radar:**
- `config/`: Runtime descriptors and webhook files
- `data/`: Core `radar.db`
- `raw/`: Raw scraped HTML caches
- `logs/`: Flat logs
- `diagnostics/`: Telemetry dumps
- `backups/`: OEM explicit backups

## Explict Legacy Switch
Legacy fallback pathways and warnings can be disabled entirely by setting:
\SEMINTEL_ALLOW_LEGACY_PATHS=0\
\OEM_RADAR_ALLOW_LEGACY_PATHS=0\
This bypasses deprecated path emission and securely targets localized canonical routes only.

