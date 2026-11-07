# Pinned, so Dependabot's bump is what changes it: an unchanged caddy/ reuses the previous release's image (release.yml).
FROM caddy:2.11.9-alpine

COPY Caddyfile /etc/caddy/Caddyfile

# caddy as the entrypoint, like the other infra images, so CI's check is just
# `docker run --rm <image> <args>`. The default command is upstream's.
ENTRYPOINT ["caddy"]
CMD ["run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
