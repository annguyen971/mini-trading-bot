# Use an official Python runtime as a parent image
FROM python:3.11-slim

RUN apt-get update && apt-get install -y curl libgomp1 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the project files into the container
COPY . .

# ---> SỬA LỖI <---
# 1. Cài đặt core_lib
RUN pip install ./core_lib

# 2. Cài đặt các thư viện public của 'api' (từ pyproject.toml)
RUN pip install fastapi uvicorn gunicorn slowapi prometheus-fastapi-instrumentator google-generativeai lightgbm scikit-learn
# 3. Cài đặt 'api' mà KHÔNG KIỂM TRA dependency (vì ta đã cài)
RUN pip install --no-deps ./api
# ---> HẾT SỬA LỖI <---

# Run the application
CMD ["python", "-m", "api.main"]