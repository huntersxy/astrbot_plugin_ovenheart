# OvenHeart

An AstrBot plugin for expression learning, conversation planning and source-backed memory.

## Install

Requires AstrBot 4.29.1–4.x and the QQ / OneBot v11 adapter (`aiocqhttp`). Configure a chat provider in AstrBot, then install this repository from Plugin Management:

```text
https://github.com/huntersxy/astrbot_plugin_ovenheart
```

Open **Persona Workshop** on the plugin card. It uses AstrBot authentication and needs no separate service.

## Use

- Set the persona supplement and learning scope. New habits activate automatically by default after passing AI review and meeting the confidence threshold; other candidates remain available for manual review.
- Configure existing AstrBot provider IDs for text embeddings and, optionally, image embeddings.
- Review sourced facts, shared experiences, images and corrections in the Memory tab.
- Automatic group participation is on by default. Adjust its frequency or select observation-only or off. Existing installations keep their saved settings.

The learning library opens with all statuses visible. Planning and reply generation share the same conversation guidance: respond from the configured persona, continue the stance established in recent messages, and adjust when new information arrives. Brief reactions, banter and disagreement are valid responses; advice and follow-up questions are optional. Familiarity and relationship changes need real interaction history, while explicit tasks still receive complete answers.

Each page owns its filters. Learning, history, memory and emoji views start with all sources; self portraits list all personas. Expand a page's filters to narrow its results. Record edits keep their original owner; imports, new relations and material cleanup choose their target within the relevant form. Browsing all records in the authenticated console does not change chat memory isolation.

Settings → Model roles provides independent choices for Planner, habit learning, learning review, contextual expression selection, memory consolidation, person facts, proactive memory review, semantic splitting and optional visual emoji selection. Choose from AstrBot's enabled chat providers. Empty choices use the current chat provider directly; visual emoji selection instead uses AstrBot's image-caption provider. The final reply and native tool calls always retain AstrBot's session model and generation settings. Planner-selected expressions do not trigger an additional selector call, and Jev keeps its own connection settings.

Group member tools resolve current members by display card, nickname or account and arrange real mentions in the same native reply as its text or command, including mentions of other bots. Multiple members, streaming and segmented replies are supported; mentions appear once at the start. Ambiguous names require clarification, and arranged mentions remain pending until the reply is sent. Tools follow AstrBot's persona permissions and the autonomous tool policy. Sending a command does not confirm that another bot executed it.

The dedicated **Emoji** tab includes a gallery, uploads/imports, and selection settings. Upload images with usage descriptions or copy existing meme_manager packs. Public library assets are shared with authorized chats; conversation candidates remain scoped. Native image storage handles content deduplication and preserves GIF originals. The planner can choose candidates by caption without another emotion-model call. Text and selected media are coordinated as one reply; image-only requests can send directly without generating filler text. Optional visual selection costs an additional model call. OvenHeart skips duplicate meme_manager selection for its own planner replies, while leaving its source and configuration intact.

Reply splitting defaults to local rules using AstrBot’s split words and line breaks. No additional model is required. The first part sends without a typing delay; later parts use bounded typing intervals, with at most three messages by default. Optional semantic splitting uses a chat provider and a separate three-second timeout, falling back to local rules. Code blocks and lists stay intact. Kaomoji, bracketed and quoted fragments are protected internally while surrounding sentences and line breaks can still split. Host segmentation takes precedence; streaming and mixed media keep their original delivery. Configure this under Settings → Expression & retrieval.

Learning changes retrieval and prompt guidance, not model weights. Model calls may include relevant chat excerpts. Runtime data belongs in AstrBot's plugin data directory, not in the source tree.

See the [user guide](../guide.md) and [developer guide](../../CONTRIBUTING.md). Reports should include versions, reproduction steps and sanitized logs; never submit keys or real chat exports.

Licensed under [AGPL-3.0-or-later](../../LICENSE). Source attribution and retained licenses are in [vendor/maibot](../../core/vendor/maibot/NOTICE.md).

Memory → Import & sources can copy LivingMemory text from the same AstrBot data directory into the selected conversation and persona. Select a source scope and start the transfer; repeated imports skip existing source records and can pause between batches. LivingMemory does not need to be enabled. Its vectors and settings are not copied. Chat and learning use OvenHeart’s native memory.

Memory settings default to conversation isolation. Turning it off shares a memory bank across chats using the same native persona, including private and group chats; different people are still identified by their real accounts. The switch controls visibility: global recall automatically includes existing conversation memories, while enabling isolation restricts recall to the current conversation. Records retain their original sources. Proactive review checks at most two oldest or related memories per plugin-wide hourly round. Only well-supported corrections apply automatically; review other proposals and undo changes in Memory → Inspection → Correction history.

Use **Self portrait** to browse all personas, then edit appearance notes and an optional static PNG/JPG/WebP reference (up to 2 MB). Notes are temporary context; the reference is attached only to incoming-image turns whose native reply provider supports vision. It uses the existing model request, with no extra captioning call and no replacement of the native persona identity.
