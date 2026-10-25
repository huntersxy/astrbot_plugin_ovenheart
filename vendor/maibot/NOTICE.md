# MaiBot source attribution

Authors: Mai-with-u/MaiBot contributors; see the linked source history.

Source: https://github.com/Mai-with-u/MaiBot/tree/5c7637e6913c84d1c3d5e742fa9e95a7e70d3501
License: GPL-3.0. The unmodified license text is included as LICENSE-GPL-3.0.txt.
The two turn-trigger files below are verbatim copies. Their AstrBot adapters live
outside this directory. The separately licensed A_memorix port is documented in
[amemorix/NOTICE.md](amemorix/NOTICE.md).
The rest of OvenHeart remains under its repository AGPL-3.0 license.

`mid_term.py` contains the original pure summary parser, cue normalization,
cosine ranking and reference formatting from `src/maisaka/memory/mid_term.py`.
Its native internal identity DTO replaces MaiBot's context-message dependency;
the original algorithm bodies and the Chinese summary prompt are preserved.
File and per-symbol AST hashes are recorded in `mid_term_source.json`;
`scripts/port_midterm.py` reproduces the selection. Native compression scheduling,
permissions, persistence and model transport are implemented in the root
`midterm.py`, outside this vendor directory.

`emoji_tiles.py` preserves the original contact-sheet layout, numbered tile and
PNG composition algorithms from `src/maisaka/builtin_tool/send_emoji.py` at the
same commit. File and symbol hashes are in `emoji_source.json`; selection and
AstrBot delivery live in the root `emoji_actions.py` adapter.

`response_splitter.py` changes only host logger/model imports from the original
`src/chat/utils/llm_sentence_splitter.py`. `response_segments.py` and
`typing_time.py` preserve the original segment compression and typing-time
functions from `src/chat/utils/utils.py`. Source, file and symbol AST hashes are
in `response_source.json`; `scripts/port_response.py` reproduces these ports.
`response_host.py` is an OvenHeart scoped host binding. AstrBot send handling,
permission checks, partial receipts and the five-second delay cap live in the
root `reply_rhythm.py`, outside the original algorithms.

- `src/maisaka/turn_trigger/dynamic_gate.py`: SHA-256 `29a4129c7f4ec23a466ad90bd26e35262bc7ec69c5da536a00b5239ca5624de3`
- `src/maisaka/turn_trigger/reply_likelihood.py`: SHA-256 `8c1f71d775f81b2585025b0bda51f1114ae02211e2fbd4b91f232e87acf712f2`
