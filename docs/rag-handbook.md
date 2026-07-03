# The RAG Handbook — from zero to advanced

> Study material. Everything about Retrieval-Augmented Generation: what each technique is, when to use it, and how to choose. Generalized so you can apply it to any project, with pointers back to how Ava uses it. Pairs with the applied [rag-pipeline.md](rag-pipeline.md).

---

## Part I — Why RAG exists

An LLM is a next-token predictor. Its "knowledge" is frozen in its weights at training time, and it **cannot look things up** — when it doesn't know, it produces plausible text anyway (hallucination). Three consequences:

1. It doesn't know your **private** data (your meetings, notes, code).
2. It doesn't know **recent** events (knowledge cutoff).
3. It **makes things up** confidently.

**RAG** fixes all three by changing the question from *"what does the model know?"* to *"what can we retrieve and put in front of it?"* You fetch relevant text at query time and instruct the model to answer *using it*. The model becomes a reasoning engine over *your* facts, not a fuzzy memory of the internet.

> Mental model: **the LLM is the CPU; RAG is the RAM+disk.** You don't teach the CPU your files — you load them into memory when needed.

---

## Part II — The five stages (and every choice inside them)

Every RAG system is: **Parse → Chunk → Embed & Index → Retrieve → Generate.** Mastery = knowing the options at each stage and when each applies.

### Stage 1 — Parse

Turn raw sources (PDF, DOCX, HTML, Markdown, audio) into clean text + structure.
- **Markdown/HTML:** preserve headings, lists, code blocks — structure is signal.
- **PDF:** layout-aware parsers (PyMuPDF, unstructured, LlamaParse) beat naive text extraction.
- **Audio:** STT (Whisper) → text (Ava does this for meetings).
- **Why it matters:** garbage parsing → garbage everything downstream. This unglamorous step decides your ceiling.

### Stage 2 — Chunk

Split documents into retrievable pieces. You retrieve *chunks*, not whole documents, so precision depends on chunk quality.

