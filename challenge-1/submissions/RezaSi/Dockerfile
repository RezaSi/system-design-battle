FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so layer caching works on iterations.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8080

# uvicorn binds to 0.0.0.0 so the container's port is reachable from the host.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "warning"]
