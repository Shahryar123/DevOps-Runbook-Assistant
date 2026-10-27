# 12 — Microsoft Foundry models

**Status:** code done and checked locally (tests, `terraform validate`). Not yet applied to Azure.

The chat and embedding models now come from **Microsoft Foundry** in our own Azure subscription instead of api.openai.com. The app and the ingest job sign in with their **managed identities**, so no API key is needed for the models. Foundry lives in its own resource group, `rg-rbk-dev-ai`.

Before / after:

| | Before | After |
|---|---|---|
| Chat + embeddings | api.openai.com, `OPENAI_API_KEY` | Foundry in `rg-rbk-dev-ai`, managed identity (Entra ID token) |
| Moderation | OpenAI | OpenAI (unchanged; moves to Azure Content Safety next milestone) |
| Local default | OpenAI | OpenAI (`LLM_PROVIDER=openai`), so nothing changes unless you opt in |

## What changed (files)

**Terraform**
- `infra/modules/foundry/` (new): the Foundry resource (`azurerm_cognitive_account`, `kind = AIServices`, keys disabled), a project, and one model deployment per entry in `foundry_deployments`.
- `infra/main.tf`: new resource group `rg-rbk-dev-ai`, the `foundry` module, a second budget (`budget_ai`), and the **Cognitive Services OpenAI User** role on the Foundry resource for id-app, id-job and you.
- `infra/locals.tf`: names (`rg-rbk-dev-ai`, `aif-rbk-dev-<suffix>`, `proj-rbk-dev`, `budget-rbk-dev-ai`) and two new container env vars: `LLM_PROVIDER`, `FOUNDRY_ENDPOINT`.
- `infra/variables.tf`: `foundry_location`, `foundry_deployments`, `llm_provider`, `ai_budget_amount`.
- `infra/outputs.tf`: `ai_resource_group_name`, `foundry_name`, `foundry_endpoint`, `foundry_project_name`, `foundry_deployments`.
- `infra/environments/dev/dev.tfvars`: `foundry_location = null` (same region as the rest), `ai_budget_amount = 10`.

**Python**
- `src/runbook_assistant/llm.py` (new): `get_chat_model(model)` and `get_embeddings()`, the one place that builds models for the configured provider.
- `config.py`: `LLM_PROVIDER` (`openai` | `foundry`), `FOUNDRY_ENDPOINT`, `FOUNDRY_TOKEN_SCOPE`. `OPENAI_API_KEY` is now required only when the provider is `openai` or moderation is on.
- `rag.py`, `graph/nodes.py`, `guardrails/injection.py`, `evals/evaluators.py`, `scripts/check_setup.py`: use `get_chat_model()` instead of building `ChatOpenAI` themselves.
- `retriever.py`: `get_embeddings` now comes from `llm.py` (same name, so ingest and the vector stores are unchanged).
- `service.py`: clearer messages for auth errors, plus a new one for 403 (identity is signed in but lacks the role).
- Tests: `tests/test_llm.py` (new), new cases in `test_config.py` and `test_service.py`.
- Docs: `.env.example`, `infra/README.md` (diagram, resources, roles, env vars).

## How it works

```
Container App / Job ──(managed identity token)──> Foundry  https://aif-...cognitiveservices.azure.com/openai/v1/
                                                    ├── deployment "gpt-5.4-mini"
                                                    └── deployment "text-embedding-3-small"
```

1. Terraform creates the Foundry resource with `local_auth_enabled = false`: it refuses API keys and accepts only Entra ID tokens.
2. Each deployment is **named after its model**. With Foundry the `model` the app sends is the deployment name, so `ANSWER_MODEL=gpt-5.4-mini` works with both providers and config.py needed no new model settings.
3. Foundry serves the **OpenAI v1 API** at `<endpoint>/openai/v1/`. `llm.py` therefore uses the same LangChain `ChatOpenAI` / `OpenAIEmbeddings` classes and changes only two things:
   - `base_url` → the Foundry endpoint
   - `api_key` → a **token provider** instead of a key
4. The token provider comes from `get_bearer_token_provider(DefaultAzureCredential(), scope)`. In Azure, `DefaultAzureCredential` uses the managed identity named by `AZURE_CLIENT_ID`; locally it uses your `az login`.
5. Same embedding model, same vectors: the existing Azure AI Search index stays valid, so **no re-ingest is needed**.

## New concepts

