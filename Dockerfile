FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home appuser
COPY main.py state.py tools.py portfolio.py upstox.py sectors.py start.py ./
USER appuser

EXPOSE 8000
CMD ["python", "start.py"]
