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
- **Limite que fica**: é por `user_id` (`users.id`). Desde 2026-11-27 cada conta web de
  operador/admin tem o próprio `users.id` (o id da conta), então cada uma tem o seu balde
  (revisão QA N2). Só os tokens Bearer emitidos por `scripts/issue_token.py` para os usuários
  seed (`operador01`, `admin01`) continuam sendo esses usuários.

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
- **Custo do canal de eventos** (revisão QA N9): `/v1/conversations/{id}/events` consulta o banco
  a cada 1 s (o ticket, o checkpoint da conversa e o último handoff: 3 leituras) e fecha após 60
  ciclos; o navegador reabre. São no máximo 3 canais por usuário (`SSE_EVENTS_MAX_PER_USER`), ou
  seja até 9 leituras/s por usuário com a conversa aberta, todas por chave primária. Com o volume
  atual isso não pesa no pool (30 conexões); com muitos lojistas simultâneos, sim.
  **Evolução**: `LISTEN/NOTIFY` do Postgres (um gatilho em `handoffs`/`conversations` e um
  ouvinte por processo que distribui aos canais), mantendo a ordem `final_message` antes de
  `handoff_update`, que hoje vem da leitura em sequência.

## 7. Testes planejados que não existem

A auditoria de 2026-11-24 desmarcou em `specs/*/tasks.md` as tasks cujo teste não existe:
contrato de `/v1/auth/*` por schemathesis (T036), verificação de e-mail com token usado/expirado
(T038), listagem de sessões (T039, parcial), "o mesmo link de reset não serve de novo" (T040,
parcial), métricas/spans de auth (T035), chat E2E com login e api reais fora do HITL (T054,
parcial) e o arquivo de roteiros dos 10+8 cenários (T126). O comportamento existe no código;
falta o teste que o prove. O E2E de privacidade (T080) foi feito em 2026-11-26, depois que o QA
independente mostrou que a tela era um placeholder; a listagem de sessões (T039) ganhou teste de
integração para o lojista vinculado (QA-060), mas a task continua parcial.

## 8. Golden set: o cenário do Pix (estável desde 2026-11-27)

- **Onde**: [docs/reports/eval.md](reports/eval.md) (segunda rodada de 2026-11-26, saída bruta em
  `docs/reports/data/golden-set-2026-11-26-v2.*`).
- **O que**: rota exata **98,3% de média e 95% de mínimo** em 3 passagens (gate 95%: passa; era
  86,7% na primeira rodada do dia e 88,3% em 2026-11-25). `sc-02-knowledge-pix` (taxa do Pix
  para PJ) passa em 1 de 3; nenhum cenário falha sempre.
- **Por que o sc-02 ainda oscila**: as páginas da Getnet se contradizem sobre a taxa do Pix e não
  trazem número. A resposta certa é relatar as duas fontes e dizer onde o lojista vê o valor do
  plano; ela sai na maioria das vezes, mas numa passagem o verificador de saída ainda trocou a
  resposta e noutra a especialista ficou abaixo de 0,70 e o turno escalou.
- **2026-11-27** ([eval.md](reports/eval.md), saída em `golden-set-2026-11-27.*`): rota
  **100%**, fontes **100%**, nenhum cenário instável. O `sc-02` voltou a passar nas 3 passagens
  depois que a evidência do verificador passou a seguir a numeração das citações (§14).
- **Juiz em lote**: relevância 0,70 no conhecimento, com `sc-13`, `sc-17` e `sc-20` em 0 (ver
  eval.md); não investigado nesta rodada.
- **Não feito de propósito**: baixar `CONFIDENCE_REPLAN`/`CONFIDENCE_ESCALATE_HARD` ou mudar o
  gabarito.
- **Evolução**: fonte oficial única sobre a taxa do Pix (se a Getnet publicar); olhar as três
  respostas com relevância 0.

## 9. Itens recomendados: C1 e C2 feitos, com ressalvas

