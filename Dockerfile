FROM python:3.11-slim

WORKDIR /app

# install dependencies first so this layer is cached
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy source
COPY . .

# initialize the database at build time so the container is ready to run immediately
RUN python setup_db.py

# logs directory for audit trail
RUN mkdir -p logs

EXPOSE 8501

# headless so streamlit doesn't wait for browser input on startup
CMD ["streamlit", "run", "ui.py", "--server.headless", "true", "--server.address", "0.0.0.0"]
