# Consequential Open Interaction Directions

**Status:** Design analysis, not an implemented runtime contract.

## Problem

The current controller boundary is deliberately closed:

```text
model tool call
  -> typed immutable intent
  -> deterministic domain execution
  -> authoritative outcome
```

This protects physical authority, privacy, replay, and stable conflict
resolution. It also makes the activity vocabulary narrow. `say` permits
free-form words, but speech currently has only `voice` and `whisper` delivery
ranges and no direct effect on nearby characters. Physical interaction is
limited to the closed `InteractionVerb` set. `SOCIALIZE` is an internal timed
activity rather than a general interaction protocol.

Many desirable activities are both difficult to enumerate and consequential:

- dancing changes activity, location, energy, stress, and social observations;
- shouting has intensity, range, audibility, salience, and possible stress or
  attention effects;
- fighting can change position, posture, fatigue, injury, possessions, and
  relationships;
- pickpocketing is unilateral and contested rather than consensual;
- helping, obstructing, comforting, intimidating, teaching, performing, and
  improvising can combine physical and social effects.

Treating all such behavior as semantic description is too weak. Treating
generated narrative as authoritative state is too unsafe.

## Required distinction: open actions, closed effects

The scalable boundary is not a closed list of activity names. It is a closed
list of state transitions the simulation is capable of representing.

An action may be free-form:

> Rowan attempts to distract Morgan with an argument while taking the access
> card from Morgan's coat pocket.

Its proposed effects must still be typed:

- emit speech with a particular intensity;
- enter a contested interaction with Morgan;
- spend energy and time;
- test access to a particular embodied object;
- transfer custody only if the contest succeeds;
- change observable posture or position;
- apply bounded stress, injury, or relationship effects where those state
  models exist.

This changes the extension question from "do we have a tool for dancing?" to
"can dancing be expressed using supported activity, movement, physiological,
social, and perceptual effects?"

Effects that have no authoritative state model cannot be real effects yet.
In particular, the current project has no live injury/health model and no live
relationship/reputation state. Character dossier relationships are descriptive
only. Fighting and lasting social consequences therefore require those domain
models before an LLM can legitimately produce such outcomes.

## Consent, resistance, and participation

A generic interaction must be **unilateral by default**. Consent cannot be a
universal precondition:

- shouting, attacking, stealing, blocking, observing, and deceiving do not
  require the target's agreement;
- a target's lack of consent may create resistance, avoidance, retaliation,
  perception, or legal/social evidence;
- cooperative activities such as dancing together require participation, but
  the initial invitation or attempted lead remains unilateral.

The useful distinctions are:

| Mode | Meaning |
| --- | --- |
| `unilateral` | The actor can perform the behavior without target participation |
| `contested` | The actor attempts an effect that the target can resist |
| `cooperative` | The effect requires one or more participants to join |
| `ambient` | The action affects eligible nearby observers rather than named targets |

Agreement is therefore one possible resolution input, not a gate on creating
an interaction.

## Direction 1: narrative is authoritative

One model receives the scene and invents the complete interaction, transcript,
and outcome. The runtime then writes the described result into state.

**Advantages**

- maximum expressive freedom;
- coherent prose and multi-party scenes;
- very little activity-specific controller design.

**Problems**

- generated claims can contradict geometry, custody, capabilities, timing, or
  concurrent actions;
- the model can invent private knowledge, possessions, injuries, relationships,
  and successful outcomes;
- conflict resolution and causal lineage become prompt behavior;
- retries or provider changes can alter material history;
- it is difficult to distinguish narration from evidence;
- testing becomes evaluation of a stochastic world model rather than the
  simulation's domain rules.

This is unsuitable as the authoritative Stage 0 execution model.

## Direction 2: narrative model followed by a restricted translator model

The first model writes an interaction story or transcript. A second,
tool-restricted model parses it into memory and state updates.

This is materially better than applying prose directly. The translator can
return a strict schema and can be prevented from emitting arbitrary database
changes. It is also attractive because the transcript can preserve subtle
social behavior while the translator extracts structured consequences.

However, a translator does not solve adjudication:

