# Django Image - Lightweight Python base image
FROM python:3.12-slim   

# Environment flags: No .pyc files, Logs appear immediately (good for Docker)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency installation: Dependencies are baked into the image, Changing requirements.txt → rebuild required
# copy requirements (or use pyproject if you prefer)
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip
RUN pip install -r /app/requirements.txt

# Code copy: Image contains full project, but volume mount overrides this in dev
COPY . /app
