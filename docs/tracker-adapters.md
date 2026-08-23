# Tracker adapters

The workflow accepts either Forgejo or GitHub as the issue source. The board stores a tracker-neutral source reference, while the importer preserves native metadata.

## Forgejo

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

## Kanban import contract

For either tracker:

1. fetch the live source;
2. filter tracking-only issues using declared policy;
3. create parent cards before dependent cards;
4. use an idempotency key per source issue;
5. store the source URL and original body in the card;
6. verify the imported count and dependency graph programmatically.

Do not close, relabel, or otherwise mutate the tracker as part of a local import unless the user explicitly asks for tracker changes.
