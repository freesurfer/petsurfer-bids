IMAGE_NAME := freesurfer/petsurfer-bids
TAG := $(shell python3 -c "import re; print(re.search(r'__version__\s*=\s*\"(.+?)\"', open('petsurfer_km/__init__.py').read()).group(1))")

.PHONY: build

build:
	docker build -t $(IMAGE_NAME):$(TAG) .

push:
	docker push -t $(IMAGE_NAME):$(TAG)
