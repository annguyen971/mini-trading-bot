# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the project files into the container
COPY . .

# Copy scripts directory from scraper/ (for backfill and pipeline tools)
COPY scraper/scripts/ /app/scripts/

# ---> SỬA LỖI <---
# 1. Cài đặt core_lib
RUN pip install ./core_lib

# 2. Cài đặt các thư viện public của 'scraper' (từ pyproject.toml)
RUN pip install httpx beautifulsoup4 psycopg2-binary pandas vnstock google-genai

# 3. Cài đặt 'scraper' mà KHÔNG KIỂM TRA dependency
RUN pip install --no-deps ./scraper
# ---> HẾT SỬA LỖI <---

# Run the application
CMD ["python", "-m", "scraper.main"]