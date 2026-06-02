# General test failure analysis

This analysis covers test failures that do not match kube-burner, orion, k8s-netperf, or ingress-perf patterns.

## Job under analysis

- **Prow job URL:** {job_url}
- **Job name / build ID:** `{job_name}` / `{build_id}`
- **Failed step / test:** `{failed_step}` / `{failed_test}`

## Diagnosis Procedure

Follow these steps in order. Do not skip steps. Think carefully between each step about what you've learned before proceeding.

### Step 1: Fetch the build log

Fetch the failed test's build log:

```
{artifacts_base}/gcs/test-platform-results/logs/{job_name}/{build_id}/artifacts/{failed_step}/{failed_test}/build-log.txt
```

If the build log is not available at this path, try the step-level build log:

```
{artifacts_base}/gcs/test-platform-results/logs/{job_name}/{build_id}/artifacts/{failed_step}/build-log.txt
```

### Step 2: Identify the failure mode

Read the log and categorize the failure:

- **Infrastructure**: cloud API errors, quota exhaustion, provisioning failures, machine failures, DNS resolution errors
- **Installation**: OCP install timeout, CVO errors, operator deployment failures, certificate errors
- **Test execution**: assertion failures, workload crashes, resource creation errors, command not found, script errors
- **Day-2 operations**: operator upgrade failures, node scaling issues, MachineSet errors
- **Timeout**: test or job exceeded its deadline without a specific error
- **Configuration**: invalid parameters, missing environment variables, incorrect resource specifications

Extract from the log:
- The exact error message or exit code
- The timestamp and sequence of events leading to the failure
- Any stack traces or panic output
- Resource names, namespaces, or node names involved

### Step 3: Fetch additional artifacts

Based on the failure mode, fetch relevant artifacts to gather more context:

- For infrastructure/installation failures, check the cluster's `junit` results:
  ```
  {artifacts_base}/gcs/test-platform-results/logs/{job_name}/{build_id}/artifacts/{failed_step}/junit*.xml
  ```

- For pod or operator failures, check must-gather artifacts if available:
  ```
  {artifacts_base}/gcs/test-platform-results/logs/{job_name}/{build_id}/artifacts/{failed_step}/must-gather/
  ```

### Step 4: Cross-reference with system health

Look for systemic issues in the logs that could explain the failure:
- etcd performance degradation (high commit/fsync latency, leader changes)
- API server slowness (high request latency for specific verbs/resources)
- Node-level failures (ovnkube-node pods down, kubelet restarts, NotReady nodes)
- Network instability (DNS timeouts, OVN pod failures, CNI errors)
- Storage I/O issues (slow PV operations, etcd disk pressure)
- Resource exhaustion (OOMKilled pods, CPU throttling, disk full)

### Step 5: Determine root cause

Synthesize the evidence collected in previous steps:
- Is this a known flaky test or infrastructure issue?
- Is there a clear causal chain from a system event to the test failure?
- Could a recent payload or RHCOS change have introduced this failure?


### Step 6: Return the failure evidence and the OCP version

Return the evidence and the OCP version in the following format:

```
Evidence: `evidence`
ocp_version: `ocp_version`
```