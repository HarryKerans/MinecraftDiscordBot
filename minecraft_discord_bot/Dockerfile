FROM python:3.13-alpine

# Install uv and bash
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN apk add --no-cache bash

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
