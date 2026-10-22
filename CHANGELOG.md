# Changelog

## 0.34.1

- Cache validated rerank scores in memory for five minutes, with at most 64 entries and access-order eviction. Fingerprint the effective scope, ordered habit IDs/text, actual query and non-secret model/endpoint/options; reset on provider-instance replacement.
- Rebuild hit results from current habit metadata rather than cached trait copies. Serve warm hits before checking remote capacity/deadlines, while retaining the two-request limit for actual provider calls. Expose `rerank_cache_hit` and a readable reuse message in retrieval diagnostics.
- Never cache failed, invalid or cancelled outputs, and prevent old in-flight providers from publishing after replacement. Existing native/scoped application rechecks, selector calls, learning and activation rules remain intact.
- Pass four focused reuse, input/expiry invalidation, failure/cancellation and provider-replacement checks. This is a local rerank-result cache, not evidence of chat-provider KV hits, production latency or billing improvements.

## 0.34.0

- Integrate the optional LivingMemory reply-source binding API when autonomous replies answer an earlier member. Supply an actual native event anchor after checking source ID/text/sender, effective scope and host permissions; do not rebind the triggering event.
- Keep at most 128 bounded runtime source references independently of learning. The paired LivingMemory local build uses one source snapshot for recall and reflection, rechecks its own policy and a live-admission guard, and skips requested missing/invalid sources rather than reading the wrong trigger user's memory.
- Preserve unbound/current-target requests, existing native memory calls and learning-batch recall. Older LivingMemory builds retain their native behavior with opt-in diagnostic guidance to install the paired adapter. No new configuration or runtime dependency is added.
- Pass three focused OvenHeart source/legacy checks and seven paired LivingMemory binding checks, plus one existing native recall check. No external provider, production memory database or real QQ acceptance was performed.

## 0.33.1

- Bind autonomous request prompts to the actual planner-selected message instead of always sending the latest triggering input. Preserve the full original input for current-message targets; older targets retain the planner's existing 1,200-character bound.
- Include explicit `reply_message` data with sender, text and inbound quote in temporary participation context, even when the target falls outside the final twelve-message window. Add the editable placeholder and explain how trigger-event sender metadata differs from the reply target.
- Keep native conversation binding, history reload, access checks, source identity during generation and existing send-stage decoration intact. Add the request prompt to opt-in injection diagnostics, without extra calls or settings.
- Pass three focused native checks for older-target binding, untruncated current inputs and out-of-window quote preservation through native request collection. LivingMemory runtime query/scope still follow the original event; this change does not rebind that separate integration.

## 0.33.0

- Use configured embedding/rerank recall before participation planning instead of lexical-only habit ranking. Lexical mode remains lexical; contextual mode uses hybrid candidate recall here because the planner itself judges applicability, without a second chat-selector call.
- Reuse existing scoped habit vectors, query caches, provider concurrency and retrieval budgets. Keep authored profile/character limits, literal local vocabulary from recent human text/quotes and all participation gates. Recall failure falls back through the existing lexical path.
- Recheck scope/profile/settings after recall, omit edited or disabled candidates and preserve cancellation of interrupted work. Keep planner recall outside runtime retrieval status and exposure accounting. Add `planner_retrieval` diagnostics with candidate/included IDs and fallback reasons.
- Clarify that recalled habits do not force participation. Update bilingual guides to disclose configured recall calls and their separate latency budget; no settings or runtime dependencies are added.
- Pass five focused native planning checks with a crowded SQLite habit library and simulated embedding/rerank providers: semantic access, fallback, lexical mode, pause cancellation and mid-recall disable. Production participation quality remains pending access.

## 0.32.1

- Retain nonempty single-character human responses such as acknowledgements, questions and Unicode emoji. Previously the two-character capture minimum silently removed them from learning, timing statistics and linked quote/self context; participation already accepted them.
- Keep whitespace, command, self-message, blacklist, selected-speaker, evidence and activation rules intact. Default extraction/review prompts require context for terse responses and reject inferring personality, full agreement or local definitions from assent or repeated symbols alone.
- Count these responses toward existing batch thresholds and language statistics without adding per-batch model calls or settings. This can make existing scheduled batches eligible sooner.
- Reproduce both all-speaker and selected-speaker failures, then pass the two focused native capture-to-review checks, including quote/self linkage, statistics and excluded sources. Actual semantic interpretation remains pending production evaluation.

## 0.32.0

