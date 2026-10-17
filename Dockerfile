FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NUDGE_WATCHES=/data/watches.yaml \
    NUDGE_STATE=/data/.nudge-state.json

WORKDIR /app
COPY pyproject.toml README.md ./
COPY nudge ./nudge
RUN pip install --no-cache-dir ".[llm]" && useradd --create-home nudge && mkdir /data && chown nudge /data

USER nudge
VOLUME /data
ENTRYPOINT ["nudge"]
CMD ["run"]
