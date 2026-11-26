FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv xvfb x11vnc novnc websockify openbox x11-utils ca-certificates fonts-dejavu-core \
    libegl1 libgl1 libgl1-mesa-dri libdbus-1-3 libfontconfig1 libglib2.0-0t64 \
    libx11-xcb1 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-render-util0 \
    libxcb-xinerama0 libxcb-xinput0 libxcb-xkb1 libxkbcommon-x11-0 \
    build-essential git util-linux \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/digifly
COPY src/ src/
COPY mechanisms/ mechanisms/
COPY presets/ presets/
COPY schemas/ schemas/
COPY bundled_data/ bundled_data/
COPY docs/ docs/
COPY scripts/ scripts/
COPY LICENSE README.md THIRD_PARTY_NOTICES.md VERSION digifly_build.json pyproject.toml ./
RUN python3 -m venv /opt/digifly-venv \
    && /opt/digifly-venv/bin/python -m pip install --no-cache-dir . \
       "NEURON==9.0.1" "arbor==0.12.2" "bmtk>=1.1,<2" "h5py>=3.11,<4"
ENV PATH=/opt/digifly-venv/bin:$PATH
RUN useradd --create-home --uid 10001 digifly
COPY .devcontainer/entrypoint.sh /usr/local/bin/run-digifly
RUN chmod 755 /usr/local/bin/run-digifly
USER digifly
ENV DISPLAY=:99 QT_QPA_PLATFORM=xcb LIBGL_ALWAYS_SOFTWARE=1 PYTHONUNBUFFERED=1
EXPOSE 6080
