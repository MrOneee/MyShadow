FROM ghcr.io/nickrunning/wechat-selkies@sha256:b2ec45f4975abe246ba380fa987fe3233fea4c1b4aee794b0e553ab09515a379
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv xdotool xclip libzstd1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/weixin-requirements.txt
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN python3 -m venv /opt/weixin-venv \
    && /opt/weixin-venv/bin/pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r /tmp/weixin-requirements.txt
COPY services/weixin-bot /custom-services.d/weixin-bot
RUN sed -i 's/\r$//' /custom-services.d/weixin-bot && chmod 755 /custom-services.d/weixin-bot
