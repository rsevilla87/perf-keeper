# Job context (for the report header)

- **Prow job URL**: `{job_url}`
- **Job name**: `{job_name}`
- **Build ID**: `{build_id}`
- **Failed test**: `{failed_test}`

# Task

Produce **one** final report in the exact structure below. Use Markdown headings as shown. Be concise but specific: quote numbers, thresholds, alert names, exit codes, and PR titles **only** when they appear in the conversation.

## Required output structure
Omit the sections that dont' contain any information.

## Job summary

In case the job failed because of a regression, produce a table with the following format for each affected metric:

``` 
| Metric | Value | Percentage change | Test name |
|--------|-------|------------------|-----------|
| `metric_name` | `value` | `percentage_change` | `regressing_test` |
```

Also include the following information, don't include it if it's not available:

- Job URL: `{job_url}`
- Failed test: `{failed_test}`
- Regressing version: `{regressing_version}`
- Previous version: `{previous_version}`

## Root cause

Paragraph(s) explaining the chain from evidence to conclusion. If the conversation gave competing hypotheses, state the leading one and what would falsify it.

## Suspect changes

If payload / RHCOS / component RPM / GitHub PR analysis was discussed, enumerate only the relevant changes here. Use the following format:

- PR URL: <PR_URL> - <PR_DESCRIPTION>

## Classification

Pick **exactly one** label (use this exact token on the line after the heading):

- `performance-regression`
- `test-error`
- `alerting-violation`
- `measurement-threshold`
- `OpenShift installation failure`
- `Day-2 operations failure`
- `platform issue`
- `workload timeout`
- `job timeout`
- `configuration-error`
- `unknown` — use only if the conversation does not support any of the above

Format:

Classification: <token>