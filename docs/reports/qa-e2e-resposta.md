# Resposta ao QA E2E independente (2026-11-26)

O relatório do QA (`docs/reports/qa-e2e.md` na branch `qa/e2e`, commit `62ba3d0`) testou a branch
`fix/frontend-eval-guardrail` em `43bcb8b` e listou 92 achados (16 altos, 27 médios, 37 baixos, 12
sem gravidade). Esta página diz, para cada um, o que mudou no código, qual teste cobre e o que foi
medido de novo com o modelo real; o que não foi corrigido está em
[limitations.md](../limitations.md), com o motivo.

Nenhum teste existente foi afrouxado. Mudanças em testes antigos, todas por mudança de
comportamento pedida pelo QA:

- `tests/contract/test_feedback.py`: o feedback agora só vale para um `trace_id` de resposta que o
  próprio usuário recebeu (QA-024); os testes gravam esse turno antes de avaliar e ganharam o
  caso do trace de outro lojista (404).
- `tests/security/test_rate_limit.py`: a docstring falava em janela fixa; ganhou o caso da rajada
  que atravessa a virada do minuto.
- `tests/unit/test_conversation_events.py`: o repositório simulado devolvia o `ticket_id` como
  texto; o Postgres devolve `UUID`, e foi exatamente isso que quebrava o canal (QA-023). O
  simulado agora devolve `UUID`.

## Medição de novo com o modelo real

Stack do compose com as correções, `gpt-4o-mini`, base de 96 páginas / 316 trechos, 2026-11-26.
Evidência: [data/qa-recheck-2026-11-26.json](data/qa-recheck-2026-11-26.json) (turnos de chat e
frases do QA) e [data/guard-rates-2026-11-26.json](data/guard-rates-2026-11-26.json) (ataques e
frases da suíte).

| Medida | QA (antes) | Agora |
|---|---|---|
| Ataques da suíte bloqueados na entrada | 38/38 | **38/38** |
| Frases legítimas do QA bloqueadas na entrada (32, fora do prompt) | 13/32 (40,6%) | **2/32 (6,3%)** |
| Frases legítimas da suíte bloqueadas (34, a maioria com CPF/CNPJ) | 16/34 | **12/34** |
| Turnos bloqueados na entrada nos 14 casos de chat do QA, 2 passagens (38 turnos, incl. continuações curtas) | continuações bloqueadas | **0/38** |
| Cenário 8 (recusa) consulta a transação e o código 51 | 0/6 | **4/4** |
| Turnos trocados pelo guardrail de saída | compostas 4/4 na 1ª rodada; S02, S07, S09, S10 intermitentes | **7/38** |

Os 7 bloqueios de saída que sobraram, pelo motivo que o verificador registrou (o log agora traz
quantas passagens e quantos dados de conta ele recebeu):

- **Acertos prováveis** (a resposta diz algo que as passagens não dizem): S09 afirmou 48 parcelas
  quando as passagens recuperadas falam em 24 ou 12; M04 deu um preço da Get Smart diferente do
  da passagem; C04 afirmou que o Pix dispensa conta específica.
- **Falso positivo conhecido**: C03 (2 de 2) e uma composta do README. A página sobre a
  descontinuação do WhatsApp Business diz que o Link de Pagamento continua servindo para vender
  pelo WhatsApp, e o verificador leu só a descontinuação. O prompt ganhou uma regra geral (julgar
  pelo texto da passagem, não pelo título); o `gpt-4o-mini` ainda erra esse caso. Não foi feito
  ajuste específico para ele.

O que fica pendente está em [limitations.md](../limitations.md) §10 e §14; a régua do guardrail de
saída não foi afrouxada.

## Achados, um a um

### Preparação

