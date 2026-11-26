ARG PYTHON_VERSION=3.12

# ---- the firewall itself: standard library only, nothing to download at run time
FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1
WORKDIR /app
COPY main.py raksha.toml requirements.txt pyproject.toml ./
COPY config ./config
COPY resources ./resources
COPY wards ./wards

# ---- tests and the development loop
FROM base AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
COPY tools ./tools
USER 10001:10001
CMD ["python", "-m", "pytest", "-q"]

# ---- what runs in production (the default target)
FROM base AS runtime
USER 10001:10001
ENTRYPOINT ["python", "/app/main.py"]
CMD ["--input", "/data/input", "--output", "/data/output"]
