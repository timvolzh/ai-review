FROM python:3.12-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app

WORKDIR /app

COPY . /app

RUN pip install --upgrade pip
RUN pip install -r /app/requirements.txt
RUN ls

ENTRYPOINT ["python", "/app/review.py"]
CMD ["ls"]