| QA | O que era | O que mudou | Evidência |
|---|---|---|---|
| QA-001 | Recriar a api (subir o profile `obs`) deixava a UI inteira em 502: o nginx resolvia `api` só na partida | O nginx resolve a api por requisição: `resolver` gerado na partida a partir do `/etc/resolv.conf` (Docker e Podman) e `proxy_pass $api` | `tests/test_nginx_conf.py`; ao vivo: api recriada (IP .42 → .44) e `/v1/auth/*` via nginx seguiu respondendo, sem reiniciar o web |
| QA-002 | Cada `docker compose run --rm api …` roda o seed, que apagava e recriava os estabelecimentos e perdia o vínculo com a conta web | O seed faz upsert dos estabelecimentos (só terminais, transações e recebíveis são refeitos para a data de referência); `account_id` fica | `tests/integration/test_admin_link_merchant.py::test_reseed_keeps_the_admin_link`; ao vivo: vínculo → `docker compose run --rm ingest` → vínculo continua |
| QA-003 | Vincular ao `cliente1988` uma conta recém-cadastrada dava 500 (`UNIQUE account_id`: o cadastro já cria um estabelecimento demo) | O vínculo libera, na mesma transação, o estabelecimento demo que a conta tinha; a auditoria registra qual (`released_merchant`) | `test_self_registered_merchant_links_to_seed_establishment`; ao vivo: 204 e chat com os dados do `cliente1988` |

### Chat

| QA | O que era | O que mudou | Evidência |
|---|---|---|---|
| QA-004/005/008/009/010–013/072 | Guardrail de saída trocava respostas certas por "não encontrei base suficiente", sobretudo nas compostas | Causa: numa resposta composta (knowledge + support) o verificador recebia só as passagens da base, e cada dado verdadeiro da conta (terminal offline, valores, datas) parecia sem fonte. Agora o support guarda o que as ferramentas devolveram e o verificador recebe isso junto; o prompt `grounding.md` explica os dois tipos de fonte e que `[n]` é referência, não afirmação. A régua não mudou: uma afirmação sem fonte continua bloqueando | `tests/unit/test_output_guardrail_context.py` (2 casos novos); medição acima |
| QA-006/007/085/086 | "Erro de transação recusada" virava diagnóstico de conectividade; a recusa código 51 nunca era consultada | `support.md` e as descrições das ferramentas separam recusa de venda (`get_recent_transactions` com `status=declined` + `lookup_decline_code`) de problema de conexão (`diagnose_terminal`) | medição acima |
| QA-014/018/019/020 | Continuações curtas ("E quanto custa?", "And on debit?") bloqueadas na entrada | O classificador de entrada recebe o turno anterior (limitado a 400 caracteres, marcado como dado) para entender a continuação; só a última mensagem é classificada | `tests/unit/test_input_guardrail*.py`; medição acima |
| QA-015, QA-044–056 | 40,6% das frases legítimas do QA bloqueadas na entrada | `input_guard.md`: "ignore/esqueça/finja/aja como" só é ataque quando o alvo são as regras, o prompt, a persona ou dados de terceiros; pedir os próprios dados nunca é terceiro. Os exemplos do prompt são paráfrases, não as frases do QA, que ficaram como conjunto de validação separado | medição acima (ataques e frases do QA) |
| QA-017 | Números de citação não batiam com a lista de fontes ([3], [5] com 2 fontes) | As citações apontavam para trechos; a lista é por página. As citações são renumeradas pela lista (uma entrada por URL, na ordem em que aparecem) | `tests/unit/test_citation_renumber.py` |
| QA-021 | Conta de lojista sem estabelecimento: chat dava 500 (FK) | 409 `merchant_not_linked` nos dois endpoints de chat, e a tela diz que a conta ainda não está vinculada | `tests/integration/test_merchant_account_fixes.py`, `ChatPage.session.test.tsx` |
| QA-041 | Conversa reaberta pelo histórico perdia as fontes | Tabela `chat_turns` (migração `0003`) guarda dono, mensagem e fontes de cada resposta; `GET /v1/conversations/{id}` devolve `sources` e `trace_id` por resposta | contrato atualizado; ao vivo |
| QA-057 | 4.001 caracteres aceitos no campo e o 422 virava "tente de novo" | `maxLength=4000` no campo, aviso de limite e mensagem própria para 422 | `ChatPage.session.test.tsx` |
| QA-084 | S02 PT escalou para humano uma pergunta de clima | Intermitente (confiança baixa no resultado da busca web); não reproduzido na medição acima. Fica registrado | — |

