FROM freesurfer/freesurfer:8.1.0

WORKDIR /root

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Create petsurfer-bids Python environment
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN /root/.local/bin/uv venv /opt/petsurfer-bids --python 3.11

# Copy project and install
COPY . /opt/petsurfer-bids-src
RUN /root/.local/bin/uv pip install --python /opt/petsurfer-bids/bin/python /opt/petsurfer-bids-src && \
    chmod -R a+rX /opt/petsurfer-bids /opt/uv-python

# Add petsurfer-bids environment to PATH
ENV PATH=/opt/petsurfer-bids/bin:${PATH}

# Create user
RUN useradd -m -s /bin/bash -G users petsurfer-bids

WORKDIR /home/petsurfer-bids
USER petsurfer-bids

CMD ["/bin/bash"]