- **C1 — juiz em lote**: rodado em 2026-11-26 (uma passagem, 12 respostas julgadas): support
  1,00/1,00, knowledge 0,75/0,75 (faithfulness/relevância), com `sc-17-followup` e
  `sc-20-fanout-crediario` em 0. Uma passagem só; ver [reports/eval.md](reports/eval.md).
- **C2 — holdout**: feito em 2026-11-26 (`evals/retrieval_holdout.yaml`, 30 perguntas escritas
  depois da calibração): recall@5 **0,967** da busca entregue. No conjunto de calibração, medido
  de novo no mesmo dia, o recall@5 caiu para **0,702** (era 0,798; causa não investigada). O
  holdout tem perguntas pontuais de uma página; a calibração, temas amplos. Ver ADR 0003.

## 10. Classificador de entrada: casos que ainda erram

- **Onde**: `prompts/input_guard.md` (papel `input_guard`), medido por `evals/guard_rates.py`
  pela API e por uma sonda direta no classificador.
- **Histórico**: 16 de 34 frases legítimas da suíte bloqueadas (2026-11-25), 12 de 34 e 2 de 32
  frases do QA (manhã de 2026-11-26). A causa era tratar CPF/CNPJ no texto como acesso a dados
  de terceiro, o que a arquitetura já impede (ADR 0005), e decidir a classe antes do motivo.
- **O que mudou (2026-11-26, revisão externa item 1)**: documento no texto não é mais ataque;
  `injection` é só instrução contra as regras/identidade do assistente ou pedido de dados que o
  texto atribui a outra parte. O classificador responde motivo → alvo da instrução → dono do
  dado → assunto → classe, bloqueia quando a classe **ou** os campos indicam ataque, e roda com
  temperatura 0. Os exemplos do prompt são paráfrases: um teste
  (`tests/unit/test_input_guard_decision.py`) garante que nenhuma frase das suítes está no prompt.
- **Agora**, pela API ([guard-rates-2026-11-26-v2.json](reports/data/guard-rates-2026-11-26-v2.json)):
  **38/38** ataques bloqueados e **0/34** frases legítimas bloqueadas. Na sonda direta
  ([input-guard-probe-2026-11-26.json](reports/data/input-guard-probe-2026-11-26.json), 3
  passagens): 0/96 nas frases do QA, 0/60 num holdout legítimo escrito depois do prompt, 48/48
  ataques do holdout e todas as perguntas fora de escopo bloqueadas.
- **Falso positivo por conjunto** (uma taxa interna de 0% não vale para frases que o projeto não
  viu; revisão QA N5):

  | Conjunto | Quem escreveu | Exemplos no prompt? | Bloqueadas (legítimas) | Fonte |
  |---|---|---|---|---|
  | Suíte do projeto (34) | nós | só paráfrases, nunca as frases | 0/34 (API), 0/102 (sonda ×3) | guard-rates / probe 2026-11-26 |
  | Frases do QA independente (32) | QA | não | 0/96 (sonda ×3) | probe 2026-11-26 |
  | — mesmas 32, harness do próprio QA | QA | não | **2/32 (6,2%)**, medido antes da última mudança do prompt, ainda não remedido por ele | [qa-e2e-resposta.md](reports/qa-e2e-resposta.md) |
  | Holdout legítimo (20) | nós, depois do prompt | não | 0/60 (sonda ×3) | probe 2026-11-26 |
  | Pedido com documento após turno anterior (4) | nós | não | **3/12 (sonda ×3)** | probe 2026-11-26 |

  O número a citar para frases de terceiros é o do harness do QA (6,2%) até ele remedir; a
  sonda mede só o classificador, sem a API em volta.
- **O que ainda erra**: com o turno anterior no contexto, um pedido seguinte com um documento
  diferente e sem possessivo ("Meu CPF é ..." e depois "Liste as vendas do CNPJ ... de ontem")
  ainda é lido como de outra parte (sonda `ctx-legit`: 3 de 12 execuções bloqueadas, todas
  nessa frase). Com "meu"/"minha" passa. A sonda usa um conjunto pequeno escrito por nós; o
  conjunto do QA independente é a validação externa.
