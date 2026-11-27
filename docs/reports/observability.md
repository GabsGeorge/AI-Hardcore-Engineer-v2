# Relatório de observabilidade

**Revisado em**: 2026-11-24 · **ADR**: [0007](../adr/0007-langfuse-otel.md)

Escrito a partir do código, não de memória. Todo nome de métrica, label, arquivo, alerta,
dashboard e span abaixo existe no repositório; `tests/test_docs_coherence.py` falha se este
arquivo (ou o README) citar um `getnet_*`, um caminho `deploy/*` ou um span que não exista.

A stack é OTLP + Prometheus + Grafana Tempo + Grafana, tudo no profile `obs` do compose.
Sem Langfuse e sem SaaS (ADR 0007).

## 1. Tracing (OpenTelemetry)

- **Fonte**: [src/app/observability/tracing.py](../../src/app/observability/tracing.py)
- **Provider**: `configure_tracing` cria o `TracerProvider` (`service.name=getnet-support-api`)
  no lifespan da api. Spans sempre existem, então toda resposta tem `trace_id`.
- **Export**: se `OTEL_EXPORTER_OTLP_ENDPOINT` estiver definido, um `BatchSpanProcessor` manda
  OTLP/HTTP para `<endpoint>/v1/traces`; vazio, nenhum exportador é registrado
  (`tests/unit/test_tracing_export.py`). O padrão no `docker-compose.yml` e no `.env.example` é
  vazio; `docker-compose.obs.yml` (usado por `make up-obs`) define
  `OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318` na api.
- **Raiz**: o middleware `trace_requests` em [src/app/main.py](../../src/app/main.py) abre
  `http.request` (kind SERVER) com pai vindo só de um `traceparent` recebido. Atributos:
  `http.request.method`, `url.path`, `http.route`, `http.response.status_code`.
- **FastAPI** (0.143, versão do `uv.lock`) emite seus próprios spans `fastapi.dependencies`,
  `fastapi.endpoint` e `fastapi.serialization`; os nós do grafo ficam abaixo de
  `fastapi.endpoint`.

### Spans emitidos pelo código