- if the story says the theft or punch succeeded, a parser will normally
  preserve that claim rather than independently determine feasibility;
- the first model can leak private facts into the story, after which the second
  model may convert the leak into public memory or state;
- two stochastic calls compound cost, latency, replay data, and failure modes;
- disagreements between story and translated effects require an authority
  rule;
- unconstrained prose is an unstable intermediate representation.

This pipeline is feasible only if the second stage is treated as an
**effect proposer**, not a state translator. Every proposed effect must still
be validated or adjudicated by domain code. Both model inputs and outputs must
be recorded for replay.

## Direction 3: open action with deterministic effect templates

The controller submits a free-form action description plus structured
participants, targets, duration, intensity, and desired effects. Domain code
selects a configured interaction template and resolves it deterministically.

For example, scenario content could map `dance` to bounded locomotion, energy,
stress, sound, and visibility effects, or map `shout` to an auditory emission
profile.

**Advantages**

- strong determinism and testability;
- effects remain scenario-authorable and bounded;
- unilateral and contested actions can use explicit rules;
- no second model call is required.

**Limitations**

- semantic matching from arbitrary text to templates is itself uncertain;
- templates can become another large activity catalog;
- novel combinations work only when their effect primitives and resolution
  rules already exist.

This is appropriate for common interactions, but not sufficient by itself for
unrestricted activity.

## Direction 4: constrained LLM interaction compiler

The controller submits an open interaction proposal only when no specialized
tool expresses the intention. A separate model receives a purpose-built,
sanitized scene snapshot and proposes:

1. a bounded interaction transcript or public description;
2. typed effect requests from an offered effect schema;
3. a coarse outcome and observable evidence for each effect.

The runtime validates, clamps, rejects, or commits each invocation against
current state. The compiler cannot directly mutate ECS state.

This is the most promising high-freedom direction, provided that the authority
boundary remains:

```text
specialized tool or generic engage proposal
  -> frozen sanitized scene
  -> optional stochastic interaction compilation
  -> immutable typed interaction program
  -> deterministic invariant validation and ordered commit
  -> atomic authoritative commit
  -> privacy-safe perception and participant memories
```

The compiler can decide which supported effects a novel activity plausibly
requests and may choose among admissible outcomes. Domain systems still decide
whether the resulting state transition is legal.

## Recommended hybrid

Use specialized tools for established mechanics and a constrained stochastic
compiler for the residual behavior that the simulation does not model
precisely.

The important optimization is to avoid building a second, disconnected
interaction runtime. Specialized actions and compiled `engage` actions should
share:

- `ActionInstance` identity and lifecycle;
- state-revision and System 1 checks;
- ordered commit and conflict handling;
- domain validation and atomic mutation;
- event, perception, dataset, and memory paths.

The generic path differs only in how its proposed action program is produced.
It should not have its own physics, social rules, or state mutation code.

### 1. Keep the `engage` contract minimal and stable

A conceptual controller tool should accept only the information that is
intrinsic to any free-form intention:

```json
{
  "intent": "Shout a warning and pull Morgan away from the doorway.",
  "reference_ids": ["morgan", "doorway"],
  "reason": "private controller reason"
}
```

`intent` is an attempted behavior, not an outcome. `reference_ids` optionally grounds
named entities without prescribing roles, consent, intensity, duration,
mechanics, or effects. The compiler infers those from the frozen scene and
the currently offered resolution capabilities.

Do not add fields such as `mode`, `damage`, `volume`, `skill`, or
`desired_effects` to `engage`. Those fields would turn it into an accumulating
shadow action API. New specialized tools and new domain effects should not
require an `engage` schema migration.

### 2. Make fallback priority explicit

`engage` should be described and prompted as:

> Attempt an embodied intention that cannot be expressed by any other offered
> action tool.

The controller should prefer, for example:

- `say` over `engage` for ordinary speech;
- `interact_with` over `engage` for an advertised physical verb;
- `perform` over `engage` for an established bounded activity;
- `transact` over `engage` for a configured exchange.

This should primarily be a controller policy rather than a growing set of
keyword checks. A deterministic semantic test for whether arbitrary text
"could have used another tool" would itself be brittle.

