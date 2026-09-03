# Development History

The working tree keeps only a compressed architectural history. Detailed
requirements, completed plans, assessments, and prompt notes remain available
through Git history and must not be treated as current instructions.

Read [Development history](DEVELOPMENT_HISTORY.md) for the sequence of major
design replacements and their implementation commits. Use the
[current documentation map](../README.md) for all development and operating
guidance.

When a future breaking change supersedes an architecture:

1. update the current owning document;
2. add one concise entry to the development history when the rationale will
   remain useful;
3. rely on Git history for the detailed implementation plan and removed text.

Do not restore historical plan bodies merely for provenance; they introduce
obsolete schemas, routes, tools, and compatibility behavior into normal agent
searches.
