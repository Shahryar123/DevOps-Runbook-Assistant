# 14 — Foundry content filter and cloud evaluations

**Status:** code done and checked locally (tests, `terraform validate`). Not yet applied to Azure or run against the project.

Milestones 12–13 moved the models and the input guardrails to Azure, but the Foundry portal still showed nothing under **Guardrails** or **Evaluation**:
- **Guardrails:** our checks are API calls from our code, and the deployment used the built-in `Microsoft.DefaultV2` filter.
- **Evaluation:** our evals run in LangSmith.

This milestone puts both into the Foundry project:

1. **A custom content filter** (`runbook-guardrails`), defined in Terraform and attached to the `gpt-5.4-mini` deployment. It appears under **Guardrails** in the portal.
2. **Foundry cloud evaluations** (`evals/foundry_evals.py`): the app answers the eval dataset locally, Foundry's built-in evaluators score the answers in the cloud, and the results appear under **Evaluation** in project `proj-rbk-dev`.

## What changed (files)

**Terraform**
- `infra/modules/foundry/main.tf`: `azurerm_cognitive_account_rai_policy` (based on `Microsoft.DefaultV2`) with 12 filters. Deployments with `content_filter = true` use it via `rai_policy_name`.
- `infra/modules/foundry/variables.tf`, `outputs.tf`: the `content_filter` input, and the `project_endpoint`, `project_principal_id` and `content_filter_name` outputs.
- `infra/variables.tf`: `content_filter` (`runbook-guardrails`, `Medium`). `foundry_deployments` gains `content_filter` (true for `gpt-5.4-mini`).
- `infra/main.tf`: **Foundry User** on the Foundry resource for you (to run evals and view them) and for the project's managed identity (it runs the cloud evaluations).
- `infra/outputs.tf`: `foundry_project_endpoint`, `foundry_content_filter`.

**Python**
- `evals/foundry_evals.py` (new): answers the dataset, then runs two Foundry evaluations and prints the results and report links.
- `config.py`: `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_EVAL_MODEL`, `FOUNDRY_EVAL_SAFETY`, `FOUNDRY_EVAL_QUALITY_NAME`, `FOUNDRY_EVAL_SAFETY_NAME`.
- `pyproject.toml`: new **optional** dependency group `foundry-evals` (`azure-ai-projects>=2.2.0`). It isn't installed in the Docker image (`uv sync --no-dev` skips it).
- `tests/test_foundry_evals.py` (new): rows, criteria, schema, metadata limits, and the evals API calls with a fake client.

**Docs:** `infra/README.md`, `.env.example`, `CLAUDE.md`, README.

## How it works

### The content filter

```
question ─> our guardrails (milestone 13) ─> graph ─> gpt-5.4-mini ──> answer
                                                          │
                                   Foundry content filter "runbook-guardrails"
                                   checks the prompt AND the completion
```

| Filter | Applies to | Action |
|---|---|---|
| Hate, Sexual, Violence, Selfharm | prompt + completion | block at **Medium** and above (`content_filter.severity_threshold`) |
| Jailbreak (Prompt Shields) | prompt | block |
| Indirect Attack (Prompt Shields for documents) | prompt | annotate only |
| Protected Material Text | completion | block |
| Protected Material Code | completion | annotate only |

- It's a **second, independent layer**. Our own guardrails run before any model call and give friendly replies. The content filter is Azure's enforcement on *every* model call: router, grader, rewriter and generator, prompt and completion.
- When it blocks a prompt, the app shows the normal moderation reply (milestone 13, `is_content_filtered`).
- **Indirect Attack only annotates**, because it only inspects text an app marks as a document in a special format, and runbooks are already scanned at ingest. **Protected Material Code only annotates**, so a common PowerShell snippet isn't blocked as "known code".

### Cloud evaluations

```
evals/dataset.jsonl ──> app (same target as LangSmith) ──> rows {query, response, context, ground_truth}
                                                              │ inline, with the run
                                                              ▼
                       Foundry project proj-rbk-dev:  runbook-quality   runbook-safety
                                                     (judge: gpt-5.4-mini)  (hosted safety models)
                                                              ▼
                                       Evaluation page: runs, per-row scores, reasons
```

| Evaluation | Rows | Evaluators |
|---|---|---|
| `runbook-quality` | in-scope questions that got a real answer (not blocked or refused, with retrieved chunks) | groundedness (answer supported by the chunks?), relevance (answers the question?), retrieval (were the chunks relevant?), response_completeness (covers the reference answer?). Scored 1–5, pass ≥ 3 |
| `runbook-safety` | every question, including the adversarial ones | violence, hate_unfairness, sexual, self_harm (severity 0–7, pass ≤ 3), indirect_attack (did the answer follow injected instructions?) |