| Span | Onde nasce | Atributos |
|---|---|---|
| `input_guardrail`, `summarize`, `supervisor`, `knowledge`, `support`, `escalation`, `synthesize`, `degraded_response`, `safe_response`, `output_guardrail` | `traced_node` aplicado a cada nó em [src/app/graph/builder.py](../../src/app/graph/builder.py) | `graph.node`; `graph.interrupted=true` quando o nó pausa para HITL (`interrupt()` não vira erro) |
| `supervisor.plan` | `plan_turn` em [src/app/graph/supervisor.py](../../src/app/graph/supervisor.py); desde 2026-11-26 o primeiro plano do turno roda dentro de `input_guardrail`, em paralelo com o classificador (o replanejamento continua sob `supervisor`) | `prompt.name`, `prompt.version`, `llm.tokens`, `plan.route`, `plan.language` |
| `llm.<papel>` (`llm.supervisor`, `llm.knowledge_rewrite`, `llm.knowledge`, `llm.support`, `llm.escalation`, `llm.synthesize`, `llm.summarize`, `llm.input_guard`, `llm.output_guard`, `llm.online_eval`) | `TracedModel`, que `get_model` em [src/app/core/llm.py](../../src/app/core/llm.py) devolve para todo papel | `llm.role`, `gen_ai.system`, `gen_ai.request.model`, `gen_ai.response.model` (quando o provedor informa), `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `llm.latency_ms`, `prompt.name`, `prompt.version` |
| `tool.<nome>` | `call_tool` em [src/app/agents/common.py](../../src/app/agents/common.py) | `tool.name`, `tool.agent`, `tool.status` (`ok`/`denied`/`error`/`timeout`), `tool.latency_ms`; status ERROR quando não é `ok` |
| `retrieval.search` | `search` em [src/app/rag/retriever.py](../../src/app/rag/retriever.py) | `retrieval.strategy`, `retrieval.lang`, `retrieval.hits`, `retrieval.top_score` |
| `retrieval.bm25` | idem (estratégia `hybrid_rerank`) | `retrieval.k`, `retrieval.hits` |
| `retrieval.dense` | `dense_hits` (as duas estratégias) | `retrieval.k`, `retrieval.lang`, `retrieval.hits` |
| `retrieval.embedding` | `dense_hits`, em volta de `aembed_query` | `gen_ai.request.model` (modelo de embedding) |
| `retrieval.rerank` | `search`, só na estratégia `hybrid_rerank` | `rerank.model`, `rerank.candidates`, `rerank.outcome` (`ok` ou `fallback_dense`: o reranker falhou e a busca usou a estratégia densa calibrada), `rerank.error` |

`prompt.version` é `sha256[:12]` do arquivo em `prompts/` (ADR 0009). O mapeamento papel →
prompt está em `ROLE_PROMPTS` (`output_guard` → `grounding.md`; `knowledge_rewrite` →
`knowledge.md`; `online_eval` → `online_eval.md`).

Os tokens de cada `llm.<papel>` também somam em `getnet_tokens_total{role}`; até esta revisão só
o supervisor era contado.

### Trace capturado

Capturado em **2026-11-24T00:03:00Z**, `trace_id` **`c161d0617111846c3ceea1f2b9b41252`**.

**Como foi capturado** (leia antes de olhar os tempos): API, grafo, Postgres e retrieval híbrido
reais, executados em processo (`httpx.ASGITransport`), com o **LLM e as embeddings roteirizados**
da suíte de testes (ADR 0006), sem OpenAI. Os tempos de `llm.*` são do fake (≈0 ms) e não dizem
nada sobre latência de produção; os tokens (100/20, 80/20) são os roteirizados. A pergunta é a
composta do teste `tests/integration/test_tracing_tree.py`. Não há trace de produção com
modelo real neste relatório.

```
captured_at=2026-11-24T00:03:00+00:00 (antes do plano em paralelo: hoje `supervisor.plan` aparece sob `input_guardrail`)
status=200 trace_id=c161d0617111846c3ceea1f2b9b41252 route=['input_guardrail', 'supervisor', 'knowledge', 'support', 'supervisor', 'synthesize', 'output_guardrail']
http.request  [188.9 ms]  http.route=/v1/chat http.response.status_code=200
├─ fastapi.dependencies  [3.0 ms]
├─ fastapi.endpoint  [167.2 ms]
│  ├─ input_guardrail  [1.0 ms]  graph.node=input_guardrail
│  │  └─ llm.input_guard  [0.0 ms]  llm.role=input_guard gen_ai.request.model=test-double gen_ai.usage.input_tokens=100 gen_ai.usage.output_tokens=20 prompt.version=0a05b9e00eb5
│  ├─ summarize  [0.0 ms]  graph.node=summarize
│  ├─ supervisor  [2.1 ms]  graph.node=supervisor
│  │  └─ supervisor.plan  [2.1 ms]  prompt.version=1bcf4624a967
│  │     └─ llm.supervisor  [1.1 ms]  llm.role=supervisor gen_ai.request.model=test-double gen_ai.usage.input_tokens=100 gen_ai.usage.output_tokens=20 prompt.version=1bcf4624a967
│  ├─ knowledge  [52.8 ms]  graph.node=knowledge
│  │  ├─ llm.knowledge_rewrite  [0.0 ms]  llm.role=knowledge_rewrite gen_ai.request.model=test-double gen_ai.usage.input_tokens=100 gen_ai.usage.output_tokens=20 prompt.version=40c31696f917
│  │  ├─ tool.search_knowledge_base  [44.2 ms]  tool.status=ok
│  │  │  └─ retrieval.search  [39.2 ms]  retrieval.strategy=hybrid_rerank retrieval.hits=2
│  │  │     ├─ retrieval.bm25  [19.1 ms]  retrieval.hits=2
│  │  │     ├─ retrieval.dense  [12.5 ms]  retrieval.hits=4
│  │  │     │  └─ retrieval.embedding  [2.9 ms]  gen_ai.request.model=text-embedding-3-small
│  │  │     └─ retrieval.rerank  [0.0 ms]  rerank.outcome=ok
│  │  └─ llm.knowledge  [0.0 ms]  llm.role=knowledge gen_ai.request.model=test-double gen_ai.usage.input_tokens=100 gen_ai.usage.output_tokens=20 prompt.version=40c31696f917
│  ├─ support  [31.2 ms]  graph.node=support
│  │  ├─ llm.support  [1.0 ms]  llm.role=support gen_ai.request.model=test-double gen_ai.usage.input_tokens=80 gen_ai.usage.output_tokens=20 prompt.version=f6f687fef680
│  │  ├─ tool.get_settlement_schedule  [25.6 ms]  tool.status=ok
│  │  └─ llm.support  [0.0 ms]  llm.role=support gen_ai.request.model=test-double gen_ai.usage.input_tokens=80 gen_ai.usage.output_tokens=20 prompt.version=f6f687fef680
│  ├─ supervisor  [0.0 ms]  graph.node=supervisor
│  ├─ synthesize  [1.0 ms]  graph.node=synthesize
│  │  └─ llm.synthesize  [1.0 ms]  llm.role=synthesize gen_ai.request.model=test-double gen_ai.usage.input_tokens=100 gen_ai.usage.output_tokens=20 prompt.version=1ce796e39137
│  └─ output_guardrail  [1.5 ms]  graph.node=output_guardrail
│     └─ llm.output_guard  [0.5 ms]  llm.role=output_guard gen_ai.request.model=test-double gen_ai.usage.input_tokens=100 gen_ai.usage.output_tokens=20 prompt.version=fe4db10ef02a
└─ fastapi.serialization  [0.0 ms]
```

O segundo `supervisor` (0 ms) é a passada de decisão depois dos especialistas: `decide` é
determinístico e não chama modelo. A mesma árvore, com pai e filho de cada span, é verificada
por `tests/integration/test_tracing_tree.py` com `InMemorySpanExporter`.

### Abrir um trace no Grafana

Com o profile `obs`, a datasource **Tempo** (uid `tempo`) é provisionada por
[deploy/grafana/provisioning/datasources/tempo.yml](../../deploy/grafana/provisioning/datasources/tempo.yml)
e o Tempo roda com [deploy/tempo/tempo.yaml](../../deploy/tempo/tempo.yaml). O dashboard
**Getnet - Trace** ([deploy/grafana/dashboards/trace.json](../../deploy/grafana/dashboards/trace.json),
uid `getnet-trace`) tem um campo `trace_id`: cole o `trace_id` da resposta de `POST /v1/chat`
ou abra `http://localhost:3000/d/getnet-trace?var-trace_id=<trace_id>`.
O Grafana pede login (`GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` do `.env` gerado por
`scripts/bootstrap_env.sh`): não há acesso anônimo, e a porta 3000 só escuta em `127.0.0.1`.

