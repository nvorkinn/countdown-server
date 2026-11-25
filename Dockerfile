# The IP location data the settings page suggests a weather place from (broker/services/geoip.py).
# Data, not code, so it is fetched once on the build machine whatever platform the image is for.
FROM --platform=$BUILDPLATFORM python:3.14-slim AS fetch
COPY scripts/fetch_geodata.py /

# One stage and one layer per file, each keyed on its version on the server (`fetch_geodata.py versions`, which the
# release and test builds pass in): a build cache keeps a file until it changes there, and the image's layer for it
# with it, so a host pulls the 60 MB DB-IP layer again only when DB-IP publishes a new month. GeoNames' small file
# changes daily. Left empty (a local build), the first download is kept.
FROM fetch AS dbip
ARG DBIP_VERSION
RUN python /fetch_geodata.py dbip /geodata "$DBIP_VERSION"

FROM fetch AS places
ARG PLACES_VERSION
RUN python /fetch_geodata.py places /geodata

FROM ghcr.io/astral-sh/uv:0.13.10 AS uv

# The Python .python-version pins, which the tests run on (Dependabot leaves its minor version alone).
FROM python:3.14-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

# The dependencies as uv.lock pins them (no dev group), in /app/.venv. vendor/countdown-api (the store contract) is
# installed editable, so it has to stay where it is. uv is only mounted and keeps no cache, so neither is left behind.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY vendor/countdown-api/ vendor/countdown-api/
COPY pyproject.toml uv.lock ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv uv sync --locked --no-dev --no-cache
ENV PATH="/app/.venv/bin:$PATH"

COPY --from=dbip /geodata/ geodata/
COPY --from=places /geodata/ geodata/
COPY broker/ broker/
COPY wsgi.py gunicorn.conf.py ./

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "wsgi:app"]
