# Changelog

## 0.18.1

- Always isolate jargon by its native session/persona, including shared-mode learning, known merge targets, refinement, previews, reply retrieval and delivery accounting. Keep personality, expression and behavior under the existing general isolation switch. Snapshot both affected spaces atomically and identify restore ownership in Panel.
- Move unambiguous, complete legacy common jargon records intact to their sources without changing IDs, decisions, counters or cached vectors. Retain mixed, unknown, truncated or conflicting legacy records for inspection without injecting them or allowing global approval. Show their retained-only status in Library.
- Add native SDK and SQLite regression coverage for conflicting local meanings, mode changes, independent support, atomic merge rejection and idempotent lossless migration. Update Panel guidance and bilingual instructions.

## 0.18.0

- Default to a common authored profile and learning space across permitted conversations. Add opt-in session/persona isolation, retain both spaces and legacy records, and keep raw batches, native histories, volatile context and participation admissions source-scoped. The header selects learning sources in shared mode and local profiles in isolated mode.
- Namespace evidence by its source scope, keep source labels, and recheck mode changes at generation and atomic commit. Preserve sources if all learning categories are disabled during an in-flight batch.
- Default new installations to cache-friendly delivery: keep authored supplements stable in system, append selected habits and participation goals only after history in temporary user content, and place stable-ID selector candidates before changing dialogue. Preserve explicitly configured legacy system/dual modes.
- Add native SDK regression coverage and illustrative desktop/mobile verification. Record a real synthetic provider replay with an identical system/history hash across different selected habits; the second reply reported 1,408 cached tokens out of 1,778 input tokens. This is one observation, not a general cache-hit or quality claim.

## 0.17.0

- Add opt-in Debug mode in Panel and host configuration. Log full plugin system/temporary-context injections, retrieval results, learning and correction requests/completions, self-check audits and participation plans at INFO level. Keep it off by default; never collect provider credentials, headers or arbitrary provider exception messages. Verify exact request/log equality and immediate disabling.

- Add an All conversations Library view, defaulting to pending review, with owner labels, status/category filters before 100-entry pagination and global pending counts. The header conversation continues to control Profile, Start learning and History; review/edit/rating actions use the selected entry owner.
- Add global learning exclusions via bare QQ numbers in Settings and `blocked_senders` in host configuration. Exclude capture, retained pending sources, material counts and volatile neighbor context; reject late batches after blacklist changes and recheck persisted settings within transactions. Keep existing habits and evidence for manual review.
- Verify scoped actions, pagination, draft guards, QQ input parsing and blacklist races using native SDK tests and illustrative desktop/mobile browser checks.

## 0.16.0

- Run the two independent extraction passes concurrently, then review their combined results in stable pass order. Raise the shared background batch budget to 180 seconds after observed review calls exhausted the former 90-second budget; retain per-pass format correction limits and single atomic commit. Disabled categories do not add requests.
- Cancel and await remaining extraction tasks on failure or caller cancellation before recording the failed batch. Preserve all sources and keep self-check from starting on incomplete extraction.
- Add phase timing to the isolated native evaluator and expose its provider timeout as a CLI parameter, defaulting to AstrBot's 120 seconds. Retain the previous 45-second failure and a successful sequential baseline using the host timeout; do not attribute timing differences solely to parallelism or claim a general success rate.

## 0.15.1

- Give the learning reviewer the exact existing target of each explicit merge, including its meaning and conditions, so source-supported but unrelated proposals do not silently reinforce another habit. Preserve compatible paraphrases and evidence-backed local vocabulary refinement.
- Record reviewed targets in bounded learning audits. Recheck their content, conditions and status at the start of the commit transaction; concurrent human changes invalidate the entire late batch without consuming sources. Reject conflicts between existing aliases and explicitly reviewed targets.
- Preserve neighboring requests from the same batch as linked review context, including requests cited by a different candidate, within the existing 16-excerpt and 400-character bounds. Use them to understand trigger conditions without increasing evidence or support counts, or transferring peer identities.
- Restore original stored records after relevance ranking so older rejected or disabled targets retain their actual status in model inputs and transaction checks.
- Add synthetic merge diagnostics through the actual learning reviewer and regression coverage for flagged merges, concurrent edits, and multiple approved proposals sharing a target.

## 0.15.0

- Add configurable learning self-check, enabled by default, using original evidence, source conditions and the identity anchor. The model can only partition fixed candidate IDs; it cannot rewrite candidates or evidence.
- Keep flagged new candidates pending even in automatic activation mode, and ignore flagged updates to existing entries. Human approval remains authoritative. Failed checks preserve the entire batch; extraction, review and their format corrections share the existing 90-second budget.
- Store bounded self-check audits with learning runs, migrate old run records with empty audits, and expose escaped reasons in Panel history and matching library details. Preserve expanded details during automatic refresh.
- Add predeclared synthetic proposal diagnostics with the actual reviewer and a complete native-core acceptance run. Retain a real reviewer timeout showing source preservation, and label simulated UI screenshots separately.

