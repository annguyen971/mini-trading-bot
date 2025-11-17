# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the project files into the container
COPY . .

# ---> SỬA LỖI <---
# 1. Cài đặt core_lib
RUN pip install ./core_lib

# 2. Cài đặt các thư viện public của 'worker' (từ pyproject.toml)
RUN pip install polars transformers snorkel scikit-learn onnxruntime vectorbt scipy hmmlearn psycopg[binary] pandas SQLAlchemy

# 3. Cài đặt 'worker' mà KHÔNG KIỂM TRA dependency
RUN pip install --no-deps ./worker
# ---> HẾT SỬA LỖI <---

# Run the application
CMD ["python", "-m", "worker.main"]