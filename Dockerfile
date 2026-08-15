FROM python:3.12-slim

# fonts-noto-cjk is not optional: without a CJK font every Chinese, Japanese
# and Korean cover title renders as tofu boxes. It is the bulk of the image.
# fonts-dejavu-core covers Latin titles.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        fonts-noto-cjk-extra \
        fonts-dejavu-core \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY . .

RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8080

# One worker on purpose: jobs live in this process's memory, so a second worker
# would answer requests that can't see the uploaded book. Threads give
# concurrency within the single worker instead. The long timeout covers cover
# rendering and Claude calls on a slow machine.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "180", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "app:app"]
