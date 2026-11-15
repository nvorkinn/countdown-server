# sqlite-web (https://github.com/coleifer/sqlite-web): a browser view of the broker's SQLite database, for dev/QA only
# (the compose profile `db-viewer`, which config/<environment>.env's DB_VIEWER turns on). Upstream publishes only an
# unversioned `latest` image, so it is built here from PyPI, pinned with hashes in requirements.txt
# (`uv pip compile --generate-hashes requirements.in -o requirements.txt` to bump it).
FROM python:3.14-slim

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt \
    && useradd --system --no-create-home sqlite-web

USER sqlite-web

EXPOSE 8080

# Read-only: the database is opened with mode=ro (compose mounts it read-only as well), so the query tab can't write.
# On the container's own address: Caddy reaches it over the edge network, behind the login, and nothing is published.
# Werkzeug's own server, which is enough for a few people looking.
ENTRYPOINT ["sqlite_web"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--no-browser", "--read-only", "/app/data/broker.db"]