The compiler may nevertheless return `specialized_tool_required` when the
proposal is fully covered by an existing authoritative path. The application
can reject the fallback and allow a later controller decision to use the
specialized tool. This catches obvious misuse without changing `engage` or
silently bypassing specialized validation.

Track the proportion and content clusters of `engage` calls. When a recurring
cluster deserves higher fidelity, introduce a specialized tool or capability.
`engage` remains unchanged and naturally handles a smaller residual set.

### 3. Compile to domain capabilities, not a universal effect VM

The compiler should receive a generated catalog of what the current scene
can represent. This catalog is separate from the `engage` tool schema and may
grow with the simulation.

The catalog should have two layers:

| Layer | Purpose | Examples |
| --- | --- |
| Capability handlers | Domain-owned operations with their own validation and atomic semantics | emit sound, bounded activity, posture transition, displacement attempt, contested custody transfer, strike attempt |
| Bounded scalar effects | Simple configured changes that do not deserve a bespoke state machine | energy cost, temporary stress, attention salience, relationship delta |

Specialized tools select known capability handlers directly with precise typed
arguments. The compiler selects from the same dynamically offered handlers
when interpreting `engage`. This creates one execution architecture with two
front ends:

```text
specialized tool -> known capability invocation
engage compiler  -> generated capability invocations
                         |
                         v
             shared validation and execution
```

Do not flatten complex mechanics such as custody transfer, collision-aware
movement, transactions, injury, or door topology into arbitrary scalar
effects. Their handlers should retain domain-specific rules. Conversely, do
not create a new top-level controller tool for every harmless activity that
can be expressed through existing capabilities and bounded effects.

The catalog can include:

- capability handlers available for the actor and referenced entities;
- bounded scalar effects supported by the domain;
- each handler's strict argument schema and consequence tier;
- broad magnitude bands and legal ranges;
- which invocations and effects may be grouped atomically;
- public observability options;
- unsupported state categories.

Adding fighting, sound intensity, body condition, or relationship effects then
adds or extends handlers and scalar effects in the catalog, not fields in
`engage`.

Invocation schemas should identify source, subject, target, magnitude band,
duration, and atomic group as appropriate. They must not accept arbitrary
component paths or unrestricted numeric mutations.

### 4. Use one stochastic compilation call

A story-generator call followed by a parser call is unnecessarily fractured.
The first output is untrusted prose, the second model must reconstruct its
meaning, and failures can occur between two stochastic interpretations.

Prefer one structured interaction-compiler response containing both:

- a short proposed scene description or ordered beats;
- a typed `InteractionProgramProposal`.

Conceptually:

```json
{
  "summary": "Rowan shouts a warning and tries to pull Morgan aside.",
  "duration_band": "brief",
  "invocations": [
    {
      "capability": "emit_sound",
      "subject_id": "rowan",
      "magnitude": "loud",
      "atomic_group": "warning"
    },
    {
      "capability": "attempt_displacement",
      "subject_id": "rowan",
      "target_id": "morgan",
      "magnitude": "small",
      "atomic_group": "pull"
    }
  ],
  "observability": ["audible", "visible"]
}
```

Provider-native structured output or tool calling should perform the parsing
boundary. A second LLM is not needed merely to translate prose into JSON.

### 5. Accept stochastic outcomes, preserve invariant correctness

The generic path represents low-fidelity behavior. It does not need false
precision from elaborate deterministic formulas for every dance, argument,
struggle, or theft attempt.

The compiler may randomly choose among outcomes that the current scene and
effect catalog permit. For example, it may propose that a distraction partly
works or that a shove fails. This is acceptable nondeterministic input in the
same sense as an ordinary live-provider decision.

The hard boundary should be **validity, not repeatability of a fresh live
call**:

- referenced entities and state must exist;
- conservation, custody, capacity, topology, and range invariants must hold;
- numeric effects must stay inside configured bands;
- unavailable state models cannot be invented;
- commits must remain atomic and ordered;
- recorded model output must replay the same run.

This avoids spending substantial complexity on precise contests whose inputs
and physical/social models are themselves low accuracy.