- **Foundry resource / project / deployment.** The resource (`kind = AIServices`) is the endpoint, auth and billing boundary. A project groups work inside it (evals, tracing, agents later). A deployment is one model you can call, with its own quota.
- **Deployment SKU `GlobalStandard`.** Pay per token. Azure may process requests in any region, which gives the widest model availability and the most quota. `DataZoneStandard` keeps processing inside a data zone such as the EU; set `sku` in `foundry_deployments` if you need that.
- **Capacity** = quota in thousands of tokens per minute (TPM). 50 means 50K TPM, which is plenty for this app. It's a ceiling, not a charge.
- **Token provider (callable `api_key`).** LangChain's OpenAI classes accept a function in place of a key and call it for each request. `get_bearer_token_provider` caches the token and refreshes it before it expires.
- **Cognitive Services OpenAI User.** A data-plane role: call deployments, nothing else (no keys, no management).

## How to run / verify

**Locally (no Azure needed):**
```powershell
uv run python -m pytest
uv run ruff check . ; uv run ruff format --check .
cd infra; terraform fmt -check -recursive; terraform validate
```

**Before applying (read-only checks; you run them):**
```powershell
# Is gpt-5.4-mini offered in the region, and with which versions/SKUs?
az cognitiveservices model list -l uksouth --query "[?model.name=='gpt-5.4-mini'].{v:model.version, skus:model.skus[].name}" -o table
# Quota you have left (look for GlobalStandard gpt-5.4-mini and text-embedding-3-small)
az cognitiveservices usage list -l uksouth -o table
```
If the model isn't offered in uksouth, set `foundry_location` in `dev.tfvars` (e.g. `swedencentral` or `eastus2`). Only the Foundry resource group moves; the app stays in uksouth.

**Deploy (you run these, from `infra/`):**
```powershell
# 1. Commit, then build the new image (tag = git SHA)
./scripts/build-image.ps1
# 2. Plan: expect a new RG, the Foundry resource/project/2 deployments, a budget,
#    3 role assignments, and an in-place update of the app + job (2 env vars)
./scripts/plan.ps1 -DeployApps -ImageTag <sha>
./scripts/apply.ps1
```
New role assignments can take a few minutes to apply. If the first answer shows "Access to the model was denied", wait and retry.

**Verify:**
- Ask the app a question. In LangSmith the run's model calls should show the Foundry base URL.
- Foundry portal (https://ai.azure.com) → project `proj-rbk-dev` → Models + endpoints: the request counts go up.
- Locally against Foundry: in `.env`, set `LLM_PROVIDER=foundry` and `FOUNDRY_ENDPOINT=<terraform output foundry_endpoint>`, run `az login`, then `uv run python scripts/check_setup.py`.

**Roll back:** set `llm_provider = "openai"` in `dev.tfvars`, then plan and apply. The OpenAI key is still in Key Vault.

## Notes / decisions

- **Separate resource group, same state.** The model layer can be costed, locked or deleted on its own, while the outputs and role assignments still wire directly into the app. `destroy.ps1 -AppsOnly` doesn't touch it; a full destroy removes it.
- **The OpenAI key stays for now.** Moderation still calls OpenAI's moderation API, which is the only reason `openai-api-key` remains in Key Vault. The next milestone replaces it with Azure AI Content Safety, and then the key can go.
- **Model version.** The `gpt-5.4-mini` deployment uses the model's current default version (`version = null`), and `version_upgrade_option = "NoAutoUpgrade"` keeps it fixed after creation. Pin a version in `foundry_deployments` once you've seen it in the portal or the `az` output.
- **Content filters.** Deployments get Foundry's default content filter. If it blocks harmless DevOps text ("kill the process"), a custom RAI policy can be added in Terraform (`azurerm_cognitive_account_rai_policy`).
- **Workload profile fix.** The app and job modules now set `workload_profile_name = "Consumption"`. Without it, every plan proposed removing the profile Azure had set (`"Consumption" -> null`).
- **Image tag check.** `image_tag` now rejects digests (`sha256:...`). Pass the git SHA that `build-image.ps1` prints, never the digest from the `az acr build` output.
- **Embeddings** are sent as plain strings with Foundry (`check_embedding_ctx_length=False`). Pre-tokenising is an OpenAI-only optimisation, and our chunks are far below the limit.

## Next milestone

13 — Azure AI Content Safety: replace OpenAI moderation, add Prompt Shields (user prompts and runbook documents) next to the existing regex and LLM injection checks, then remove `openai-api-key`.
