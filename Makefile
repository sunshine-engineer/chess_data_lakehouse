DOCKER ?= docker
.PHONY: setup build up fetch prepare python python-mp spark ingest compare test benchmark backup cluster-smoke down
setup build:
	$(DOCKER) compose build python
up:
	$(DOCKER) compose up -d --wait postgres
fetch:
	$(DOCKER) compose run --rm fetch
prepare:
	$(DOCKER) compose run --rm python prepare
python ingest:
	$(DOCKER) compose run --rm python
python-mp:
	$(DOCKER) compose run --rm python-mp
spark:
	$(DOCKER) compose run --rm spark
compare:
	$(DOCKER) compose run --rm compare compare --benchmark-group $(GROUP)
test:
	$(DOCKER) compose run --rm tests
benchmark:
	bash scripts/benchmark.sh 3
backup:
	$(DOCKER) compose run --rm backup-restore
cluster-smoke:
	$(DOCKER) compose --profile tools --profile cluster run --rm --use-aliases cluster-driver
down:
	$(DOCKER) compose --profile tools --profile cluster down