- **Inline data:** the rows are sent with the run itself, so there's no dataset upload and no storage account.
- **One evaluation per kind, many runs:** the script reuses the evaluation by name, so every run lands under it in the portal and you can compare runs. Run names match the LangSmith experiment prefix plus a timestamp, e.g. `graph-routing-20261027-153000`, and carry the setup's settings as metadata.
- **LangSmith stays.** It holds the everyday experiments and your own evaluators (retrieval_hit, correct_refusal, the correctness judge). Foundry adds Microsoft's built-in evaluators and a record inside the Azure project.

## New concepts

- **RAI policy = content filter = "guardrail"** in the Foundry portal. It's attached per deployment. Without one, Azure applies `Microsoft.DefaultV2`. A custom policy starts from a base policy and overrides filters.
- **Severity threshold (content filter):** `Low` blocks low, medium and high (strictest); `Medium` blocks medium and high (Azure's default); `High` blocks only high.
- **Block vs annotate:** annotate lets the request through but marks it in the response's filter results, which is useful for watching a category before blocking it.
- **Cloud evaluation:** an *evaluation* holds the data schema and the *testing criteria* (evaluators); a *run* scores one set of rows. It uses the OpenAI Evals API shape, served by the Foundry project (`project.get_openai_client().evals`).
- **`data_mapping`:** connects evaluator inputs to row fields with `{{item.field}}`.
- **LLM-judge vs hosted evaluators:** quality evaluators need a judge deployment (`initialization_parameters.deployment_name`); safety evaluators use Microsoft's own hosted models.

## How to run / verify

**Locally (no Azure):**
```powershell
uv run python -m pytest
cd infra; terraform fmt -check -recursive; terraform validate
```

**Deploy (you run these, from `infra/`).** Only Terraform changed for the app; the image is unchanged, so reuse the current tag:
```powershell
./scripts/plan.ps1 -DeployApps -ImageTag 79b26c9b33d6
./scripts/apply.ps1
```
The plan should show:
- **3 to add:** the content filter policy and 2 role assignments (Foundry User for you and for the project identity).
- **1 to change:** the `gpt-5.4-mini` deployment gets `rai_policy_name = "runbook-guardrails"`.
- Nothing else changes, and nothing is destroyed.

**Check the content filter:** in https://ai.azure.com, open project `proj-rbk-dev`:
- **Guardrails** lists `runbook-guardrails`.
- **Models + endpoints → gpt-5.4-mini** shows it as the content filter.
- Ask the app a normal question: it still answers.

**Run the cloud evaluations** (after the role has applied, a few minutes):
```powershell
# .env (local): LLM_PROVIDER=foundry, FOUNDRY_ENDPOINT=..., VECTOR_STORE=azure_search,
#   AZURE_SEARCH_ENDPOINT=..., AZURE_SEARCH_INDEX_NAME=runbooks, RETRIEVAL_MODE=hybrid,
#   FOUNDRY_PROJECT_ENDPOINT=<terraform output foundry_project_endpoint>
az login
uv run --group foundry-evals python -m evals.foundry_evals --setup graph_routing
```
The script prints pass/fail per evaluator for both runs and a `report:` link. Open the link, or go to **Evaluation** in the project.

**If the safety run fails with a region or availability error:** the hosted safety evaluators aren't offered in every region. Set `FOUNDRY_EVAL_SAFETY=false` (or pass `--no-safety`), and the quality evaluation still runs.

## Notes / decisions

- **Renaming the content filter replaces it**, and Azure refuses to delete a policy that a deployment still uses. To rename, first set `content_filter = false` on the deployment and apply, then rename.
- **`Medium` threshold** matches Azure's default. Our own moderation (milestone 13) already blocks at severity 4 before any model call, so the two layers agree.
- **No red-teaming scan in this milestone.** Foundry's cloud red teaming targets a *model deployment or a Foundry agent*, not our LangGraph app with its guardrails, so it would test the bare model rather than the system users talk to. The local `RedTeam` scanner can call our app, but it needs PyRIT (a large dependency) and is still in preview. The adversarial category in the LangSmith dataset plus the safety evaluation cover this for now.
- **The judge is `gpt-5.4-mini`**, the same deployment as the app. A different (stronger) judge would be fairer: deploy it and set `FOUNDRY_EVAL_MODEL`.
- **Changing evaluators:** an evaluation's criteria are fixed at creation. After changing them, change `FOUNDRY_EVAL_*_NAME` so old and new runs aren't mixed.
- **Cost:** quality evaluators are judge-model calls (4 per answered question); safety evaluators are billed by the evaluation service. A full run of the 33-question dataset is small; see the Azure pricing page.

## Next milestone

To be decided. Candidates from the improvement list: Azure Monitor alerts, a CI pipeline (tests, `ingest --validate-only`, `terraform plan` with OIDC), and a cheaper model for routing and grading checked with these evals.
