# PromptOps and immutable versioning

Runtime prompts are stored under `templates/<prompt_name>/<version>.yaml`.
Agent and service modules must load them through `backend.prompts.registry`;
business prompt text must not be embedded in Python files.

The old flat files `templates/recommendation.yaml`,
`templates/opinion_report.yaml`, and `templates/evidence_answering.yaml` have
been removed. Do not recreate or read flat prompt files in runtime code.

## Files

- `templates/active_versions.yaml`: the only file changed by a rollback.
- `templates/manifest.yaml`: immutable version names and normalized SHA-256
  hashes.
- `templates/<name>/<version>.yaml`: immutable system, user, and optional
  section templates.

The registry validates every version against the manifest before use. Editing
an existing version file therefore causes startup/runtime validation to fail.
Create a new version instead of modifying an old one.

## Add a version

1. Copy the previous YAML to a new version filename.
2. Change its internal `version` field and prompt content.
3. Compute its line-ending-independent hash with
   `compute_prompt_hash(path)` and add it to `manifest.yaml`.
4. Run `python -m unittest tests.test_prompt_registry_unittest`.
5. Switch `active_versions.yaml` after validation.

## Activate or roll back

Use the atomic registry operation:

```python
from backend.prompts.registry import activate_prompt_version

activate_prompt_version("recommendation", "rag-v1")
```

This validates the target version and atomically changes only
`active_versions.yaml`. Historical template files and their hashes are not
overwritten.

Each Agent task that uses a prompt records `template_name`,
`template_version`, and `template_hash` in `prompt_trace`. The template object
is pinned for that task, so an active-version switch cannot change a running
task halfway through generation or repair.

## Current guarded versions

- `recommendation@rag-v3-injection-guard`
- `opinion_report@rag-v2-injection-guard`

These templates explicitly mark user input, history, candidate data, comments,
RAG evidence, and tool results as `UNTRUSTED DATA`. Template instructions are
only one defense layer: the backend must still inspect input before routing,
sanitize external evidence before packing context, constrain tools to read-only
operations, and validate structured output.

LLM-produced preference changes are suggestions only. They must never be
written directly to persistent user memory. Only deterministic preference
updates accepted by the recommendation graph may be stored.

## Verification

```powershell
python -m unittest tests.test_prompt_registry_unittest
python -m unittest tests.test_prompt_security_unittest
```

The registry test verifies manifest hashes and confirms that runtime modules do
not embed system prompt literals. The security test fixes expected behavior for
direct injection, indirect evidence injection, tool-result injection, and
preference-memory poisoning.
