# OvenHeart

[中文](../zh/README.md) · [Project home](../../README.md) · [Changelog](../../CHANGELOG.md)

Personality learning, conversation planning and native memory for AstrBot. This guide covers version 0.68.0. Habits retain source evidence and default to manual approval; learning uses retrieval and prompting rather than model weight training.

## Design origin: what comes from MaiBot

OvenHeart's way of being with people comes from [MaiBot](https://github.com/Mai-with-u/MaiBot), a standalone digital-life framework that chats like a real person: it imitates the speaking style of people around it, picks up new slang and in-group language, reads the room to decide when to speak, and builds its understanding of people over time. OvenHeart brings that learn-from-living-together approach into AstrBot:

| Track | MaiBot counterpart | Form here |
| --- | --- | --- |
| Expression learning | Expression learner | Distills phrasing, tone and rhythm from members' real messages; habits keep verbatim evidence and are retrieved per context before each reply |
| Group jargon | Jargon learning | Learns local word meanings, isolated by source session and persona |
| Participation | Heart flow willingness system | Plans reply / wait / stop and follows actual reply results |
| Personality | "Becoming human" | Accumulates tendencies and situational responses instead of a fixed persona sheet |

Neither project trains model weights: habits are evidence-backed data entries applied through retrieval and prompting.

`learning_blocked_groups` accepts group IDs or exact unified session IDs. It stops learning capture, scheduled/manual learning and late commits for those groups, while keeping existing data, normal chat context and approved habits. The list defaults to empty. Explicit mentions preempt this plugin's unsent autonomous work in the same physical room before the host's session lock; ordinary planning defaults to a 60-second interval.

OvenHeart integrates learning, continuous conversation planning and native A_memorix memory into AstrBot. Delivery, permissions, tools and the existing persona remain with the host. Native memory runs independently; the official LivingMemory plugin is an optional integration and migration source.

## Requirements and installation

Market category: **AI tools**. Display name: **OvenHeart**. The supported platform is **QQ / OneBot v11 (`aiocqhttp`)**; other platforms are not supported yet.

AstrBot `>=4.29.1,<5` is required. Use a working chat provider. Install through the WebUI plugin manager using:

```text
https://github.com/huntersxy/astrbot_plugin_ovenheart
```

Or clone into an AstrBot checkout:

```bash
git clone --branch main https://github.com/huntersxy/astrbot_plugin_ovenheart.git data/plugins/astrbot_plugin_ovenheart
```

Reload the plugin and open **人格工坊 (Persona workshop)** from its plugin card; the page uses AstrBot authentication and needs no separate port. Autonomous participation requires AstrBot's local Agent runner.

### Workshop layout

The six destinations are persona, learning library, memory, learning/participation history, settings and prompts. Settings are grouped into learning, participation, expression/retrieval and maintenance. Memory has its own console and a separate settings button. Inner settings tabs preserve unsaved inputs; leaving an editor asks before discarding changes. Navigation adapts to narrow screens and the host theme.

![Memory console with illustrative data](../assets/memory-console-v0.68-desktop.png)

The screenshot uses fictional people and sample memory, not production chat data.

## First run

1. Add optional identity, expression and behavioral guidance in the persona tab. AstrBot's original persona remains in effect.
2. Select a learning provider ID, or leave it empty to follow the host selection. API keys belong in AstrBot provider settings.
3. Configure learning targets, blocked users and allowed sessions. QQ users can be blocked by entering only their numeric IDs.
4. Collect ordinary messages in an allowed conversation. Background learning starts after at least 12 eligible messages by default; manual learning is also available.
5. Review evidence and self-check results in the library, then approve the habits you want to apply.

Review is the default activation mode. Autonomous participation, name mention triggers and Debug logging are disabled by default.

## What it learns

| Category | Use |
| --- | --- |
| Personality | Stable tendencies consistent with the identity brief |
| Expression | Situational phrasing, tone, rhythm and verified short excerpts |
| Group jargon | Local meanings restricted to their original session and persona |
| Behavior | Responses such as listening, clarifying, reassuring or joining a joke |

The library has an all-session view, category/status filters, self-check groups and issue categories. Extractor confidence is a model self-assessment, separate from the later self-check and activation state. A high percentage does not override an evidence concern.

