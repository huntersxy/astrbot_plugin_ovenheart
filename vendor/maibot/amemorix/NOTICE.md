# A_memorix source port

Source: Mai-with-u/MaiBot `5c7637e6913c84d1c3d5e742fa9e95a7e70d3501`,
`src/A_memorix/core`. Authors: A-Dawn/A_memorix and MaiBot contributors.
The upstream AGPL-3.0 license is retained in `LICENSE-AGPL-3.0.txt`.

`source_manifest.json` lists every upstream Git-blob SHA-256 and the corresponding
ported file hash. The fact state machine, SQLite schema/storage, profile snapshot,
classification, evidence ownership checks and text projection are upstream code.

Modifications by OvenHeart contributors, 2026-10-25:

- Host imports use the explicit namespaced `host.py` bridge, never a global `src`
  module or changes to an installed MaiBot/AstrBot/LivingMemory repository.
- `person_profile_service.py` uses deferred annotations and its automatic graph
  retriever constructor returns `None`. Optional graph/vector retrieval is not
  included in this stage; the native adapter uses the original fact-ledger path.
- Package initializers avoid eager imports of heavy vector/embedding modules.
- `host.py` is the AstrBot identity/database/model interface adapter. The native
  fact-only profile path does not require a classification model call; extraction
  uses OvenHeart's metered auxiliary model path.

Remaining episode, image, and complete general long-term memory runtime work
is tracked in `docs/design/maibot-parity.md`. Their SQLite tables being present is
not a claim that those pipelines are integrated.

The next local stage adds the original dual-path retrieval, graph and vector
stores (37 upstream modules total). Their algorithms are unchanged; logger imports
are namespaced. `storage/__init__.py` loads heavy modules lazily, and the explicit
embedding transport in `memory_embedding.py` calls AstrBot's configured provider.
The complete SDK runtime, ingestion/feedback orchestration and migration remain
in progress; see `docs/design/room-memory.md`.

The consolidation stage adds 9 original modules (46 modules total) and 3 original
localized summary prompt templates. SummaryImporter retains the original
incremental-review, model parsing, checkpoints and metadata/entity/relation writes;
only its embedding runtime preflight is replaced by the initialized native room
check. NativeSummaryImporter extends vector persistence and native provenance.
EpisodeService, EpisodeSegmentationService, EpisodeRetrievalService and the
MemoryIngestService job/lease/revision publication algorithms remain unchanged.
The latter's complete service is ported, but only its episode job path is bound
in this stage; this is not a claim that SDKMemoryKernel is integrated.
`summary_host.py` binds real authorized retained text and AstrBot model transport.
Image evidence is not yet supplied by this native text boundary.