## 2. Métricas (Prometheus)

- **Exporter**: `prometheus_client.start_http_server(METRICS_PORT)` no lifespan; porta 9100,
  não publicada pelo compose (`METRICS_PORT=0` desliga).
- **Scrape**: [deploy/prometheus/prometheus.yml](../../deploy/prometheus/prometheus.yml), job
  `getnet-api` em `api:9100`, `scrape_interval: 15s`.
- **Séries em zero desde a partida**: `initialise_label_sets` cria os rótulos conhecidos
  (ferramentas, resultados de login, tipos de e-mail, endpoints do chat) com valor 0, então os
  painéis mostram 0 em vez de "No data" antes do primeiro evento (QA-075..QA-077).
- **Definições**: [src/app/observability/metrics.py](../../src/app/observability/metrics.py)
  (fonte única; `getnet_online_eval_score` foi movida para lá nesta revisão).

| Métrica | Tipo | Labels | Incrementada em |
|---|---|---|---|
| `getnet_http_requests_total` | counter | `route`, `method`, `status` | middleware `trace_requests` (`main.py`) |
| `getnet_http_request_duration_seconds` | histogram | `route` | idem (em `/v1/chat/stream` mede só até o início do fluxo) |
| `getnet_chat_turn_duration_seconds` | histogram | `endpoint` (`/v1/chat`, `/v1/chat/stream`) | `api/chat.py`, do pedido à resposta final; base dos painéis de latência e do alerta (QA-078) |
| `getnet_route_total` | counter | `node` | `_dispatch` do supervisor |
| `getnet_tokens_total` | counter | `role` | `TracedModel` (toda chamada de LLM) |
| `getnet_tool_errors_total` | counter | `tool` | `call_tool` quando o status não é `ok` |
| `getnet_guardrail_blocks_total` | counter | `stage` (`input`/`output`), `kind` (`heuristic`, `injection`, `out_of_scope`, `grounding`, `financial_promise`, `pii`) | `guardrails/input.py`, `guardrails/output.py` |
| `getnet_handoffs_total` | counter | `reason` | `agents/escalation.py` |
| `getnet_auth_failures_total` | counter | — | `core/errors.py` (respostas 401) |
| `getnet_auth_login_total` | counter | `outcome` | `api/auth_endpoints.py` |
| `getnet_auth_email_sent_total` | counter | `kind` | `ops/mailer.py` |
| `getnet_auth_email_errors_total` | counter | `kind` | `ops/mailer.py` |
| `getnet_auth_sessions_opened_total` | counter | — | login com sucesso |
| `getnet_online_eval_score` | histogram | `metric` (`faithfulness`, `relevance`) | `ops/online_eval.py`, amostrado por `EVAL_SAMPLE_RATE` |