- **Evolução**: medir com o próximo QA independente e com conversas reais; se o caso de contexto
  aparecer, passar a classificar o dono do dado só pela última mensagem.

## 11. Latência acima do SLO com o modelo real

- **Onde**: grafo inteiro (`gpt-4o-mini` em até 6 chamadas sequenciais por turno: guardrail de
  entrada, supervisor, reescrita da busca, especialista, síntese quando há mais de um,
  guardrail de saída).
- **Medições com 20 usuários × 5 min** (meta: p95 8 s simples / 15 s composto):

  | Rodada | p95 simples | p95 composto | média simples | pedidos | erros |
  |---|---|---|---|---|---|
  | QA independente | 17,7 s | 18,4 s | | | |
  | Rodada 3 do QA | 28,3 s | 27,3 s | | 75 | 0 |
  | 2026-11-26, plano em paralelo | 25,4 s | 30,6 s | 16,1 s | 332 | 0 |
  | 2026-11-26, + pool de 30 conexões | 17,8 s | 28,0 s | 10,5 s | 459 | 0 |
  | **2026-11-27, busca em paralelo + consultas do plano** | **16,5 s** | **21,7 s** | **9,4 s** | **503** | 0 |

  Saídas em [reports/data/load-2026-11-26.txt](reports/data/load-2026-11-26.txt),
  [load-2026-11-26-pool30.txt](reports/data/load-2026-11-26-pool30.txt) e
  [load-2026-11-27.txt](reports/data/load-2026-11-27.txt); tempo por etapa em
  [stages-2026-11-27.json](reports/data/stages-2026-11-27.json).
- **O que foi feito**: (1) o plano do supervisor roda em paralelo com o classificador de entrada
  (sem carga: média 7,7 s → 7,2 s). Custo aceito (revisão QA N3): quando a entrada é bloqueada, o
  plano é cancelado, mas os tokens que ele já gastou se perdem — um plano lê o prompt do supervisor
  (3,9 mil caracteres, cerca de mil tokens) mais o esquema e a mensagem, menos de US$ 0,001 no
  `gpt-4o-mini`, e só turnos bloqueados pagam isso; (2) o pool de conexões da api com o
  Postgres passou de 10 para 30 (`DB_POOL_MAX_SIZE`): cada passo do grafo grava um checkpoint,
  e com 20 turnos ao mesmo tempo os turnos esperavam conexão. Com isso a média sob carga caiu 35% e a vazão subiu 38%.
- **O que foi medido e descartado**: a CPU da api ficou em ~35% com o pool antigo e o Postgres
  em 2% (não era CPU nem banco); 3 workers uvicorn com o pool novo não melhoraram o p95 (20,4 s /
  27,7 s numa corrida de 90 s, contra 18,4 s / 22,1 s com um processo) e exigiriam métricas
  Prometheus multiprocesso; a OpenAI recusou 3 de 332 pedidos (não é limite da chave).
- **Feito em 2026-11-27**: (3) a busca com a pergunta como foi escrita roda em paralelo com a
  reescrita da busca; (4) o plano do supervisor já traz as consultas de busca, e o especialista
  de conhecimento pula a reescrita (uma chamada ao modelo a menos), exceto em pergunta de taxa:
  ali o plano nunca pedia a página de MDR e a resposta do Pix dizia que não há custo por
  transação, então pergunta de taxa continua com a reescrita dedicada; (5) histogramas de tempo
  por etapa e por chamada ao modelo no Prometheus. Resultado: p95 17,8 s → 16,5 s (simples) e
  28,0 s → 21,7 s (composto), vazão +10% (uma corrida anterior no mesmo dia deu 15,7 s /
  20,7 s).
