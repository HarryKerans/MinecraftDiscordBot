FROM ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.23

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock ./
COPY app/ ./app/
COPY rootfs/ /

# Install dependencies using uv
RUN uv sync --frozen --no-dev

# Run the bot via s6-overlay
CMD ["/init"]
