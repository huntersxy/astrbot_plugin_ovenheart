# Changelog

## 0.52.0

- Share frozen native direct-turn sender/message/quote context between habit selection and final generation, with bounded existing same-scope recent turns. Reuse editable participation system/user templates in temporary tail content for native @, bot quotes and name wakes; retain host prompts, history, multimodal inputs, tools, conversation and runner.
- Keep explicit native wakes on the native one-agent path, independent of autonomous mode/confidence/cooldowns/tool lists. Do not add a planner or duplicate memory/model stage. Create an application scope when needed even with learning paused or quote-only input; do not collect empty/media-only inputs as learning evidence.
- Restore the native direct-streaming option, replacing 0.51.1's forced full buffering. The host decorator skips streaming results, so intercept only the current OneBot event's adapter call and lazily correct a bounded possible leading address prefix. Preserve traced iteration, fallback flags, control/media chunks and native adapter results; restore the original adapter before generation so independent tool streams do not recurse. Close both guard and native source on cancellation. Autonomous generation keeps its existing buffered stale-plan checks.
- Preserve existing structured mentions and normal inline content. Keep this correction limited to separated leading prefixes; do not promise semantic addressee inference or repair already delivered output. Direct debug context now distinguishes at/quote/name/native routes. No host edits, database schema, settings, dependencies or extra ports.
- Validate native wake/process stages with simulated agents for @ and bot quotes, unchanged tools/media/history, quote-only input, streaming prefix splits through native RespondStage, incremental delivery, adapter restoration/cancellation and existing address/follow-up/name-wake paths. Real QQ delivery, provider/tool execution, live audio and other runner/platform combinations remain unverified.

## 0.51.2

- Extend the default planner prompt with context-dependent playful jealousy toward an established close member interacting with another AI, more active participation when the bot is genuinely mentioned, and suitable meme/banter participation. Resolve relationships from existing identity/context and names from identity/configuration; hardcode no member or bot names.
- Pass existing bot aliases and the actual reply-confidence threshold to planner templates and structured context. Explain that an executable reply requires sufficient genuine confidence; otherwise wait for expected information or observe. Preserve the existing admission threshold, native wake switches and model-call budgets.
- Remove the speaker-type-specific rule that discourages responding to another AI's reply. Keep general actual-target selection without that blanket scenario restriction.
- Retain custom planner text on upgrade. Restore the planner default in the workshop to adopt the new wording; add optional `bot_names` and `reply_threshold` placeholders. No new configuration or database fields.

## 0.51.1

- Bind ordinary group replies to a temporary snapshot of the actual incoming sender/message, and autonomous replies to their validated target. Convert separated model-written leading address prefixes to a real mention of that target, deduplicate existing correct mentions and preserve enabled native quote decoration. Keep inline mentions, literal code/email examples and existing structured At components unchanged.
- Buffer OneBot group direct/name-triggered replies before the local agent reads its streaming flag, allowing correction before delivery without another wake or model call. Preserve stable system/history prefixes and use temporary tail context for the current target. Debug `direct_reply_target` and `reply_addressee` expose the actual target and corrected model prefixes.
- Explain in learning, review, selection and application guidance that delivery mentions/quotes are not transferable language or behavior habits. Supply structured delivery rules to extraction/review and final requests even with retained custom templates. Keep existing learned entries and manual decisions; this does not automatically purge old habits or guarantee model compliance.
- Pass focused native-SDK cases for stale direct/autonomous addresses, decoration deduplication, native wake/name handling, preserved inline literals/components and learning-review format repair using simulated providers. Production QQ delivery and planner target quality remain unverified.

## 0.51.0

