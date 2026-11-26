# Limitações aceitas (`shortcut:`) e trade-offs (T178)

Decisões conscientes que trocam robustez máxima por simplicidade/custo, e o que ficou de fora.
Cada item lista **onde**, **o que** e **o caminho de evolução**.

---

## 1. Rate limit em janela fixa (processo-local)

- **Onde**: [src/app/api/limits.py](../src/app/api/limits.py)
- **O que**: contador por `user_id` com `INCR` + `EXPIRE` em Redis, janela fixa de 60 s.
- **Consequência**: na troca de minuto, um lojista pode disparar **até 2× o limite em uma
  janela deslizante de 60 s** (as duas pontas do minuto).
- **Risco aceito**: baixo. O limite por si só é defensivo, não contratual, e o perfil de uso
  pessoa-máquina não aproveita essa borda.
- **Evolução**: trocar por "sliding window log" ou "token bucket" quando o tráfego crescer.
  A interface do módulo não muda — só a implementação de `check_rate_limit`.

## 2. Circuit breaker in-process

- **Onde**: [src/app/core/resilience.py](../src/app/core/resilience.py), aplicado ao DuckDuckGo
  em [knowledge_tools.py](../src/app/tools/knowledge_tools.py).
- **O que**: estado do breaker vive por processo (dict em memória, protegido por `asyncio.Lock`).
- **Consequência**: num cluster multi-réplica, cada réplica "aprende" o estado da dependência
  separadamente — a quinta falha seguida de **cada** réplica abre o circuito dela, não o global.
- **Risco aceito**: baixo para o volume atual. Com o circuito aberto, o web_search devolve lista
  vazia e o Knowledge responde só com a base interna.
- **Fora do breaker, por decisão**: o LLM já tem `max_retries` e o modelo de fallback
  (`OPENAI_MODEL_FALLBACK`); um breaker na frente só trocaria uma resposta degradada por outra.
  O reranker (FlashRank) roda em processo, sem rede, então não há dependência para isolar.
- **Evolução**: mover para Redis com chaves por `cb:{service}` + `SET NX EX` quando houver
  mais de uma réplica em produção. A API pública (`get_breaker`, `with_circuit_breaker`) fica
  igual.

## 3. Chunker baseado em palavras

- **Onde**: [src/app/rag/chunking.py](../src/app/rag/chunking.py)
- **O que**: contamos palavras separadas por whitespace como aproximação de tokens.
- **Consequência**: o tamanho real do chunk em tokens varia (PT ~1.3 tokens/palavra, EN ~1.0),
  então o teto efetivo oscila. Ainda assim fica dentro do limite do embedding model.
- **Risco aceito**: baixo. Para `text-embedding-3-small` (8191 tokens), a margem é folgada.
- **Evolução**: trocar por `tiktoken` quando o perfil de documento ficar mais longo (ou mudar
  para outro embedding com limite menor).

## 4. CVEs aceitas em `.trivyignore.yaml`

Toda entrada do `.trivyignore.yaml` tem data de expiração; depois dessa data o Trivy volta
a quebrar a CI. A lista é curta por desenho.

### CVE-2023-54404 — `zod@3.23.8` (SPA)

- **Onde**: [apps/web/package-lock.json](../apps/web/package-lock.json)
- **O que**: `zod` 3.23.8 tem DoS via array sem limite; o fix é em `zod@4.6.6`.
- **Por que aceito até 2026-12-15**: migrar `zod` 3.x → 4.x é breaking (schema API muda —
  `.pick/.omit/.extend` ficam diferentes, `z.string().email()` vira `z.email()`). Todos os
  schemas do SPA validam inputs pequenos de usuário (login, mensagem de chat, feedback), com
  rate limit upstream de 30 req/min (REQ-SEC-08); a superfície de DoS real é mínima. A
  migração vai acompanhada de reescrita de todos os schemas em [apps/web/src/features/](../apps/web/src/features/).
- **Caminho de resolução**: PR dedicado antes de 2026-12-15, com `zod@4.x` + atualização de
  todos os `.safeParse` do SPA + regressão da suite Vitest.
- **Evidência de monitoramento**: a CI roda `trivy fs` em cada PR; qualquer CVE nova HIGH ou
  CRITICAL que não esteja aqui quebra o pipeline.

## 5. Classificador de entrada fail-open

- **Onde**: `src/app/guardrails/input.py::_classify`.
- **O que**: o classificador LLM (`input_guard`) é a terceira barreira do guardrail de entrada.
  Se o LLM falha (timeout, erro da API, saída inválida), `_classify` devolve `None` e a pergunta
  **segue** como `pass`: a heurística (segunda barreira) já rodou, mas nada mais bloqueia.
- **Por que é aceitável aqui**: a defesa que importa não depende do classificador.
  (1) A identidade vem só do token (ADR 0005), então nenhuma frase troca o cliente consultado;
  (2) cada agente só chama as tools da sua allowlist, que leem o cliente de `config`, nunca do
  texto; (3) ações com efeito colateral exigem aprovação do operador; (4) o guardrail de saída
  re-mascara PII, reescreve promessas financeiras e barra respostas sem base (grounding). Com o
  LLM fora, um ataque de prompt injection que escape da heurística chega ao supervisor, mas não
  ganha acesso a dados de outro cliente nem executa ação.
