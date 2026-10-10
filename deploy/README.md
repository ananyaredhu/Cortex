# Deployment

The whole system on one Linux host with Docker: meant for a Tencent Cloud Lighthouse or CVM instance in Singapore
(check #10 in [06-risks-and-checks](../docs/06-risks-and-checks.md) is still open: instance, domain, credits).
What A's services need is in [connectors/DEPLOY.md](../connectors/DEPLOY.md); this folder runs them, the Brain and
the UI together.

| File | What |
|---|---|
| `docker-compose.yml` | The six services and their private network |
| `Dockerfile` | The Python image: the Brain, ingestion and both simulators run from it |
| `Dockerfile.web`, `Caddyfile` | The built UI and the reverse proxy (HTTPS, one public door) |
| `entry.py` | Starts a service inside the image; links the account files that are not in it |
| `.env.example` | The settings to fill in, names only. Copy to `.env` on the server |

## What runs
| Service | Does | Reachable from |
|---|---|---|
| `web` | Caddy: HTTPS on 443 (80 redirects), serves the UI, forwards `/v1/*`, `/idp/*`, `/mcp`, `/.well-known/oauth-*` to the Brain and `/drive/notify` to ingestion | The internet |
| `brain` | The API and the MCP server (`brain.api.main`), `BRAIN_ENV=production`, dev login off | `web` only |
| `ingestion` | The one writer to the index; polls every `INGEST_POLL_SECONDS`, Slack events and Drive notifications when configured | `web` (`/drive/notify`) only |
| `sim-confluence`, `sim-jira` | The Confluence and Jira simulators with the Company A story; admin endpoints need `SIM_ADMIN_TOKEN` | The private network only |
| `db` | Postgres 16 with pgvector, schema from `db/init.sql` | The private network only |

Nothing but `web` publishes a port. `/sim/*` answers 404 at the proxy, and the Brain does not serve it either with
dev login off. The images contain no `.env`, token or account file (`.dockerignore` at the repo root).

## The server
- **With the models (the default):** 4 vCPU, 8 GB of memory, 40 GB of disk. bge-m3 is loaded twice (the Brain and
  ingestion, about 2 GB each) next to the checker and Postgres. The first start downloads the models (about 2.5 GB)
  into the `models` volume and can take 10 to 15 minutes; later starts take 1 to 3.
- **A small machine (2 vCPU, 4 GB):** set `EMBEDDING_BACKEND=none` (keyword search only, no bge-m3 in memory) and
  keep `WITH_MODELS=1` and `CHECKER_MODEL`: production refuses to start without the checker. Not measured yet.
- Open inbound 80 and 443 in the instance's firewall, and nothing else but SSH.
- A hostname with a DNS A record to the server. Caddy gets the certificate by itself on the first request. With no
  domain, `<the server's IP with dashes>.sslip.io` works as a hostname.

## First deployment
1. Install Docker Engine with the Compose plugin, then `git clone` the repo and `cd Internal-Brain/deploy`.
2. `cp .env.example .env` and fill it in. Five values are random secrets made on the server
   (`python3 -c "import secrets; print(secrets.token_hex(32))"`, a different one each); `ADP_APP_KEY` and
   `GENERATOR_MODEL` come from the ADP console. Leave `BRAIN_SOURCES=confluence,jira` for now. `chmod 600 .env`.
3. `mkdir -p secrets` (it stays empty until step 5).
4. `docker compose up -d --build`, then `docker compose ps` until `brain` is `healthy`
   (`docker compose logs -f brain` shows the models loading).
5. **Real Slack and Drive** (scenarios 1 and 4 need Slack). Copy these from the machine where the accounts were
   signed in into `deploy/secrets/`, with these names, `chmod 600`:

   | In the repo checkout | In `deploy/secrets/` |
   |---|---|
   | `connectors/identity-map.local.json` | `identity-map.local.json` |
   | `connectors/slack/seed-manifest.local.json` | `slack-seed-manifest.local.json` |
   | `connectors/gdrive/gdrive.local.json` | `gdrive.local.json` |
   | `connectors/gdrive/seed-manifest.local.json` | `gdrive-seed-manifest.local.json` |
   | `connectors/gdrive/token-gdrive-*.json` | the same names |

   Fill in the Slack and Google values in `.env`, set `BRAIN_SOURCES=confluence,jira,slack,gdrive`, then
   `docker compose up -d`. For Drive push notifications:
   `docker compose exec ingestion python -m connectors.gdrive.watch start https://<SITE_HOST>/drive/notify`
   (channels last 7 days: run it again weekly, or Drive falls back to the poll).
6. Check it, below.

## Is it up?
```
docker compose ps                                                        # six services, brain healthy
curl -s https://<SITE_HOST>/v1/health                                    # sources and the generator in use
docker compose exec brain python -m connectors.ingestion.check_index     # the index matches the seeded story
docker compose exec brain python -m connectors.ingestion.freshness_report
docker compose logs --tail 20 ingestion                                  # one JSON line per pass; `errors` names a source in trouble
```
Then open `https://<SITE_HOST>`, sign in as Priya and ask scenario 1; sign in as Sam and ask for the Q3 breach
report (a refusal). From a laptop with the repo: `python -m mcp_server.smoke --url https://<SITE_HOST>` runs the
MCP checks.

## The Leak-CI scoreboard
The Admin page shows the file Leak-CI wrote last, kept in the `scoreboard` volume:
```
docker compose exec brain python -m evals.leakci --target fixture
```
That runs the six cases on the fixture corpus inside the container (no model, a few seconds). The run against a
served Brain plants documents and ingests them itself, so on this host it needs ingestion stopped and the index's
own embedding backend: see the notes at the top of `evals/leakci.py` before trying it here.

## Day to day
- **Update:** `git pull && docker compose up -d --build`. The database, models, certificates and scoreboard are in
  volumes and stay.
- **Before a demo:** `docker compose exec ingestion python -m connectors.reset_demo` lists what differs from the
  seeded story; `--apply` resets the simulators and re-ingests. Real Slack and Drive are reset by hand
  (connectors/DEPLOY.md, "Before a demo").
- **Restart one service:** `docker compose restart brain`. Restarting a simulator puts its seeded data back;
  ingestion notices and crawls again.
- **Back up the audit log and index:** `docker compose exec db pg_dump -U brain brain | gzip > brain-$(date +%F).sql.gz`.
- **Keep `AUDIT_SIGNING_KEY` unchanged:** a new key cannot sign for checkpoints made with the old one.
- **Never** `docker compose down -v` on the server: `-v` deletes the database, the audit log included.

## Try it on a laptop
`SITE_HOST=localhost`, `SITE_ADDRESS=http://localhost`, `BRAIN_ENV=dev`, `WITH_MODELS=0`, `EMBEDDING_BACKEND=none`,
`CHECKER_MODEL=none`, `GENERATOR_BACKEND=template`, the five secrets, then `docker compose up -d --build` and open
`http://localhost`. No model is downloaded and the image is about 420 MB. This stack is its own Compose project
(`internal-brain-deploy`), separate from the development database of the repo root's `docker-compose.yml`.

## Not done yet
- **The host itself** (check #10): no instance has been created, so nothing here has run on Tencent Cloud, with
  HTTPS, with the model libraries in the image, or with real Slack and Drive from inside the containers.
- **The LLM-plane sandbox** ([01-architecture](../docs/01-architecture.md), check #9): the generator call runs in
  the Brain's process. Network policy here is "one private network, one public door", not a per-plane egress list.
- **A pipeline:** deployment is `git pull` and `docker compose up` by hand; CI does not build these images.
- **The mock IdP is the only sign-in.** Anyone who can open the site can sign in as any fictional persona, which is
  what the judged demo needs and must not sit in front of real data.
