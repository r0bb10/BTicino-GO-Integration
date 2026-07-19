FROM ghcr.io/home-assistant/home-assistant:stable AS test

WORKDIR /work
COPY custom_components ./custom_components
COPY tests ./tests

ENTRYPOINT []
RUN python -m unittest discover -s tests