Bulk decisions affect only explicitly selected pending entries on the current page. Operators can edit, approve, reject, disable and restore entries. Existing manual decisions and locks take precedence over new candidates.

## Learning and retrieval settings

| Setting | Default |
| --- | --- |
| Minimum eligible messages | 12 |
| Maximum learning batch | 30 |
| Learning interval | 600 seconds |
| Batch model budget | 360 seconds; extraction capped at 240 seconds with 120 seconds reserved for review |
| Maximum applied habits | 8; configurable from 1 to 24 |
| Supplement budget | 3000 characters |
| Additional group window | 12 messages / 12000 characters; either budget can be zero |
| Raw source retention | 14 days / 300 messages per source scope |

Failed learning batches retain their sources. Retry state survives reloads. A validated extraction draft can be reused briefly only when its complete input and configuration still match; review and commit guards still run.

Lexical, hybrid and contextual selection are available. Optional embedding and reranking use provider IDs configured in AstrBot. Unavailable services fall back according to the selected mode; contextual selection failure can skip learned habits while preserving the host persona. Directly matched local jargon has a separate retention path.

Free preview shows the supplement without sending a message or calling a model. Model-generated examples consume provider quota. Injection does not prove adoption by the model.

## Session boundaries

By default, allowed sessions share the authored brief and non-jargon habits. Enable session isolation for separate configuration and application. Raw sources, native histories, quotations and participation context retain their original boundaries in both modes. **Group jargon is always isolated by source session and persona.**

Blocked users take priority over learning targets. The block applies to collection, pending learning sources and relevant learning context; changing the policy invalidates stale in-flight learning. Existing learned habits and evidence remain available for manual review rather than being silently deleted.

Workshop settings and native plugin configuration share the same host configuration. Workshop saves take effect immediately; native configuration saves synchronize after reload. Stale forms are rejected to avoid overwriting newer settings.

## Replies and participation

Native mentions, replies to the bot and wake prefixes remain AstrBot entry points. OvenHeart supplements eligible requests with their actual target and selected habits while preserving host media, tools and permissions.

Optional name mention triggers use configured case-sensitive names or aliases in the message body. They bypass autonomous planning and hand the request to the native reply path. Quoted/forwarded bodies and names displayed in mentions of other users are excluded. Avoid short common words.

Autonomous participation supports off, observe and automatic modes; it is disabled by default. The default MaiBot rhythm uses the original scoring and real-reply feedback, with a target frequency of 0.5. Fixed message-count, interval and cooldown gates belong to the optional fixed rhythm. Host permissions and operator quotas remain in effect. The default `planner_mode=maibot` uses a continuous tool loop for authorized memory queries, concrete reply references, actual send feedback, waits and stop; `legacy` retains the earlier single-decision flow. Jev can assist expression selection, with its connection configured in the native plugin settings.

The autonomous tool picker supports search, allowlists and denylists. Its default empty allowlist permits text only. It never enables a host-disabled tool and does not restrict ordinary native replies.

A bare mention from the same member can be coalesced into an earlier name-triggered native request while that request is still being processed. Mentions containing text, quotes or media, different senders and completed requests remain independent turns.

## Context and cache behavior

For owned room context, set `context_mode` to `structured` and disable AstrBot's group-context awareness and native active reply. Planning and generation share one frozen snapshot of up to 40 messages / 24000 characters, retaining message and member IDs, quotes and media state. Records survive plugin reloads; new dialogs and `/reset` isolate old context. Restored rows without their original event are background-only. Keep native conversation history, platform message storage, tools and media enabled. The default `native` mode preserves existing behavior. Room context is separate from learning evidence and does not bypass learning exclusions.

Cached delivery keeps stable persona guidance in the system portion and current habits/targets in temporary trailing content excluded from native saved history. When AstrBot group history injection or active-reply context is enabled, the final reply does not receive a duplicate plugin group transcript.

The host's `agent_runner.config.compression.max_turns` is independent. At a small fixed limit, every new turn can remove old messages and invalidate the history prefix. Where appropriate, use `-1` while retaining a positive token capacity and the existing `llm_compress` strategy. Check the profile actually assigned to the session.

