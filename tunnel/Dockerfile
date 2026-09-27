# syntax=docker/dockerfile:1
# Tunnel image: the official cloudflared binary on Alpine. It is the only way
# into production - the Cloudflare Tunnel connects out to Cloudflare, and
# Cloudflare hands it the requests for goldfishlab.app, so no node has a public
# port or IP.
#
# Why not the official image itself: it is distroless on Debian 13 (no shell),
# and Jelastic accepts a custom container only on a distribution it supports
# (Alpine 3, Debian 12, ...). The sibling project wiemeinsch.ch's first import
# failed on exactly that with "unsupported OS template" (2026-09-27); this
# file is its working answer.
#
# The binary is statically linked, so it runs unchanged on musl. Both bases
# are pinned by digest; Dependabot raises tag and digest together
# (.github/dependabot.yml, directory /tunnel).

FROM cloudflare/cloudflared:2026.9.3@sha256:072c067d25ccbe61d46e18f0d0723255f2bb5304f7317caa95b27031520ff92c AS cloudflared

FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared
# The same non-root uid as the official image (distroless `nonroot`).
USER 65532:65532
# The token arrives at run time as TUNNEL_TOKEN from the node's environment,
# never from the image. No self-updates: an upgrade is a new image.
ENTRYPOINT ["cloudflared", "--no-autoupdate"]
CMD ["tunnel", "run"]
