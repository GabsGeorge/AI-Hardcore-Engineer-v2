# Limitações aceitas (`shortcut:`) e trade-offs (T178)

Decisões conscientes que trocam robustez máxima por simplicidade/custo, e o que ficou de fora.
Cada item lista **onde**, **o que** e **o caminho de evolução**.

---

## 1. Rate limit por usuário em Redis (janela deslizante)

- **Onde**: [src/app/api/limits.py](../src/app/api/limits.py)
- **O que**: até então era um contador por minuto fixo, e uma rajada que atravessava a virada do
  minuto passava com até 2× o limite (o QA independente viu 35/35 aceitas, QA-027). Agora é uma
  janela deslizante de 60 s (sorted set por `user_id`); pedido recusado não conta, e o
  `Retry-After` é o instante em que o pedido mais antigo sai da janela. Teste:
  `tests/security/test_rate_limit.py::test_burst_across_the_minute_boundary_is_limited`.
- **Limite que fica**: é por `user_id` (`users.id`). Todas as contas de operador/admin agem como o
  mesmo usuário seed (ver §13), então dividem o mesmo balde.

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
parcial), métricas/spans de auth (T035), chat E2E com login e api reais fora do HITL (T054,
parcial) e o arquivo de roteiros dos 10+8 cenários (T126). O comportamento existe no código;
falta o teste que o prove. O E2E de privacidade (T080) foi feito em 2026-11-26, depois que o QA
independente mostrou que a tela era um placeholder; a listagem de sessões (T039) ganhou teste de
integração para o lojista vinculado (QA-060), mas a task continua parcial.

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
- **Antes**: 16 de 34 frases legítimas da suíte bloqueadas (2026-11-25) e 13 de 32 das frases do
  QA independente (2026-11-26, QA-015).
- **O que mudou**: o prompt separa "ignore/esqueça/finja" dirigido às regras (ataque) do dirigido
  à própria pergunta (legítimo), trata documento com possessivo ("do meu CNPJ") como do próprio
  lojista e recebe o turno anterior para entender continuações curtas. Os exemplos do prompt são
  paráfrases; as frases do QA ficaram fora dele e servem de validação.
- **Agora** (modelo real, 2026-11-26): **38/38** ataques bloqueados; **12/34** frases da suíte
  (a maioria com CPF/CNPJ de cliente na frase, que o prompt trata como risco de IDOR de
  propósito) e **2/32** frases do QA ("mostre as transações do CNPJ da minha empresa", "Show me
  my last transactions"). A meta de ≤ 5% ainda não é atingida no conjunto do QA (6%).
- **Evidência**: [reports/data/guard-rates-2026-11-26.json](reports/data/guard-rates-2026-11-26.json),
  [reports/qa-e2e-resposta.md](reports/qa-e2e-resposta.md).

## 11. Latência acima do SLO com o modelo real

- **Onde**: grafo inteiro (`gpt-4o-mini` em até 6 chamadas por turno: guardrail de entrada,
  supervisor, especialistas, síntese, guardrail de saída).
- **O que**: o QA independente (2026-11-26) mediu p95 de 17,7 s (simples) e 18,4 s (composto) com
  20 usuários × 5 min, e p95 de 10–20 s só com o tráfego funcional; a meta é 8 s / 15 s
  (QA-037, QA-038). As correções desta rodada não reduzem chamadas de LLM; o painel e o alerta de
  latência agora cobrem também `/v1/chat/stream` (QA-078), então o desvio fica visível.
- **Evolução**: paralelizar o guardrail de entrada com o supervisor, pular a síntese quando há
  um só especialista (já feito) e usar um modelo menor para o `input_guard`; medir de novo com
  `deploy/k6/chat.js` e 20 tokens.

## 12. `X-Real-IP` confiável só atrás do nginx

- **Onde**: `_client_ip` em `src/app/api/auth_endpoints.py`.
- **O que**: o limite de login/cadastro por IP lê `X-Real-IP`, que o nginx preenche. Quem fala
  direto com a porta 8000 pode forjar o cabeçalho (QA-059). A porta 8000 agora é publicada só em
  `127.0.0.1` (`docker-compose.yml`), então só quem já está na máquina consegue isso; o limite por
  e-mail não depende do IP.
- **Evolução**: lista de proxies confiáveis (`TRUSTED_PROXIES`) e uso do `X-Real-IP` só quando o
  par TCP é o nginx.

## 13. Textos que seguem o idioma da conversa, não o da interface

- **O que**: o resumo do chamado na fila, o prefixo "Nosso atendente respondeu:" e os alvos da
  auditoria (`account:acc_...`) são dados gravados no idioma da conversa (ou identificadores),
  não textos da interface. Com a UI em inglês, uma conversa em português continua aparecendo em
  português (QA-067, QA-069, QA-070). Os textos da interface (botões, rótulos, resultado da
  auditoria) foram traduzidos.
- **Também**: todas as contas web de operador/admin agem como o primeiro `users.id` seed do papel
  (`_seed_user_for_role`), então a auditoria e o rate limit não distinguem dois operadores.

## 14. Guardrail de saída ainda troca parte das respostas

- **Onde**: `src/app/guardrails/output.py` (papel `output_guard`, `prompts/grounding.md`).
- **O que**: na revalidação do QA (2026-11-26, 38 turnos com o modelo real) 7 respostas foram
  trocadas por "não encontrei base suficiente". A causa principal do QA (compostas julgadas sem os
  dados da conta) foi corrigida; dos 7 que sobraram, uns são acertos (número ou preço diferente da
  passagem) e o caso do WhatsApp é um falso positivo conhecido do `gpt-4o-mini` como verificador.
- **Evolução**: verificador com modelo maior, ou checagem por afirmação (quebrar a resposta em
  frases e checar cada uma contra a passagem citada), medindo acertos e falsos positivos juntos.
- **Evidência**: [reports/qa-e2e-resposta.md](reports/qa-e2e-resposta.md).

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