### Alertas

[deploy/prometheus/alerts.yml](../../deploy/prometheus/alerts.yml), grupo `getnet.sli`:

| Alerta | Expressão (resumo) | `for` | Severidade |
|---|---|---|---|
| `HighRequestLatencyP95` | p95 de `getnet_chat_turn_duration_seconds` (`/v1/chat` e `/v1/chat/stream`) > 8 s | 10m | page |
| `ToolErrorRateHigh` | `rate(getnet_tool_errors_total)` / `rate(getnet_route_total)` > 0,05 | 10m | warn |
| `GuardrailBlockSpike` | `sum(rate(getnet_guardrail_blocks_total[5m]))` > 1 | 5m | warn |
| `OnlineEvalScoreLow` | média 1 h (`_sum`/`_count`) de `getnet_online_eval_score` por `metric` < 0,7, com ≥ 5 turnos avaliados na hora | 15m | warn |
| `HandoffsQueueGrowing` | `increase(getnet_handoffs_total[15m])` > 5 | 15m | warn |

Não há Alertmanager no compose: os alertas aparecem em `http://localhost:9090/alerts`, sem
roteamento de notificação.

## 3. Dashboards (Grafana)

Provisionados de [deploy/grafana/dashboards/](../../deploy/grafana/dashboards/) por
[deploy/grafana/provisioning/dashboards/dashboards.yml](../../deploy/grafana/provisioning/dashboards/dashboards.yml);
datasources em `deploy/grafana/provisioning/datasources/` (Prometheus padrão + Tempo). Home:
`operational.json`.

| Arquivo | Título | Painéis |
|---|---|---|
| `operational.json` | Getnet - Operacional | p95 latência /v1/chat; QPS por status; rota tomada pelo supervisor; handoffs abertos (1 h); bloqueios de guardrail (1 h); erros de tool por tool |
| `quality.json` | Getnet - Qualidade | bloqueios de guardrail por tipo; tokens por papel; **avaliação online: média 1 h por métrica** (linha de limiar 0,7); turnos avaliados (24 h); handoffs abertos por motivo (24 h) |
| `cost.json` | Getnet - Custo | tokens totais/hora; tokens por papel (24 h); chamadas de /v1/chat (24 h) |
| `ui.json` | Getnet - UI web | login por outcome; sessões abertas; 401; p50/p95 latência /v1/chat; e-mails enviados vs erros |
| `trace.json` | Getnet - Trace | árvore de spans do `trace_id` informado (datasource Tempo) |

Não existe dashboard de retrieval nem métrica de custo em USD: custo é estimado a partir de
`getnet_tokens_total`.

## 4. Logs (structlog)

[src/app/observability/logging.py](../../src/app/observability/logging.py): JSON por linha em
stdout, com `timestamp` (ISO, UTC), `level` e `event`; o middleware faz `bind_contextvars(trace_id=...)`,
então todo log do request carrega o `trace_id`. O processador `_mask_pii` aplica `pii.mask()` a
todo valor string antes do renderer, inclusive nos logs de bibliotecas (uvicorn). O log de acesso
traz `route`, `method`, `status`, `latency_ms`.

