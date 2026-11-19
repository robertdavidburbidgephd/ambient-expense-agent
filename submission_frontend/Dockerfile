FROM python:3.12-slim

WORKDIR /app

# Install build deps
RUN pip install --no-cache-dir --upgrade pip

# Copy project definition and install deps first (layer caching)
COPY pyproject.toml .
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" google-adk google-cloud-aiplatform python-dotenv httpx

# Copy application source
COPY main.py .

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
