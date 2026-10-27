# A_memorix source attribution

Source: Mai-with-u/MaiBot commit `5c7637e6913c84d1c3d5e742fa9e95a7e70d3501`, `src/A_memorix/core`.
Authors: A-Dawn/A_memorix and MaiBot contributors. The upstream AGPL-3.0 text is retained in `LICENSE-AGPL-3.0.txt`.

`source_manifest.json` records upstream Git-blob hashes and ported-file hashes. The fact ledger, profile services, metadata storage, graph/vector retrieval, summaries, episodes and image runtime are namespaced here for use by AstrBot.

OvenHeart contributors made the following adaptations:

- `host.py`, `sdk_host.py` and `summary_host.py` bind native identities, paths, authorized model transports and source snapshots. They do not register a global `src` package or modify an installed third-party repository.
- Package initializers defer heavy imports. `embedding/__init__.py` points to `memory/memory_embedding.py`; providers and credentials remain owned by AstrBot.
- Runtime lifecycle disk work uses the retained cancellation-aware `run_sync_work` boundary. Sparse warmup and vector loading run outside the host message event loop.
- `memory/native_memory_kernel.py` supplies effective-version filtering, native episode evidence, bounded SQLite caches and transaction ownership around correction/graph persistence.
- `utils/async_work.py` comes from MaiBot `src/common/async_work.py` under GPL-3.0; its license is retained in the parent MaiBot directory. Other A_memorix files retain AGPL-3.0.

AstrBot adapters are maintained in the repository's `memory/` package. Source selection and retained licenses remain auditable independently of application data.