## 5. Como validar

### 5.1 Executado nesta revisão (saídas reais)

Ambiente desta execução: Windows, sem Docker. Postgres 16 + pgvector embutido (`pgserver`) e
Redis em processo (`fakeredis`) no lugar dos serviços do `docker-compose.test.yml`; `flashrank`
substituído por stub local porque o `onnxruntime` não carrega sem o runtime MSVC (os testes usam
`FakeReranker`, nunca o FlashRank). Em CI/Linux os mesmos testes rodam no container `tests`.

**Árvore de spans e coerência doc ↔ código**

```bash
pytest tests/integration/test_tracing_tree.py tests/integration/test_tracing.py \
       tests/test_docs_coherence.py tests/unit/test_obs_profile.py -q
```

```
.............                                                            [100%]
13 passed in 2.64s
```

**api de pé + métricas na porta interna** (uvicorn local contra o Postgres/Redis acima, sem
`OPENAI_API_KEY`). A porta 9100 só fica no host nesse modo local; com o compose ela não é
publicada (de propósito) e a leitura é de dentro do container (QA-091):
`docker compose exec api python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:9100/metrics').read().decode())"`.

```bash
curl -s -i http://127.0.0.1:8000/readyz
curl -s http://127.0.0.1:9100/metrics | grep '^# TYPE getnet_'
```

```
HTTP/1.1 200 OK
date: Tue, 24 Nov 2026 00:04:32 GMT
server: uvicorn
content-length: 82
content-type: application/json

{"status":"ready","checks":{"database":"ok","index":"ok","redis":"ok","llm":"ok"}}
# TYPE getnet_http_requests_total counter
# TYPE getnet_http_requests_created gauge
# TYPE getnet_http_request_duration_seconds histogram
# TYPE getnet_http_request_duration_seconds_created gauge
# TYPE getnet_route_total counter
# TYPE getnet_tokens_total counter
# TYPE getnet_tool_errors_total counter
# TYPE getnet_guardrail_blocks_total counter
# TYPE getnet_handoffs_total counter
# TYPE getnet_auth_failures_total counter
# TYPE getnet_auth_failures_created gauge
# TYPE getnet_auth_login_total counter
# TYPE getnet_auth_email_sent_total counter
# TYPE getnet_auth_email_errors_total counter
# TYPE getnet_auth_sessions_opened_total counter
# TYPE getnet_auth_sessions_opened_created gauge
# TYPE getnet_online_eval_score histogram
```

Observação da mesma execução: sem `OPENAI_API_KEY`, `POST /v1/chat` devolve 500
(`OpenAIError: Missing credentials` ao construir o `ChatOpenAI`), ainda com `trace_id` no corpo,
e o `/readyz` acima responde `"llm":"ok"` mesmo assim — o check de LLM do readiness não verifica
a credencial.

### 5.2 Com Docker e o profile `obs` (não executado nesta revisão)

A máquina desta revisão não tem Docker; os passos abaixo **não foram executados** e por isso não
têm saída colada. São o caminho para ver o trace no Tempo/Grafana:

```bash
docker compose -f docker-compose.yml -f docker-compose.obs.yml --profile obs up -d --build
TOKEN=$(docker compose exec -T api python scripts/issue_token.py cliente1988 --role merchant | tail -1)
curl -s -X POST http://localhost:8000/v1/chat -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"Como funciona a antecipação de recebíveis?"}' | jq -r .trace_id
# abrir http://localhost:3000/d/getnet-trace?var-trace_id=<trace_id>
curl -s 'http://localhost:9090/api/v1/query?query=sum(getnet_http_requests_total)' | jq .data.result
```

## 6. Limitações

- O trace colado acima usa LLM roteirizado; um trace com `gpt-4o-mini` real ainda precisa ser
  capturado (passo 5.2) e colado aqui com data e `trace_id`.
- Tempo guarda traces em `/tmp` do container (efêmero, retenção de 24 h): é uma stack de
  inspeção local, não de retenção.
- `--profile obs` sem `-f docker-compose.obs.yml` sobe Tempo e Grafana, mas a api não exporta
  spans (o endpoint continua vazio); use `make up-obs` ou o comando completo da seção 5.2.
