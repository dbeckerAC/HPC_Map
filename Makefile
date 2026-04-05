PYTHON ?= python3

.PHONY: pipeline api frontend graphhopper-fetch graphhopper-start

pipeline:
	$(PYTHON) -m pipeline.run_pipeline --config config/default.yaml

api:
	uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

frontend:
	cd frontend && npm run dev

graphhopper-fetch:
	./scripts/fetch_graphhopper.sh

graphhopper-start:
	./scripts/start_graphhopper.sh