- Add bounded periodic document-index maintenance to the existing scheduler. When application/non-lexical embeddings are enabled, visit one eligible native scope at most once per minute, rotating by a single stable scope-ID cursor. Reuse existing one-batch/64-document warming and its two-second vector-stage budget, source permissions, current profile and persisted content-addressed vectors.
- Skip known learning, warming, vector-lock, selector, rerank or participation activity. Filter application-disabled profiles and the session allowlist before rotation; recheck native host/session/plugin access in the warm pass. Advance cursor/deadline before attempting, so failure/denial cannot spin. Existing write/startup warm triggers remain separate and may add passes.
- Keep maintenance independent of the learning switch: existing active habits can prepare while extraction is paused. Fully cached passes make no document calls; no query embeddings, chat generations, source consumption, exposure or new database/configuration fields are introduced. Warming can advance configured document-service usage before a reply; foreground indexing remains available.
- Pass two focused scheduler/native-SQLite cases and the existing committed-learning warm-failure case using simulated time/providers. Confirm 130 documents progress through 64/64/2, completed-cache reuse, no queries/chat/source consumption, scope rotation/allowlist and busy-selector skips. Production coverage/latency and actual service cost remain unverified.

## 0.50.1

- Prioritize literal definitions mentioned in the actual human reply body over terms appearing only in its inbound quote. Apply this stable priority after nesting resolution/scoring and before count truncation, so a strongly rated quote term cannot occupy the only slot ahead of the word actually asked about.
- Bind the optional internal `literal_focus` to the actual autonomous target or direct event body, never rewritten provider prompts, history or planner intent. Without a body match, preserve quote recall; without explicit focus, preserve existing caller behavior. Keep relative scoring within each source class and all scope/status/character budgets.
- Report actually included body-match IDs as `literal_focus_ids` and explain mixed-source ordering in retrieval diagnostics. Keep native prompt, target, message IDs, quote handling, temporary delivery, model stages and cache prefixes unchanged.
- Pass one native-SDK injection case and one selector/quote-only retrieval case with simulated providers. Confirm low-rated body terms precede stronger quote terms, a selector cannot displace the literal body match, quote-only anaphora still works and no extra calls occur. Production response quality remains unverified.

## 0.50.0

- Resolve nested literal vocabulary matches by actual normalized-text occurrence before applying count limits. Process active terms longest first; suppress a short definition only when every valid occurrence is fully contained in a strictly longer matched term. A separate occurrence keeps the short term eligible, while equal terms and partial overlap remain.
- Reuse the existing width/case normalization and ASCII boundaries through optional match-span output from the literal matcher. Inactive or absent longer entries do not shadow active terms. Remove fully shadowed IDs from ordinary selector output too, then prioritize surviving literal definitions through existing scoring, status and budget checks.
- Add debug `shadowed_jargon_ids` and a readable retrieval explanation. Keep native scope eligibility, actual target/quote input, existing cache prefixes and all model-call budgets; history, adjacent additions and planner intent do not force literal definitions. This is mechanical nesting resolution, not general word segmentation or semantic disambiguation.
- Pass two focused cases plus both existing empty/invalid-selector definition paths with simulated models. Cover a strong short fragment, an explicit selector choosing it, independent occurrences, disabled longer terms, equal entries, partial overlap and normalized ASCII boundaries. Production response quality remains unverified.

## 0.49.0

- Add per-speaker literal terminal-punctuation summaries to existing language features, still requiring at least four eligible samples for that speaker. Include up to six patterns, deterministic frequency/text ordering, source message IDs and counts, plus the count of other patterns. Keep exact punctuation without case/width normalization or inventing style samples.
- Distinguish messages without recognized terminal marks from omitted summaries. Omit possible 1,200-character capture-boundary truncation and punctuation runs longer than 12; record their source IDs rather than interpreting missing endings as a style preference. Recognize literal trailing comma/period/question/exclamation/semicolon/colon/ellipsis/tilde/em dash forms only; no closing-quote removal or emoji classification.
- Explain in the learning prompt that summaries are descriptive aids, not new evidence, personality/emotion inference or mandatory punctuation. Keep raw observations, per-candidate evidence validation, grounded samples, review/activation, speaker boundaries and vocabulary-stage structured feature clearing. No new model stage, switch or schema.
- Pass two focused native-SDK cases with simulated models covering separate speakers, target/blacklist/quote exclusion, grounded punctuation samples, deterministic bounds and omission handling. Production learning quality remains unverified.

## 0.48.0

