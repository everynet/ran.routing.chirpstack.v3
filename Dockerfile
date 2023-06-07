FROM python:3.10.8-slim-buster as builder

ARG ENVIRONMENT
ENV ENVIRONMENT=${ENVIRONMENT:-production}

# Install dependencies
RUN apt-get update && apt-get install --no-install-recommends --yes \
    apt-transport-https \
    ca-certificates \
    build-essential \
    g++ \
    git \
    libssl-dev \
    bash \
    dumb-init \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*
RUN pip install -U pip poetry==1.1.14
RUN poetry config virtualenvs.create false

COPY poetry.lock /
COPY pyproject.toml /
RUN poetry install --no-dev --no-root \
    && if [ "$ENVIRONMENT" = "development" ]; then poetry install; fi

FROM python:3.10.8-slim-buster

COPY --from=builder /usr/local /usr/local

ADD src /app
WORKDIR /app

ENV PATH="/app:${PATH}"
CMD ["python", "main.py"]
