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

Learning changes retrieval and prompt guidance, not model weights. Model calls may include relevant chat excerpts. Runtime data belongs in AstrBot's plugin data directory, not in the source tree.

See the [user guide](../guide.md) and [developer guide](../../CONTRIBUTING.md). Reports should include versions, reproduction steps and sanitized logs; never submit keys or real chat exports.

Licensed under [AGPL-3.0-or-later](../../LICENSE). Source attribution and retained licenses are in [vendor/maibot](../../vendor/maibot/NOTICE.md).
