# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the project files into the container
COPY . .

# Install system dependencies (libgomp1 for LightGBM)
RUN apt-get update && apt-get install -y libgomp1 && rm -rf /var/lib/apt/lists/*

# ---> SỬA LỖI <---
# 1. Cài đặt core_lib
RUN pip install ./core_lib

# 2. Cài đặt các thư viện public của 'worker' (từ pyproject.toml)
RUN pip install --no-cache-dir psycopg[binary] polars pandas sqlalchemy scikit-learn hmmlearn pyarrow adbc-driver-postgresql connectorx google-generativeai vectorbt lightgbm

# 3. Cài đặt 'worker' mà KHÔNG KIỂM TRA dependency
RUN pip install --no-deps ./worker
# ---> HẾT SỬA LỖI <---

# Run the application
CMD ["python", "-m", "worker.main"]