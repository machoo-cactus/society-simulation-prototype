# Text Content and Character Read/Write Actions

**Owner:** Authoritative in-world text, content endpoints, character reading,
writing, attribution, access control, and deterministic message delivery.

## Authority and representations

In-world text is authoritative simulation state. Books, notes, letters,
documents, posts, news items, messages, collections, addresses, and mailboxes
are not model-provider state and are not ordinary character memories.

Three representations remain distinct:

1. the current text artifact and immutable revision history;
2. a private read receipt pinned to the exact revision a character completed;
3. a provenance-bearing `world.text.read` information document derived from
   that receipt for later character retrieval.

Editing an artifact never changes what a character previously read. A later
read can create knowledge of a newer revision.

## Content endpoints

Physical and logical access entities expose explicitly configured content
endpoints. One object may retain all of its existing physical capabilities and
also expose multiple endpoints.

Examples include a book bound to one immutable artifact, a notebook bound to a
mutable artifact, a notice board bound to a collection, and a phone exposing a
private notes endpoint plus a mailbox terminal. Messages live in logical
mailboxes; phones and computers are access terminals rather than copies of
mailbox state.

Endpoints advertise only safe metadata:

- endpoint ID, label, kind, and supported operations;
- bound artifact or collection ID;
- the required live access mode;
- authorized item headers and current revisions;
- known destination addresses when the character is allowed to use them.

Names, prose, ownership metadata, and generic usability never imply a content
capability.

## Access

An operation succeeds only when both checks pass:

- the artifact or collection policy grants the exact operation to the
  character, one of the character's groups, or one of the character's
  controlled addresses;
- the character has current access through the endpoint's configured physical
  or logical mode.

Physical modes can require an exposed reachable object, a held-or-reachable
object, a held object, or an occupied terminal. Closed containers, lost
custody, movement away from a terminal, System 1 preemption, or policy changes
can invalidate a pending action. `OwnershipComponent`, descriptive
possessions, and custody do not silently grant logical permission.

Proposal-time advertising is not proof of execution-time access. Domain
execution revalidates live ECS state, endpoint binding, policy, expected
revisions, and capacity before commit.

## Artifacts, blocks, and revisions

Text is plain Unicode with normalized `LF` newlines and bounded sizes. It is
never trusted HTML.

An artifact has a stable ID, media kind, behavior mode, access policy, current
revision, and immutable revision history. Its ordered blocks have stable IDs,
individual revisions, a closed block kind, plain text, and optional tombstone
state.

Behavior modes are:

- `immutable`: read-only after creation;
- `mutable`: supports authorized append, replace, edit, and delete;
- `append_only`: supports authorized append but not replacement or deletion.

Delete creates a tombstone revision. Prior bodies remain available only to
authorized private research or operator paths.

## Tools and actions

`read_text` and `write_text` are specialized character-controller tools.
They create embodied actions; neither returns text during the same controller
decision round.

`read_text` selects an advertised endpoint and artifact. Execution pins the
current artifact revision, consumes deterministic simulation time, revalidates
access, and creates a private read receipt on completion. That receipt is
guaranteed in the character's next decision and then remains available through
bounded information retrieval.

`write_text` uses a strict operation-discriminated schema:

- `create` creates one artifact in an authorized collection;
- `append` adds new stable blocks;
- `replace` replaces one stable block;
- `edit` replaces a Unicode code-point range within one block;
- `delete` tombstones one block or the whole artifact.

Existing-content mutations require the expected artifact revision. Block
replacement, editing, and deletion also require the expected block revision.
Collection creation and mailbox delivery require the observed collection
revision.

## Concurrency and atomicity

Text actions commit in stable simulation order. If two characters act from the
same revision, the first stable commit succeeds and increments the revision;
the later action fails with `revision_conflict`. Append does not merge
automatically.

All affected state is candidate-validated before commit. A failed operation
leaves no partial artifact, block, collection membership, delivery, unread
count, or persisted row. Repeating the same operation identity is idempotent.

## Attribution

Every character write retains the authoritative acting character privately.
Reader-visible attribution is a separate policy-controlled projection:

- `verified`: the actor controls the displayed sender address or identity;
- `pseudonymous`: an authorized stable display label;
- `anonymous`: no reader-visible identity;
- `unverified`: a displayed claim explicitly marked as unverified.

Anonymous means anonymous to ordinary readers, not untraceable to simulation
authority. A controller cannot make an attribution verified by claiming it.

## In-world messaging

The initial message contract supports one recipient. Sending is an atomic
`write_text(create)` transaction through an accessible originating endpoint:

1. the actor controls the selected sender address;
2. the destination address is known and resolves to a mailbox;
3. the destination accepts the sender and has capacity;
4. one immutable message artifact is created;
5. recipient inbox and sender sent-mail memberships update together;
6. recipient unread state increments.

The message body is not injected into recipient perception or controller
context. An associated terminal may produce a private metadata-only arrival
notification. The recipient must complete `read_text` to receive the body.

Signal simulation, delays, retries, delivery/read receipts, threads,
forwarding, multi-recipient messages, blocking lists, spam classification, and
external services are outside this contract.

## Events and privacy

Text actions retain the canonical `action.*` lifecycle and add:

```text
text.read_requested
  -> text.read_started
       -> text.read_completed | text.read_failed | text.read_cancelled

text.write_requested
  -> text.write_started
       -> text.write_completed | text.write_failed | text.write_cancelled

text.delivery_requested
  -> text.delivery_completed | text.delivery_failed
```

Ordinary events, snapshots, telemetry, and exports contain safe IDs, operation
names, revisions, hashes, lengths, status, and policy-approved displayed
attribution. They do not contain text bodies, deleted text, mailbox contents,
or the true actor behind anonymous content.

Nearby characters may perceive visible reading or writing activity and the
involved object. They do not receive the text body. Screen peeking, visible
surface text, OCR, attachments, rich text, encryption, and signatures require
separate designs.
