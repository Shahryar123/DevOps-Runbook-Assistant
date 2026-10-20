# Milestone 10 — Azure infrastructure with Terraform (deployment Phase B)

**Status:** Written and validated locally (`terraform fmt`, `init -backend=false`, `validate` pass; scripts parse). Nothing has been created in Azure yet. You run the bootstrap, plans and applies.
**Goal:** Describe all Azure resources as code (Terraform, azurerm v5) and script every deployment step. The Phase A image then runs in Azure Container Apps against Azure AI Search, with managed identities and Key Vault instead of keys in files.

Full details: [infra/README.md](../../infra/README.md) (architecture diagram, roles, state, stages, costs, teardown).

---

## What changed

| File | Purpose |
|---|---|
| `infra/versions.tf`, `providers.tf` | Terraform `~> 1.16`, azurerm `~> 5.10`, random, time; azurerm backend with Entra ID auth; explicit resource provider registration (v5 registers none by default) |
| `infra/variables.tf`, `locals.tf`, `main.tf`, `outputs.tf` | Root module: naming + tags, role assignments, `time_sleep`, `deploy_apps` switch, outputs used by the scripts |
| `infra/modules/*` | `logs`, `acr`, `keyvault`, `search`, `identity`, `environment`, `containerapp`, `job`, `budget` |
| `infra/environments/dev/` | `backend.hcl` (state location) and `dev.tfvars` (names, region, sizes; no secrets) |
| `infra/.terraform.lock.hcl` | Pinned provider builds and checksums (commit it) |
| `infra/scripts/*.ps1` | bootstrap-state, init, plan, apply, set-secrets, build-image, run-ingest, destroy (+ `_common.ps1`) |
| `infra/.tflint.hcl`, `.trivyignore.yaml` | Lint config (azurerm ruleset 0.32.0); two justified Trivy exceptions |
| `.gitignore`, `.dockerignore` | Terraform state/plan/cache files ignored; `infra/` kept out of the image |
| `vectorstores/azure_search.py` | Comment only: `AZURE_CLIENT_ID` selects the user-assigned identity |
| `README.md`, `CLAUDE.md` | Deploy section and infra commands |

No application logic changed. `DefaultAzureCredential` already reads `AZURE_CLIENT_ID` (checked in the installed `azure-identity`), and every setting already comes from environment variables.

---

## How it works

```
bootstrap-state.ps1 ─► rg-runbook-tfstate / storage (Entra-only, versioned) ◄── terraform state + lock

stage 1  plan/apply (deploy_apps=false)  ─► RG, Log Analytics, ACR, Key Vault, Search (free, RBAC only),
                                              id-app, id-job, role assignments, Container Apps env, budget
         set-secrets.ps1                  ─► Key Vault: openai-api-key, langsmith-api-key
         build-image.ps1                  ─► ACR: runbook-assistant:<git sha>
stage 2  plan/apply (-DeployApps)        ─► (wait 120 s for RBAC) Container App + ingest Job
         run-ingest.ps1                   ─► job creates/updates the "runbooks" index
```

**Decisions made in this milestone:**
- **Search auth: Entra ID / RBAC, keys disabled.** The docs list RBAC as available on every tier, including free. An off-by-default fallback (`search_local_auth_enabled`) passes the admin key through Key Vault instead.
- **id-app gets Reader on Search as well as Search Index Data Reader.** LangChain's `AzureSearch` calls `GET /indexes/<name>` when it starts, which the data reader role alone doesn't allow.
- **Manifest:** option B from Phase A, derived from the index. The job needs no storage.
- **Replicas: min 0 / max 1.** Chat state and rate limits live in one replica's memory. Session affinity wouldn't fix scale-in or restarts (see the infra README).
- **App env:** `RATE_LIMIT_TRUST_FORWARDED_FOR=true`, because Container Apps ingress is the trusted proxy that sets `X-Forwarded-For`.

---

## New concepts