For high-consequence operations, domain code may narrow the admissible outcome
set before the model chooses. A pickpocket attempt cannot succeed if there is
no reachable embodied object; a strike cannot cause injury until injury state
exists. Within the admissible set, stochastic success or failure is reasonable.

### 6. Validate an interaction program, not isolated prose claims

The compiler output should contain capability/effect groups:

- all effects in a required atomic group commit together or not at all;
- optional descriptive or perceptual effects may survive the failure of a
  separate material group;
- unsupported effects are explicitly rejected;
- narrative delivered to characters is reconstructed from the committed and
  failed groups, not copied blindly from the proposal.

This avoids both bad extremes:

- rejecting an entire rich interaction because one minor effect is
  unsupported;
- accepting a transcript that claims material success after the corresponding
  state mutation failed.

One bounded repair round may be allowed when the program is structurally
invalid. Volatile-state conflicts discovered at commit time should normally
produce an explicit interaction failure rather than another model call.

### 7. Treat other characters as affected agents, not consenting APIs

An initiating controller can unilaterally target another character. Immediate
effects are resolved from the scene, effect catalog, and bounded compiler
choice. The target does not need to approve creation of the interaction.

The compiler should not freely author the target's private beliefs, long-term
decision, or future cooperation. It may produce only:

- unavoidable perception or physiological effects;
- a bounded immediate reflex when the domain permits one;
- a contested outcome among admissible results;
- public behavior needed to make the committed interaction coherent.

The target receives observer-specific evidence and can choose its own next
intent on a later cognition turn. Longer cooperative behavior requires that
participant to continue it through its own controller decisions.

### 8. Keep narration downstream of validation

For high-integrity runs:

```text
engage -> interaction program -> validation/commit -> grounded narration
```

The compiler's proposed summary is research/debug material until resolution.
Character perception and memory should use grounded descriptions derived from
committed effects and explicit failures.

This does not require polished deterministic templates for every scene. A
second optional narration call may render committed results when natural prose
is important, but it is presentation only and cannot add effects. Most runs
can use the compiler's text after removing or correcting uncommitted claims.

### 9. Preserve privacy-separated products

Do not create one omniscient transcript and give it to every participant.
Maintain:

- an admin/research interaction record;
- authoritative public events;
- observer-specific perceived facts;
- participant-specific memories derived only from what each participant could
  perceive or privately experience.

The compiler should receive the minimum scene needed to resolve the
interaction. Private controller reasons, unrelated memories, prompts, and
hidden plans should remain excluded. If private participant state is required
for resolution, domain code should expose a typed derived factor rather than
raw private text.

### 10. Replay stochastic compilation

The project already records and replays model turns by request hash. An
interaction compiler can use the same provider-neutral pattern. Its request
must include the frozen scene revision, references, and generated resolution
catalog version.

Record separately:

- initiating proposal;
- compiler request and model turn;
- interaction program proposal;
- validation/rejection decisions;
- committed effects;
- grounded narrative;
- observer-specific deliveries and memories.

Provider failure must fail or defer the open interaction explicitly. It must
not silently convert a consequential interaction into a successful cosmetic
one.

Replay, rather than deterministic regeneration from a live provider, is the
reproducibility contract for generic interactions.

## Optimized interaction flow

```text
1. Character controller chooses exactly one intention.
   - Use a specialized tool when it fits.
   - Use engage only for the residual case.

2. Application validates the stable engage envelope.
   - intent is bounded text
   - reference IDs are known/observable as policy requires
   - actor is eligible and has no conflicting action

3. At the global barrier, freeze a sanitized interaction scene.
   - authoritative spatial and public state
   - typed private factors only where resolution needs them
   - current resolution catalog

4. One model call compiles an InteractionProgramProposal.
   - proposed beats/summary
   - typed capability invocations and bounded effect groups
   - coarse stochastic outcomes
   - observability hints

5. Application/domain validators produce an immutable executable program.
   - reject unsupported capabilities and impossible references
   - clamp bands
   - preserve atomic groups
   - optionally reject as specialized_tool_required

6. Queue one normal ActionInstance.

7. Ordered systems revalidate and commit effect groups.
   - stable conflict order
   - explicit completed/partial/failed/cancelled outcome

8. Project committed evidence.
   - public events
   - observer-specific perception
   - participant-specific memory
   - research interaction record
```

