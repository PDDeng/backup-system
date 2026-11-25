FROM python:3.12-slim@sha256:a0e2dd78cc679ddfffea960fe45ecfcff9ac2f68d67917a0edfcde5c2100151b
WORKDIR /app
COPY requirements.txt .
COPY wheelhouse /wheels
COPY verify_wheels.py .
RUN python verify_wheels.py /wheels && pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt
COPY app.py model.py acceptance.py ./
COPY static ./static
USER 10001:10001
EXPOSE 8100
CMD ["gunicorn","--bind","0.0.0.0:8100","--workers","2","--threads","4","--timeout","30","--access-logfile","-","app:app"]
