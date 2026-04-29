FROM python:3.12-slim

LABEL maintainer="inet-health-engine"
LABEL description="FastAPI REST + WebSocket API"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -u 1001 apiuser
USER apiuser

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