## 0.14.0

- Require jargon names to occur in their cited source messages. Treat malformed term-definition formatting as repairable, but reject ungrounded names without a format retry or partial batch commit.
- Normalize balanced quotes, Unicode compatibility forms and case for term comparison without rewriting stored wording. Use ASCII boundaries to avoid matching abbreviations inside unrelated words, and recall quoted single-character terms without embeddings.
- Preserve active vocabulary directly mentioned in the current human input as factual context, independently of expression selection or selector failure. Keep existing total limits, scope/persona isolation and late-disable checks. Do not use history or planner goals for this direct-match path.
- Retain an actual learned-but-unused single-character failure and the corrected real-provider replay, including a selector timeout with successful local definition delivery. Extend replay records with selector candidates and expected-local-term checks.

## 0.13.0

- Clarify that identity and behavior boundaries guide generation internally, and should not become a recited self-introduction. Preserve the current request's scope and complete detailed explanations when requested.
- Add a controlled application replay that freezes actual learned habits, selected IDs, identity, style and history. Verify exact reconstruction of the original guidance before comparing a single rule change through the native provider.
- Add order-swapped, source-blind diagnostic model reviews with strict score/tie validation. Preserve the incomplete pilot and its contradictory verdict as evidence of critic limitations; do not treat model preferences as independent ratings or a MaiBot win rate.

## 0.12.0

- Preserve LivingMemory's explicit unfiltered legacy scope instead of replacing it with the current session. Keep session, user, alias, global and forced-isolation policies unchanged.
- Add an isolated two-plugin native acceptance runner using the real LivingMemory initializer, SQLite/FTS5/FAISS, free bge-m3 embeddings and an actual chat-model learning batch. Check persona filters, access denial, host/session plugin switches and source-evidence boundaries without real QQ messages or existing memory data.

## 0.11.0

- Include per-sender language rhythm summaries from at least four eligible source messages: median/maximum length, paragraph use, questions and enumerated lists, with original evidence IDs. Keep peers and different speakers separate.
- Learn contextual pacing alongside recognizable expressions without turning sample lengths into fixed reply limits. Keep detailed user requests complete and prevent learned habits alone from adding unrelated advice or checklists.
- Analyze jargon in its own enabled-category pass so rhythm and expression mining do not crowd out explicitly defined local terms. Share a 90-second budget across both passes and their schema corrections; commit and consume sources only after the complete batch succeeds.
- Add a native MaiBot learning-to-reply evaluator, explicit endpoint adaptation and a controlled Planner identity. Retain actual learning, vector retrieval, scoped jargon, Planner reply references and native reply generation without predefined habits.
- Extend fictional replay to diverse expressions and a new local term, with separate detailed-request regressions. Keep whole-bot quality claims outside these limited checks.

## 0.10.0

- Preserve scoped, volatile neighbor context when learning from selected users, so replies retain their conversational conditions without treating peers as learning sources. Keep peer text out of SQLite, evidence and pending counts.
- Recheck current targets when selecting retained batches, counting Panel/scheduler materials and committing delayed results. Keep excluded users' old rows unconsumed for later target changes.
- Bound context to 64 messages in each of 128 scopes, use neighborhoods within five minutes with at most 16 excerpts, and clear it on settings/profile changes, source clearing and shutdown. Restarting does not restore peer context.
- Verify target eligibility, transactional races, scope isolation, expiry and actual native-core capture. Add an optional neighbor-context control to the synthetic lifecycle evaluator.

## 0.9.0

- Keep native command prefixes out of learning after AstrBot's waking stage strips them, by checking both original platform text and processed pipeline text.
- Retain late-arriving context without letting older asynchronous permission/persona work cancel the newest debounce window. Count the complete burst after debounce and keep native arrival ordering.
- Add an isolated real-core acceptance runner using native plugin loading, all nine pipeline stages, authenticated Panel routes, real providers and persistent session history. Replace only platform transport with a local collector.
- Cover stripped-command capture through a subprocess regression and retain native-model replay evidence without credentials or provider reasoning blocks.

## 0.8.0

- Give contextual habit selection the native text blocks from recent user/assistant turns and the current scope's admitted live group conversation. Preserve the current input and reply goal within the existing query budget.
- Exclude tool/system messages, thinking, media references and temporary parts from selector conversation text. Preserve native request contexts without copying another persona's live plan.
- Verify actual native hook ordering and extend provider replay to forward structured history to final generation and retain the selector's exact synthetic inputs.

