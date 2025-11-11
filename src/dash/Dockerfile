# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the project files into the container
COPY . .

# Install any needed packages specified in pyproject.toml
RUN pip install .

# Expose the port that Streamlit runs on
EXPOSE 8501

# Run the application
CMD ["streamlit", "run", "app.py"]
