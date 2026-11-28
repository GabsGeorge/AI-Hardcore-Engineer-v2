# Contrato de eventos SSE (P2, REQ-API-07)

Formato `text/event-stream`; cada evento tem `event:` e `data:` em JSON. Toda conexão exige o
mesmo Bearer token das rotas REST.

## `POST /v1/chat/stream`

| Evento | `data` | Ordem |
| --- | --- | --- |
| `agent_step` | `{node: string, status: "start"\|"end", ts}` | 0..n, na ordem de execução |
| `token` | `{text: string}` | 0..n, antes do `done`: pedaço seguinte do texto da resposta enquanto ela é gerada |
| `token_reset` | `{}` | 0..n: a resposta em geração foi descartada (replanejamento); o cliente limpa o rascunho |
| `done` | `ChatResponse` completo (openapi.yaml) | exatamente 1, sempre o último |
| `error` | `Error` (openapi.yaml) | substitui `done` em falha |

**`token` é um rascunho, `done` é a resposta.** Os `token` saem antes de o guardrail de saída
rodar, então o cliente os mostra como texto ainda não verificado e, ao receber `done`, exibe
**somente** `done.response` (que pode ser diferente: resposta reescrita, trocada por "não
encontrei base" ou encaminhada a um atendente). Se o fluxo cair sem `done`, o rascunho é
descartado. Regras do servidor (`src/app/api/token_stream.py`):

- só o texto que o lojista vai ler: com um especialista no plano, a resposta dele; com vários, só
  a síntese. Classificador, planejador, reescrita da busca e verificador nunca aparecem;
- a máscara de PII é aplicada ao texto acumulado antes de cada envio, e os últimos 32 caracteres
  e a palavra em curso ficam retidos, para um documento, cartão, telefone ou e-mail nunca sair em
  pedaços antes de a máscara vê-lo inteiro;
- promessa financeira (estorno, reembolso, chargeback, taxa zerada) interrompe os `token` daquela
  resposta antes de a frase aparecer;
- o fim do texto não é enviado em `token` (fica retido): o `done` sempre traz a resposta inteira;
- `CHAT_STREAM_TOKENS=false` desliga os `token` e mantém o resto do fluxo.

## `GET /v1/conversations/{id}/events`

| Evento | `data` |
| --- | --- |
| `handoff_update` | `{ticket_id, status, priority, sla_due_at}` (inclui aviso de SLA estourado) |
| `final_message` | `{content, trace_id}` após retomada ou expiração |
| `keepalive` | `{}` a cada 15 s |

O canal base continua sendo `GET /v1/conversations/{id}` com polling a cada 3 s; SSE é melhoria.

### Limite de conexões (REQ-SEC-08, ADR 0011)

O canal `events` honra `SSE_EVENTS_MAX_PER_USER` (default 3) conexões simultâneas por usuário.
Excedente é rejeitado com:

```
HTTP/1.1 429 Too Many Requests
Content-Type: application/json

{"code": "too_many_connections", "message": "Too many open event streams (max 3)."}
```

Cliente deve esperar backoff antes de reconectar. `/v1/chat/stream` não aplica esse teto (é
short-lived; defesa é o rate limit de requests).