- **Caminho fail-closed**: quando `_classify` devolver `None`, marcar `input:blocked` (ou
  `degraded_response` com "tente de novo") e contar em `getnet_guardrail_blocks_total{kind="classifier_unavailable"}`;
  ligar por variável (`INPUT_GUARD_FAIL_CLOSED=1`) para não derrubar o atendimento quando o
  provedor do LLM oscila. Custo: indisponibilidade do LLM vira indisponibilidade do chat.

## 6. Canais SSE sem reconexão manual

- **Onde**: `apps/web/src/lib/sse.ts`.
- **O que**: o POST do chat (`/v1/chat/stream`) não reconecta: se o fluxo cai, a tela avisa
  "resposta interrompida" e o lojista reenvia; não há desduplicação por `trace_id` entre
  tentativas (T056 desmarcada). O canal de eventos da conversa usa a reconexão nativa do
  `EventSource`; quando o navegador desiste (HTTP 401/403/404/429), o canal fica fechado até o
  lojista recarregar a conversa.
- **Evolução**: retomar o POST por `trace_id` exigiria guardar o resultado do turno no servidor
  (o `Idempotency-Key` já faz isso para `/v1/chat`).

## 7. Testes planejados que não existem

A auditoria de 2026-11-24 desmarcou em `specs/*/tasks.md` as tasks cujo teste não existe:
contrato de `/v1/auth/*` por schemathesis (T036), verificação de e-mail com token usado/expirado
(T038), listagem de sessões (T039, parcial), "o mesmo link de reset não serve de novo" (T040,
parcial), métricas/spans de auth (T035), E2E de privacidade (T080), chat E2E com login e api
reais fora do HITL (T054, parcial) e o arquivo de roteiros dos 10+8 cenários (T126). O
comportamento existe no código; falta o teste que o prove.

## 8. Golden set abaixo do gate na rota exata

- **Onde**: [docs/reports/eval.md](reports/eval.md) (run de 2026-11-25, saída bruta em
  `docs/reports/data/`).
- **O que**: com a checagem de rota **exata** (B1), 3 passagens deram **88,3% de média e 85% de
  mínimo** (gate 95%). `sc-02-knowledge-pix` falha sempre; `sc-13-en-fanout` e
  `sc-20-fanout-crediario` falham em 2 de 3.
- **Causa medida**: o supervisor despacha certo; o agente de conhecimento não acha na base as
  taxas do Pix para PJ nem as do crediário, responde com confiança 0,2 e a política escala para um
  humano. No fan-out, a resposta correta do suporte se perde porque o turno inteiro escala.
- **Não feito de propósito**: afrouxar a checagem, baixar `CONFIDENCE_ESCALATE_HARD` ou mudar o
  gabarito.
- **Evolução**: indexar a fonte oficial dessas taxas, se publicada; no fan-out, sintetizar as
  partes com base e escalar só a parte sem base.

## 9. Itens recomendados não feitos (C1, C2)

- **C1 — juiz em lote**: `evals/judge_batch.py` não foi rodado nesta rodada; não há faithfulness /
  relevância média por tipo de cenário publicada além do juiz online amostrado em produção.
- **C2 — holdout**: não existe `evals/holdout.yaml`. As métricas de retrieval publicadas
  (recall@5 0,798 etc., ADR 0003) vêm do mesmo conjunto usado para calibrar o limiar
  `RAG_DENSE_THRESHOLD`, então podem estar otimistas; não há número fora da calibração.

## 10. Classificador de entrada barra frases legítimas

- **Onde**: `prompts/input_guard.md` (papel `input_guard`), medido por `evals/guard_rates.py`.
- **O que**: com o modelo real, o classificador bloqueia os 38 ataques da suíte, mas também **16
  das 34 frases legítimas** de `tests/security/data/legit_phrases.yaml` (47%): correções do tipo
  "ignore a mensagem anterior", perguntas com o CPF/CNPJ do próprio lojista e até "qual é o e-mail
  de contato do suporte". A heurística (E1) já não barra essas frases; o bloqueio vem do LLM.
- **Evidência**: [reports/data/guard-rates-2026-11-25.json](reports/data/guard-rates-2026-11-25.json).
- **Evolução**: incluir exemplos negativos no prompt (correções de mensagem, documento do próprio
  lojista, perguntas de contato) e medir de novo as duas taxas juntas, exigindo que o bloqueio dos
  ataques continue ≥ 95%.

---

## O que NÃO é shortcut

- **Identidade** (`user_id`): nunca vem do payload, só do JWT (`sub`), via `config["configurable"]`.
  Isso é regra dura — vide [ADR 0005](adr/0005-identidade-via-token.md).
- **PII**: `mask()` roda antes de qualquer LLM/log/checkpoint; re-máscara no output. Teste
  dedicado em `tests/security/test_pii.py`.
- **HITL**: `interrupt()` + `Command(resume=...)` **na mesma thread** e com a identidade do
  dono do ticket. Ver [ADR 0004](adr/0004-hitl-interrupt.md).
- **Retrieval**: o padrão é a busca densa (`RAG_STRATEGY=dense`, limiar 0,40) sem reranker
  (`SKIP_RERANKER=1` no compose); o pipeline híbrido BM25 + densa + RRF + rerank existe e é
  testado, mas é opcional (`RAG_STRATEGY=hybrid_rerank`). Ver [ADR 0003](adr/0003-busca-hibrida.md).
