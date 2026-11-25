# Block Review — registros por fase

Este documento resume as revisões "read-only" acionadas no fechamento de cada bloco. A fonte
primária são os checklists versionados:

- [specs/001-multi-agent-support/checklists/block-review.md](../../specs/001-multi-agent-support/checklists/block-review.md)
  (tabela "Resultado": datas, achados e commits de correção por bloco);
- [specs/002-web-ui/checklists/block-review.md](../../specs/002-web-ui/checklists/block-review.md)
  (bloco da spec 002 e T177).

A regra é fixa: revisor não edita; aponta ❌ com `arquivo:linha`; o agente principal
corrige; revisor roda de novo até liberar.

> **Auditoria 2026-11-24**: a versão anterior deste resumo divergia dos checklists (datas e
> fases trocadas, e três achados da "Fase 1" que não aparecem em nenhum checklist nem no código).
> O texto abaixo segue os checklists. Os hashes citados são do **repositório v1**
> (`AI-Hardcore-Engineer`), onde foram conferidos; o v2 tem histórico consolidado e não os contém.

---

## Fase 0 + grafo base

- **Data**: 2026-10-13 (checklist 001).
- **Achado bloqueante**: auditoria gravada sem máscara de PII — o checklist cita a correção em
  `d67ff1c`, mas **esse hash não existe no histórico do v1** (conferido em 2026-11-24). O
  comportamento atual está coberto por `tests/security/test_admin_audit_no_pii.py` e
  `tests/security/test_security_idor.py::test_audit_target_never_stores_raw_pii`.
- **Status**: liberado na re-revisão.

## US2 + US1 + US3

- **Data**: 2026-10-13 (checklist 001).
- **Achado bloqueante**: delimitador aninhado remontável no contexto recuperado — corrigido em
  `38ede50` ("texto recuperado e respostas são escapados"), hoje em
  `src/app/agents/knowledge.py::_as_data` e `src/app/graph/synthesize.py`.
- **Status**: liberado.

## Fase 2 — US4 + US5, US6, US7 + suítes transversais

- **Data**: 2026-10-20 (checklist 001).
- **Achados bloqueantes**: nenhum. A auditoria T176 registrou 3 débitos não bloqueantes (breaker
  só no DuckDuckGo, online-eval sem Langfuse, run_eval sem RAGAS), citados em
  [docs/limitations.md](../limitations.md).
- **Status**: liberado.

## Fase 3 — US9, US8, plataforma, polish + spec 002 (T177)

- **Data**: 2026-10-28 (checklists 001 e 002).
- **Achados bloqueantes** (4, todos corrigidos; commits conferidos no v1):
  - **Item 1 — Primeiro uso quebrado** (`f72784a`): docker build do web, nginx não-root e API
    publicada.
  - **Item 2 — CSRF middleware global** (`d0fd0b0`): toda mutação cookie-authed passa por
    `enforce_csrf`; Bearer é isento (ADR 0005).
  - **Item 3 — CI web completo** (`e600454`): Vitest cobrindo guards e ChatPage.
  - **Item 4 — Auditoria + limite SSE + resume duplo** (`d4b4cf2`): `/v1/chat/stream` audita;
    `MAX_SSE_PER_USER` (padrão 3) com 429 `too_many_connections`; `handoff/reply` faz claim
    atômico e bloqueia resume duplo.
- **Outros commits do mesmo fechamento** (conferidos no v1): k6 com um token por VU (`ea4449d`;
  thresholds de p95 por cenário `simple`/`compound`, não por rota), crediário no golden set
  `sc-19/20/21` (`067234c`), fontes `getnet.net/en` (`a1cf941`).
- **Status**: liberado.

## Fase 4 — hardening de entrega (pós-fechamento)

- **Data**: 2026-10-29 → 2026-10-30.
- **Escopo**: primeiro uso real, CI com pin por SHA, Bearer vs cookie, threat-model com coluna
  de teste, CVE aceita documentada, smoke dos cenários.
- **Commits** (existem no v1, onde também existe a branch `002-release-hardening`): `d239e09`
  (bootstrap_env.sh), `03c0dc6` (trivy por SHA + Bearer + perms de cache + traceability),
  `a3b25a5` (threat-model com coluna de teste + CVE zod).
- **Instrumento**: backlog interno; não há checklist versionado desta fase.

---

## Removido na auditoria (sem evidência)

- "Guardrail de saída não re-mascarava PII — corrigido com
  `tests/security/test_pii.py::test_output_reapplies_mask`": esse teste não existe; a re-máscara
  existe em `src/app/guardrails/output.py` e é coberta indiretamente por
  `test_pii_never_reaches_llm_or_logs`.
- "Supervisor podia rotear para `knowledge` sem threshold mínimo — ajustado para 0,30": o 0,30 é
  `RAG_RELEVANCE_THRESHOLD` do retrieval, não um limiar do supervisor; o achado não está em
  checklist.
- "`search_knowledge_base` logava documento inteiro em DEBUG": a tool não loga; o achado não está
  em checklist.
- "CI verde em todos os testes de injeção" e "k6 reporta p50/p95 por rota": sem registro / não
  confere com `deploy/k6/chat.js`.

## Como re-rodar uma revisão

1. Checkout do commit a revisar; `git log --name-only --since=<data>` para o escopo real.
2. Abrir o checklist do bloco e marcar ✅/❌/N/A por item, com `arquivo:linha`.
3. ❌ em `[bloqueante]` → agente principal corrige; revisor roda de novo.
4. Fechar o bloco só quando não restar bloqueante aberto.