- Carry up to four same-member adjacent turns beside the actual autonomous reply target, using only the 20 messages offered to the planner. Stop each direction at another member, a self turn or more than 30 seconds from the target. Prefer nearby turns and retain chronology, original sequence/message IDs and bounded 300-character head/tail excerpts.
- Pass trusted `reply_context` to the final temporary context and a bounded head/tail summary to habit retrieval/selection. This preserves short additions/corrections around older targets even when they leave the latest-12 reply context. Keep the original target, native prompt, reply decoration, memory-source binding and literal jargon input unchanged; adjacency is context, not a merged message, evidence or guaranteed topic relation.
- Add an optional `reply_context` template placeholder and default guidance to consider additions/negation without replying to every fragment or changing addressee. Existing custom templates retain their text and receive the structured payload field. No model call, configuration switch, persistent source copy or database migration is added; cached delivery keeps this context temporary.
- Pass two focused native-SDK cases and the existing style-intent path using simulated models. Confirm nearest-four selection, head/tail bounds, selector/final propagation, unchanged target/literal input, time/member boundaries, temporary delivery and call count. Actual group-chat quality remains unverified.

## 0.47.1

- Validate autonomous reply targets against the same latest 20 messages actually sent to the planner, rather than the full retained 30-message window. A guessed sequence from an unseen older turn can no longer admit a reply, quote, mention or memory-source binding to that turn.
- Keep retained context, candidate limits, latest-message fallback for older custom prompts and all call budgets unchanged. Invalid output follows the existing error/observation path; the oldest actually offered target remains valid.
- Pass one focused native-SDK case with a 30-message window, confirming an unoffered target is rejected and the first offered target retains quote/mention decoration. No external provider or QQ delivery is used.

## 0.47.0

- Diversify contextual reply candidates with existing validated document vectors, following MaiBot's relevance-first MMR approach. Consider at most 64 ranked eligible habits before offering at most 24 to rerank/selection; use 0.85 normalized relevance and 0.15 maximum vector redundancy for indexed expression slots only.
- Preserve the strongest indexed expression, original non-expression/unindexed slots, relevance ordering within the chosen expression set, eligibility, literal vocabulary priority and final contextual choice. Keep lexical fallback outside diversification; hybrid/lexical retrieval, planner and learning counterpart recall do not change.
- Reuse vectors from the successful current retrieval, without another embedding/query/chat stage or database schema. Run bounded dot products in a worker thread and retain existing query/rerank/selector caches. Report promoted candidate IDs in debug retrieval and a readable candidate-pool message; promotion is not actual adoption or feedback.
- Pass two focused cases plus existing invalid-vector fallback and stable selector-prefix checks with simulated providers. Confirm a relevant alternative survives 24 near-identical candidates, weak novelty does not displace strong matches, fixed slots survive, and repeated requests reuse caches. Production quality, latency and billing remain unverified.

## 0.46.0

- Cache completed, validated contextual selection IDs in memory for 60 seconds, at most 64 entries with access-order eviction. Fingerprint the effective retrieval scope, full actual selector request, provider instance/model/configuration and offered sample text; valid empty selections can be reused.
- Serve exact hits before remote capacity checks and rebuild selected traits from current metadata. Keep existing permission/profile/status rechecks, literal jargon handling and final reply generation. Move the two-request limit into actual selector calls; do not share in-flight work.
- Never cache invalid/failed outputs or requests cancelled during generation. Check provider identity/configuration again before publication; unknown/unfingerprintable providers retain normal uncached selection. Store only a digest, native provider reference and IDs, not request/configuration text or raw completions.
- Expose `selector_cache_hit` and a readable reuse message. No new setting or model stage is introduced; vector/rerank recall may still run with its own caches. Pass four focused cache cases and three existing selection/prefix checks using simulated providers. Provider KV hits, production latency and billing improvements are not claimed.

## 0.45.0

- Rotate explicit target evidence groups across successful batches when one batch cannot cover everyone. Break same-group ties by the least recently processed speaker in that native scope, retaining the scheduler's own order separately from restored chat chronology.
- Atomically advance a compact per-speaker order only when a batch succeeds, including successful empty extraction. Failed/retried batches retain their source selection and scheduling position. Persist across restart without changing messages, evidence IDs, model payloads or batch/call budgets.
- Add a separate scope/sender/order/timestamp table with indexes for retention and ordering. Expire its rows with the raw-material retention period and clear them with that scope's raw materials; habit snapshots do not rewind operational scheduling. All-speaker and single-target selection remain FIFO.
- Pass four focused SQLite cases for capacity-limited rotation/restart, failure/scoping and expiry/clear, plus the existing native coverage check confirming private scheduling fields never enter model prompts. Production coverage/quality remain unverified.