### Human-in-the-loop

| QA | O que era | O que mudou | Evidência |
|---|---|---|---|
| QA-022 | Detalhe do chamado mostrava só o JSON da rota | O contexto segue o contrato: `merchant` (id e nome), `recent_messages` (até 6, mascaradas), `route`, `tools_used`. A tela mostra lojista e mensagens como texto; a rota fica recolhida | `tests/unit/test_ticket_context.py`, `TicketContext.test.tsx` |
| QA-023 | `/events` entregava `final_message` e morria antes do `handoff_update` (`TypeError` com UUID) | O `ticket_id` vira texto antes do `json.dumps` | `tests/unit/test_conversation_events.py` com `UUID` |
| QA-036 | Ticket "sumiu" da fila após restart | O token Bearer do harness tinha expirado (TTL 1 h, 401); o ticket fica no Postgres. Sem mudança | — |

### Segurança

| QA | O que era | O que mudou | Evidência |
|---|---|---|---|
| QA-016, QA-026 | `/privacy` era um placeholder "Em construção" (T082 estava marcada como feita) | Tela com as duas ações (apagar conversas, excluir conta com senha), confirmação digitada e resumo. O "apagar conversas" usa `DELETE /v1/me/data` (todas as conversas), que já existia; apagar uma conversa avulsa (`DELETE /v1/conversations/{id}`) não está no contrato | `PrivacyPage.test.tsx`, `e2e/05-privacy.spec.ts` |
| QA-024/025 | Feedback aceito para `trace_id` de outro lojista | `POST /v1/feedback` só aceita trace de resposta do próprio usuário (404 nos outros) | `tests/contract/test_feedback.py` |
| QA-027 | Rajada de 35 passou (contador por minuto fixo) | Janela deslizante de 60 s em Redis | `tests/security/test_rate_limit.py` |
| QA-028, QA-061–063 | `/docs`, `/redoc`, `/openapi.json` abertos | Desligados com `APP_ENV=production`; a porta 8000 do compose só em `127.0.0.1`. Ficam no ambiente local, onde o teste de contrato do OpenAPI os usa | `tests/unit/test_api_docs_exposure.py` |
| QA-029–035 | CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy` ausentes em `/`, `/assets/`, `/healthz` | `add_header` saiu de todos os `location` (no nginx ele apagava os do `server`) | `tests/test_nginx_conf.py`; ao vivo: os quatro cabeçalhos em `/`, `/chat`, `/login`, `/healthz` e assets |
| QA-058 | CSRF pulado com `Authorization: Bearer ` vazio + cookie | Só um Bearer com token pula o CSRF; `Authorization` inválido é 401, nunca cai no cookie | `test_empty_bearer_does_not_skip_csrf` |
| QA-059 | `X-Real-IP` forjável na porta 8000 | Porta 8000 só em `127.0.0.1`; o resto em [limitations.md](../limitations.md) §12 | — |
| QA-060 | Lojista vinculado listava sessões vazias | Sessões são buscadas (e revogadas) pela conta (`acc_*`), não pelo `users.id` | `test_linked_merchant_sees_and_revokes_its_own_sessions`; ao vivo |
| QA-081–083 | Cadastro de teste recebeu 429 | É o limite de 5 cadastros por hora por IP (E2) agindo sobre o harness | — |
| — | FR-015 (avaliar cada resposta) não tinha tela; não estava no relatório nem nas limitações | Botões "Útil"/"Não ajudou" em cada resposta com `trace_id` | `MessageFeedback.test.tsx` |

### Resiliência e carga

| QA | O que era | Situação |
|---|---|---|
| QA-037, QA-038 | p95 de 17,7 s / 18,4 s com 20 usuários; 10–20 s com tráfego funcional | Não corrigido: [limitations.md](../limitations.md) §11. O painel e o alerta agora cobrem o `/v1/chat/stream` (QA-078), então o desvio aparece |
| QA-088 | k6 do README falhou | O README mandava usar um token só para 20 usuários (todos no mesmo limite de 30/min); agora aponta para a receita com 20 tokens de `load-test.md`. O "connection refused" da rodada coincidiu com a api reiniciando nos testes de falha |

### UI/UX e acessibilidade

| QA | O que mudou | Evidência |
|---|---|---|
| QA-039 | Abaixo de 640 px a navegação vira uma segunda linha rolável, com os mesmos links | — |
| QA-040 | Sessão expirada no chat leva a `/login?next=…&expired=1`, com aviso | `ChatPage.session.test.tsx` |
| QA-064, QA-066 | Login sem `next` e `/` levam cada papel à sua tela (lojista `/chat`, operador `/queue`, admin `/admin`) | `TicketContext.test.tsx` (`homeFor`), `e2e/05-privacy.spec.ts` |
| QA-065 | Auditoria em celular vira cartões; "Carregar mais" pagina por `before_id` | contrato `admin.yaml` |
| QA-067, QA-069, QA-070 | São dados no idioma da conversa (resumo do chamado, prefixo do atendente, alvo `account:…`), não texto da interface: [limitations.md](../limitations.md) §13. O resultado da auditoria e o botão de senha foram traduzidos | — |
| QA-068 | `<html lang>` acompanha o idioma desde a primeira renderização | — |
| QA-071 | O foco volta ao campo de mensagem quando a resposta termina | `ChatPage.session.test.tsx` |
| QA-073 | Palavra longa quebra dentro do balão | — |
| QA-074 | E-mail recusado pelo servidor aparece no campo E-mail | — |

### Observabilidade e documentação

| QA | O que mudou | Evidência |
|---|---|---|
| QA-042 | O juiz online recebia só título e URL das fontes e não tinha como verificar nada; agora recebe o texto das passagens e os dados da conta (até 8.000 caracteres) | `tests/unit/test_observability_qa_fixes.py` |
| QA-043, QA-090 | `docker-compose.obs.yml` + `--profile eval` sem `obs` quebrava (`tempo` indefinido): `depends_on` com `required: false` | `docker compose ... config` |
| QA-075–077 | Painéis "No data": rótulos conhecidos nascem em 0 | `tests/unit/test_observability_qa_fixes.py` |
| QA-078 | Latência, volume e custo ignoravam `/v1/chat/stream`: nova métrica `getnet_chat_turn_duration_seconds` nos dois endpoints, usada pelos painéis, pelo custo e pelo alerta | idem |
| QA-079, QA-089 | Contagem de testes no README desatualizada: números atualizados | README |
| QA-080, QA-092 | `smoke_report` com resultados diferentes do `smoke.md`: o relatório registra rodadas datadas; a rodada desta revisão está na medição acima | — |
| QA-091 | `curl 127.0.0.1:9100/metrics` só vale no modo local; com o compose a leitura é de dentro do container | `observability.md` |

## Rodada 3 (QA em `554d9a3`, relatório na branch `qa/e2e`, commit `b6ed3b0`)

Cada achado foi conferido primeiro nas evidências do próprio QA (`evidence/http/*.jsonl`,
`evidence/results/*.jsonl`) e depois rodando de novo o comando do harness, **uma fase por vez**.

| QA | Conclusão | O que mudou | Evidência |
|---|---|---|---|
| QA-R3-001 (3.8.a, Alto) | Não era falha da plataforma. Na rodada 3 vieram 30 × 200 e 5 × 429, todos com `retry-after: 60` (registrado em `evidence/http/3-security.jsonl`). O harness procurava `Retry-After` diferenciando maiúsculas e minúsculas; o uvicorn manda o nome em minúsculas, e o nome de cabeçalho HTTP não diferencia maiúsculas (RFC 9110 §5.1) | Nada na plataforma. No harness, a leitura do cabeçalho ignora maiúsculas (1 linha em `p3_security.py`) | `p3_security.py 3.8 --go`: 3.8.a PASS, `retry-after=58`; 3.8.b–d PASS |
| QA-R3-002 (4.4.a, Alto) | Não era falha da plataforma. As duas chamadas com a mesma chave devolveram o mesmo `trace_id` (`543fc10d…`) e a mesma conversa. A segunda conversa contada era a S01.pt da fase 1, que rodava **em paralelo** com o mesmo lojista (`cliente1988`) | Nada | `p4_resilience.py idem --go`: 4.4.a PASS, `new_conversations=1`; 4.4.b e 4.4.c PASS |
| QA-R3-005 (3.1.b) | Não era falha da plataforma. O 403 sai antes de criar conversa ou chamar o grafo (`src/app/api/chat.py`). As 3 conversas a mais eram S01.en (fase 1), 2.1-open (fase 2) e 4.4-1 (fase 4), criadas no mesmo minuto | Nada | `p3_security.py 3.1 --go` sozinho: 3.1.b PASS, `conversations 761→761`, 0 linhas do grafo no log |
| QA-R3-004 (K06) | Era falha. Todo turno que termina em handoff (`awaiting_human`) devolvia `response: ""`. A tela escondia a bolha vazia atrás do aviso de fila, mas quem usa a API ficava sem texto. O K06 em si foi escalado, o que o próprio QA aceita como resposta certa | `build_response` devolve um aviso curto, no idioma da conversa, quando o turno para no handoff sem resposta sintetizada | `tests/unit/test_handoff_ack.py`; `p1_chat.py 1.4 --go`: K06 e K01 com o aviso em PT, K05 em EN |
| QA-R3-003 (S01.en) | Falso positivo intermitente do guardrail de saída (`output: blocked`, confiança 0,85, 4 buscas na base). Repetido 10 vezes com a mesma pergunta em 2026-11-26, as 10 passaram com 2 ou 3 fontes | Nada; a régua não foi afrouxada | [limitations.md](../limitations.md) §14 |
| QA-R3-006–011 (axe) | Era falha, mas não de `role`/`aria-label`: as violações eram todas `color-contrast`. O texto `#B45309` do selo "SLA estourado" sobre o próprio fundo âmbar a 10% dava 4,39:1; a fila lista até 100 tickets e todos os antigos estão com SLA vencido, daí os 100 nós. O mesmo selo aparece em `/history` ("aguardando atendente") e em `/admin/accounts` (conta pendente) | `state.warning` → `#9A4507` (≥ 5,2:1 no fundo tingido), `state.success` → `#0F6E27` (o anterior dava 4,43:1 sobre `surface-200`), selo `brand` com `brand-pressed` (era 3,79:1) | `apps/web/src/design/contrast.test.ts` (inclui um caso mostrando que a cor antiga reprovava) |
| QA-R3-015–019 (i18n) | As palavras encontradas são dados, não textos da interface: prévia de mensagens do lojista no `/history`, resumos dos tickets (gerados no idioma da conversa) na `/queue` e alvos como `account:acc_…` na auditoria. Os arquivos `pt.json`/`en.json` não têm palavra do outro idioma nem chave faltando. A varredura achou, porém, textos fixos de verdade: papel e status da conta (`operator`, `active`) crus em `/admin/accounts` e o nome acessível do `Spinner` fixo em português | Papéis e status traduzidos (`admin.accounts.roles.*`, `admin.accounts.statuses.*`) e `Spinner` com `app.loading` | `apps/web/src/features/admin/AccountsPage.test.tsx` |

Os casos 3.1.c com o cookie do `merchant_a` deram 401 na nova passagem porque a sessão salva pelo
harness se perdeu quando o Redis foi reiniciado no teste de falhas (o próprio relatório registra
isso). Renovar exige o passo `accounts` do harness; os mesmos casos com Bearer passaram.