- Supply native send-stage self replies as volatile learning context, including ordinary conversations with autonomous participation disabled. Require an eligible captured human anchor and a matching native model-request scope; support both all-speaker and target-speaker learning.
- Mark own and explicitly quoted own text with `self=true` throughout extraction/review. Keep it outside observations, evidence IDs, source counts, stored raw materials and exposure accounting. Default prompts forbid learning self wording or treating human assent as endorsement of the bot's preferences or conclusions.
- Reuse five-minute, 64-entry, 128-scope cache bounds and existing neighbor/request budgets. Recheck settings/profile access, reject expired or cleared anchors and persona mismatches, and deduplicate repeated callbacks. Streaming paths without a native send callback are not reconstructed from generated text.
- Add opt-in `learning_self_context` diagnostics and update bilingual documentation without extra model calls or configuration. Eight focused native learning checks and two existing neighbor/participation checks pass; real-platform learning quality remains pending access.

## 0.31.1

- Preserve stored capture timestamps in extraction/review observations and automatically supplied review neighbors. Previously learning omitted timing, and review could associate adjacent rows from distant periods.
- Limit inferred review neighbors to the existing five-minute context radius, excluding missing-time records while preserving explicit quote links and evidence IDs. Default prompts distinguish temporal proximity from actual reply relationships and allow independent repeated evidence across periods.
- Identify timestamps as Unix capture times, not original send times or proof of response speed, availability or mood. Keep dynamic timing after stable known-habit fields; add no calls, settings, schema migrations or new data retention.
- Pass five focused learning/review checks covering both extraction stages, review propagation, gap and missing-time exclusion, quote preservation, context limits and existing associations. Production learning quality remains pending access.

## 0.31.0

- Add an explicitly requested model example beside the existing free guidance preview. Use the saved profile, active habits, source persona and current conversation provider, with configured retrieval and the same bounded system/dual/cached assembly as runtime replies.
- Keep examples outside platform sending, tools, real history, learning materials and exposure accounting. Exclude other plugin injections, disclose possible retrieval/model costs, and preserve runtime retrieval status. Examples may populate existing embedding caches.
- Serialize model previews, bound the generation stage to 90 seconds and discard outputs after relevant settings, persona, provider or selected-habit changes. Render output as plain text and protect drafts from late results; redact raw provider failures.
- Pass five focused backend checks, two frontend checks and two existing delivery checks. Inspect desktop/mobile layouts with a local simulated model; do not claim production quality or real-provider performance. Update bilingual guides and screenshots.

## 0.30.0

- Place ID-sorted stable known-habit fields before changing learning-batch context. Move support counts/refinement stages into ID-linked `known_trait_stats`, preserving information and per-category filtering while keeping count updates out of the earlier text prefix.
- Preserve the original task/system text during extraction and review format correction; append correction instructions and diagnostics at the request tail. Keep retry limits, deadlines and evidence checks unchanged.
- Add an editable statistics placeholder and verify three focused native request cases for learning-prefix stability and both retry paths. No provider calls were made to measure cache hits or billing.

## 0.29.0

- Allow explicitly requested, reviewed refinements of unlocked personality, expression and behavior entries using optional boolean `refine` with a known `merge_id`. Ordinary paraphrases continue reinforcing evidence without rewriting stored wording.
- Reuse independent-batch refinement stages, existing snapshots and admission review. Preserve human locks, reject unreviewed/disabled-review updates, and keep old active content in review mode or below automatic confidence thresholds.
- Include refinement intent in reviewer inputs/audits without adding provider calls, schema tables or settings. Older outputs without the flag remain compatible; jargon behavior is unchanged.
- Pass eleven focused parser, SQLite and native learning/review checks covering refinement, stages, manual locks, policy/confidence boundaries and the end-to-end submission path. Actual learned phrasing quality remains pending production evaluation.

## 0.28.1

- Use the existing shared jargon-term normalizer during stored definition refinement, matching extraction validation for quoted, nested-quoted and full-width/ASCII names. Previously these valid same-term merges accumulated evidence without advancing the refinement stage or updating the definition.
- Preserve explicit-merge/exact-alias requirements, independent-batch thresholds, review/activation rules and manual locks. Do not perform automatic spelling-similarity merges or rewrite existing records on upgrade.
- Reproduce three parser-to-SQLite failures before the fix, then pass those cases plus existing refinement/manual-edit checks. Ruff passes; no model or platform calls were required.

## 0.28.0

