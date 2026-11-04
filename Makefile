DOCKER ?= docker
.PHONY: setup build up fetch python spark ingest compare test benchmark down
setup build:
	$(DOCKER) compose build python
up:
	$(DOCKER) compose up -d --wait postgres
fetch:
	$(DOCKER) compose run --rm fetch
python ingest:
	$(DOCKER) compose run --rm python
spark:
	$(DOCKER) compose run --rm spark
compare:
	$(DOCKER) compose run --rm compare
test:
	$(DOCKER) compose run --rm tests
benchmark:
	bash scripts/benchmark.sh 3
down:
	$(DOCKER) compose down