Explained in [infra/README.md → Concepts](../../infra/README.md#concepts):
- providers and the lock file
- state, locking and backends
- modules
- variables vs locals vs outputs
- plan vs apply, and why apply a saved plan
- `count` for `deploy_apps`
- managed identities and RBAC role assignments
- Key Vault references
- scale to zero and cold starts
- revisions
- why max 1 replica, and session affinity

---

## How to run / verify

The order is in [infra/README.md → Deploying step by step](../../infra/README.md#deploying-step-by-step).

Local checks (no Azure access needed):
```powershell
cd infra
terraform fmt -recursive -check
terraform init -backend=false
terraform validate
tflint --init; tflint --recursive
trivy config --ignorefile .trivyignore.yaml --tf-vars environments/dev/dev.tfvars .
```

After deploying:
- `terraform output app_url` opens the chat.
- **Behind the scenes:** retrieval comes from Azure AI Search (hybrid).
- **LangSmith:** traces go to the `runbook-assistant-prod` project.
- **Log Analytics:** `ContainerAppConsoleLogs_CL` shows the app and ingest output.

---

## Notes and decisions

- **azurerm v5** (5.10.0 is current). Changes from v4 that matter here:
  - `subscription_id` is required
  - no automatic resource provider registration
  - Key Vault requires `rbac_authorization_enabled`
  - the Container Apps environment needs `logs_destination = "log-analytics"`
- **The Search admin key is in state**, because the provider reads it back even with keys disabled. The OpenAI and LangSmith keys aren't, since only their Key Vault URLs are in Terraform. This is why the state account is Entra-only, versioned and in its own resource group.
- **Immutable image tags.** `plan.ps1` defaults to the git SHA and `latest` is rejected by validation, so every deployment names exactly one build.
- **Free Search tier:**
  - limits are 50 MB, 3 indexes, one per subscription
  - no replicas or semantic ranker
  - an inactive service may be removed during capacity shortages
  - switching tiers recreates the service; then run ingest again
- **Scripts work in Windows PowerShell 5.1 and PowerShell 7.** Native calls go through `Invoke-Native { ... }`, which throws on a non-zero exit code; that's how they "stop on first error". `set-secrets.ps1` passes values via a temporary file and `--file`, never on a command line.
- **Not yet tested against real Azure.** Expect small first-run fixes: role propagation timing, the ACR Tasks availability on your subscription, and Search RBAC on the free tier.

### Fixes from the first real run
- **`bootstrap-state.ps1`:** on Windows `az` is `az.cmd`, so arguments pass through cmd.exe, which broke on `--query 'length(@)'`. The role check now counts in PowerShell. Windows PowerShell 5.1 also returns a JSON array as a single object, so the array is unrolled before counting.
- **Dockerfile:** ACR Tasks (`az acr build`) uses the classic Docker builder, which rejects `RUN --mount=type=cache` (BuildKit only). The cache mounts are removed; `UV_NO_CACHE=1` keeps uv's cache out of the layer, and Docker's layer cache still works.
- **`.dockerignore`:** `az acr build` ignores patterns ending in `/`, so `infra/` (257 MB of Terraform provider binaries), `chroma_db/` and `tests/` were uploaded. The patterns no longer have trailing slashes, and the upload dropped from 68.5 MB to 0.4 MB. `.env` was never included.
- **Container Apps environment:** Azure creates every environment with a built-in `Consumption` workload profile. Since the code didn't declare it, the next plan wanted to remove it, and removing all profiles forces the environment to be recreated. The `environment` module now declares `workload_profile { name = "Consumption", workload_profile_type = "Consumption" }`.
- **`set-secrets.ps1`:** the first ingest failed with an empty OpenAI `400` because Key Vault held a 1-character key. A paste into the hidden prompt was cut off, which can happen with Windows PowerShell in the VS Code terminal. The script now rejects values that don't fit the expected pattern (prefix `sk-` / `lsv2_`, at least 40 characters, no spaces, quotes or `=`), and after saving it prints only the length.

## Next

- Run the stages and fix anything the first real deployment turns up.
- Afterwards, possibly:
  - CI/CD: GitHub Actions with OIDC (federated credentials), running fmt / validate / tflint / trivy / plan on PRs and apply on approval
  - a shared store (Redis) for chat history and rate limits, to allow more than one replica
  - a VNet with private endpoints for Key Vault and Search
