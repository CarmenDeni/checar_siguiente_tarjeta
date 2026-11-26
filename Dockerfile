FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY robot.py .
ENV PYTHONUNBUFFERED=1
CMD ["python", "robot.py"]
