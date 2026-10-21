# OvenHeart workshop guide

## Install and open

Requires AstrBot `>=4.29.1,<5` and a configured chat provider. Install the release ZIP through the local plugin installer, or copy the plugin directory to the host's `data/plugins/astrbot_plugin_ovenheart` and reload it. Open the native plugin View named **人格工坊** (Persona workshop).

The view uses AstrBot authentication, its native bridge and light/dark themes. No extra port, frontend build or Node runtime is needed. Opening the HTML directly displays a Panel connection instruction. Version 0.2 renames `astrbot_plugin_persona` to `astrbot_plugin_ovenheart`. Disable the old plugin before enabling the new one. An empty new database imports a consistent SQLite backup from `data/plugin_data/astrbot_plugin_persona/persona.sqlite3`; the original is preserved and existing new data is never overwritten. Panel overrides migrate with the database; host plugin configuration files do not. The plugin directory and API namespace change from `astrbot_plugin_persona` to `astrbot_plugin_ovenheart`; the native View remains 人格工坊 and the page heading becomes 烤箱之心.

| View | Purpose |
| --- | --- |
| 人格底稿 / Profile | Supplement identity, reply style and behavior; pause learning/application for the selected scope; preview effective guidance |
| 学习库 / Library | Review source evidence, edit, approve, reject and disable learned habits |
| 学习记录 / History | Inspect batches, actionable errors, memory integration and restore prior versions |
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

Library ratings are reversible choices (helpful, less helpful, clear), not inferred user approval. History records prompt references reaching the host send stage, which does not establish delivery or model compliance. Usage counts do not increase confidence. Old habits gradually lose ranking weight without automatic deletion.

## LivingMemory and QQ Enhance

LivingMemory is optional. The adapter targets the supplied `2.7.0-beta.3` interface and discovers the active instance at runtime. It respects LivingMemory access control, configured session/user/global scopes, persona filtering and host plugin switches. At most three memory excerpts are retrieved by default, only while learning. Missing, disabled, incompatible, unavailable or timed-out memory integration does not prevent independent learning. After reload, a scope without a new real event uses retained chat material only.

Memory searches may update LivingMemory access times and trigger its existing legacy-scope migration. OvenHeart does not write additional memories or change LivingMemory's database directly. QQ Enhance is independently optional; no automatic QQ tool invocation is added.

## Data and rollback

Raw messages expire after 14 days by default, with at most 300 retained per session/persona scope. Background cleanup also runs without new incoming messages. The latest 32 evidence excerpts per habit and cumulative counts persist with their entries and snapshots. Clearing raw material does **not** delete learned evidence and cannot be undone.

Learning, feedback, profile and trait changes retain up to 30 preceding snapshots. Restoring a snapshot restores its profile and earlier trait states, while disabling later entries. Restoration itself also creates a snapshot. This does not restore raw messages.

Data is stored in the host's `data/plugin_data/astrbot_plugin_ovenheart/persona.sqlite3`. Disable the plugin before backing up the entire data directory so WAL files are not accidentally omitted.

## Scope of behavior learning

This is structured habit learning applied through supplemental prompts, not model weight training or a reproduction of MaiBot's autonomous scheduler. It affects replies already triggered by AstrBot. It does not independently wake group chats, decide when to send messages or execute QQ tools. Real model quality and real platform behavior need deployment acceptance with your chosen provider and conversations.

## Screenshots and verification

Screenshots below use explicitly labeled sample data and a simulated model. Production views contain no sample data or acceptance banner.

![Profile and effective guidance preview](../assets/profile.jpg)

![Mobile learning library](../assets/mobile.jpg)

The test suite covers SQLite transactions, evidence validation and native AstrBot SDK contracts. Provider output and platform permissions are controlled test doubles. See the repository README for development commands; SDK tests explicitly skip if AstrBot is not installed. The optional preview fixture is excluded from release ZIPs.