- Query LivingMemory from the selected learning batch, using up to eight evenly spaced source excerpts within the existing 500-character query budget. Preserve a single recall per batch and all existing retrieval limits.
- Keep bounded real sender-event anchors and select a batch member's event for access checks, avoiding unrelated latest-message identities. Reject rebound sender anchors and clear scope anchors with material deletion or plugin reload.
- Preserve LivingMemory scope/alias/persona policy. In user scopes, query only the real anchor user's observations and link returned context to their evidence IDs; review excludes unrelated linked memories.
- Add opt-in batch-memory diagnostics. Verify batch queries, actual backlog identity, bounded sampling, existing scope/whitelist behavior, mismatched users, rebound events and review filtering with two focused SDK checks using the real policy parser and a simulated search engine.

## 0.27.1

- Recheck autonomous admission liveness and current allow/deny policy at native tool-name resolution in both schema modes. Superseded or paused requests stop and clear pending results instead of dispatching an initially allowed tool from an obsolete request.
- Advance a local participation window marker after workshop profile edits/restores, so queued tool lookup can immediately recognize the change. Common-profile edits continue closing affected participation windows.
- Preserve native tool instances, inactive-tool filtering, skills-like text-only initialization and direct-wake behavior. Add opt-in cancellation diagnostics without logging tool arguments or adding provider calls.
- Verify six stale-admission dispatch cases plus four existing native boundary cases. This guards name resolution; already-started tool effects remain outside its cancellation boundary.

## 0.27.0

- Give contextual selection native-scope expression exposure IDs from its last three send-stage records within ten minutes. Exclude inactive or subsequently updated expressions, and keep recency local even when personality is shared.
- Encourage variety only between equally suitable expressions. Reuse remains allowed; vocabulary facts, personality and behavior rules are unaffected, as are lexical/hybrid ranking modes.
- Reuse existing application records without adding model calls or stored reply text. Place dynamic IDs after stable selector candidates, expose them in retrieval debug output and add an editable selector placeholder.
- Verify three focused checks for native send-stage accounting, scope, expiry, edits, stable default prefixes, valid offered IDs and permitted reuse. Production repetition/naturalness remains unverified.

## 0.26.0

- Supply explicitly linked inbound quote context to both learning extraction and admission review, for all-speaker and target-speaker learning. Distinguish quoted authors from responders instead of relying solely on neighboring message order.
- Keep quote text volatile and scope-bound, outside raw-material and evidence counts. Reuse existing cache/request bounds and prioritize explicit reply links within the sixteen-item context limit, without adding model calls.
- Exclude blocked quoted authors and unattributed quotes when a blacklist is active. Preserve existing context expiry, source eligibility, settings-change cancellation and clearing behavior.
- Verify six focused SDK cases for native text/chain quotes, review linkage, evidence/storage boundaries, blocked or unknown authors, expiry and foreign scopes. Production learning quality remains pending access.

## 0.25.0

- Add configurable conversational follow-ups, enabled by default. The first uninterrupted continuation from the just-addressed member within 120 seconds can bypass the minimum-message gate and cooldown, while retaining input debounce, planner choice, hourly quota, scope, permission and tool boundaries.
- Consume the early opportunity after observation instead of repeatedly reopening it from an old own-reply record. Restrict early replies to newer messages from the continuing member; disabling the setting cancels pending follow-up admissions.
- Add the switch in the existing autonomous-participation form and native config, plus `is_followup` in editable planner placeholders and structured context. Existing navigation stays unchanged.
- Verify six focused SDK cases and one setting-save check, and inspect the desktop control on local illustrative data. Production dialogue quality remains pending access.

## 0.24.0

- Buffer autonomous generation through the host's native per-event streaming override. This preserves the normal stale-result guard, real quote/mention decoration and after-send context path that AstrBot's streaming branch skips. Direct wakes retain their streaming settings; normal host segmented sending remains available.
- Retain a first direct send-stage reply in a bounded session/persona participation window. Own replies identify the bot and the answered message, so later planning can distinguish already-answered questions from new input.
- Give own replies distinct sequences without cancelling further segments from the same current admission. Exclude own text from new-human-message thresholds and retain newer human inputs when a previous reply reaches the send stage.
- Verify native Agent streaming configuration and the focused continuity paths with five SDK checks; broader production behavior remains pending user-provided access.

## 0.23.1

- Ground habit retrieval and literal local-vocabulary matching in the actual autonomous reply target and its inbound quote, rather than the latest unrelated group message. Direct replies also use native quote text or plain-text chain parts.
- Include quotes in the planner's lexical habit lookup. Prioritize reply-source text, bounded quote text and the reply goal within the existing 2,400-character selector query budget; unrelated history uses the remaining space.
- Keep literal vocabulary fallback independent of style selection, unrelated chatter and planner-generated intent. Reuse existing retrieval calls and preserve native session/persona vocabulary boundaries.

