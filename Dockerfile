ARG PYTHON_VERSION=3.12

# ---- the firewall itself: nothing is downloaded at run time
FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    OMP_NUM_THREADS=2
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py raksha.toml pyproject.toml ./
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
