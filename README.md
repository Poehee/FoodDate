# FoodDate
Keeping track of portions of food in freezers

FoodDate records what is in your freezers, where it is and when to eat it by.
See [PLAN.md](PLAN.md) for the design and milestones.

## Requirements

- [uv](https://docs.astral.sh/uv/), which also provides Python 3.13
- Docker with Compose

## Setup

```bash
uv sync
uv run python scripts/create_env.py
docker compose up -d --wait
uv run python manage.py migrate
uv run python manage.py runserver
```

Open <http://127.0.0.1:8000>. `/health/` reports whether the database is reachable.

PostgreSQL listens on port 5433, on localhost only. Its data is kept in the Docker volume
`fooddate_data`. `docker compose down` keeps that data, `docker compose down -v` deletes it.

The database password is set when the volume is first created, so keep `.env` with it.

## Checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run
uv run pytest
```

## Contributing

Changes to `main` go through a pull request with a passing `test` check.
Never commit `.env`, database dumps or real inventory data.
