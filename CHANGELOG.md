# Changelog

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
