# My_cloud_project — Serverless URL Shortener on Microsoft Azure

A cloud-computing assignment project: a **serverless REST API** that turns long URLs
into short links, built with **Azure Functions** (Python) and **Azure Table Storage**,
deployed with **Infrastructure as Code (Bicep)** and a **GitHub Actions CI/CD pipeline**.

---

## 1. Overview

| | |
|---|---|
| **Problem** | Long URLs are awkward to share. A shortener maps a long URL to a short code and redirects visitors. |
| **Approach** | Serverless: no servers or VMs to manage; Azure runs the code only when a request arrives and bills per execution. |
| **Compute** | Azure Functions, Consumption plan, Python 3.11 |
| **Storage** | Azure Table Storage (NoSQL key–value) |
| **Monitoring** | Application Insights + Log Analytics |
| **IaC** | Bicep (`infra/main.bicep`) |
| **CI/CD** | GitHub Actions (`.github/workflows/ci-cd.yml`) |

## 2. Architecture

```
               HTTPS
  Client  ───────────────►  Azure Functions (Consumption plan, auto-scaling)
 (browser,                    │  POST /api/shorten      create link
  curl, app)                  │  GET  /api/r/{code}     302 redirect + count click
                              │  GET  /api/stats/{code} link details
                              │  DELETE /api/links/{code}  (function key required)
                              │  GET  /api/health
                              ▼
                    Azure Table Storage  ── table "links"
                    PartitionKey="link", RowKey=<code>, url, created_at, clicks
                              │
                    Application Insights  ◄── logs, request metrics, failures

  GitHub repo ──push──► GitHub Actions: run tests ──► deploy to Function App
```

**Code layout**

```
function_app.py          Azure Functions entry point: routes → handlers
shortener/handlers.py    HTTP layer (parse request, build response, status codes)
shortener/service.py     Business rules (URL validation, code generation)
shortener/repository.py  Data layer: Azure Table Storage + in-memory version for tests
tests/test_api.py        20 unit tests (pytest)
infra/main.bicep         All Azure resources as code
.github/workflows/       CI/CD pipeline
```

Separating the HTTP, business and data layers means the logic can be tested
without Azure, and the storage backend could be swapped (e.g. for Cosmos DB)
without touching the API.

## 3. API reference

### Create a short link
```http
POST /api/shorten
Content-Type: application/json

{ "url": "https://learn.microsoft.com/azure/azure-functions/", "custom_code": "az-func" }
```
`custom_code` is optional (3–32 chars: letters, digits, `-`, `_`). Without it a random 7-character code is generated.

**201 Created**
```json
{
  "code": "az-func",
  "url": "https://learn.microsoft.com/azure/azure-functions/",
  "created_at": "2026-10-06T19:00:00+00:00",
  "clicks": 0,
  "short_url": "https://<app>.azurewebsites.net/api/r/az-func"
}
```
Errors: `400` invalid JSON / URL / code, `409` custom code already taken.

### Follow a short link
`GET /api/r/{code}` → `302 Found` with `Location: <original URL>` (click count +1), or `404`.

### Link statistics
`GET /api/stats/{code}` → `200` with the same JSON as above (current click count), or `404`.

### Delete a link
`DELETE /api/links/{code}?code=<function key>` → `204`, or `404`. Protected by an Azure function key.

### Health check
`GET /api/health` → `{"status": "ok"}`

## 4. Running locally

Prerequisites: Python 3.11, [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local), optionally [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) (local storage emulator).

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

pytest -v                                           # run the tests

cp local.settings.example.json local.settings.json
azurite --silent &                                  # local Table Storage
func start                                          # API on http://localhost:7071/api
```

Try it:
```bash
curl -X POST http://localhost:7071/api/shorten \
     -H "Content-Type: application/json" \
     -d '{"url": "https://azure.microsoft.com"}'
curl -i http://localhost:7071/api/r/<code>
curl http://localhost:7071/api/stats/<code>
```

If no storage connection setting is present the app falls back to an in-memory store
(useful for a quick demo; data is lost on restart).

## 5. Deploying to Azure

Prerequisites: an Azure subscription (the [free account](https://azure.microsoft.com/free/) or Azure for Students is enough) and the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli).

```bash
az login
az group create --name rg-url-shortener --location uksouth

