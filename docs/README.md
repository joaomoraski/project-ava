# Ava — Documentation

Living documentation for the Ava project. Written to be **read and studied**, not just referenced — this project is a learning vehicle for AI engineering.

## Index

| Doc | What it is |
|---|---|
| [startup.md](startup.md) | How to run Ava — first-time setup on a fresh machine, and the everyday run in an existing environment. |
| [architecture.md](architecture.md) | How the whole system is built: components, data model, data flows, design decisions, and a glossary of every concept/library used. Start here. |
| [ai-engineer-roadmap.md](ai-engineer-roadmap.md) | **Study guide** mapping the whole [roadmap.sh AI Engineer roadmap](https://roadmap.sh/ai-engineer) to what/why/where-to-study and how each topic shows up in Ava. Your learning spine. |
| [rag-pipeline.md](rag-pipeline.md) | **The recommended RAG pipeline for Ava** — concrete and applied: ingestion → chunking → indexing → hybrid retrieval → reranking → answer → evaluation, plus how Claude Code reads from and feeds into Ava. |
| [rag-handbook.md](rag-handbook.md) | **RAG from zero to advanced** — every strategy, how to choose between them, RAG vs fine-tuning, evaluation, a glossary, and decision trees. Study material. |

## How these fit together

- **architecture.md** = the *system* (what exists, how it's wired, why).
- **rag-pipeline.md** = the *retrieval core* as it should be built for Ava specifically.
- **rag-handbook.md** = the *theory and menu of options* behind the choices in `rag-pipeline.md`, generalized so you can apply it anywhere.

> Internal working docs (the deep audit, the original plan, raw feedback) live **outside the repo** in the personal notes folder — they're not committed. These `docs/` files are the curated, keep-forever documentation.
