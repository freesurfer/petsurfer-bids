FROM freesurfer/freesurfer:8.1.0

WORKDIR /root

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Copy requirements file
COPY requirements.txt /root/requirements.txt

# Create petsurfer-bids environment with Python 3.11 and install requirements
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN /root/.local/bin/uv venv /opt/petsurfer-bids --python 3.11 && \
    /root/.local/bin/uv pip install --python /opt/petsurfer-bids/bin/python -r /root/requirements.txt && \
    chmod -R a+rX /opt/petsurfer-bids /opt/uv-python
    
# Add petsurfer-bids environment to PATH
ENV PATH=/opt/petsurfer-bids/bin:${PATH}

# Create user
RUN useradd -m -s /bin/bash -G users petsurfer-bids

# Copy repo contents (add this later)
# COPY --chown=petsurfer-bids:petsurfer-bids . /home/petsurfer-bids/petsurfer-bids

WORKDIR /home/petsurfer-bids
USER petsurfer-bids

CMD ["/bin/bash"]