## 0.23.0

- Carry incoming quote text through autonomous participation planning and final reply injection, including quotes outside the live window. Prefer same-scope live sources, fall back to native text or plain-text chain parts, and cap each quote at 1,200 characters without querying another conversation or platform history.
- Distinguish quoted authors from current askers in default prompts. External quote IDs remain data and cannot be selected as target sequences. Cache-friendly delivery keeps quote context temporary and out of the stable system prefix.
- Verify the quote path with three focused SDK checks and Ruff; defer wider effect evaluation to production access. Preserve before-fix synthetic diagnostics, including their harness cleanup errors, without treating them as complete successful runs.
- Exclude recorded evaluation transcripts from formatter rewrites so fenced provider output remains verbatim.

## 0.22.1

- Enforce autonomous tool policies at the native dispatch boundary in both schema modes. Remove denied and host-inactive tools from the raw request instead of retaining inactive handlers; a provider hallucinating a hidden name can no longer resolve it through the skills-like raw tool set.
- Keep an actually empty skills-like input tool set truthy only for runner initialization. This preserves text-only replies when the original tool set is absent, empty or entirely excluded, without advertising or retaining blocked tools. Direct-wake tool policy remains owned by the host.
- Convert plain-text mentions only for unambiguous, complete display names. Preserve longer-name prefixes, email addresses, URL paths and fenced/inline code as text; support full-width mention markers and avoid guessing between members with identical names.
- Recheck the incoming 0.22 reply-decoration changes with real native runner/decorator regression coverage.

## 0.22.0

- Let the participation planner choose `decoration` together with the reply target: `none`, `quote`, `mention` or `quote_mention`. The pre-send hook inserts a real `Reply` and/or `At` for the answered message and sender; because the chain is no longer plain text, AstrBot's own static decoration skips it and cannot add a second quote or @.
- Forbid the reply body from writing `@昵称`, full-width ＠, quote markers or message IDs. As a safety net, matched plain-text mentions in the window, the current sender or the inbound quoted message become real `At` components; unmatched names never become fake mentions. Direct guided replies use the same conversion.
- Keep older custom planner prompts working: the request appends the missing `decoration` field requirement, a missing field falls back to the host settings and the 0.20.1 target binding, and wait/observe plans never carry decoration. Malformed values are rejected.
- Add a `reply_decoration` prompt template, Debug `reply_mentions` output and regression tests for real components, host non-duplication, plain-text conversion and custom-prompt compatibility.

## 0.21.1

- Stop a single unusable candidate from failing the whole learning batch. A merge target that is missing, out of range or of another kind, and a jargon candidate merging into a differently named entry, are now dropped like an under-evidenced candidate instead of raising. Because a failed run preserves its sources, the old behavior replayed the same batch on every cycle and a deterministic model answer repeated the warning indefinitely; the remaining candidates now commit and the sources are consumed.
- Back off the automatic scheduler per session after a real failure: the first retry keeps the configured interval, later ones double it up to one hour, and a success restores the normal cadence. Local guards that reject a batch before the model call still retry at the normal interval, so fixing a paused switch or a stale persona draft takes effect immediately.
- Update the merge regression tests to assert that the candidate is dropped and the batch still returns a result.

## 0.21.0

- Add an independent, default-off “Reply immediately when mentioned” switch and a list of bot names/aliases in both Panel Settings and native plugin config. Match case-sensitive substrings in the current message's own plain text, excluding quoted/forwarded content and @ display names.
- Dispatch matching group messages through AstrBot's native direct-wake path once, preserving its current conversation, persona, tools, access checks, rate limits and safety checks. Bypass autonomous message counts, debounce, cooldown, quota and planner calls, including in off/observe mode. Native @/reply/prefix wakes and command handlers keep precedence.
- Recheck mention configuration and session permission before generation and delivery; cancel stale mention results after disabling the feature or removing its matched alias.

## 0.20.1

- Bind autonomous replies to the message the planner actually answered. The planner now returns a `target_sequence` from the recent-message window; the pre-send hook points AstrBot's own quote and mention decoration at that message and sender, so a reply intended for an earlier participant no longer appears under the newest message, such as another bot's answer.
- Reject self-authored, out-of-window or malformed targets. Older custom planner prompts that omit the field keep the previous behavior and fall back to the newest non-self message. Wait and observe plans never carry a target.
- Carry the selected participant into both participation injections and Debug output, and cover earlier-target binding, latest-message fallback, self-target rejection and malformed targets with regression tests.