| Strategy | What | When to use |
|---|---|---|
| **Fixed / token** | N tokens, fixed overlap | Default; simplest. ~200–400 tok, 15–20% overlap. Research: fixed ~200-word chunks match/beat fancier methods in 2025 benchmarks |
| **Recursive** | Split on paragraph→sentence→word in order | Good default for prose (LangChain `RecursiveCharacterTextSplitter`) |
| **Structure / Markdown-aware** | Split on headers; prepend header path | **Best for structured docs** (Ava's markdown + meeting summaries). Highest leverage |
| **Semantic** | Embed sentences, split at meaning shifts | Long, topically-varied docs. 3–10× slower; not always worth it |
| **Sentence-window** | Embed 1–3 sentences, return a larger window | When the matched sentence needs surrounding context (meeting summaries) |
| **Parent-child / hierarchical** | Retrieve small chunks, return their big parent | Long documents; precision of small + context of large. Needs `parent_id` |
| **Late chunking** (Jina) | Embed whole doc first, then pool per-chunk vectors | Long docs where chunks reference far-away context. Needs a Jina long-context model (non-local) |

**How to choose:** start fixed/recursive. If your docs are structured (Markdown, sections), go structure-aware immediately — it's cheap and high-impact. Add sentence-window or parent-child only when eval shows context is being cut. Reach for semantic/late chunking only for long, dense documents *and* only if the metrics justify the cost.

**The single most impactful chunking upgrade: Contextual Retrieval** (Anthropic, 2024). Prepend an LLM-generated 1–2 sentence context to each chunk before embedding and BM25. Situates ambiguous chunks ("it was approved" → "The Q3 budget was approved…"). Published: **−35%** retrieval failures (embeddings), **−49%** (+ contextual BM25), **−67%** (+ reranking). Works with local models.

### Stage 3 — Embed & Index

**Embedding models** turn text into vectors. What to compare:
- **Dimensions** (256–3072): higher = more expressive, more storage/compute.
- **Context length** (512–32k): how much text per embed call.
- **Multilingual quality**, **local vs API**, **license.**

| Model | Dims | Local? | Note |
|---|---|---|---|
| `nomic-embed-text` v1.5 | 768 | ✅ Ollama | English-first; PT marginal |
| **`nomic-embed-text-v2-moe`** | 768 | ✅ Ollama | **Ava's recommended upgrade** — adds multilingual/PT, drop-in |
| `bge-m3` | 1024 | ✅ | Strongest local multilingual; dense+sparse+multi-vector |
| `multilingual-e5-large` | 1024 | ✅ | Solid baseline, 512 ctx |
| `jina-embeddings-v3` | 1024 | ✅ | Supports late chunking |
| OpenAI `text-embedding-3-large` | ≤3072 | ❌ | Strong English, API |
| Cohere `embed-v4` | 1024 | ❌ | Top commercial |

Rule: **exhaust local options before an API.** Measure on *your* data (MTEB is a guide, not gospel).

**Vector index** (in the DB): **HNSW** vs **IVFFlat**.
- **HNSW** — layered graph, O(log N) queries, handles incremental inserts well → **default for a growing KB like Ava.** Knobs: `m` (16 default), `ef_construction` (64), `ef_search` (40, tunable at query time).
- **IVFFlat** — partitions into lists; faster build, needs rebuild/ANALYZE as data grows; knob `probes`.
- **Critical gotcha:** with a `WHERE` filter, set `hnsw.iterative_scan='strict_order'` or you may get fewer than `LIMIT` rows (the index picks candidates before the filter).
- **Storage:** `halfvec` (float16) halves storage with negligible recall loss.

**Distance metrics:** cosine (default for text), dot product, L2. Match the metric to how the model was trained (most text models → cosine).

### Stage 4 — Retrieve (where most quality is won or lost)

- **Dense (vector):** matches *meaning*; misses exact tokens (names, IDs, acronyms).
- **Sparse (BM25 / full-text):** matches *keywords* exactly; misses paraphrase.
- **Hybrid = both, fused with RRF (Reciprocal Rank Fusion).** Over-fetch ~60 from each arm, merge by `Σ 1/(k+rank)` (k=60), keep top ~20. Recall typically ~62% → ~84%. **Always do hybrid in production.**
- **Query transforms:**
  - **HyDE** — LLM writes a hypothetical answer; embed *that* (its vocabulary matches real docs better than a short question). Good for vague queries; adds an LLM call.
  - **Multi-query** — generate 3–5 paraphrases, retrieve for each, dedupe, fuse. Boosts recall on complex questions.
  - **Query decomposition** — break a complex question into sub-questions; retrieve each.
- **Metadata filtering** — constrain by `workspace`, `date`, `type`, `tags` before the vector op. Precision + security (workspace isolation).
- **MMR (Maximal Marginal Relevance)** — pick results relevant *and* diverse; reduces redundant context.

### Stage 5 — Rerank & Generate

- **Reranking:** a **cross-encoder** re-scores each (query, chunk) pair jointly (sees both together) — far more accurate than the first-pass bi-encoder, but O(N) so only on the top ~20 candidates → keep top ~5.
  - Local: **`bge-reranker-v2-m3`** (multilingual, MIT — Ava's pick), `mxbai-rerank-v2` (English), `jina-reranker-v2`. API: Cohere Rerank.
  - Worth it when you retrieve >10 candidates from mixed sources; ~50–200ms/20 on GPU.
- **CRAG (Corrective RAG):** grade the retrieved context; if it's weak, reformulate/abstain instead of hallucinating. The practical anti-hallucination pattern (a grader step, no fine-tuning needed).
- **Generation:** put reranked chunks in the prompt **with citations**; instruct "answer only from context; else say you don't know"; budget the context window.

---

## Part III — Advanced architectures (know they exist; use when justified)

| Architecture | Problem it solves | Use when | Skip when |
|---|---|---|---|
| **RAPTOR** | "Global" questions needing many docs | Corpus of 100s of docs; theme-level Qs ("all decisions about X") | Small corpus / specific Qs |
| **GraphRAG** (Microsoft) | Multi-hop reasoning over connected entities | Interconnected domain, analytical Qs | Simple notes/summaries (high ingest cost) |
| **ColBERT / late interaction** | Max precision via per-token vectors | Precision >> latency; long dense docs | Latency-sensitive; storage 30–100× |
| **Agentic RAG** | Deciding *when/how* to retrieve, multi-step | Complex, multi-source queries (Ava already uses LangGraph) | Simple lookups |
| **Self-RAG** | Model self-decides retrieval + critiques | Willing to fine-tune | No training budget → use CRAG instead |

Don't cargo-cult these. Most production wins come from **hybrid + reranking + contextual retrieval + good chunking + evals** — the boring, high-ROI stuff. Reach for RAPTOR/GraphRAG only when your eval set shows a class of questions the basics can't answer.

---

## Part IV — RAG vs Fine-tuning (the question everyone gets wrong)

**They solve different problems.**

| You want the model to… | Tool |
|---|---|
| Know *facts* (your docs, recent, private, changing) | **RAG** |
| Adopt a *format* (always valid JSON, citation style) | Fine-tune (or structured-output constraints) |
| Adopt a *tone/persona* | Fine-tune |
| Use domain *vocabulary* consistently | Fine-tune |
| Answer with *lower latency* (no retrieval step) | Fine-tune |

**The rule: RAG for knowledge, fine-tuning for behavior.** A model fine-tuned "on your meeting notes" will still hallucinate specific facts (LLMs memorize facts poorly); a RAG system retrieves them exactly. Don't fine-tune to teach facts.

### The kinds of fine-tuning

- **LoRA** (Low-Rank Adaptation): freeze the base model, train tiny injected matrices (~0.1–1% of params). Cheap, results near full fine-tuning.
- **QLoRA:** LoRA on a 4-bit-quantized base → ~4× less GPU memory. An 8B model tunes on a single 24 GB GPU in hours.
- **Full fine-tuning:** all weights; expensive, rarely needed.
- **Instruction tuning / DPO/GRPO:** align behavior/preferences.

### The one fine-tuning that helps RAG: **embedding fine-tuning**

If your corpus uses domain jargon that general embeddings can't separate (internal codenames, acronyms), fine-tune the *embedding* model:
- Library: `sentence-transformers` (`SentenceTransformerTrainer`).
- Loss: **MultipleNegativesRankingLoss** (needs only positive (query, passage) pairs; other batch items are negatives). Avoid TripletLoss from a pretrained base — it can catastrophically degrade.
- **Hard negatives** (topically-close-but-wrong passages) are the highest-impact ingredient (~40/query).
- Data: 500–2,000 pairs is enough; trains in <1h on CPU for small models.
- **But measure first** — general embeddings already handle standard English prose (meeting notes). Only fine-tune if your eval set shows the embedding is the bottleneck.

**2026 pattern:** QLoRA-tuned small model (behavior/format) served by Ollama + RAG (facts) on top. Not either/or.

---

## Part V — Evaluation (the skill that makes you dangerous)

> You cannot improve what you don't measure. Most people build RAG by vibes and plateau. Evals are the difference.

### Retrieval metrics (retriever alone; need labeled `question → relevant doc IDs`)
- **Hit Rate / Recall@k** — did ≥1 relevant doc land in top-k? Bluntest signal. Aim ≥0.85 @5.
- **MRR (Mean Reciprocal Rank)** — `avg(1/rank of first relevant)`. Rewards putting the right chunk at position 1–2 (LLMs weight early context). Aim ≥0.7.
- **nDCG@k** — graded relevance, rank-discounted. Most sensitive if you have multi-level labels.

### Generation metrics (need the answer; some need ground truth)
- **Context Precision** — of retrieved chunks, fraction actually relevant.
- **Context Recall** — of chunks needed, fraction retrieved.
- **Faithfulness** — is every claim grounded in the context? (hallucination detector; reference-free).
- **Answer Relevancy** — does the answer address the question? (reference-free).

Faithfulness + Answer Relevancy need no ground truth → run them on live traffic.

### Frameworks
- **RAGAS** — most practical to start; the four metrics above; swap in a **local Ollama judge** (offline, matches Ava). *Recommended.*
- **DeepEval** — same metrics + `GEval` custom criteria + pytest-style assertions.
- **TruLens** — the "RAG Triad" + OpenTelemetry tracing; more for observability dashboards.
- **promptfoo** — prompt/model regression testing (CLI).

### Build a golden set as a solo dev (do this early)
1. For each doc, LLM: "3 questions this answers" → collect `(question, source_id)`.
2. Hand-write expected answers for ≥20.
3. Store JSON: `[{question, expected_answer, expected_source_ids}]`.
4. Re-run after **every** change; **change one variable at a time**; keep what moves the number.
5. Later: gate CI on the metrics so quality can't regress.

---

## Part VI — Production concerns (the last 20% that's 80% of the value)

- **Idempotent ingestion** — key by `source` + `content_hash`; skip unchanged, replace changed. No duplicates.
- **Deletion propagation** — delete document → cascade-delete chunks. No orphans (`ON DELETE CASCADE`).
- **Freshness** — re-embed on edit; version embeddings when you change the embedding model.
- **The state machine** — only fully-processed (`ready`) content is retrievable; never index placeholders/failures.
- **Isolation & permissions** — every query carries a `workspace_id` filter; never leak across tenants.
- **Cost/latency** — cache embeddings; batch; over-fetch then rerank; expose `ef_search` as a knob.
- **Observability** — log queries, retrieved IDs, scores, and answers; you'll debug retrieval constantly.

---

## Part VII — Glossary

- **RAG** — retrieve relevant text, generate an answer grounded in it.
- **Chunk** — a retrievable slice of a document.
- **Embedding** — a vector representing meaning; similar meaning → nearby vectors.
- **Bi-encoder** — embeds query and doc *separately* (fast, used for first-pass retrieval).
- **Cross-encoder** — scores (query, doc) *together* (accurate, used for reranking).
- **ANN** — Approximate Nearest Neighbor search (fast, slightly inexact) — what HNSW/IVFFlat do.
- **HNSW / IVFFlat** — vector index types.
- **BM25** — classic keyword ranking (term frequency × rarity).
- **Hybrid search** — vector + keyword combined.
- **RRF (Reciprocal Rank Fusion)** — merge two ranked lists by `Σ 1/(k+rank)`.
- **Reranker** — a cross-encoder that re-orders top candidates precisely.
- **HyDE** — embed a hypothetical answer instead of the raw query.
- **MMR** — pick relevant *and* diverse results.
- **Contextual Retrieval** — prepend LLM-generated context to each chunk before indexing.
- **RAPTOR / GraphRAG / ColBERT** — advanced architectures (see Part III).
- **CRAG / Self-RAG** — corrective retrieval patterns.
- **LoRA / QLoRA** — parameter-efficient fine-tuning.
- **MNRL** — MultipleNegativesRankingLoss, for embedding fine-tuning.
- **Faithfulness / Answer Relevancy / Context Precision/Recall** — RAG generation metrics.
- **Recall@k / MRR / nDCG** — retrieval metrics.
- **Golden set** — labeled questions+answers for evaluation.

---

## Part VIII — A curated study path

1. **Pinecone Learn** — embeddings + vector search fundamentals (free).
2. **Anthropic: Contextual Retrieval** + **Building Effective Agents** (blog posts, high signal).
3. **LlamaIndex docs** — RAG patterns, hands-on.
4. **RAGAS docs** — evaluation.
5. **Hugging Face: Train Sentence Transformers** — embedding fine-tuning.
6. **Chip Huyen, *AI Engineering*** (2025 book) — the systems view.
7. Then: build every technique here *into Ava* and measure it (see [ai-engineer-roadmap.md](ai-engineer-roadmap.md) §13 ladder).

> The fastest way to learn this is not to read more — it's to wire each technique into Ava, run your golden set, and watch the number move. Retrieval is empirical. Measure, change one thing, repeat.

---

*Applied version for Ava: [rag-pipeline.md](rag-pipeline.md). System context: [architecture.md](architecture.md).*
