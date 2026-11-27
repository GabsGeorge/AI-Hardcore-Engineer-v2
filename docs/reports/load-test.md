# Teste de carga — `/v1/chat` (REQ-REL-04)

**Cenário**: 20 VUs em `/v1/chat`, mix 70/30 simples/composto, duração 2 min; cada VU faz um
request e dorme 1 s (`sleep(1)` em `deploy/k6/chat.js`), então a vazão depende da latência.

> **Auditoria 2026-11-24**: o run abaixo é de 2026-10-28, no código do repositório v1 (commit
> `ea4449d`, que existe no histórico do v1; o v2 consolidou o histórico). A saída bruta do k6 não
> foi versionada: os números são a transcrição daquele commit e não foram re-executados. O k6
> **não** roda no CI (`.github/workflows/ci.yml` só faz `docker compose build` da imagem).

## Rodar localmente

O rate limit do `/v1/chat` é 30 req/min por usuário (REQ-SEC-08). Para 20 VUs em paralelo
sem saturar, cada VU usa seu próprio token — passe uma lista separada por vírgula em
`AUTH_TOKENS`. Com só um token em `AUTH_TOKEN`, a corrida é válida mas as iterações
batem no rate limit e viram 429.

```bash
cp .env.example .env
docker compose up -d --build api
# Seed 20 usuarios de carga + emissao dos tokens:
docker compose exec api python -c "
import asyncio
from app.db.pool import get_pool, open_pool
from app.core.config import get_settings
async def main():
    await open_pool(get_settings().database_url)
    async with get_pool().connection() as c:
        for i in range(1, 21):
            uid = f'k6user{i:02d}'
            await c.execute(\"INSERT INTO users (id, role, display_name) VALUES (%s, 'merchant', %s) ON CONFLICT (id) DO NOTHING\", (uid, f'K6 User {i:02d}'))
asyncio.run(main())
"
TOKENS=""
for i in $(seq -w 1 20); do
  T=$(docker compose exec -T api python scripts/issue_token.py "k6user${i}" --role merchant | tail -1)
  TOKENS="${TOKENS}${TOKENS:+,}${T}"
done
docker compose --profile load run --rm -e AUTH_TOKENS="${TOKENS}" k6 run /scripts/chat.js
```

Thresholds de bloqueio (ver `deploy/k6/chat.js`):

| cenário          | métrica            | alvo        |
|------------------|--------------------|-------------|
| simples          | p(95) duração      | **< 8 s**   |
| composto         | p(95) duração      | **< 15 s**  |
| global           | taxa de erro HTTP  | **< 1 %**   |

## Como ler o resultado

- O k6 encerra com código ≠ 0 se qualquer threshold estourar (o CI não roda o k6; é um passo
  manual).
- O resumo final sai no stdout; para arquivo use `--out json=load.json`.
- Com o profile `obs` ativo, o painel "p95 latência /v1/chat" de
  `deploy/grafana/dashboards/operational.json` mostra a mesma distribuição pelo histograma
  `getnet_http_request_duration_seconds`.

## Run de 2026-11-27 (busca em paralelo com a reescrita, consultas do plano)

Mesmo cenário (20 VUs × 5 min, `gpt-4o-mini` em todos os papéis, pool de 30). Saída em
[data/load-2026-11-27.txt](data/load-2026-11-27.txt); tempo médio por etapa sob a mesma carga em
[data/stages-2026-11-27.json](data/stages-2026-11-27.json) (histogramas
`getnet_graph_node_duration_seconds` e `getnet_llm_call_duration_seconds`).

| Cenário | média | mediana | p90 | p95 | alvo |
|---|---|---|---|---|---|
| simples | 9,1 s | 8,2 s | 13,8 s | **15,7 s** | < 8 s ❌ |
| composto | 14,4 s | 14,8 s | 19,0 s | **20,7 s** | < 15 s ❌ |
| erros HTTP | | | | **0/523** | < 1 % ✅ |

Por etapa (média sob carga): guardrail de entrada 2,4 s, conhecimento 4,3 s (era 5,3 s numa
medição de etapas anterior, sem publicar), suporte 2,2 s, síntese 0,5 s, guardrail de saída 1,4 s.
Por chamada ao modelo: supervisor 2,4 s, especialista de conhecimento 2,6 s, síntese 2,6 s,
classificador de entrada 1,4 s, verificador de saída 1,4 s, reescrita da busca 1,4 s.

