ARG LIBRESPEED_BASE_IMAGE=ghcr.io/librespeed/speedtest@sha256:c2d5da9b11ffaf6fab535e4b84405878e67a714d650d292319369278834eb1c8
FROM ${LIBRESPEED_BASE_IMAGE}

COPY docker/entrypoint.sh /entrypoint.sh
COPY index-modern.html /speedtest/index-modern.html
COPY index-classic.html /speedtest/index-classic.html
COPY vps.html /speedtest/vps.html
COPY vps-agent/ /speedtest/vps-agent/

LABEL org.opencontainers.image.title="LibreSpeed VPS Pair"
LABEL org.opencontainers.image.description="LibreSpeed with a token-protected VPS-to-VPS bidirectional measurement agent"
LABEL org.opencontainers.image.source="https://github.com/Borisgohard/librespeed"
LABEL org.opencontainers.image.version="1.3.0"
