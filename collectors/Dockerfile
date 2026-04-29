FROM python:3.12-slim

LABEL maintainer="inet-health-engine"
LABEL description="BGP, traffic, and community data collectors"

# Build deps for bgpstream + lxml + asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpcap-dev \
    libpcap0.8 \
    curl \
    git \
    libssl-dev \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps — pybgpstream last (heaviest build)
COPY requirements.txt .

# Install everything except pybgpstream first for layer caching
RUN pip install --no-cache-dir \
    httpx aiohttp \
    "celery[redis]==5.4.0" redis==5.0.7 \
    asyncpg psycopg2-binary \
    "neo4j==5.22.0" \
    "elasticsearch[async]==8.13.2" \
    minio==7.2.8 \
    beautifulsoup4 lxml \
    praw==7.7.1 \
    pydantic pydantic-settings python-dotenv \
    structlog tenacity pytz netaddr

# pybgpstream separately — may fail on ARM gracefully (stub mode used)
RUN pip install --no-cache-dir pybgpstream==2.0.0 || \
    echo "WARNING: pybgpstream install failed — stub mode will be used"

COPY . .

RUN useradd -m -u 1001 collector
USER collector

CMD ["python", "-m", "collectors.bgp_collector"]
