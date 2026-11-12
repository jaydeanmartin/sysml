.PHONY: up down logs test reset
up:      ## build and start the whole platform on http://localhost:8080
	docker compose up --build -d
down:
	docker compose down
logs:
	docker compose logs -f --tail=100
test:    ## run the service test suites inside the running stack
	docker compose exec interpreter pytest -q
	docker compose exec -e DATABASE_URL=postgresql://sysml:sysml@postgres:5432/sysml analysis pytest -q
	docker compose exec -e DATABASE_URL=postgresql://sysml:sysml@postgres:5432/sysml model pytest -q
	docker compose exec geometry pytest -q
	docker compose exec mission-sim pytest -q
	docker compose exec aero pytest -q
reset:   ## drop the database volume (re-seeds the demo on next start)
	docker compose down -v
