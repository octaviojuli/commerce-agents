# Deploying the workbench

One ECS instance runs three containers: the API (`api/main.py` under uvicorn), the advisor
workbench (`storefront-web` under `next start`), and an nginx that holds the certificate and
puts both on one origin. `compose.yaml` builds and runs them; `nginx.conf` is its proxy; the
files a deployment fills in are `env.example` (copy to `.env`), the certificate directory,
and the state directory.

The workbench is the agency's internal tool and this deployment treats it as one: it answers
one host name over TLS, and nothing in it is meant for the open internet. Put it behind the
company's own network, an IP allow list, or a VPN.

## What the deployment holds

| | Where | Note |
|---|---|---|
| Advisor conversations and memory | `$TOUR_STATE_PATH/{sessions.sqlite,memory-store.json}` | Written on every turn; a restart resumes them |
| 线路文档 | `$TOUR_STATE_PATH/route-docs/` | The agency's own product data, reviewed; never in the repository |
| The deployment's ERP account | `.env` | Boots the catalog and resolves the 同行 customer |
| An advisor's own ERP token | The API process's memory | Forwarded once at sign-in, never written down, gone on restart |

Each advisor signs in with their own ERP mobile and password, and from then on the session
id in `X-Session-Id` is what identifies them. That id is a bearer credential: TLS is not
optional, and anyone who can reach the host can attempt a sign-in against the agency's ERP,
which is why the host belongs on a private network.

## First run

```bash
# On the server, as a user in the docker group.
git clone <this repository> /srv/acme-tour/src && cd /srv/acme-tour/src
cp examples/tour/deploy/env.example examples/tour/deploy/.env && vi examples/tour/deploy/.env

# The state directory, and the reviewed 线路文档 from wherever they are kept.
mkdir -p /srv/acme-tour/state
rsync -a route-docs/ /srv/acme-tour/state/route-docs/

docker compose -f examples/tour/deploy/compose.yaml --env-file examples/tour/deploy/.env up -d --build
curl -fsS https://$TOUR_PUBLIC_HOST/api/health
```

`/api/health` answers the store name, the model and how many 线路 the boot snapshot read. The
API logs one line per model call at `DEMO_LOG_LEVEL=INFO`.

## What each piece needs

- **The certificate.** `TOUR_TLS_DIR` is mounted read-only at `/etc/nginx/tls` and must hold
  `fullchain.pem` and `privkey.pem`. Renewing on the host is enough; `docker compose restart
  proxy` picks the new one up.
- **The public host name.** `TOUR_PUBLIC_HOST` reaches three places: nginx's `server_name`,
  the API's `DEMO_ALLOWED_HOSTS` (a Host header naming anything else is refused), and the
  workbench's `NEXT_PUBLIC_API_URL`, which Next.js inlines at build time. Changing it means
  `up -d --build`, not a restart. A public name in China needs an ICP filing; an internal
  name does not.
- **The model.** Any Anthropic-compatible endpoint through `ANTHROPIC_BASE_URL`, or
  Anthropic's own with the variable unset. A mainland ECS cannot reach `api.anthropic.com`
  directly, which is why the example points at a domestic endpoint.
- **The ERP.** Production. Every 占位 an advisor takes through the workbench is a real order,
  and ten failed sign-ins lock a mobile for fifteen minutes.
- **pdftotext.** In the API image (`poppler-utils`): a `.pdf` 行程附件 is read through it.

## Sizing

Two cores and 4 GB carry the advisors of one agency. The API is one process holding tokens in
memory and one SQLite file, so this runs as a single instance: a second replica would neither
see the first's sign-ins nor share its sessions. Moving to two would mean the tokens in Redis
and the sessions in a shared database first.

Disk: about 3 GB of images, plus the state directory (the reviewed documents are around
20 MB and grow with the catalog) and the attachment cache under it.

## Backups

`$TOUR_STATE_PATH` is the whole of it. A nightly snapshot of that path restores the
conversations, what the workbench remembers about each advisor, and the reviewed 线路文档.

```bash
tar czf /backup/acme-tour-$(date +%F).tgz -C /srv/acme-tour state
```

## Updating

```bash
cd /srv/acme-tour/src && git pull
docker compose -f examples/tour/deploy/compose.yaml --env-file examples/tour/deploy/.env up -d --build
```

The state directory is a bind mount and survives. Rebuilding the workbench re-inlines
`NEXT_PUBLIC_API_URL`, so a host name change takes effect here too.
