DOCKER ?= docker
.PHONY: setup build up fetch prepare python python-mp spark ingest compare test benchmark backup cluster-smoke down web ml web-test browser-test study study-test study-browser-test study-ai
.PHONY: product-preview product-test
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
	$(DOCKER) compose --profile tools --profile cluster --profile web --profile study --profile study-ai down
web:
	$(DOCKER) compose --profile web up -d --wait web
ml:
	$(DOCKER) compose --profile web run --rm ml-train
web-test:
	$(DOCKER) compose --profile web run --rm web-tests
browser-test:
	$(DOCKER) compose --profile web run --rm browser-tests
product-preview:
	$(DOCKER) compose --profile web build web
	$(DOCKER) compose -f compose.yaml -f compose.product-qa.yaml --profile product-qa up -d --no-deps --wait product-preview
product-test:
	$(DOCKER) compose -f compose.yaml -f compose.product-qa.yaml --profile product-qa run --rm --no-deps product-browser-tests
study:
	$(DOCKER) compose --profile web --profile study build web
	$(DOCKER) compose --profile web --profile study up -d --wait web study-api
study-test:
	$(DOCKER) compose --profile study run --rm study-tests
study-browser-test:
	WEB_PORT=18082 $(DOCKER) compose -p chess-workspace-qa -f compose.yaml -f compose.workspace-qa.yaml --profile web --profile study up -d --wait web study-api
	WEB_PORT=18082 $(DOCKER) compose -p chess-workspace-qa -f compose.yaml -f compose.workspace-qa.yaml --profile web --profile study run --rm personal-browser-tests
# Historical router experiment; the current workspace uses computed answers.
study-ai:
	$(DOCKER) compose --profile study-ai up -d --wait study-llm
	$(DOCKER) compose --profile study-ai exec study-llm ollama pull qwen2.5:0.5b
	@echo 'Legacy router model prepared. The current Personal Study workspace uses computed answers.'
