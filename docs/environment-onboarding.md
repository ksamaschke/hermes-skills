# Environment onboarding

`examples/factory-environment.yaml` is the smallest configuration boundary for
one Hermes Software Factory instance. It is a template, not a live deployment
file: replace its angle-bracket placeholders with values belonging to the target
environment and keep credentials out of the file.

## Contract

The overlay declares only the values that differ between factory instances:

- `environment.kind`: `homelab` or `external`;
- `gitops.controller` and `gitops.repository`: the declared GitOps owner of
  desired state;
- `factory.repository`: the reusable factory source repository;
- `tracker.kind`, `tracker.project`, and `tracker.board`;
- `profiles`: the local profile selected for each factory role, including the
  `code_reviewer` role;
- `providers.aliases` and `models.aliases`: names resolved by the target
  environment, rather than hard-coded provider endpoints or model IDs;
- `secrets`: references containing only a secret resource `name` and `key`.

The `brain` section is deliberately optional. If Fritz Brain already exists,
the factory detects and reuses it. Installation is never automatic. If it is
absent, the factory requires explicit approval before anything is installed.
The provider mapping uses the `local_qwen` alias for a homelab and the
`external` alias for other environments; the aliases are resolved outside this
public template.

The validator checks structure, required fields, the Brain safety policy, and
secret-reference shape. It reads only the supplied YAML file. It does not
contact a cluster, tracker, model provider, or Brain service.

## Short flow

1. Copy the template into the environment's overlay location:

   ```bash
   cp examples/factory-environment.yaml path/to/factory-environment.yaml
   ```

2. Fill in the environment kind, GitOps and factory repositories, tracker
   coordinates, role-to-profile mapping, and local model/provider aliases.
3. Provision the referenced secrets out of band. Put only their resource names
   and keys in the overlay, never token, password, or key material.
4. Validate and render with the project-declared tools:

   ```bash
   python scripts/validate_factory_environment.py path/to/factory-environment.yaml
   <project-render-command> path/to/factory-environment.yaml
   ```

   Rendering is a local/configuration check; it does not authorize a live
   mutation.
5. Commit the overlay to the repository that owns the environment policy.
6. Let the controller declared in `gitops.controller` apply the committed
   desired state. Do not replace that ownership with an ad-hoc cluster change.

The reusable mechanics stay policy-driven. Product-specific repository names,
hosts, paths, credentials, and deployment choices belong in the instantiated
overlay or its external provisioning system.
