# Reproducible builds

Use Python 3.12/3.13 and uv 0.11.32: `uv sync --locked --all-extras && uv build`. Regenerate the hash-checked container dependency file only with `uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file requirements.container.lock` and review the diff. Build with `docker build --build-arg GIT_REVISION=FULL_40_CHAR_SHA -t semint:FULL_40_CHAR_SHA .`. CI emits a reproducible CycloneDX SBOM and provenance JSON. Do not publish or promote.