This allows append-only growth until capacity-based compression is required, not unlimited history or guaranteed cache hits. Summaries, edits, session switches, system/tool changes and provider policies can still invalidate prefixes. Existing diagnostic events are not removed by changing a setting.

## Native memory, migration and prompts

Native memory is enabled by default and isolated by conversation and persona, independently of shared learning preferences. It stores source-grounded paragraphs, facts, relations, episodes, people and images. Text retrieval is available without an embedding provider. Configure an embedding provider for vector retrieval; similar-image search requires a supported native DashScope multimodal embedding provider. Exact-image recall works without it.

The memory console follows MaiBot's eight-page organization: records, profiles, images, graph, timeline, imports/sources, inspection and deletion. Operators can inspect full text and evidence, correct records, roll back corrections, preview deletions and restore deletion operations. Removing an image occurrence has its own confirmation and cannot be undone through that control.

Memory settings group general/image memory, experiences/people and legacy migration. Automatic chat summaries default to 12 new messages and a 600-second attempt interval; person extraction defaults to six new messages per person and 600 seconds. Pausing learning stops associated background updates while existing authorized memories remain readable.

Legacy migration requires the official LivingMemory plugin to be active. It follows the selected conversation, persona and access rules, preserves a complete source snapshot, and does not revive records forgotten on the native side. Requests without a recent real event wait for an authorized message. After verifying migration, the old plugin can be disabled; native memory continues independently.

Imports support text, TXT/Markdown files and native `.amembundle` packages. Bundles merge into the selected conversation; the panel limit is 16 MB. Browsing does not initiate embedding requests. Writes use the same SDK and writer lock as chat. If text has been restored but index recovery is pending, the panel reports partial completion.

The prompts page lists available placeholders and supports template editing and default restoration. Model-generated reply previews consume provider quota; prompt-only previews do not send chat messages.

## Data and diagnostics

Learning data is stored in `data/plugin_data/astrbot_plugin_ovenheart/persona.sqlite3`; native memory uses per-conversation/persona stores under `amemorix/rooms/` in the same data directory. Back up the entire data directory and host plugin configuration before upgrades or rollback.

Raw sources and associated explicit quotations expire under the retention policy. Learned evidence excerpts and revision snapshots have separate lifetimes: clearing raw messages does **not** delete them. Restoring a revision leaves subsequently added entries disabled.

Debug is off by default. Enabled INFO logs containing `OvenHeart debug` include complete injected content and learning/planning diagnostics. Configuration credentials are not deliberately logged, but message text itself can contain sensitive information. Redact logs before sharing them. Model services receive the relevant excerpts needed for learning and replies.

Per-turn records show targets, selection, injection budgets and stages. A send-hook record does not prove platform delivery, and injection does not prove the model used the habit.

## Troubleshooting and contributing

- No sources: check learning switches, session permissions, targets, blocked users and message eligibility.
- Invalid learning JSON: inspect the failing stage, model output compatibility and timeout. Successful ordinary chat does not establish structured-task compatibility.
- High confidence with a flagged review: inspect the actual evidence and learning subject; these are independent judgments.
- Active habit missing from a reply: check scope, applicability, selection and injection budgets.
- Cache drift: check host history limits, compression, system/tools, session changes and other plugins.
- Stale settings: refresh the native settings page or workshop after save/reload.

Report versions, platform, steps and redacted logs in [GitHub Issues](https://github.com/huntersxy/astrbot_plugin_ovenheart/issues). Never upload keys, production databases or real chat exports. Development commands are in the [project README](../../README.md#文档与贡献); SDK integration tests need a compatible adjacent AstrBot checkout.

## Credits and license

Built for [AstrBot](https://github.com/AstrBotDevs/AstrBot), with conversation and memory mechanisms adapted from [MaiBot](https://github.com/Mai-with-u/MaiBot). The official LivingMemory plugin remains an optional integration.

The repository uses [AGPL-3.0-or-later](../../LICENSE). Ported code retains its source attribution and original licenses: [MaiBot](../../vendor/maibot/NOTICE.md), [A_memorix](../../vendor/maibot/amemorix/NOTICE.md). See the [acceptance notes](../design/maibot-parity.md) for the scope of full conversation-parity verification.
