# Vectural — from-scratch indexing runbook

Every step below is a command you run yourself. Each has an **expected result** so you
can tell success from failure without guessing.

## Preparation that was done once (you do not need to repeat this)

Recorded here so the starting state is not a mystery:

1. **Terminated the in-flight run** and removed all containers:
   `docker compose --profile datastores --profile indexing down --remove-orphans`
2. **Dropped every data volume**, keeping only the model cache:
   `docker volume rm vectural_pgdata vectural_osdata vectural_neo4jdata vectural_temporalpgdata`
   — `vectural_hf-cache` survives because it holds the BGE-M3 weights (~2 GB) and `.env`
   sets `HF_HUB_OFFLINE=1`, which requires them present.
3. **Fixed three indexing defects** (`backend/ingestion/walker.py`,
   `backend/summarise/driver.py`, `backend/llm/openai_client.py`): a byte-based size cap
   that let a 730 KB generated file produce a 214k-token prompt; a permanent HTTP 400
   that aborted an entire service instead of dead-lettering one file; and 36 files of
   non-source noise (lock files, `.gitignore`, `LICENSE`, `.svg`) being indexed.
4. **Added `restart: unless-stopped`** to `backend` and `worker` in `docker-compose.yml`
   (see Step 5 for why).
5. **Rebuilt `vectural-backend:latest`** with those fixes, patching **both** source
   locations in the image — see *Rebuilding after a source change* at the end.

Gates at that point: `ruff` clean, `mypy` clean (114 files), **292 passed / 8 skipped**.

All commands run from the repo root:

```bash
cd /Users/tabishjunaid/work/project/codebase/vectural
```

---

## Step 0 — Docker memory

Docker Desktop → Settings → Resources → **Memory ≥ 10 GB** → Apply & Restart.

The stack is 9 containers and BGE-M3 embedding is memory-hungry; at the previous
7.75 GB the worker was OOM-killed mid-run (exit 137).

```bash
docker info --format 'memory={{.MemTotal}} running={{.ContainersRunning}}'
```

**Expect:** `memory=` at least ~10.7e9.

## Step 1 — Confirm you are starting clean

```bash
docker ps -aq --filter name=vectural | wc -l && docker volume ls --filter name=vectural --format '{{.Name}}'
```

**Expect:** `0`, then only `vectural_hf-cache`.

If other volumes are listed, delete them:

```bash
docker volume rm vectural_pgdata vectural_osdata vectural_neo4jdata vectural_temporalpgdata
```

## Step 2 — Check `.env`

```bash
sed 's/\(OPENAI_API_KEY=.\{8\}\).*/\1…MASKED/' .env | grep -v '^#' | grep .
```

**Expect** exactly these active:

| Key | Value |
|---|---|
| `VECTURAL_ESTATE_HOST_PATH` | `/Users/tabishjunaid/work/project/codebase/test` |
| `VECTURAL_BACKING` | `real` |
| `VECTURAL_EMBEDDER` | `bge-m3` |
| `VECTURAL_GATEWAY` | `openai` |
| `OPENAI_API_KEY` | `sk-proj-…` |
| `HF_HUB_OFFLINE` | `1` |

## Step 3 — Verify the exclusions before spending anything

This is a dry run against the real estate — no containers, no API calls, no cost.

```bash
uv run python -c "
from pathlib import Path
from backend.domain.manifest import load_manifest
from backend.ingestion import walker
root=Path('/Users/tabishjunaid/work/project/codebase/test')
mf=load_manifest((root/'manifest.yaml').read_text())
new=list(walker.walk_estate(root,mf))
old=list(walker.walk_estate(root,mf,ignore_filenames=frozenset(),ignore_suffixes=()))
drop=set(w.path for w in old)-set(w.path for w in new)
print('indexed :',len(new),'files')
print('excluded:',len(drop),'files,',sum((root/p).stat().st_size for p in drop),'bytes')
assert not any('graphify-out' in w.path for w in new), 'graphify output leaked in'
assert not any(w.path.endswith(('.lock','.svg','.gitignore')) for w in new), 'noise leaked in'
print('OK — no lock files, no .gitignore, no svg, no graphify-out')
"
```

**Expect:** `indexed : 348 files`, `excluded: 36 files, 575432 bytes`, then `OK — …`.

## Step 4 — Start the stack

No `--build` — the image is already built and a rebuild re-downloads torch (~15 min).

```bash
docker compose --profile datastores --profile indexing up -d
```

**Expect:** 10 containers created. Check:

```bash
docker compose --profile datastores --profile indexing ps --format '{{.Name}}\t{{.Status}}'
```

## Step 5 — Wait for the datastores

OpenSearch and Neo4j need 30–60 s. Re-run until all three answer:

```bash
curl -s localhost:9200/_cluster/health | head -c 60; echo; curl -s -o /dev/null -w 'neo4j=%{http_code}\n' localhost:7474; curl -s -o /dev/null -w 'backend=%{http_code}\n' localhost:8000/healthz
```

**Expect:** OpenSearch `"status":"green"` or `"yellow"`, `neo4j=200`, `backend=200`.

`backend` and `worker` may each restart once or twice here, which is expected and
self-healing. With `VECTURAL_BACKING=real` they connect to OpenSearch and Neo4j eagerly
at boot and exit if those are still starting. Those two live in the `datastores`
profile, so they cannot be listed under `depends_on` — Compose rejects a dependency an
inactive profile disables, which would break the no-profile demo. Both services
therefore carry `restart: unless-stopped` and simply retry until the stores answer.

## Step 6 — Confirm the worker is on the REAL gateway

```bash
docker compose logs worker --tail 20 | grep -E "gateway|worker ready"
```

**Expect:**

```
LLM gateway = openai (api.openai.com)
worker ready on task queue 'vectural-indexing' (target temporal:7233) — waiting for work
```

**Stop here if it says `LLM gateway = FAKE`** — `.env` was not loaded and answers would
be canned templates. Re-run compose from the repo root.

## Step 7 — Start indexing

```bash
docker compose run --rm --no-deps backend vectural-index --wait
```

**Expect:** a weekly tranche plan, then `Ordered service queue (8): vectural, synapse,
notes, java-core, reactive-spring, spring, code-snippet, azure-workshop`, then
`Started workflow 'index-estate'`.

- **Duration: ~30–40 minutes.** BGE-M3 embeds ~2,100 chunks on CPU.
- **Cost: roughly $0.25–0.50** (~900k tokens after the exclusions cut ~192k).
- **Long silences are normal.** A service is embedded fully before a single bulk index
  call, and embedding logs nothing. The chunk count jumps per service, it does not creep.

Leave the terminal open. If you close it the workflow keeps running — it is durable;
only the log stream stops.

## Step 8 — Monitor (in a second terminal)

Is it alive? High CPU means embedding, not hung:

```bash
docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' vectural-worker-1
```

**Expect:** several hundred to ~1300% CPU, memory well under 7 GB.

Progress:

```bash
curl -s 'localhost:9200/vectural-chunks-code/_count'
```

```bash
docker exec vectural-postgres-1 psql -U vectural -d vectural -c "SELECT (SELECT count(*) FROM file_ledger) files, (SELECT count(*) FROM summaries) summaries, (SELECT count(*) FROM coverage_manifest) services, (SELECT count(*) FROM dead_letter) failed;"
```

The two failures that must stay at **zero** — these are the bugs just fixed:

```bash
docker compose logs worker | grep -cE "context_length_exceeded|OOMKilled"
```

Workflow history, timers, retries: <http://localhost:8080>

## Step 9 — Confirm the run finished correctly

```bash
docker exec vectural-postgres-1 psql -U vectural -d vectural -c "SELECT service, files_indexed, updated_at FROM coverage_manifest ORDER BY service;"
```

**Expect:** 8 rows, one per service.

Confirm the excluded noise never made it in — all three must return `0`:

```bash
curl -s 'localhost:9200/vectural-chunks-code/_count?q=path:*.lock' | python3 -c "import sys,json;print('lock files :',json.load(sys.stdin)['count'])"
```

```bash
curl -s 'localhost:9200/vectural-chunks-code/_count?q=path:*graphify-out*' | python3 -c "import sys,json;print('graphify   :',json.load(sys.stdin)['count'])"
```

```bash
curl -s 'localhost:9200/vectural-chunks-code/_count?q=path:*.gitignore' | python3 -c "import sys,json;print('gitignore  :',json.load(sys.stdin)['count'])"
```

Anything genuinely unparseable is dead-lettered rather than having killed the run:

```bash
docker exec vectural-postgres-1 psql -U vectural -d vectural -c "SELECT kind, count(*) FROM dead_letter GROUP BY kind;"
```

## Step 10 — Use it

```bash
open http://localhost:5175
```

Try: *"What are the main modules of vectural?"* — this is the query that previously
returned `java-core/.gitignore`. It should now cite real vectural source files.

Direct API check:

```bash
curl -s -X POST localhost:8000/search -H 'content-type: application/json' -d '{"query":"main modules of vectural"}' | python3 -m json.tool | head -30
```

## Step 11 — Shut down (keeps the index)

```bash
docker compose --profile datastores --profile indexing stop
```

Resume later from **Step 4**; no re-indexing, no new spend.

---

## Re-indexing after the estate changes

When the code in the estate changes — you pulled a repo, checked out a different
commit, added a service — indexing again is cheap: the file ledger skips every
file whose content hash and prompt version still match, so only what actually
changed is re-summarised.

