# OvenHeart

For owned room context, set `context_mode` to `structured` and disable AstrBot's group-context awareness and native active reply. Planning and generation share one frozen snapshot of up to 40 messages / 24000 characters, retaining message and member IDs, quotes and media state. Records survive plugin reloads; new dialogs and `/reset` isolate old context. Restored rows without their original event are background-only. Keep native conversation history, platform message storage, tools and media enabled. The default `native` mode preserves existing behavior. Room context is separate from learning evidence and does not bypass learning exclusions.

[中文](../zh/README.md) · [Project home](../../README.md) · [Changelog](../../CHANGELOG.md)

Personality, expression and conversational behavior learning for AstrBot. OvenHeart supplements the existing persona with evidence-based habits and gives the operator control over their activation. It uses retrieval and prompting, not model weight training.

## Design origin: what comes from MaiBot

OvenHeart's way of being with people comes from [MaiBot](https://github.com/Mai-with-u/MaiBot), a standalone digital-life framework that chats like a real person: it imitates the speaking style of people around it, picks up new slang and in-group language, reads the room to decide when to speak, and builds its understanding of people over time. OvenHeart brings that learn-from-living-together approach into AstrBot:

| Track | MaiBot counterpart | Form here |
| --- | --- | --- |
| Expression learning | Expression learner | Distills phrasing, tone and rhythm from members' real messages; habits keep verbatim evidence and are retrieved per context before each reply |
| Group jargon | Jargon learning | Learns local word meanings, isolated by source session and persona |
| Participation | Heart flow willingness system | Decides reply / wait / observe before deciding what to say |
| Personality | "Becoming human" | Accumulates tendencies and situational responses instead of a fixed persona sheet |

Neither project trains model weights: habits are evidence-backed data entries applied through retrieval and prompting.

`learning_blocked_groups` accepts group IDs or exact unified session IDs. It stops learning capture, scheduled/manual learning and late commits for those groups, while keeping existing data, normal chat context and approved habits. The list defaults to empty. Explicit mentions preempt this plugin's unsent autonomous work in the same physical room before the host's session lock; ordinary planning defaults to a 60-second interval.

MaiBot is a self-contained framework with its own message pipeline, persona, memory and emoji systems, and its learning loop runs mostly automatically. OvenHeart is an AstrBot plugin: delivery, wake, permissions, tools and the original persona stay with the host; new habits default to manual review in the library where you approve, edit or disable them, with automatic activation as an opt-in; memory is optional via the unmodified LivingMemory plugin.

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

Desktop navigation sits on the left, switching between the persona brief, learning library, learning and participation, learning settings, and prompts; on smaller screens it becomes a horizontally scrollable bar above the workspace. Session selection, source status and memory status appear at the top of the workspace. Light and dark modes follow AstrBot. The persona brief and preview sit side by side on wide screens; library filters and bulk actions sit above the list and detail view; the settings save button sticks to the viewport bottom while scrolling.

![Persona brief and preview](../assets/profile.jpg)

![Library filters, entries and review detail](../assets/library-triage-v0.56-desktop.jpg)

Screenshots use local illustrative data and a simulated model, not real conversations or provider output.

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

Autonomous participation supports off, observe and automatic modes. Defaults are three new messages, a two-second quiet interval, a 90-second real-send cooldown and 12 autonomous replies per hour. New input cancels stale work and schedules another check after quiet time, subject to remaining permissions, cooldown and quota. Eligible same-member follow-ups can receive an additional participation check.

The autonomous tool picker supports search, allowlists and denylists. Its default empty allowlist permits text only. It never enables a host-disabled tool and does not restrict ordinary native replies.

A bare mention from the same member can be coalesced into an earlier name-triggered native request while that request is still being processed. Mentions containing text, quotes or media, different senders and completed requests remain independent turns.

## Context and cache behavior

Cached delivery keeps stable persona guidance in the system portion and current habits/targets in temporary trailing content excluded from native saved history. When AstrBot group history injection or active-reply context is enabled, the final reply does not receive a duplicate plugin group transcript.

The host's `agent_runner.config.compression.max_turns` is independent. At a small fixed limit, every new turn can remove old messages and invalidate the history prefix. Where appropriate, use `-1` while retaining a positive token capacity and the existing `llm_compress` strategy. Check the profile actually assigned to the session.

This allows append-only growth until capacity-based compression is required, not unlimited history or guaranteed cache hits. Summaries, edits, session switches, system/tool changes and provider policies can still invalidate prefixes. Existing diagnostic events are not removed by changing a setting.

## LivingMemory and prompts

LivingMemory is optional. Learning and planning can retrieve background within their budgets; final replies retain the memory plugin's native recall path. Missing or unavailable memory does not prevent independent habit learning. Retrieval may update access metadata or trigger the memory plugin's existing migrations.

Use an official, unmodified LivingMemory installation. OvenHeart owns an in-memory adapter around its two native recall/reflection dispatch entries. Verified cross-message autonomous targets receive isolated source snapshots; ordinary requests, host event identity, tools and passive group capture retain their native path. Plugin reload reattaches the adapter; disabling OvenHeart restores the original entries. The workshop reports engine and source-routing availability separately.

This adapter has a compatibility boundary: invalid or no-longer-permitted sources skip this turn's memory hooks. If the native hooks cannot be identified, the cross-message autonomous reply is cancelled and the reason is logged.

The prompts tab edits learning, review, selection, planning and reply templates with reset-to-default support. Templates change wording; evidence, access and tool restrictions remain enforced in code. Upgrades preserve custom templates.

## Data and diagnostics

The database is `data/plugin_data/astrbot_plugin_ovenheart/persona.sqlite3`, outside the plugin source directory. Back up that data directory and the host plugin configuration before upgrades or rollback.

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

Independently implemented with design inspiration from [MaiBot](https://github.com/Mai-with-u/MaiBot), built for [AstrBot](https://github.com/AstrBotDevs/AstrBot), with optional [LivingMemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory) integration. Their source implementations are not bundled. Licensed under [AGPL-3.0-or-later](../../LICENSE).

When Jev participation is enabled, a single typed request chooses a real target, waits, or observes. It replaces the chat-model planner entirely. Unavailable or malformed Jev results observe without a chat-model fallback. Explicit mentions still bypass participation decisions. Learning verification remains independent and conservative.

Autonomous participation collects ordinary arrivals in a fixed silence window (8 seconds by default). A positive initial decision waits before generation. New messages neither restart the timer nor cancel the judge; only changed input is re-evaluated at the boundary. With no new input, reuse a reply decision or stop waiting without polling. Explicit wakes still preempt immediately. Set `participation_silence_seconds` to 0 for immediate mode.
