FROM python:3.12-slim

# docker build --build-arg WITH_BROWSER=true .   → adds headless Chromium for browser mode (~400 MB)
ARG WITH_BROWSER=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    NUDGE_WATCHES=/data/watches.yaml \
    NUDGE_STATE=/data/.nudge-state.json

WORKDIR /app
COPY pyproject.toml README.md ./
COPY nudge ./nudge
RUN if [ "$WITH_BROWSER" = "true" ]; then \
        pip install --no-cache-dir ".[browser]" && playwright install --with-deps chromium; \
    else \
        pip install --no-cache-dir .; \
    fi \
    && useradd --create-home nudge && mkdir /data && chown nudge /data

USER nudge
VOLUME /data
ENTRYPOINT ["nudge"]
CMD ["run"]