O ajuste do prompt de suporte (erro citado que não está nos dados) entrou depois desta corrida;
é uma regra de texto e não muda o número de chamadas.

## Run de 2026-11-26 com pool de 30 conexões (`DB_POOL_MAX_SIZE=30`)

Mesmo cenário (20 VUs × 5 min). Saída em [data/load-2026-11-26-pool30.txt](data/load-2026-11-26-pool30.txt).

| Cenário | média | mediana | p90 | p95 | alvo |
|---|---|---|---|---|---|
| simples | 10,5 s | 9,6 s | 14,7 s | **17,8 s** | < 8 s ❌ |
| composto | 15,8 s | 15,3 s | 19,4 s | **28,0 s** | < 15 s ❌ |
| erros HTTP | | | | **0/459** | < 1 % ✅ |

## Run de 2026-11-26 (plano do supervisor em paralelo com o classificador)

20 VUs × 5 min, 20 tokens (`k6user01..20`), `gpt-4o-mini`. Saída completa em
[data/load-2026-11-26.txt](data/load-2026-11-26.txt).

| Cenário | média | mediana | p90 | p95 | alvo |
|---|---|---|---|---|---|
| simples | 16,1 s | 15,0 s | 22,8 s | **25,4 s** | < 8 s ❌ |
| composto | 20,2 s | 20,2 s | 25,9 s | **30,6 s** | < 15 s ❌ |
| erros HTTP | | | | **0/332** | < 1 % ✅ |

Sem carga, o mesmo build fez 15 turnos sequenciais com média 7,2 s (7,7 s antes do plano em
paralelo). A análise de onde está o tempo sob carga está em
[docs/limitations.md](../limitations.md) §11.

## Último run

**Quando**: 2026-10-28 15:57 UTC · **Host**: desenvolvedor local (Windows 10, Docker Desktop)
**Stack**: `docker compose up -d api postgres redis mailpit` sem profile `obs`, dummy
`OPENAI_API_KEY` (todas as respostas via `degraded_response`, retriever `index: empty` —
mesmo assim o pipeline inteiro é exercitado: auth, guardrails, rate limit, idempotência,
supervisor e route). Carga: 20 VUs × 2 min, 1 s de pausa entre requests de cada VU,
70/30 simples/composto (800 iterações ≈ 6,6 req/s, coerente com ~2 s de latência + 1 s de pausa).

| Métrica                                | Observado   | Threshold   | Resultado |
|---------------------------------------|-------------|-------------|-----------|
| `http_req_duration{scenario:simple}` p95   | **2.42 s**  | < 8 s       | ✅        |
| `http_req_duration{scenario:compound}` p95 | **2.96 s**  | < 15 s      | ✅        |
| `http_req_failed` rate                    | **0.00%**   | < 1 %       | ✅        |

**Resumo** (saída do k6):

```
checks_total.......: 800    6.579707/s
checks_succeeded...: 100.00% 800 out of 800
checks_failed......: 0.00%   0 out of 800
http_req_duration..............: avg=2.03s med=1.92s p(90)=2.13s p(95)=2.52s max=5.02s
http_req_failed................: 0.00%  0 out of 800
http_reqs......................: 800    6.579707/s
iterations.....................: 800    6.579707/s
```

### Interpretação

- **O que o run mede**: com chave dummy todas as respostas saem por `degraded_response`, então
  o número cobre auth, guardrails, rate limit, idempotência e o caminho até o supervisor — não
  cobre chamadas reais de LLM, retrieval nem tools. Não serve como estimativa de latência com
  modelo real.
- Máximo de 5.02 s: a causa não foi investigada (não há trace nem log do run versionado).
- Zero erros com 20 tokens distintos é coerente com o rate limit por usuário de 30/min
  (`RATE_LIMIT_PER_MIN`, padrão 30 em `src/app/core/config.py`): cada VU faz no máximo
  ~20 req/min. O comportamento com um único token (429 após ~30 requests) é o esperado pelo
  limite, mas não foi medido neste run.
- Pendente: rodar o mesmo cenário com `OPENAI_API_KEY` real e base ingerida, quando o
  supervisor passa a chamar `knowledge`/`support`, e comparar p95.

### Histórico

| Data       | p95 simples | p95 composto | Erro | Observação                                    |
|------------|-------------|--------------|------|-----------------------------------------------|
| 2026-10-28 | 2.42 s      | 2.96 s       | 0 %  | Único run; código v1, dummy key, degraded; saída bruta não versionada |