## 0.7.0

- Consolidate habits only through exact aliases or model-specified scoped targets; remove spelling-based merges that collapsed opposing conditions or meanings.
- Count one independent support per habit per learning run, retaining distinct evidence and averaging same-run confidence before combining batches. Recheck automatic activation using the final aggregate without overriding prior human decisions.
- Keep jargon refinement at independent-batch thresholds even when a response repeats the same word. Add multi-batch native-provider replay with configurable corpus and delivery modes.

## 0.6.0

- Coordinate autonomous admission, cooldown and rolling quota across per-member native sessions in the same physical group. Keep learning, persona and chat bodies isolated.
- Interrupt stale planning and queued replies on permitted group activity, including direct wakes with learning disabled. Ignore delayed older capture and duplicate callbacks; separate groups and bot accounts.
- Migrate decisions with an optional room key while retaining legacy records and scoped limits. Verify shared quota after reload and actual EventBus/PipelineScheduler/ProcessStage interleaving with controlled transport.

## 0.5.0

- Add optional dual delivery for endpoints that fail to follow system-only role/style guidance. Preserve the system prompt and add bounded, temporary context from the current host persona and selected habits; keep system-only delivery as default.
- Mark participation goals and recent messages as temporary native content, keeping them out of native Agent history. Verify serialization, scope isolation, paused application and duplicate hook behavior.
- Add real-provider learning-to-reply replay with source fingerprints, incremental checkpoints and ASCII-safe progress for Windows consoles. Retain pre-fix results and limited response checks; no whole-system MaiBot or real QQ acceptance claim is made.

## 0.4.0

- Add contextual habit selection after bounded recall and optional native rerank. New installations default to selecting 0–5 offered IDs per reply; existing saved retrieval modes remain unchanged.
- Keep the host/manual persona and skip learned entries when contextual selection fails, times out or is busy. Recheck scope, profile, settings and active content after model waits.
- Support native AstrBot rerank providers; validate complete index/score responses and share the vector/rerank deadline. Fix embedding cache invalidation for native model, endpoint and dimension settings.
- Add fixed synthetic selection scenarios, a reproducible real-provider evaluator, and SiliconFlow free-model setup instructions. This is selection-stage evidence, not a whole-bot MaiBot comparison or real QQ acceptance.

## 0.3.0

- Add configurable off/observe/auto participation planning with reply, wait and observe decisions. Debounce bursts, cancel stale tasks, recheck queued requests, and persist cooldown plus rolling admission limits.
- Handoff autonomous replies to the local native AstrBot Agent with conversation/persona/history binding and a per-request tool allowlist. Preserve direct wakes and commands; default autonomous tools to text-only.
- Rename the internal 学习记录 tab to 学习与参与 (same View/URL), add reasons/goals/outcomes and place participation controls first in Settings. Update Chinese/English navigation guidance and screenshots.
- Validate real-model participation on three synthetic scenarios; add native process-stage and conversation/history tests. Real QQ delivery and long-run comparative evaluation remain unverified.

## 0.2.0

- Rename the plugin from `astrbot_plugin_persona` to `astrbot_plugin_ovenheart` (OvenHeart / 烤箱之心), including directory, API namespace and release artifact. The native 人格工坊 View stays in place. Copy legacy SQLite data into an empty new database without changing the original; do not run both plugin IDs concurrently.
- Add hybrid embedding/text habit retrieval with bounded model calls, model/content cache invalidation, lexical fallback and free lexical previews.
- Accumulate independent source evidence, consolidate same-category habits, refine jargon at batch thresholds and preserve manual decisions. Add learning snapshots, reversible explicit ratings and send-stage reference records.
- Fix JSON errors from explanatory text, code fences, reasoning blocks and alternate schemas; add one bounded format correction using the original evidence, while still rejecting fabricated evidence directly.
- Exercise normal learning and correction through a real AstrBot OpenAI provider with cn:deepseek-v4-flash and synthetic messages. No QQ delivery or whole-system superiority claim is made.

## 0.1.0

- Add session/persona-scoped learning of personality tendencies, expression styles, local vocabulary and conversational behavior.
- Add configurable review/automatic activation, source evidence, manual editing, disabling and reversible profile/trait snapshots.
- Add optional LivingMemory integration using its existing access, scope and persona policies.
- Add the native AstrBot Panel entry **人格工坊** with Profile, Library, History and Settings views; no previous entry points were renamed or moved.
- Preserve existing host persona prompts and passive message behavior; bound batches, retries, retention and provider concurrency.
