# Werewolf Stats Telegram Bot (@wwstatsbot)

## How to install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). uv provisions the
interpreter itself, so no system Python setup is needed.

1. Clone this project.
2. `uv sync` — creates `.venv` from the committed `uv.lock`.
3. `cp configEXAMPLE.py config.py`, then set your bot token from
   [@botfather](https://t.me/botfather) and your `DATABASE_URL` (Postgres) in `config.py`.
   Environment variables of the same names take precedence and are what production uses.
4. `uv run python main.py`

## Development

```bash
uv run pytest                  # test suite (Postgres tests skip by default)
uv run ruff check .            # lint
uv run ruff format .           # format
```

The data-layer tests need a real Postgres and are skipped unless `TEST_DATABASE_URL`
is set. CI runs `postgres:18`, matching production:

```bash
docker run -d --rm --name pgtest -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:18
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest
```

`tests/test_render_golden.py` pins the exact bytes of every rendered message. If it
fails, user-visible output changed — treat that as a bug, not a test to update.

History includes a whole-repo `ruff format` commit. To read past it:

```bash
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

## Deployment

Deployed on [Railway](https://railway.com) from the `Dockerfile` (see `railway.json`);
`k8s-deployment.example.yaml` is a reference manifest for a Kubernetes deploy. All
configuration is supplied as environment variables — see the header comment in the
`Dockerfile` for the full list. `/healthz` and `/readyz` are exposed on `HEALTH_PORT`
(default 8080).

## Credits

- Originally made by Carson True
- Edited by @jeffffc ([Telegram profile here](http://t.me/jeffffc))
- Actively maintained fork by [@jjw91](https://t.me/jjw91): <https://github.com/joeljwilliams/wwstatsbot>
