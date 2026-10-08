# Official slim Python image
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so Docker can cache this layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code and input data
COPY src/ ./src/
COPY data/ ./data/

# Reports are written here; mount a host folder over it to keep them
ENV DATA_DIR=/app/data OUTPUT_DIR=/app/output PYTHONPATH=/app/src
VOLUME /app/output

# Profile all tables, then build the referral report
CMD ["sh", "-c", "python src/profiling.py && python src/pipeline.py"]
