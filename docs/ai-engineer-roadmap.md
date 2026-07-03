# The AI Engineer Roadmap — Study Guide (mapped to Ava)

> A companion to the [roadmap.sh AI Engineer roadmap](https://roadmap.sh/ai-engineer). For every topic: **what it is**, **why it matters**, **where to study**, and **how it shows up in Ava** — so you learn the theory and immediately see it in your own codebase.

## How to use this

Don't study top-to-bottom like a textbook. Use the **build-first loop**: pick a capability you want in Ava, find it below, read the "what/why", skim one resource, then implement it. You retain 10x more by shipping it. The roadmap is a *map*, not a syllabus — you'll revisit nodes as Ava grows.

**Suggested order for you specifically** (given Ava is a RAG/knowledge system):
1. Common Terminology (§2) — get the vocabulary straight.
2. Embeddings & Vector DBs (§7) → RAG (§8) — this *is* Ava's core. Then read [rag-pipeline.md](rag-pipeline.md) + [rag-handbook.md](rag-handbook.md).
3. Using pre-trained models (§3) + LLM APIs (§4) — how you actually call models.
4. AI Agents (§9) — Ava's internal chat and Claude Code are agents.
5. Prompt engineering (woven throughout) + Safety (§5).
6. Open-source AI (§6) — you already live here (Ollama).
7. Multimodal (§10) — Ava already does STT; the rest is optional.
8. Everything in "Beyond the roadmap" (§12) — this is what separates a hobbyist from an AI engineer.

Legend for **In Ava**: 🟢 already in the codebase · 🟡 partially · 🔵 planned/target.

---

## 0. Prerequisite — Software Engineering

**What.** You need to be a real developer first: HTTP/APIs, databases, async, git, testing, deployment. The roadmap assumes Frontend, Backend, or Full-Stack.
**Why.** "AI engineer" is 80% normal engineering (data plumbing, APIs, reliability) and 20% model-specific. The model is one component in a system.
**Study.** You already have this (Ava is a full FastAPI + Postgres + Next.js app). Solidify async Python and SQL.
**In Ava.** 🟢 The whole backend. Read [architecture.md](architecture.md) first.

---

## 1. Introduction — What an AI Engineer Is

- **What is an AI Engineer / vs ML Engineer.** An **AI engineer** *builds products on top of existing models* (APIs, RAG, agents, prompts). An **ML engineer** *trains and optimizes models* (data pipelines, architectures, GPUs). You are becoming the former — and that's where most of the industry's demand is.
- **Impact on product development / roles.** AI shifts effort from "write all the logic" to "orchestrate models + ground them in data + evaluate them." The scarce skill is making them *reliable*, not making them *work once*.
- **Why.** Framing matters: your job is systems that use AI well, not research. It tells you what to learn (RAG, evals, agents) and what to skip (training transformers from scratch, at least for now).
- **Study.** [roadmap.sh/ai-engineer](https://roadmap.sh/ai-engineer) intro; Chip Huyen's *"Building LLM applications"* posts and her book *AI Engineering* (2025) — the single best big-picture read.
- **In Ava.** 🟢 Ava is a textbook AI-engineering product: no training, all orchestration + retrieval + evaluation.

---

## 2. Common Terminology (learn these cold)

- **AI vs AGI.** AI = systems that do specific intelligent tasks. AGI = hypothetical general human-level intelligence. You build AI; ignore AGI debates for engineering.
- **LLM (Large Language Model).** A neural network trained to predict the next token over huge text. Everything it "knows" is compressed into weights. **Why it matters:** it explains both its power (fluent generation) and its failure mode (confabulation — it predicts plausible text, it doesn't look things up). This is *the* reason RAG exists.
- **Inference.** Running a trained model to get output. Costs money/latency; no learning happens. **In Ava:** every Ollama/Claude call is inference.
- **Training / fine-tuning.** Adjusting weights on data. Full training = from scratch (rare). Fine-tuning = nudging an existing model (see §12). **Key insight:** you rarely train; you retrieve.
- **Tokens.** Sub-word units models read/write. Billing, context limits, and "token counting" are all about these. **In Ava:** `tiktoken` counts tokens for context compaction (`core/llm/agent.py`).
- **Context window.** Max tokens a model can consider at once. When your data exceeds it, you must retrieve/compact — the root reason RAG and summarization exist.
- **Embeddings.** Text → vector of numbers capturing meaning. The foundation of semantic search. **In Ava:** 🟢 `nomic-embed-text` via Ollama, stored in pgvector.
- **Vector database.** Stores embeddings and finds nearest ones fast. **In Ava:** 🟢 pgvector (inside Postgres).
- **RAG (Retrieval-Augmented Generation).** Retrieve relevant text, then let the LLM answer using it. **In Ava:** 🟡 the entire point of the project — see [rag-pipeline.md](rag-pipeline.md).
- **AI Agent.** An LLM in a loop that can call tools until it can act/answer. **In Ava:** 🟢 LangGraph ReAct agent.
- **Prompt engineering.** Crafting inputs (instructions, examples, format) to steer output. Woven through everything below.
- **Study.** [roadmap.sh terminology cards](https://roadmap.sh/ai-engineer); 3Blue1Brown's "Neural Networks / Transformers" videos; Andrej Karpathy's *"Intro to LLMs"* (1hr, the best single explainer) and *"Let's build the GPT tokenizer."*

---

## 3. Using Pre-trained Models

- **Benefits.** State-of-the-art capability for free/cheap, instantly, no training or GPUs. This is why AI engineering exploded — the hard part (training) is already done by labs.
- **Limitations & considerations.** Knowledge **cutoff** (they don't know recent events), **hallucination**, **context limits**, cost/latency, no access to *your* private data — every one of these is a reason to add RAG, tools, or fine-tuning around the model.
- **Model families to know.** OpenAI (GPT), **Anthropic Claude** (what Claude Code runs on), Google Gemini, Mistral, Cohere, Meta Llama, Qwen; hosting: Azure AI, AWS SageMaker/Bedrock, Hugging Face, Replicate, Ollama (local).
- **Capabilities / context length / cutoff.** For each model you use, know its context window, price per token, and knowledge cutoff. These drive architecture decisions (how much you can stuff in context vs must retrieve).
- **Why.** Choosing the right model per task (cheap+fast for classification, strong for reasoning) is a core AI-engineer skill.
- **Study.** Provider docs (Anthropic, OpenAI, Google). See the project's own [`claude-api` reference](../CLAUDE.md) for Claude model IDs/pricing. Compare on [LMArena](https://lmarena.ai) and provider model pages.
- **In Ava.** 🟢 `core/llm/provider.py` supports Ollama/OpenAI/Anthropic/Google behind one interface; model chosen per workspace/`.env`.

---

## 4. LLM APIs (the roadmap's "OpenAI Platform")

The roadmap frames this around OpenAI, but the concepts are universal (Anthropic's Messages API, Ollama's API are the same shape).

- **Chat Completions / Messages API.** You send a list of messages (system/user/assistant) and get a completion. The fundamental call.
- **Writing prompts.** System prompt = role/rules; user = the task; few-shot examples = show don't tell.
- **Managing tokens.** *Max tokens* (output cap), *token counting* (budget the context), *pricing* (input vs output token cost). **In Ava:** 🟢 context compaction keeps you under the window.
- **Streaming.** Tokens arrive incrementally for responsiveness. **In Ava:** 🟢 `astream_events` in the agent; SSE/WebSocket to the UI.
- **Structured output / function calling.** Force JSON or a schema; the basis of tools/agents. **Learn this well** — it's how you make LLMs reliable in software.
- **Fine-tuning.** Covered in §12. The roadmap lists it here but for *knowledge* you'll use RAG instead.
- **Playground.** Anthropic/OpenAI consoles to experiment before coding.
- **Why.** This is the literal interface between your code and intelligence.
- **Study.** [Anthropic API docs](https://docs.anthropic.com), the project's `claude-api` skill, OpenAI cookbook. Learn tool-calling + structured outputs deeply.
- **In Ava.** 🟢 `provider.py` + `agent.py`; tools bound via LangChain.

---

## 5. AI Safety & Ethics

- **Prompt injection.** Untrusted text (a document, a web page, an email) contains instructions that hijack your model ("ignore previous instructions…"). **The #1 security issue in AI apps.** Because Ava ingests arbitrary documents and Claude Code reads them, this is directly relevant — never let retrieved content be treated as instructions.
- **Security & privacy.** Where does data go? Local (Ollama) vs cloud. Secrets handling. **In Ava:** 🟢 local-first + encrypted secrets vault.
- **Bias & fairness.** Models reflect training-data biases. Matters more in user-facing/decision systems.
- **Safety best practices.** Moderation APIs, end-user IDs, adversarial testing, constraining inputs/outputs, "know your use case."
- **Why.** The difference between a demo and a product is handling the adversarial and the embarrassing cases.
- **Study.** [OWASP Top 10 for LLM Applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/); Anthropic's safety docs; Simon Willison's writing on prompt injection.
- **In Ava.** 🟡 local-first + secrets vault help; explicit prompt-injection defense on retrieved content is a 🔵 hardening target.

---

## 6. Open-Source AI

- **Open vs closed models.** Closed (Claude, GPT) = strongest, API-only. Open (Llama, Qwen, Mistral, Gemma) = you run them yourself, private, free, customizable, weaker at the top end.
- **Hugging Face.** The GitHub of models/datasets. Learn: **Hub** (find models), **Tasks** (what a model does), the **`transformers`** library, **`sentence-transformers`** (embeddings/rerankers).
- **Ollama.** Runs open models locally via a simple API — pull a model, call it. **In Ava:** 🟢 the local LLM + embedding runtime.
- **Inference SDKs / Transformers.js.** Ways to run models (server, browser, edge).
- **Why.** Open models give you privacy, cost control, and the ability to fine-tune — pillars of a local-first product like Ava.
- **Study.** [Hugging Face Course](https://huggingface.co/learn) (free, excellent); [Ollama docs](https://ollama.com); `sentence-transformers` docs.
- **In Ava.** 🟢 Ollama + sentence-transformers reranker.

---

## 7. Embeddings & Vector Databases  ← *Ava's foundation*

- **What are embeddings.** (See §2.) A model maps text to a vector so that *semantic* similarity = *geometric* closeness.
- **Embedding models.** OpenAI `text-embedding-3`, open ones via `sentence-transformers`, `nomic-embed-text`, `bge-m3`, `multilingual-e5`. Know: dimensions, context length, multilingual quality, local vs API. **In Ava:** 🟢 `nomic-embed-text` (768d) — the [rag-handbook](rag-handbook.md) explains when to upgrade to `nomic-embed-text-v2-moe`.
- **Use cases.** Semantic search (Ava's core), classification, recommendations, clustering, dedup, anomaly detection.
- **Vector databases.** Purpose: store vectors + metadata, do fast approximate nearest-neighbor (ANN) search with filters. Options: **Chroma, Pinecone, Weaviate, FAISS, Qdrant, LanceDB, pgvector (Postgres), Milvus.** **In Ava:** 🟢 **pgvector** — chosen so vectors live *with* the data (one store, transactional, filterable).
- **Implementing vector search.** Indexing (HNSW/IVFFlat), similarity metrics (cosine/dot/L2), the index tuning knobs (`m`, `ef_search`), and — crucially — filtered search.
- **Why.** This is how an LLM "reads" your private data. Get this wrong and every downstream answer is wrong.
- **Study.** [Pinecone Learn](https://www.pinecone.io/learn/) (best free vector/embeddings course); [pgvector README](https://github.com/pgvector/pgvector); the "Embeddings" chapter of the HF course.
- **In Ava.** 🟢🟡 `core/db/embeddings.py` + `core/knowledge/rag.py`. Deep dive: [rag-pipeline.md](rag-pipeline.md).

---

## 8. RAG & Implementation  ← *the heart of Ava*

- **RAG use cases.** Q&A over private docs, chatbots grounded in your data, "chat with your files" — exactly Ava.
- **RAG vs fine-tuning.** RAG = give the model *facts at query time* (changes freely, no hallucination of your data). Fine-tuning = change the model's *behavior/format/style*. **Rule: RAG for knowledge, fine-tuning for behavior.** (Full treatment in [rag-handbook.md](rag-handbook.md).)
- **Implementing RAG — the five steps:** **Chunking** (split docs) → **Embedding** (vectorize chunks) → **Vector DB** (store) → **Retrieval** (find relevant chunks for a query) → **Generation** (LLM answers using them). Each step has many strategies; picking them well is the whole game.
- **Ways to implement.** SDKs directly (most control — Ava's path), **LangChain**, **LlamaIndex** (RAG-focused framework). The OpenAI Assistants API is a hosted alternative (not local, not for Ava).
- **Why.** This *is* your product. "Impeccable RAG" = the difference between Ava being useful and being noise.
- **Study.** [rag-pipeline.md](rag-pipeline.md) (Ava-specific, concrete) and [rag-handbook.md](rag-handbook.md) (all strategies + how to choose); LlamaIndex docs; Anthropic's *Contextual Retrieval* post.
- **In Ava.** 🟡 pgvector + reranker today; hybrid search, contextual retrieval, and a content-state-machine are the 🔵 upgrade.

---

## 9. AI Agents

- **What.** An LLM that can **call tools** in a loop: reason → act (call a tool) → observe → repeat, until it can answer. Turns a text generator into something that *does* things.
- **ReAct prompting.** "Reason + Act": the prompt pattern that interleaves thoughts and tool calls. The basis of most agents.
- **Tools / function calling.** You describe functions (name, args schema); the model chooses to call them; you run them and feed results back. **In Ava:** 🟢 `search_meetings`, `get_context`, `manage_todos`, etc.
- **Building agents.** Manual loop, provider "functions/tools", or frameworks (**LangGraph** — Ava's choice, **CrewAI**, **AutoGen**, OpenAI Agents SDK). **Agentic RAG** = an agent that decides *when/how* to retrieve.
- **Why.** Ava's internal chat and Claude Code are both agents over Ava's tools. Understanding tool-calling reliability (schemas, errors, loops) is essential.
- **Study.** [LangGraph docs](https://langchain-ai.github.io/langgraph/); Anthropic's *"Building effective agents"* (must-read on when NOT to over-engineer agents); the ReAct paper.
- **In Ava.** 🟢 `core/llm/agent.py` (LangGraph `create_react_agent`) + `core/plugins/registry.py` (tools per workspace).

---

## 10. Multimodal AI

- **What.** Models that handle images, audio, video — not just text.
- **Tasks.** Image understanding (vision), image generation (DALL·E/Stable Diffusion), **speech-to-text** (Whisper), **text-to-speech**, audio processing, video.
- **Implementing.** Vision APIs (Claude/GPT vision), Whisper (STT), TTS engines; LangChain/LlamaIndex multimodal wrappers.
- **Why.** Meetings are audio; turning them into searchable text is multimodal AI in action.
- **Study.** [OpenAI Whisper](https://github.com/openai/whisper); Hugging Face audio course; provider vision docs.
- **In Ava.** 🟢 **STT** via `faster-whisper` (meeting transcription); TTS via Kokoro (archived voice path). Vision/image-gen: 🔵 not needed for the knowledge product.

---

## 11. Development Tools

- **What.** AI code editors and completion tools: **Claude Code**, Cursor, Copilot, Windsurf, Continue.
- **Why.** These *are* your daily leverage — and Claude Code is a primary consumer of Ava. Learning to drive them well (context, MCP, custom tools) is itself an AI-engineering skill.
- **Study.** [Claude Code docs](https://code.claude.com/docs); learn MCP (how Ava will plug into Claude Code — see [rag-pipeline.md](rag-pipeline.md) §MCP).
- **In Ava.** 🔵 Ava will expose an MCP server so Claude Code can read/write its knowledge.

---

## 12. Beyond the roadmap (what separates an AI engineer from a tutorial-follower)

The roadmap.sh map is a solid *base* but light on the things that make production AI actually work. Prioritize these:

- **Evaluation / evals.** You cannot improve what you don't measure. Retrieval metrics (recall@k, MRR, nDCG) and generation metrics (faithfulness, answer relevancy) via **RAGAS/DeepEval** + a small golden set. **This is the single highest-leverage skill.** → [rag-handbook.md](rag-handbook.md) §Evaluation. 🔵 planned for Ava.
- **Structured outputs & guardrails.** Forcing valid JSON/schemas, validating model output, retrying. Reliability engineering for LLMs.
- **Prompt engineering (deep).** System design, few-shot, chain-of-thought, decomposition, output contracts, prompt caching. → [roadmap.sh/prompt-engineering](https://roadmap.sh/prompt-engineering).
- **Cost & latency engineering.** Model routing (cheap vs strong), caching, batching, streaming, token budgeting. Real products live or die here.
- **LLMOps / observability.** Tracing (LangSmith, Langfuse, OpenTelemetry), logging prompts/outputs, versioning prompts, monitoring drift.
- **Fine-tuning (properly understood).** **LoRA/QLoRA** (train ~0.1–1% of params cheaply), embedding fine-tuning (the one that helps RAG), instruction tuning. **When:** behavior/format/style/latency — *not* facts. → [rag-handbook.md](rag-handbook.md) §Fine-tuning.
- **Data engineering for AI.** Ingestion, parsing (PDF/markdown/HTML), dedup, incremental indexing, deletion propagation — unglamorous and decisive. 🟡 Ava lives here.
- **Agentic patterns & MCP.** Multi-step agents, tool design, human-in-the-loop, and the **Model Context Protocol** for interop. → [architecture.md](architecture.md) + [rag-pipeline.md](rag-pipeline.md).
- **Foundations (optional but empowering).** How transformers/attention work, tokenization, sampling (temperature/top-p). Karpathy's *"Zero to Hero"* series if you want to go deep.

---

## 13. Learn-by-building ladder (do these *in* Ava)

Each rung teaches a roadmap section by shipping it:

1. **Instrument retrieval** → build a 30-question golden set + measure recall@k. *(Evals, §12)*
2. **Add hybrid search (vector + BM25 + RRF)** and re-measure. *(RAG §8, Vector DBs §7)*
3. **Add a reranker** (`bge-reranker-v2-m3`) and re-measure. *(RAG §8)*
4. **Add contextual retrieval** (LLM-generated chunk context). *(RAG §8, Prompting)*
5. **Build the MCP server** so Claude Code can query + feed Ava. *(Agents §9, Dev Tools §11)*
6. **Add a CRAG grader** that rejects low-relevance context to stop hallucinations. *(Agents/Safety)*
7. **Add eval gates in CI** so quality can't regress. *(LLMOps, §12)*
8. *(Optional)* **Fine-tune the embedding model** on your corpus and measure the delta. *(Fine-tuning, §12)*

Every rung has a number you can watch go up. That feedback loop is how you actually become "the god of AI" — not by reading, by measuring and improving a real system.

---

*Next: [rag-pipeline.md](rag-pipeline.md) — the concrete pipeline for Ava. Then [rag-handbook.md](rag-handbook.md) — the full theory behind every choice.*