# Create all resources from the Bicep template
az deployment group create \
  --resource-group rg-url-shortener \
  --template-file infra/main.bicep \
  --query properties.outputs

# Publish the code (use the functionAppName printed above)
func azure functionapp publish <functionAppName>
```

The deployment output includes `apiBaseUrl`, e.g. `https://urlshort-func-xxxx.azurewebsites.net/api`.

**Clean up** when finished, to avoid any charges:
```bash
az group delete --name rg-url-shortener --yes
```

### Continuous deployment (GitHub Actions)

The workflow runs the tests on every push and pull request. To also deploy on every push to `main`:

1. In the Azure Portal, open the Function App → **Get publish profile** and copy the file's contents.
2. In GitHub → repo **Settings → Secrets and variables → Actions**:
   - **Secret** `AZURE_FUNCTIONAPP_PUBLISH_PROFILE` = the publish profile contents
   - **Variable** `AZURE_FUNCTIONAPP_NAME` = the Function App name
3. Push to `main`. Until these are set, the deploy job is skipped and only tests run.

> Note: publish-profile deployment needs **SCM basic auth** enabled on the Function App
> (Configuration → General settings). For production, OpenID Connect with `azure/login` is preferred.

## 6. Design decisions

| Decision | Reason |
|---|---|
| **Serverless (Functions, Consumption plan)** | Pay only per execution, scales to zero when idle, scales out automatically under load. Ideal for spiky, short request/response workloads like redirects. |
| **Table Storage instead of SQL** | Every lookup is by a single key (the short code). A key–value store gives fast point reads at very low cost, with no schema or server to manage. |
| **Random 7-char base-62 codes** | 62⁷ ≈ 3.5 trillion combinations; collisions are rare and handled by retrying. `secrets` is used so codes aren't guessable. |
| **ETag optimistic concurrency for clicks** | Two simultaneous redirects could otherwise both read `clicks=5` and both write `6`. The update succeeds only if the entity is unchanged; otherwise it re-reads and retries. |
| **Strict URL validation** | Only absolute `http`/`https` URLs, max 2048 chars. Blocks `javascript:` and other schemes that would make the shortener an attack vector. |
| **Delete protected by function key** | Anyone may create and follow links, but only the owner can delete them. |
| **Reusing the storage client per worker** | Creating the connection once (not per request) reduces latency on warm invocations. |
| **Infrastructure as Code** | The whole environment is reproducible with one command and versioned alongside the code. |

## 7. Cloud concepts demonstrated

- **Serverless / Function-as-a-Service (FaaS)** and event-driven compute
- **Elastic scalability** (automatic scale-out, scale-to-zero)
- **Pay-as-you-go pricing**
- **Managed NoSQL storage**
- **Observability** with centralised logging and metrics (Application Insights)
- **Infrastructure as Code** (Bicep)
- **CI/CD / DevOps** (GitHub Actions)
- **Security**: HTTPS-only, TLS 1.2+, input validation, key-protected admin endpoint, secrets kept out of source control

## 8. Limitations and possible improvements

- **Cold starts**: the first request after idle time is slower. The Flex Consumption or Premium plan can keep instances warm.
- **Secrets**: the storage key is in app settings. A better approach is a **Managed Identity** with role-based access, or **Key Vault** references.
- **Abuse protection**: add rate limiting (e.g. Azure API Management) and a URL reputation check.
- **Custom domain** (e.g. `sho.rt`) via the `SHORT_URL_BASE` setting plus Azure Front Door.
- **Link expiry** and per-user ownership with authentication (Microsoft Entra ID).
- **Hot-partition scaling**: all links share one partition key; at very high scale, partition by the first character of the code.
- Microsoft recommends the newer **Flex Consumption** plan for new Linux function apps; the template uses the classic Consumption plan for simplicity.

## 9. Testing

`pytest -v` runs 20 tests covering link creation, custom codes, duplicate codes,
input validation (empty, malformed, non-HTTP, `javascript:`, too long, reserved codes),
invalid JSON, redirects, click counting, stats, deletion and 404 handling.
The tests use the in-memory repository, so they need no Azure account.
