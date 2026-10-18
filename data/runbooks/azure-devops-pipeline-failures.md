---
title: Azure DevOps Pipeline Failures
topic: cicd
---

# Azure DevOps Pipeline Failures

**Platform:** Azure DevOps Services / Azure Pipelines (YAML and classic)
**Severity:** Medium to high — builds or deployments are blocked.

## Symptoms

- A pipeline run fails, or sits in the queue at **"Waiting for an available agent"**.
- Errors such as:
  - `No agent found in pool 'X' which satisfies the specified demands`
  - `The job running on agent X ran longer than the maximum time of 60 minutes`
  - `AADSTS7000222: The provided client secret keys are expired` (service connection)
  - `401 Unauthorized` / `403 Forbidden` when restoring packages from Azure Artifacts
  - `/azure-pipelines.yml: (Line: N, Col: M): Unexpected value`
- The run passes locally but fails in CI.

## Likely Causes

1. **Agent problems:** self-hosted agents are offline, the demands or capabilities don't match, the agent disk is full, or the Microsoft-hosted image changed.
2. **Parallel job limit:** all parallel jobs for the organisation are in use.
3. **Service connection auth:** the service principal secret has expired, the identity lacks RBAC on the target, or the pipeline isn't authorised to use the connection.
4. **YAML errors:** indentation, a template path, a missing parameter or a misspelled variable/expression.
5. **Feed auth:** the build service identity has no permission on the Azure Artifacts feed, or the `NuGetAuthenticate`/`npmAuthenticate` task is missing.
6. **Timeouts:** the job timeout is too short for the work, or a step hangs waiting for interactive input.
7. **Caching:** a corrupted or mismatched `Cache@2` key restores stale dependencies.

## Diagnosis Steps

### 1. Read the failing step's log

Open the run, select the failing job and step, and read the **first** error, not the last one. Download the full logs with **⋯ → Download logs**.

### 2. Turn on debug logging

Queue the run again with the variable:

```yaml
variables:
  System.Debug: true
```

You can also tick **Enable system diagnostics** when you re-run the pipeline.

### 3. Check agents and capacity

- **Project settings → Agent pools → <pool> → Agents:** check whether the agents are online and enabled.
- Compare the agent **Capabilities** with the job's `demands:`.
- **Organization settings → Parallel jobs:** check how many jobs are in use and the limit.
- On a self-hosted agent:
  ```powershell
  Get-Service vstsagent*            # Windows agent service
  Get-PSDrive C | Select-Object Used, Free
  ```
  Logs are under `<agent>\_diag\`.

### 4. Validate the YAML

- In the pipeline editor, use **Validate**, or check the expanded YAML with **⋯ → Download full YAML**.
- Check `template:` paths and `parameters:` types, and the expression syntax: `${{ }}` is resolved at compile time and `$[ ]` at runtime.

### 5. Check the service connection

- **Project settings → Service connections → <connection> → Verify.**
- Look at the expiry date of the app registration secret in Entra ID. Better still, move to **workload identity federation**, which needs no secret.
- Confirm the service principal has a role (for example Contributor) on the target subscription or resource group.
- Under **Security** on the connection, make sure the pipeline is authorised.

### 6. Check feed permissions

- **Artifacts → <feed> → Feed settings → Permissions:** `<Project> Build Service (<Org>)` needs **Feed and Upstream Reader (Collaborator)** or higher.
- Make sure the authenticate task runs **before** restore:
  ```yaml
  - task: NuGetAuthenticate@1
  - script: dotnet restore
  ```

## Resolution

| Cause | Action |
|---|---|
| Agents offline | Restart the agent service, free disk space, or re-register the agent (`config.cmd`). |
| Demands mismatch | Fix the `demands:` or install the missing capability on the agent. |
| Parallel job limit | Wait, cancel stale runs, or buy or request more parallel jobs. |
| Expired SP secret | Rotate the secret and update the connection, or convert to workload identity federation. |
| Missing RBAC | Grant the service principal the required role at the right scope. |
| YAML error | Fix it using the validation output; test template changes on a branch. |
| Feed 401/403 | Grant the build service identity access on the feed and add the authenticate task. |
| Timeout | Set `timeoutInMinutes:` on the job, and find out why the step is slow or hanging. |
| Bad cache | Change the cache key (for example add a version suffix) to invalidate it. |

## Prevention

- Use **workload identity federation** for Azure service connections, so there is no client secret to expire.
- Monitor self-hosted agents: alert when an agent goes offline or its disk is above 85%, and clean `_work` folders on a schedule.
- Validate YAML changes in a pull request pipeline before they reach `main`.
- Version template repositories with tags (`ref: refs/tags/v1.2`) so a template change can't break every pipeline at once.
- Set explicit `timeoutInMinutes` on long jobs instead of relying on the default.

## Escalation

- Check whether Microsoft-hosted agents are affected by an outage: https://status.dev.azure.com
- Escalate to the Azure DevOps organisation admins for parallel job purchases, agent pool permissions or org-level policy blocks.
- Escalate to the cloud/identity team for service principal RBAC or Entra ID app registration changes.
