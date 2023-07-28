FROM python:latest

ENV GITHUB_TOKEN=""

WORKDIR /opt/autolabeler
COPY . .

RUN pip3 install --no-cache-dir ./

ENTRYPOINT github-autolabeler