This keeps the generic path broad at the intention boundary and narrow at the
state-mutation boundary.

## Consequence tiers

Not all generated effects require the same authority burden:

| Tier | Examples | Resolution |
| --- | --- | --- |
| 0: expressive | gesture, facial display, harmless performance description | Validate locality and emit perceptible evidence |
| 1: bounded personal | dancing fatigue, shouting effort, temporary stress | Allow stochastic magnitude bands, then clamp to configured limits |
| 2: interpersonal | intimidation, comfort, distraction, contested movement | Choose among admissible outcomes and emit asymmetric evidence |
| 3: material | theft, restraint, object damage, injury | Require an explicit domain model, legal outcome set, and atomic commit |
| 4: structural | doors, topology, transactions, ownership, incapacitation | Prefer dedicated typed systems; never accept narrative-only resolution |

This permits early breadth without granting the compiler unrestricted
authority.

## Examples

### Shouting

Extend speech from a string channel to an emission profile: vocal effort,
source intensity, maximum range, occlusion behavior, and salience. Domain
hearing sweeps determine recipients. Configured bounded effects may increase
speaker fatigue and listener stress or attention based on distance and
perception. The model supplies words and intended manner, not the recipient
list or guaranteed reaction. Ordinary speech should continue to use `say`;
`engage` is appropriate only while the intended shouting behavior has effects
that `say` cannot express. If shouting becomes common, extend or specialize the
speech tool without changing `engage`.

### Dancing

Before a dedicated activity exists, solo dancing can compile from `engage`
into a Tier 1 activity with a coarse duration, local movement constraints,
energy use, stress effect, sound, and visible evidence. Partner dancing starts
as a unilateral invitation or physical lead; joint continuation requires
cooperation or a contested contact result. The compiler may vary style,
duration band, and bounded effects, while domain code owns occupancy, legal
movement, clamping, and interruption. A later `perform` capability can absorb
the common case without changing the fallback contract.

### Fighting

A generic fight can be compiled into one bounded exchange or a short sequence
of unilateral and contested effect groups. The compiler may stochastically
choose misses, contact, retreat, or escalation from the legal outcome set; it
does not need a falsely precise combat formula. Authoritative injury still
requires body condition, pain or injury, contact, forced movement, and
incapacitation contracts. Until those exist, the legal set may include threats,
shouting, approaches, fatigue, and harmless contact, but not invented injury.

### Pickpocketing

The attempt does not require consent. Success requires an embodied target
object, current custody/exposure, reach, free-hand capacity, and a legal atomic
custody transfer. The compiler may choose stochastic success, failure, or
detection from the admissible set rather than relying on a high-precision
contest formula. Observers receive evidence only if they detect relevant parts
of the attempt. The target may discover the loss later through authoritative
perception or inventory checks.

## Direction

Adopt the stable-fallback compiler hybrid:

1. add `engage(intent, reference_ids, reason)` as a last-resort unilateral
   controller intention and keep that schema deliberately unchanged;
2. make specialized-tool preference explicit and measure recurring fallback
   clusters rather than hard-coding semantic routing rules;
3. compile one fallback proposal with one stochastic structured model call;
4. generate the compiler's resolution catalog from current domain
   capability handlers and bounded scalar effects;
5. validate legal outcome sets and invariants, but allow the compiler to choose
   coarse random outcomes within those sets;
6. execute compiled programs through the existing action, ordering, event,
   perception, memory, and replay machinery;
7. add auditory intensity and bounded generic activity effects first, then
   contested object access, live social state, and body condition as their
   required domain models become available.

This preserves Stage 0's core rule—providers propose and domain systems decide—
without demanding high-precision adjudication from a low-fidelity simulation.
The generic intention remains broad and stable; fidelity improves by expanding
the downstream resolution catalog and by moving recurring cases into
specialized tools.