- **Medido e descartado em 2026-11-27 — modelo menor nas checagens**
  ([checks-model-2026-11-27.json](reports/data/checks-model-2026-11-27.json)): `gpt-5.4-nano`,
  `gpt-4.1-mini` e `gpt-4.1-nano` empataram com o `gpt-4o-mini` em ataques e frases legítimas,
  mas nenhum foi mais rápido de forma relevante (±0,2 s por chamada). O `gpt-4.1-mini` como
  verificador de saída aceitou mais respostas honestas (8/8 contra 4/8), mas deixou passar 3/3
  "o Pix PJ é totalmente gratuito", que contradiz a fonte de MDR. `OPENAI_MODEL_CHECKS` fica vazio;
  a opção (inclusive por papel, `OPENAI_MODEL_CHECKS_ROLES`) existe para medir outro candidato.
- **O que ainda falta**: sob carga cada chamada ao modelo fica mais lenta (1,4 a 2,7 s cada), e
  o turno ainda tem 4 a 5 em sequência; o p95 segue acima da meta. O próximo passo de maior
  efeito para o lojista é a resposta aparecer enquanto é gerada (o endpoint `/v1/chat/stream` já
  existe), mas o texto só pode ser mostrado depois do verificador de saída; mostrar antes exige
  decidir como tratar uma resposta que o verificador troca depois de exibida.

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
- **Resolvido em 2026-11-27**: as contas web de operador/admin agiam todas como o primeiro
  `users.id` seed do papel, e o `operator_id` do ticket, o dono do checkpoint e o rate limit não
  distinguiam dois operadores. Agora cada conta é o próprio `users.id`
  (`tests/security/test_staff_identity.py`). Conversas antigas feitas por uma conta de
  operador/admin ficam no usuário seed e não aparecem na lista dela.

## 14. Guardrail de saída: falsos positivos do verificador

- **Onde**: `src/app/guardrails/output.py` (papel `output_guard`, `prompts/grounding.md`).
- **O que**: na revalidação do QA (2026-11-26, 38 turnos com o modelo real) 7 respostas foram
  trocadas por "não encontrei base suficiente". A causa principal do QA (compostas julgadas sem os
  dados da conta) foi corrigida; dos 7 que sobraram, uns são acertos (número ou preço diferente da
  passagem) e o caso do WhatsApp é um falso positivo conhecido do `gpt-4o-mini` como verificador.
  Na rodada 3 do QA o S01 em inglês (Get Clássica × Get Smart) foi trocado uma vez; repetido 10
  vezes em seguida com a mesma pergunta, passou nas 10 com fonte. É o mesmo falso positivo
  intermitente, não um defeito de recuperação.
- **2026-11-26**: o verificador roda com temperatura 0 e ganhou a regra 10 de
  `prompts/grounding.md` ("número ausente não é afirmação sem base"): com a mesma evidência, a
  resposta honesta sobre a taxa do Pix passou 4/4 e a que inventa "0,99%" foi bloqueada 4/4. No
  golden ainda trocou a resposta do sc-02 em 1 de 3 passagens.
- **2026-11-27, causa encontrada e corrigida**: o especialista renumera as citações por página
  citada ("taxa por transação [1]", com [1] = página de MDR), mas o verificador numerava as
  passagens na ordem da busca ([1] = página do Pix, que não fala de taxa). Ele conferia a
  afirmação contra a passagem errada e trocava respostas corretas. Agora a evidência é gravada na
  ordem das citações (`evidence_by_source`) e numerada por especialista
  (`tests/unit/test_evidence_numbering.py`). A mesma pergunta do Pix 10 vezes pela API: main 0/10
  com fonte, este build **10/10**; o verificador aceitou 8/8 respostas honestas e continuou
  barrando 15/15 inventadas (inclusive "o Pix PJ é gratuito")
  ([checks-model-2026-11-27.json](reports/data/checks-model-2026-11-27.json)). O mesmo
  desalinhamento afetava respostas compostas, em que conhecimento e suporte numeram cada um a
  partir de [1].
- **O que sobra**: o verificador ainda é o `gpt-4o-mini` e pode errar em outros casos (o do
  WhatsApp acima). Trocar por `gpt-4.1-mini` foi medido e recusado: deixou passar uma resposta
  que contradiz a fonte (§11). Uma regra extra no prompt ("nenhum registro" sustenta "não há")
  também foi testada e retirada: piorava as respostas honestas sem ajudar o caso que a motivou.
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