## 0.44.0

- Let operators mark individual expression samples for removal in the existing library edit form. Commit choices with Save or the existing review actions; checkbox drafts use normal dirty-state protection and can be unchecked before saving.
- Validate exact existing sample text and apply removal/status/wording changes atomically in the owning scope. Stale or malformed requests do not partially approve/edit. Preserve the rule, other samples and evidence; manual locks prevent future reinforcement from readding the removed sample to that entry. Existing snapshots can restore it.
- Keep full sample clearing on actual wording edits, derived-vector invalidation/prewarming and existing prompt/preview checks. Do not create a global word ban or erase evidence/snapshots. Add no model call or database column.
- Pass three native API/SQLite checks and a bounded desktop draft/save interaction with synthetic data; record screenshot/provenance. Mobile and production learning quality remain unverified. Detector findings concern unchanged incumbent fonts/palette documentation.

## 0.43.0

- Balance explicitly selected multiple speakers in bounded learning batches. Order each speaker's oldest eligible messages in groups sized by the configured minimum evidence, select across groups, then restore chronological order for model observations and context linkage.
- Prevent one target's long earlier backlog from monopolizing a capacity-sufficient batch. Preserve FIFO for all-speaker and single-target modes, blacklist priority, scope isolation, quote validation and exact successful-batch consumption. Unselected materials remain pending under existing retention/caps.
- Reuse the same batch size, thresholds, extraction/review stages and per-speaker statistics. No configuration, schema migration or extra model call is introduced. Capacity still limits coverage when target count times minimum evidence exceeds the batch; sampling does not guarantee a learned trait per speaker.
- Pass four focused SQLite/native cases for two evidence-group sizes, default/blacklist behavior, exact consumption and end-to-end speaker/statistics coverage using one simulated extraction call.

## 0.42.0

- Prefer recently unprovided expression references among near-equivalent lexical/hybrid/rerank candidates. Use the caller's existing native-session exposure IDs, a five-percent ranking tolerance and additional lexical/cosine proximity checks without rerank. Do not rotate personality, behavior, jargon, identical wording or clearly lower-ranked styles.
- Perform bounded rotation after remote ranking, preserving rerank/query cache inputs. Retain original relevance scores and expose promoted IDs/reasons in diagnostics. Contextual mode keeps its existing model-guided variety hint and explicit selections.
- Preserve literal jargon priority, application count/character budgets and cache-friendly temporary delivery. No random sampling, extra model calls, settings or persisted feedback is introduced; exposure does not imply adoption or approval.
- Pass four focused cases for native recent-ID input, identical/unrelated text, literal vocabulary and near/far rerank scores with warm-cache reuse. Production phrasing diversity remains unverified.

## 0.41.0

- Supply optional LivingMemory background before participation decisions, reusing its real access/scope/persona policy and existing bridge. Build a bounded query from recent human messages/quotes; pass at most three 300-character excerpts linked to actual message sequences, never treating those links as ownership proof or new evidence.
- Share the existing retrieval deadline between habit and memory recall. Missing services, exhausted budget, errors or timeouts retain recent-message/habit planning without another planner-chat stage. A waited second decision may repeat the memory lookup within its own shared budget.
- Defer personal user-scoped memory until the actual reply target is known; native final-reply binding remains responsible for that path. Respect configured session/global/legacy sharing for other memory scopes. Recheck LivingMemory access, scope/persona policy and instance readiness after search, discarding changed results for both learning and planning.
- Add a planner `memory` placeholder, memory diagnostics and config help. Do not persist planning excerpts or change participation gates, history, learning evidence or tool authority. Configured memory searches may call LivingMemory's services.
- Pass four focused planner-memory scenarios and the existing bridge-policy check using the native SDK, real policy module and simulated engine. Production participation quality/latency remain unverified.

## 0.40.1