## 0.20.0

- Replace the autonomous tool-name textarea with a searchable picker backed by AstrBot's current plugin, MCP and built-in tool registry. Add checkbox and filtered bulk selection, refresh, manual name import, disabled-tool labels and retained unregistered names.
- Add independent allowlist and denylist settings. Preserve the empty allowlist default and legacy allowlist names; switching policies retains both lists. Apply exact-name filtering only to the native autonomous request, never activate inactive tools, retain host permission wrappers and preserve the skills_like runner workaround. Direct replies keep their original tool policy.
- Prevent edited prompt templates from publishing superseded learning or selector results, including a persisted-override check at the learning transaction. Publish committed templates even when a client cancels, reject stale prompt forms and unknown reset keys, and protect drafts when switching editors.
- Cover both schema modes, inactive tools, catalog discovery, policy persistence, escaped metadata, refresh preservation and prompt-edit races with native SDK, SQLite and frontend regression tests.

## 0.19.2

- Stop delivering a cancelled autonomous reply twice. The pre-send guard stopped the event but kept the generated chain, and AstrBot's decorate stage returns before the responder when a handler stops the event, so the responder is reached as a sibling stage while the process stage yields more than once — the same chain was sent again. The guard now discards the result before stopping the event, so a reply that lost its admission is not delivered at all and cannot be replayed.
- Cover the discarded result and a repeated guard entry with regression assertions.

## 0.19.1

- Fix the crash that aborted the first autonomous reply on AstrBot 4.29.1 with a skills_like Agent runner: narrowing the allowlist to nothing produced an empty tool set, the runner's reset returned before initializing its stats, and the reply raised `'ToolLoopAgentRunner' object has no attribute 'stats'`. The plugin now reads the host schema mode and, in skills_like only, keeps every entry while clearing `active` on the tools that were not allowed, so the host branch stays on its normal path and the provider still receives no tool schema.
- Keep the previous exact-name filtering unchanged for the default `full` schema mode and for hosts that do not configure the key, where an empty set is handled natively. `skills_like` is selected only when the host explicitly configures it; unknown values fall back to `full` exactly like the native stage.
- Record the effective schema mode and the visible tool names in the `participation_injection` debug payload, and cover the mode resolution, the allowlisted subset and the empty allowlist with regression tests.

## 0.19.0

- Move every prompt this plugin sends into editable templates: participation planner, habit selector, learning extractor, learning reviewer, application style, and both autonomous-participation injections. A dedicated 提示词 Panel tab lists them with a body editor, the shipped default, its accepted variables and a reset action.
- Reference runtime content with double-brace placeholders (`{{persona}}`, `{{recent_messages}}`, `{{candidates}}`, ...). Known references are substituted at call time and non-string values are serialized as JSON; unknown references stay literal so a typo stays visible instead of silently emptying part of the instruction. Structured JSON payloads and the participation goal/context blocks are still appended by the plugin, so a template changes wording, never permissions, tool scope or quota.
- Persist overrides in a `prompts` table with a revision; saving validates length and emptiness, republishes the effective map, invalidates in-flight learning context and participation windows, and keeps `system_prompt` byte-identical across schema repairs.
- Store the rendered planner/selector/learning/review text as the request's system prompt and record it in Debug output, so the audit shows the wording that actually ran. Shipped constants remain the defaults and the fallback, keeping existing module-level names valid.
- Add prompt rendering, override round-trip, reinitialization and frontend editor regression coverage; the Panel now registers six native routes.

## 0.18.2

- Use native AstrBot config as the durable settings source. Workshop saves update the same config object/file exposed by the native editor; native saves and normal plugin reloads become visible in the workshop instead of being masked by old SQLite overrides.
- Migrate legacy Panel settings into host config once, preserving the previously effective values and retaining a database backup. Keep only current-instance changes as transaction guards; preserve profiles, learned entries and histories.
- Refresh untouched settings forms on configuration revision changes and reject superseded submissions, including forms from a previous plugin instance. Serialize saves, preserve runtime publication after client cancellation and reject native write failures without reporting success.
- Verify both directions with real AstrBotConfig persistence and native dashboard services, plus write-failure, reload, stale-form, concurrent-save and cancellation regressions. Update bilingual instructions and Panel save guidance.

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
