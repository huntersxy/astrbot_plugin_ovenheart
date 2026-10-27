# OvenHeart

An AstrBot plugin for expression learning, conversation planning and source-backed memory.

## Install

Requires AstrBot 4.29.1–4.x and the QQ / OneBot v11 adapter (`aiocqhttp`). Configure a chat provider in AstrBot, then install this repository from Plugin Management:

```text
https://github.com/huntersxy/astrbot_plugin_ovenheart
```

Open **Persona Workshop** on the plugin card. It uses AstrBot authentication and needs no separate service.

## Use

- Set the persona supplement and learning scope. Learned habits require approval by default.
- Configure existing AstrBot provider IDs for text embeddings and, optionally, image embeddings.
- Review sourced facts, shared experiences, images and corrections in the Memory tab.
- Enable observation before automatic group participation. Automatic participation is off by default.

The dedicated **Emoji** tab includes a gallery, uploads/imports, and selection settings. Upload images with usage descriptions or copy existing meme_manager packs. Public library assets are shared with authorized chats; conversation candidates remain scoped. Native image storage handles content deduplication and preserves GIF originals. The planner can choose candidates by caption without another emotion-model call. Optional visual selection costs an additional model call. OvenHeart skips duplicate meme_manager selection for its own planner replies, while leaving its source and configuration intact.

Reply splitting defaults to local rules using AstrBot’s split words and line breaks. No additional model is required. The first part sends without a typing delay; later parts use bounded typing intervals, with at most three messages by default. Optional semantic splitting uses a chat provider and a separate three-second timeout, falling back to local rules. Code, lists, bracketed or quoted content stay intact. Host segmentation takes precedence; streaming and mixed media keep their original delivery. Configure this under Settings → Expression & retrieval.

Learning changes retrieval and prompt guidance, not model weights. Model calls may include relevant chat excerpts. Runtime data belongs in AstrBot's plugin data directory, not in the source tree.

See the [user guide](../guide.md) and [developer guide](../../CONTRIBUTING.md). Reports should include versions, reproduction steps and sanitized logs; never submit keys or real chat exports.

Licensed under [AGPL-3.0-or-later](../../LICENSE). Source attribution and retained licenses are in [vendor/maibot](../../vendor/maibot/NOTICE.md).

Memory → Import & sources can copy LivingMemory text from the same AstrBot data directory into the selected conversation and persona. Select a source scope and start the transfer; repeated imports skip existing source records and can pause between batches. LivingMemory does not need to be enabled. Its vectors and settings are not copied. Chat and learning use OvenHeart’s native memory.