- Preserve follow-up eligibility when one direct answer produces multiple native send-stage callbacks. Track the uninterrupted room marker on that event, bound to its native scope, so subsequent parts can advance the answer's existing consumed position.
- Once another room input intervenes, late parts cannot reclaim ownership or consume newer human messages. Preserve each own part in existing bounded context; no additional admission, model call, quota exemption or configuration is introduced.
- Reproduce the three-part direct-answer failure, then pass two focused direct-path checks and one existing autonomous-segment check. This verifies native callback bookkeeping with simulated events, not real QQ segmentation or delivery.

## 0.40.0

- Bridge native `STREAMING_FINISH` notices into existing send-stage self context and exposure recording. Require a native model-request scope, the host send-operation flag, a live permitted event and the same effective persona scope; ignore duplicate notices for the same result.
- Let a completed direct streaming reply inform subsequent participation/follow-up decisions and volatile learning context without forcing buffered output, resending or decorating completed content, or adding model calls. Preserve existing raw-material/evidence exclusions, retention bounds, quotas and exposure idempotency.
- Ignore intermediate streams, missing send/request markers, stopped/disabled events and persona changes. Hosts/adapters that do not emit a usable final notice remain unsupported; completion notices and send flags are not platform-delivery acknowledgements. Autonomous replies remain buffered for pre-send cancellation and decoration.
- Pass seven focused native-SDK simulated cases covering direct follow-up, volatile learning context, duplicate completion and six exclusion boundaries. Production streaming transport remains unverified.

## 0.39.1

- Include old expression sample text in stable known-habit descriptions for extraction templates and payloads, and in the explicit merge target seen by admission review. Do not expose source metadata or treat old samples as new evidence.
- Atomically recheck reviewed target sample text, including empty-to-nonempty changes, before merging. If another update changes only samples, discard the stale result and retain its materials. Metadata-only differences do not invalidate a judgment that used text only.
- Reuse one ID-ordered description list, keeping dynamic support statistics after stable habit fields. Preserve existing model-call counts, source validation and activation rules.
- Pass three focused native checks for successful sample-aware reinforcement and concurrent changes from both populated and empty sample sets. No external provider or production database was used.

## 0.39.0

- Learn optional grounded expression samples alongside abstract style rules: at most three literal spans of up to 60 characters, each from the first 300 characters of the candidate's own cited human observation. Invalid origin fails without consuming the batch; missing samples remain compatible.
- Include samples in existing admission review, lexical/vector/rerank recall, contextual selection and bounded final guidance without another model stage. Fingerprint sample text, exclude author metadata from delivery and prefer the abstract rule if samples exceed the injection budget.
- Add a JSON sample column with provenance. Preserve manual locks and activation gates, clear samples on actual manual/learned wording changes, and restore them with snapshots. Legacy records/snapshots use empty samples; no historical extraction occurs.
- Display read-only samples in library details with existing evidence styling. Document habit/snapshot retention separately from raw expiry. Pass four focused temporary-SQLite/native/simulated-provider checks and inspect the desktop fixture. Mobile and production style quality remain unverified.

## 0.38.1

- Remove the dashboard's latest-1,000 cutoff from compact runtime habit retrieval. Older active habits, including previously cached semantic matches, remain eligible as the library grows.
- Load compact all-status references for learning instead of dashboard detail snapshots, preserving old rejection/disable decisions without reading evidence bodies and histories. Retain actual statuses and writable shared/native merge scopes; source-local jargon stays isolated.
- Keep dashboard detail limits, paginated library browsing, 64-document embedding batches, bounded rerank/selector pools and learning's at-most-80 offered references. Candidate scanning now scales with eligible library size; this is not a claim of complete semantic coverage or guaranteed recall.
- Pass three focused SQLite/native checks with over 1,000 newer habits: old cached semantic retrieval, rejection-preserving consolidation, and shared/isolated scope eligibility. No external service or production database was used.

## 0.38.0

