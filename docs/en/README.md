# OvenHeart workshop guide

## Install and open

Requires AstrBot `>=4.29.1,<5` and a configured chat provider. Install the release ZIP through the local plugin installer, or copy the plugin directory to the host's `data/plugins/astrbot_plugin_ovenheart` and reload it. Open the native plugin View named **人格工坊** (Persona workshop).

The view uses AstrBot authentication, its native bridge and light/dark themes. No extra port, frontend build or Node runtime is needed. Opening the HTML directly displays a Panel connection instruction. Version 0.2 renames `astrbot_plugin_persona` to `astrbot_plugin_ovenheart`. Disable the old plugin before enabling the new one. An empty new database imports a consistent SQLite backup from `data/plugin_data/astrbot_plugin_persona/persona.sqlite3`; the original is preserved and existing new data is never overwritten. Panel overrides migrate with the database; host plugin configuration files do not. The plugin directory and API namespace change from `astrbot_plugin_persona` to `astrbot_plugin_ovenheart`; the native View remains 人格工坊 and the page heading becomes 烤箱之心.

| View | Purpose |
| --- | --- |
| 人格底稿 / Profile | Supplement identity, reply style and behavior; pause learning/application for the selected scope; preview effective guidance |
| 学习库 / Library | Review source evidence, edit, approve, reject and disable learned habits |
| 学习与参与 / Learning and participation (formerly 学习记录) | Inspect batches, participation reasons, waits, cancellations and prior versions |
| 学习设置 / Settings | Configure activation, evidence, sources, provider, batch timing and retention |

## Learning and activation

Configure the core persona in AstrBot first. After ordinary text messages arrive, the conversation appears in the scope selector. Each complete unified message origin and effective host persona has a separate learning space. Human-readable conversation names take priority over IDs.

Four categories are supported: stable personality tendencies, conditional expression styles, local vocabulary, and conversational behavior such as listening, clarification and empathy. Source evidence comes from real messages in the current immutable batch. Memory excerpts cannot substitute for source evidence. Human identities and personal histories must not become the bot's biography.

Activation is configurable. The default is manual review. In automatic mode, new candidates meeting both confidence and evidence thresholds become active; other candidates remain pending. Existing rejected, disabled or manually edited entries are not overwritten by identical candidates. Confidence is a model assessment, not a statistical guarantee. Repeated evidence accumulates across batches. The model may consolidate into same-category entries offered in its input; conservative text deduplication is also applied. Manual edits and review decisions lock the entry against automatic content changes. Jargon meanings may be refined at 2, 4, 8 and 16 independent batches; changed meanings return to review unless automatic activation qualifies. Semantic consolidation still depends on model quality.

By default, automatic learning needs 12 pending messages, processes at most 30 per batch and observes a 600-second per-scope cooldown. The plugin runs one learning request at a time. Manual learning bypasses the automatic message threshold and cooldown, but still requires enough source evidence. Provider timeout is 90 seconds; optional memory retrieval timeout is 12 seconds. Failed batches keep their material for retry. A valid empty candidate array consumes the analyzed batch without creating habits.

The learning provider ID can be left blank to use the current conversation provider. Set it to an existing AstrBot provider ID to use another model. No API keys are stored by this plugin. Explanatory text, a single JSON code fence and closed reasoning tags are tolerated. Schema drift receives at most one correction request using the original evidence; each request has a 90-second timeout. Fabricated evidence is rejected directly. The specified cn:deepseek-v4-flash model was exercised through the real AstrBot OpenAI provider using synthetic material, including the correction branch. Pausing during generation prevents late results from being committed. Changing the identity anchor during generation discards outdated results.

The source-user list accepts one user ID per line. The allowed-conversation list accepts complete `unified_msg_origin` values obtainable through `/sid`. Empty lists allow all otherwise permitted sources. The host's allowlist, plugin set, conversation switch and plugin switch still apply. Settings saved in this native view take precedence over the host plugin configuration.

## Retrieval and explicit feedback

Set an existing AstrBot embedding provider ID to enable hybrid semantic/text retrieval. Without it, or on failure/timeout, lexical retrieval remains available. The current input plus bounded recent context forms the query. Ranking combines relevance, evidence accumulation, freshness, explicit feedback and exposure variety. Cached habit vectors are keyed by model and content; the first request indexes at most 64 missing habits and later requests advance coverage. Query vectors remain in a bounded in-memory cache. Free previews use lexical retrieval only and say so.

