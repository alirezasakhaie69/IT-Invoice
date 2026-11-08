FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Tehran \
    DATA_DIR=/data

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libffi8 \
        libgdk-pixbuf-2.0-0 \
        libcairo2 \
        libglib2.0-0 \
        shared-mime-info \
        fonts-liberation \
        fontconfig \
        tzdata \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirror-pypi.runflare.com/simple/ --trusted-host mirror-pypi.runflare.com -r requirements.txt
COPY . .
RUN mkdir -p /usr/share/fonts/truetype/vazirmatn \
    && cp app/static/fonts/*.ttf /usr/share/fonts/truetype/vazirmatn/ \
    && fc-cache -f

EXPOSE 9050
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 9050"]
