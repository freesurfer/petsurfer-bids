IMAGE_NAME := freesurfer/petsurfer-bids
TAG := latest

.PHONY: build

docker:
	docker build -t $(IMAGE_NAME):$(TAG) .
