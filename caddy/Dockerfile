# caddy-docker-proxy: Caddy with the plugin that builds its config from Docker labels (docker-compose.yml). Both
# versions are pinned, so Dependabot's bump is what changes them: an unchanged caddy/ reuses the previous release's image
# (release.yml). No DNS plugin: certificates come from the HTTP challenge, the records are DNS-only (infra/cloudflare).
FROM caddy:2.11.9-builder AS builder
RUN xcaddy build --with github.com/lucaslorentz/caddy-docker-proxy/v2@v2.13.1

FROM caddy:2.11.9-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy

# caddy as the entrypoint, like the other infra images, so CI's check is just `docker run --rm <image> <args>`. The
# default command is the plugin's: watch Docker (through DOCKER_HOST, the socket proxy) and serve what the labels say.
ENTRYPOINT ["caddy"]
CMD ["docker-proxy"]
