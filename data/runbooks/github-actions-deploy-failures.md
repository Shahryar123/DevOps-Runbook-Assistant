---
title: GitHub Actions Workflow Failing on Deploy Step
topic: cicd
---

# GitHub Actions Workflow Failing on Deploy Step

**Platform:** GitHub Actions (GitHub-hosted and self-hosted runners)
**Severity:** Medium to high. Releases are blocked, even though build and test jobs pass.

## Symptoms

- The build and test jobs are green, but the **deploy** job or step fails.
- Errors such as:
  - `Error: Input required and not supplied: <name>` (a missing secret or input)
  - `AADSTS70021: No matching federated identity record found` (Azure OIDC login)
  - `Error: Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity` (AWS OIDC)
  - `Resource not accessible by integration` (the `GITHUB_TOKEN` lacks permissions)
  - `Error: Process completed with exit code 1` from a deploy script
- The deploy job stays at **Waiting** for a long time, or never starts.

## Likely Causes

1. A secret is missing or empty: it's defined at repository level but the job uses an **environment** that doesn't have it, or the other way round.
2. **OIDC trust mismatch:** the federated credential (Azure) or IAM role trust policy (AWS) doesn't match the `sub` claim, which includes the branch, tag or environment name.
3. The workflow has no `permissions: id-token: write`, so it can't request an OIDC token.
4. **Environment protection rules:** a required reviewer hasn't approved, or the branch isn't allowed to deploy to that environment.
5. The `GITHUB_TOKEN` has restricted default permissions (`contents: read`) and the step needs to write.
6. Artifact problems: the deploy job downloads an artifact that the build job never uploaded, or uploaded under a different name.
7. The target is unhealthy: the deploy succeeds, but its health check fails (for example the app doesn't start).
8. Concurrency: a `concurrency` group cancelled or queued this run behind another deploy.

## Diagnosis Steps

### 1. Read the failing step's log

Open the run, expand the failing step, and start with the **first** error. To get more detail, re-run with **Re-run jobs → Enable debug logging**, or set the repository secret or variable `ACTIONS_STEP_DEBUG=true`.

### 2. Check secrets and environments

- **Settings → Secrets and variables → Actions:** is the secret defined at repo, environment or organisation level?
- If the job has `environment: production`, check **Settings → Environments → production** for its secrets, required reviewers and deployment branch rules.
- Secrets are **not** passed to workflows triggered from forks.

### 3. Check OIDC

```yaml
permissions:
  id-token: write
  contents: read
```

- **Azure:** the federated credential's subject must match exactly, for example `repo:org/repo:environment:production` or `repo:org/repo:ref:refs/heads/main`.
- **AWS:** the role trust policy `token.actions.githubusercontent.com:sub` condition must match the same value.

Print the claims you actually get, during debugging only:

```yaml
- run: |
    TOKEN=$(curl -s -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
      "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=api://AzureADTokenExchange" | jq -r .value)
    echo "$TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null | jq '{sub, aud}'
```

### 4. Check artifacts between jobs

The name given to `actions/upload-artifact` in the build job must match the name given to `actions/download-artifact` in the deploy job. The deploy job also needs `needs: build`.

### 5. Check the runner

- **GitHub-hosted:** has the image changed? Check the "Set up job" step for the image version.
- **Self-hosted:** is the runner online (**Settings → Actions → Runners**) and do its labels match the job's `runs-on`? Check its disk space and the `_diag` logs.

## Resolution

| Cause | Action |
|---|---|
| Missing secret | Add the secret at the right level (repo or environment) with exactly the name the workflow uses. |
| OIDC subject mismatch | Update the federated credential or trust policy to match the real `sub` claim, or add one for each environment or branch. |
| Missing `id-token: write` | Add `permissions: id-token: write` at workflow or job level. |
| Waiting on environment | Approve the deployment, or fix the deployment branch rules. |
| `GITHUB_TOKEN` permissions | Grant only the scopes that step needs, for example `contents: write` or `packages: write`. |
| Artifact not found | Make the names match and add `needs:` between the jobs. |
| Health check failed | Look at the app logs on the target. Roll back with the previous artifact and fix the app. |
| Concurrency cancel | Use `cancel-in-progress: false` for deploy jobs so a running deploy isn't cut off. |

## Prevention

- Use **OIDC** instead of long-lived cloud secrets, and scope trust to environments rather than to every branch.
- Use protected **environments** for production, with required reviewers and deployment branch rules.
- Set least-privilege `permissions:` at the top of every workflow.
- Use a `concurrency` group for each environment so two deploys never run against the same target at once.
- Add a post-deploy smoke test, and roll back automatically if it fails.
- Pin third-party actions to a full commit SHA.

## Escalation

- Escalate to the repository or organisation admins for environment, runner group or organisation secret changes.
- Escalate to the cloud or identity team for federated credential and IAM role trust changes.
- Check https://www.githubstatus.com for incidents affecting Actions.
