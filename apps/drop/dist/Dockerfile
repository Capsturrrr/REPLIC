# docker build -t replic-drop . && docker run --rm -p 8800:8800 replic-drop     then open http://localhost:8800
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . /app
RUN python3 -c "import sys; sys.argv=['x']; exec(open('apps/drop/app.py').read().split('if __name__')[0]); build()"
ENV HOST=0.0.0.0 PORT=8800
EXPOSE 8800
CMD ["python3", "apps/drop/app.py"]
