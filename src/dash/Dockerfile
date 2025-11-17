# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the project files into the container
COPY . .

# ---> SỬA LỖI <---
# 1. Cài đặt core_lib
RUN pip install ./core_lib

# 2. Cài đặt các thư viện public của 'dash' (từ pyproject.toml)
RUN pip install streamlit httpx pandas seaborn

# 3. Cài đặt 'dash' mà KHÔNG KIỂM TRA dependency
RUN pip install --no-deps ./dash
# ---> HẾT SỬA LỖI <---

# Expose the port that Streamlit runs on
EXPOSE 8501

# Run the application
CMD ["streamlit", "run", "app.py"]