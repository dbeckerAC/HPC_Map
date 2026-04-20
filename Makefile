PYTHON ?= .venv/bin/python

.PHONY: pipeline pipeline-docker api frontend graphhopper-start

pipeline:
	$(PYTHON) -m pipeline.run_pipeline --config config/default.yaml

pipeline-docker:
	docker compose run --rm --no-deps pipeline

api:
	uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

frontend:
	cd frontend && npm run dev

graphhopper-start:
	docker compose up graphhopper --remove-orphans
