FROM python:3.13-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock ./
COPY app/ ./app/
COPY run.sh ./

# Make run.sh executable
RUN chmod +x run.sh

# Install dependencies using uv
RUN uv sync --frozen --no-dev

# Run the bot
CMD ["./run.sh"]
