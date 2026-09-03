# Content Migration

Character, element, and scenario catalogs are versioned source records. Runtime
libraries accept only the versions listed in
[Current contracts](CURRENT_CONTRACTS.md). Legacy versions are accepted only
by the offline migration service.

## CLI workflow

Check catalogs without modifying them:

```powershell
stage0-sim migrate content `
  --characters-dir data\characters `
  --elements-dir data\elements `
  --scenarios-dir data\scenarios
```

Check mode is the default and returns exit code 1 when valid content needs
migration. Use `--report report.json` for the deterministic JSON report.

Write a complete migrated copy to a new directory:

```powershell
stage0-sim migrate content `
  --characters-dir examples\characters `
  --elements-dir examples\elements `
  --scenarios-dir examples\scenarios `
  --output migrated-content
```

Migrate in place only with an explicit backup directory:

```powershell
stage0-sim migrate content `
  --characters-dir data\characters `
  --elements-dir data\elements `
  --scenarios-dir data\scenarios `
  --write --backup-dir backups\content-before-v6
```

The service reads raw JSON, migrates characters first, migrates elements in
dependency order, recomputes transitive hashes, migrates scenarios last, and
validates current models plus complete scenario resolution. It stages and
validates the entire catalog before writing. Output and backup paths must be
new and cannot contain or be contained by a source directory.

Errors are explicit for malformed JSON, unsupported or incomplete version
paths, filename/ID mismatches, duplicates, missing or cyclic dependencies,
invalid current schemas, and unresolved scenarios. A failed migration does not
modify source files. Write mode creates the backup before atomic replacements
and rolls back replacements if a write fails.

## Permanent development rule

Adjacent, provider-free transforms live in
`src\stage0_sim\application\migrations\`. They operate deterministically on JSON
and return canonical output, stable changed paths and warnings, or explicit
errors. Runtime models must never be weakened to accept old content.

**A content schema change is incomplete without an adjacent migrator, an
immutable legacy fixture, exact and chained migration tests, current-model
validation, a repository content check, and migration of all tracked current
content.**

Preserve representative old inputs and exact expected outputs under
`tests\fixtures\migrations\`. Large catalogs, including locally present
untracked authoring examples, must be migrated through this service rather
than by bulk hand editing.
