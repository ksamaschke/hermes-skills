# Tracker adapters

The workflow accepts any code- and issue-tracking source that can implement the
adapter contract. The board stores a tracker-neutral source reference, while
the adapter preserves native metadata.

Supported adapter families include Forgejo/Gitea, GitHub, GitLab, Bitbucket, and
custom REST, GraphQL, CLI, or webhook integrations. The shared factory does not
assume equivalent semantics for labels, epics, milestones, pull requests,
sub-issues, or dependency APIs.

The tracker adapter is a project-specific add-on. Shared skills define the
source-to-execution contract but do not contain a product's host, repository,
board, checkout paths, profile names, model route, or label policy.

## Adapter interface

Every adapter declares how it:

- lists complete paginated work items and source states;
- fetches detail, comments/activity, labels/fields, timestamps, URLs, and kind;
- identifies a stable provider item key independent of title text;
- reads explicit dependencies or verified native relationships;
- authenticates without printing credentials;
- detects closure, parking, blocking, reopening, deletion, and updates.

## Forgejo/Gitea

Use `tea` or the REST API. Preserve:

- issue number and URL;
- labels and milestones;
- body and comments;
- explicit `Depends on:` references;
- project-specific epic/parked conventions.

Example discovery command:

```bash
page=1
while :; do
  batch=$(tea issues list --repo owner/repository --state open \
    --kind issues --page "$page" --limit 100 \
    --fields index,state,url,title,body,labels,created,updated \
    --output json)
  test "$batch" = "[]" && break
  printf '%s\n' "$batch"
  page=$((page + 1))
done
```

Fetch comments for each retained issue with `tea issues <index> --comments --output json`.

## GitHub

Use `gh` or the REST/GraphQL API. Preserve the equivalent native fields:

```bash
gh api --paginate -X GET repos/owner/repository/issues \
  -f state=open -F per_page=100 \
  --jq '.[] | select(.pull_request == null)'
```

Fetch comments for each retained issue with `gh issue view <number> --repo owner/repository --json comments`.

GitHub projects, milestones, issue types, and labels are not automatically equivalent to Forgejo concepts. The project policy must define how epics/tracking issues and parked/human-decision work are identified.

For `dependency_source: sub_issues`, fetch GitHub child issues with `gh api --paginate -X GET repos/owner/repository/issues/<number>/sub_issues`. For `body_marker`, parse the declared `Depends on:` marker. For `native`, use only a tracker-native dependency API that the project has explicitly verified.

## GitLab, Bitbucket, and custom sources

Use the provider's official CLI/API or a project-owned adapter. Preserve the
provider-native URL and stable item key, and declare how work-item kind, state,
labels/fields, comments, and dependencies map to the Kanban contract. Do not
pretend that a Bitbucket pull request, GitLab epic, or custom webhook payload
has Forgejo or GitHub semantics without a project policy decision.

Custom adapters must be deterministic and idempotent. They may poll or consume
webhooks, but writes to Kanban remain separate from gateway dispatch. Keep the
adapter and its fixtures in the project repository or an external factory
add-on, not in this shared skills repository.

## Kanban import and reconciliation contract

For any tracker:

1. fetch the complete live source;
2. filter tracking-only issues using declared policy;
3. use a canonical source key and idempotency key per source issue;
4. create or match intake cards before dependent cards;
5. store the source URL, original body, labels, and dependency metadata;
6. resolve explicit execution dependencies into real Kanban links;
7. reconcile source state conservatively without overwriting execution evidence;
8. verify counts, task fields, and the dependency graph programmatically.

A source issue may fan out into an intake card and orchestrator-created children.
Do not create duplicate intake work during a poll. Do not close, relabel, or
otherwise mutate the tracker as part of a local import unless the project
adapter explicitly owns that write and the user has authorized it.

Project-specific adapters and recurring pollers belong in a project policy,
project repository, or external factory add-on. Do not modify this shared skill
or Hermes core to encode one product's tracker conventions.