New installations default to `contextual`: recall up to 24 candidates, optionally rerank, then ask the learning provider (or current chat provider) to select 0–5 applicable IDs. Existing saved `hybrid`/`lexical` settings remain unchanged. The selector receives bounded conversation context, autonomous reply intent when present, and the host/manual identity. It cannot rewrite habits or select unknown IDs. Empty selections are valid.

Contextual mode adds at most one chat-model call per reply with active habits, with a default 12-second timeout. Embedding and rerank share a separate six-second budget. Rerank and contextual selection each allow two concurrent calls without queued work. If selection fails, times out or is busy, that reply keeps the host/manual persona and skips all learned habits. Settings, persona and active entries are checked again after waiting. Free previews remain lexical only.

For SiliconFlow, configure native `openai_embedding` with `embedding_api_base=https://api.siliconflow.cn/v1` and `embedding_model=BAAI/bge-m3`; configure native `vllm_rerank` with `rerank_api_base=https://api.siliconflow.cn`, suffix `/v1/rerank`, and model `BAAI/bge-reranker-v2-m3`. Store keys in the host provider settings and enter only the provider IDs in this plugin. These free model variants were tested through real native providers; avoid the `Pro/` variants when seeking free usage and recheck [official pricing](https://siliconflow.cn/pricing). See [synthetic evaluation](../evaluation/README.md) for reproducibility and limitations.

Library ratings are reversible choices (helpful, less helpful, clear), not inferred user approval. History records prompt references reaching the host send stage, which does not establish delivery or model compliance. Usage counts do not increase confidence. Old habits gradually lose ranking weight without automatic deletion.

## Language rhythm and independent jargon learning (0.11)

Learning now describes each eligible speaker separately once four source messages are available: median/maximum character length, multiline messages, questions and enumerations. These summaries help infer contextual pacing; they are not evidence by themselves or hard reply limits. Other participants' volatile context never contributes to the summaries.

Jargon uses its own category-specific pass so expression/rhythm extraction does not crowd out explicitly defined local terms. With all categories enabled, background learning normally makes two requests; word-only or non-word-only settings make one. Both passes and their one-time schema corrections share a 90-second budget. No partial result or source consumption occurs if either fails, and reply-time model calls are unchanged. Existing identity and style settings are preserved. See [rhythm verification](../evaluation/rhythm.md) for native MaiBot comparison conditions, failures and unscored anonymous samples.

## Target-only learning with neighbor context (0.10)

Selecting learning targets now retains a bounded, volatile neighborhood from other participants in the same session/persona. Only target messages enter the plugin's SQLite materials, evidence and pending counts. Peer text helps establish the situation; it is not an eligible learning source. Each learning request includes at most 16 excerpts of 400 characters, from within two neighboring positions and five minutes of a target message. Cache limits are 64 messages per scope and 128 scopes. Background cleanup expires entries; restarts do not restore them. Settings changes clear this context, while profile changes and source clearing clear the affected scope.

Batch selection and Panel/scheduler counts honor the current targets, including material retained before a target change. Changing targets during generation rejects the old result without consuming that batch; the SQLite commit rechecks this transactionally. Former targets' materials remain subject to the existing retention rules and can be selected again later. Missing context after restart or eviction is not reconstructed or guessed. See [target-context verification](../evaluation/targets.md) for actual-provider replay, the counterfactual control and native-core coverage.

## Local native-core simulation (0.9)

A [local comparison with MaiBot's native expression selector](../evaluation/maibot.md) now covers both its no-embedding sampling path and a genuinely built vector index. Both hosts use the same synthetic, preloaded bank and chat model. MaiBot's requests included its system selection instructions, but this endpoint returned conversational text instead of IDs. OvenHeart produced valid ID selections and met 11 of 12 applicability checks; one inappropriate listening habit remained selected during invited game banter. This primarily demonstrates endpoint compatibility, not whole-bot personality quality. Learning and final replies are outside that comparison.

`scripts/evaluate_native.py` boots a real AstrBot core in a temporary data directory, loads a byte-checked copy of this plugin and keeps the nine native pipeline stages, permissions and conversation services. Only the platform send method collects results locally. It does not connect QQ, open a Dashboard HTTP port or change the user's host data.

`--smoke` needs no keys. It checks stripped native commands stay out of learning and exercises the actual Panel ASGI routes for authentication, settings, View assets and scoped page tokens. Full mode reads chat/SiliconFlow credentials from environment variables and runs learning, autonomous generation, a direct wake and a disabled-session check. The scheduled learning worker is cancelled only to trigger explicit, bounded batches; actual learning and generation remain native. Current acceptance follows the user's choice to use local simulation.

Native waking strips prefixes before passive filters execute. Capture now checks both the original platform `message_obj.message_str` and processed text, preventing `/sid` from becoming a learned `sid` utterance. Configured wake prefixes receive the same treatment. See [native-core verification](../evaluation/native.md) for reproduction and limits.

Asynchronous permission/persona reads may complete out of order. Older inputs still contribute same-scope context, but only the newest room event can supersede the debounce window. Fresh-input counting and the context snapshot happen after debounce. Context follows native arrival timestamps, retaining collection order for ties; platform message IDs are not interpreted as timestamps. Existing interruption, shared limits and pre-send checks remain in effect.

## Recent conversation for selection (0.8)

Retrieval and contextual selection read ordinary user/assistant text in the last six native history records, supporting both string content and `type=text` blocks. Tool/system/checkpoint messages, thinking/media blocks and temporary text are excluded from the selector's conversational input. The host's final request still retains its own tool results, rules and original context.

Autonomous turns also use up to six recent live group messages from their admitted session/persona plan. Selection reads that scoped snapshot directly because its native hook runs before participation context is appended. Newer complete context lines share a 1,100-character budget, while the current input retains up to 1,000 characters and the goal up to 240, keeping the full query under the existing 2,400-character limit. No additional model calls or persisted copies are introduced. Current explicit topic/style requests take precedence over older conversation. See [conversation-context verification](../evaluation/context.md).

## Consolidation and independent batches (0.7)

Similar spelling does not establish the same meaning or compatible conditions. Consolidation uses exact aliases or an explicit `merge_id` pointing to a same-kind habit offered to the learning model. Text similarity no longer merges entries automatically. Model semantic judgments can still be wrong; review and human edits remain authoritative. Without a supported merge, separate candidates can remain for review.

Each successful run adds at most one independent support to a habit. Distinct evidence still accumulates. Same-run candidate confidence is averaged before combining it with previous independent batches. Automatic activation uses the final aggregate; an early high-scoring candidate cannot prematurely activate an otherwise pending result. Existing active states and human decisions are preserved.

Jargon refinement still occurs at independent-batch counts 2, 4, 8 and 16, without advancing multiple stages for repeated candidates in one response. No schema migration or historical recount is performed: legacy records do not contain enough information to reconstruct any past overcount reliably. See [multi-batch verification](../evaluation/evolution.md) for deterministic regressions and actual-provider replay.

## Coordination across isolated group sessions (0.6)

AstrBot may assign each group member a separate native session. Learning, persona and model context remain scoped to that session. OvenHeart shares only cooldown, rolling quota and the latest event marker for the same platform instance, bot account and physical group. Other sessions’ message bodies and habits are not transferred.

Permitted direct wakes, commands and new group inputs interrupt stale planning even with learning paused. Delayed captures and duplicate callbacks do not replace newer markers. Queued requests and pre-send results check the latest group event. A native send-stage reply also postpones other isolated sessions’ autonomous participation; already-sent streaming fragments cannot be retracted.

Fresh-message thresholds still apply within each isolated session. Use a shared host group session when the desired conversation context spans all members. Migration retains old decisions and scoped limits; new shared-group counting begins with post-upgrade records because legacy group identity cannot be reconstructed reliably. See [native scheduling verification](../evaluation/participation.md).

## Provider prompt compatibility

Settings → Memory and Expression offers `guidance_delivery=system` (default) or `dual`. Dual keeps the original system prompt and adds a temporary user-context part containing the current bound host persona and selected guidance. It does not copy the complete system prompt, modify user text or expand tool permissions. The extra context shares `prompt_budget`, with the host role text capped at one third of that budget. Application switches and scope isolation still apply, even when learning is paused.

AstrBot's native Agent history serializer removes the temporary persona/guidance parts. Autonomous recent messages and reply goals now use the same temporary marking. External runners must implement their own support. Free lexical previews do not include the extra role context.

Synthetic real-provider tests on the user-specified endpoint showed system-only identity/style instructions were ineffective. Dual delivery restored the configured role name and a learned playful sentence pattern in the tested cases. This is an observed endpoint behavior, not proof of how its gateway handles messages or a claim about all models. See [learning-to-reply replay](../evaluation/lifecycle.md) for raw results and limits.

![Compatibility delivery setting, illustrative environment](../assets/delivery-settings.jpg)

![Mobile compatibility delivery viewport, illustrative environment](../assets/delivery-settings-mobile.jpg)

## LivingMemory and QQ Enhance

LivingMemory is optional. The adapter targets the supplied `2.7.0-beta.3` interface and discovers the active instance at runtime. It respects LivingMemory access control, configured session/user/global scopes, persona filtering and host plugin switches. At most three memory excerpts are retrieved by default, only while learning. Missing, disabled, incompatible, unavailable or timed-out memory integration does not prevent independent learning. After reload, a scope without a new real event uses retained chat material only.

Version 0.12 preserves LivingMemory's intentional unfiltered legacy scope when session filtering is off and no forced-isolation list exists. Persona filtering still applies independently. If isolation is configured, LivingMemory decides between the global pool and the isolated session. See [native memory integration acceptance](../evaluation/memory.md) for the synthetic two-plugin checks and their limits.

Memory searches may update LivingMemory access times and trigger its existing legacy-scope migration. OvenHeart does not write additional memories or change LivingMemory's database directly. QQ Enhance is independently optional; autonomous tools are disabled by default; explicitly allowed tools remain subject to host and QQ Enhance policies.

## Data and rollback

Raw messages expire after 14 days by default, with at most 300 retained per session/persona scope. Background cleanup also runs without new incoming messages. The latest 32 evidence excerpts per habit and cumulative counts persist with their entries and snapshots. Clearing raw material does **not** delete learned evidence and cannot be undone.

Learning, feedback, profile and trait changes retain up to 30 preceding snapshots. Restoring a snapshot restores its profile and earlier trait states, while disabling later entries. Restoration itself also creates a snapshot. This does not restore raw messages.

Data is stored in the host's `data/plugin_data/astrbot_plugin_ovenheart/persona.sqlite3`. Disable the plugin before backing up the entire data directory so WAL files are not accidentally omitted.

## Autonomous participation

Version 0.3 renames the internal 学习记录 tab to 学习与参与 without changing its View or URL. Participation settings appear first in Settings. The default is off; observe mode records decisions without replying, and auto mode may submit a native request after a reply decision. Direct wakes, private chats, matched commands, existing requests and sent results keep their existing behavior. Automatic replies require AstrBot's local Agent runner; external runners can use observation.

Default gates are three fresh messages, two seconds of input debounce, a 90-second reply cooldown and 12 autonomous admissions per rolling hour, shared by isolated sessions in the same physical group. Cooldown and quotas survive reload. A native send-stage reply also postpones autonomous participation. Planning starts at most every 30 seconds per scope (or the configured cooldown when shorter), with two global planning slots. Unmet gates wait for a new message rather than running an independent polling reply loop.

The planner chooses reply, observe or one bounded wait followed by one reassessment. New input cancels obsolete work. Provider failure, timeout or invalid plans preserve observation. Plans state reasons and conversational goals; they cannot execute tools. The resulting native request binds a conversation and reloads history through the host's session lock, preserving persona/history. Input, permission, profile and settings changes are checked again before generation.

Autonomous tool access is an exact-name subset of tools already present in the native request. Empty means text-only. Direct-wake tool policy is unaffected. Explicitly allowing a tool permits the host Agent to invoke it autonomously under existing permissions. No model weight training is involved, and this is not a copy of every MaiBot subsystem.

The conversation window holds at most 30 messages from the last ten minutes in memory; at most 12 are passed to the reply request. Decision reasons, goals and timestamps persist separately. The UI shows the latest 30; storage preserves the latest 100 plus admissions needed for the rolling quota. Clearing raw learning material does not clear decision records. An admission records handoff to the host, not successful generation or delivery.

Real cn:deepseek-v4-flash checks on synthetic scenarios selected observation for a completed conversation between others, reply for explicitly requested support, and wait for unfinished input. This is not a long-term MaiBot comparison or real QQ delivery acceptance.

## Screenshots and verification

Screenshots below use explicitly labeled sample data and a simulated model. Production views contain no sample data or acceptance banner.

![Profile entry viewport](../assets/profile.jpg)

![Mobile library viewport](../assets/mobile.jpg)

The test suite covers SQLite transactions, evidence validation and native AstrBot SDK contracts. Provider output and platform permissions are controlled test doubles. See the repository README for development commands; SDK tests explicitly skip if AstrBot is not installed. The optional preview fixture is excluded from release ZIPs.

![Participation history with illustrative data](../assets/participation.jpg)

![Mobile participation viewport with illustrative data](../assets/participation-mobile.jpg)

![Contextual selection settings viewport, illustrative environment](../assets/selection-settings.jpg)

![Mobile selection settings viewport, illustrative environment](../assets/selection-settings-mobile.jpg)