- Carry optional structured `expression_intent` from participation planning through hybrid/rerank recall, contextual habit selection and the final native reply. Support focus, reply act, scene, tone, preferred expression and expression to avoid, without adding a model call or configuration switch.
- Validate six optional text fields, at most 80 characters each and 240 in total; clear intent for observation, waiting and low-confidence replies. Older custom planner completions remain valid. Keep actual human input/quote separate for literal jargon matching and retain the 2,400-character recall budget.
- Expose `expression_intent` in selector and participation prompt placeholders. Cached delivery keeps this per-turn data in temporary user content; it does not become learned evidence, a persistent habit or tool permission. Existing customized prompt bodies are preserved.
- Pass three focused parser, native planning-to-reply and prompt checks with simulated providers, including unchanged chat-call count and cached system prefix. Production style quality remains pending access.

## 0.37.0

- Prewarm active habit document vectors after committed learning, manual approval/content edits, snapshot restore and shared-profile startup. Schedule this derived work independently so failures cannot change successful learning/review results.
- Reuse content-addressed scoped vectors through a document-only indexing pass, without a query embedding, reranker, selector or chat call. Limit one warm job and at most 64 missing documents per pass; skip an already busy retrieval lock and bound the vector phase to two seconds or the smaller configured retrieval timeout.
- Keep lexical/unconfigured/paused application paths idle, preserve native session/plugin gates, and leave partial/failing work to existing on-demand retrieval. Add opt-in `embedding_warmup` diagnostics and disclose that configured document calls may occur before a real reply.
- Extend compact guidance metadata with its source origin for the background gate. Pass four focused document-cache, committed-run failure, approval and busy/overlap checks using simulated providers. No production latency or billing improvement is claimed.

## 0.36.1

- Replace ID-less learning's sender/second/text fallback with a cached per-event identity. Distinct identical arrivals survive equal native/arrival timestamps; repeated hooks on the same event still deduplicate. Existing platform IDs retain native redelivery deduplication.
- Reuse the source helper across capture, room interruption, planning, send-stage context and memory anchor checks. Keep a fallback stable if an adapter later fills the ID, while validating the target's separate native ID/sender/text snapshot before memory binding.
- Accept absent ID attributes and preserve generation-time event identity. Do not fabricate quoteable platform IDs or claim cross-object redelivery detection without a transport ID. No settings, provider calls or database changes are added.
- Pass three focused native cases for collision/hook overlap, capture-to-send consistency and ID-less earlier-target binding with learning off. Production adapter behavior remains pending access.

## 0.36.0

- Retain explicit native quote context with eligible raw observations, so delayed/manual learning and restarts keep the premise of a human reply. Add an idempotent `messages.reply_context` migration that preserves IDs, deduplication and existing rows; no historical quotes are invented.
- Bound quotes to 1,200 characters, with 400-character request excerpts and the existing sixteen-context limit. Source expiry, count eviction and material clearing remove its quote. Ambient peer/self context remains volatile, and quotes never become independent observations/evidence or enter evidence snapshots.
- Centralize normalization and blacklist checks at capture/read time, excluding newly blocked or unknown authors as appropriate. Stored source quotes, rather than stale volatile copies, supply learning context. Previously retained raw quotes are not physically deleted by blacklisting; normal expiry/clear controls still apply.
- Include allowed quote premises in existing bounded learning-reference and LivingMemory batch queries, without extra per-batch memory calls. Update retention help and bilingual documentation to disclose SQLite retention.
- Pass four new persistence/migration/blacklist checks and six related quote/policy checks using isolated SQLite and simulated providers. Production learning quality remains pending access.

## 0.35.0

- Recall old consolidation counterparts using configured embedding/rerank services instead of lexical-only ranking. Keep recent references, stable ID ordering and actual pending/active/rejected/disabled states; the active view exists only for internal retrieval eligibility.
- Build a bounded 2,400-character query from every batch source, retaining head/tail excerpts for long messages rather than letting the first messages hide later topics. Full observations and evidence accounting remain unchanged.
- Contextual mode uses hybrid recall here, without a chat-selector call. Reuse existing vector/rerank caches and retrieval budgets; service failures retain lexical fallback. Refresh references/profile checks after recall and avoid replacing runtime retrieval status.
- Update configuration help and bilingual guides. Pass four focused native checks for an old rejected counterpart, fallback, full-batch query coverage and profile-change cancellation. Production consolidation quality remains pending access.

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