But there is one order that matters.

> **Restart the worker before you re-index.**
>
> ```bash
> docker compose --profile indexing up -d --force-recreate worker
> ```

**Why.** The worker walks the estate and builds its work map **once, at startup**.
A worker that was already running holds the old file list, so a re-index against
a changed estate finds nothing to do — and reports success while doing it:

```
Workflow finished: {'completed': ['vectural', 'synapse', …]}   ← all 8 services "done"
```

…with the chunk count unchanged and the new files nowhere in the index. Nothing
errors, nothing warns. The files are visible inside the container the whole time
(`docker exec vectural-worker-1 ls /estate/…` finds them); it is the worker's
in-memory map that is stale, not the mount.

**Verify by the numbers, not by the message.** Take a count before and after:

```bash
curl -s 'localhost:9200/vectural-chunks-code/_count'
```

If the count did not move and you expected new files, the worker was stale —
restart it and run again. A more specific check, for a file you know is new:

```bash
curl -s 'localhost:9200/vectural-chunks-code/_count' -H 'content-type: application/json' -d '{"query":{"match_phrase":{"path":"<service>/<path/to/new_file.py>"}}}'
```

**Full sequence:**

```bash
cd /Users/tabishjunaid/work/project/codebase/vectural && docker compose --profile indexing up -d --force-recreate worker
```

```bash
docker compose run --rm --no-deps backend vectural-index --wait
```

Give the worker ~30 s to load BGE-M3 and log `worker ready` before starting the
run, or the starter waits on an empty task queue.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Container exits **137** | Docker VM OOM → Step 0, raise memory. |
| `LLM gateway = FAKE` | `.env` not loaded; run compose from the repo root. |
| `unknown VECTURAL_GATEWAY` | Typo. Valid: `openai`, `anthropic`, `real`, `fake`. Failing loudly here is deliberate — the alternative is silently serving canned answers. |
| `context_length_exceeded` | Should be impossible now (walker caps at 300 KB, tier 1 guards at 96k tokens). If it recurs, the image is stale — rebuild, see below. |
| BGE-M3 won't load | `hf-cache` was deleted. Set `HF_HUB_OFFLINE=0` in `.env` for one run, then back to `1`. |
| Workflow stuck, worker idle | Activities have no heartbeats, so Temporal waits out the 30-min timeout after a worker crash. Terminate and re-run — completed files are skipped, so resume costs nothing: `docker exec vectural-temporal-1 temporal workflow terminate --workflow-id index-estate --address temporal:7233 --reason restart` |
| Workflow id not found | The id comes from the `/estate` **mount** name, not your host folder — it is always `index-estate`. |
| Re-index says `Workflow finished … completed` but **nothing changed** | The worker was started before the estate changed and is still holding the file map it built at boot. Restart it and re-run — see *Re-indexing after the estate changes*. Confirm with the chunk count, not the success message. |
| `[Errno -2] Name or service not known` / `APIConnectionError` in worker logs | DNS to the gateway failed, usually a blip just after a container restart. This is a `TransientGatewayError`: Temporal retries with backoff and the ledger means no completed file is redone. Not fatal — confirm DNS is back before intervening: `docker exec vectural-worker-1 python -c "import httpx;print(httpx.get('https://api.openai.com/v1/models',timeout=10).status_code)"` — **any** HTTP status means DNS and TLS work; `401` is the expected answer here because the probe sends no API key. Only a raised exception means still-broken. |
| Worker logs show `Traceback` | Not automatically a failure — caught-and-retried gateway errors log a traceback too. Only `context_length_exceeded`, `OOMKilled`, or a terminal workflow status are fatal. Grepping for `Traceback` alone produces false alarms. |

### Rebuilding after a source change

Source is baked into the image, so edits need a rebuild. Note the image carries the
package in **two** locations — `/app/backend` and site-packages — and different
entrypoints import from different ones, so patch both or the fix applies to only half
the processes:

```bash
printf 'FROM vectural-backend:latest\nCOPY backend /app/backend\nCOPY backend /usr/local/lib/python3.12/site-packages/backend\n' > /tmp/Dockerfile.patch && docker build -q -f /tmp/Dockerfile.patch -t vectural-backend:latest . && docker compose up -d --force-recreate worker backend
```

A full `docker compose build` also works but reinstalls torch (~15 min, and pip has
timed out on it here before).

### Starting over completely

```bash
docker compose --profile datastores --profile indexing down --remove-orphans && docker volume rm vectural_pgdata vectural_osdata vectural_neo4jdata vectural_temporalpgdata
```

Then resume from Step 4. Keep `vectural_hf-cache` unless you want to re-download the
2 GB model.
