# ADR 0005: Identidade só via token; tools com contexto injetado

- **Status**: Aceito · **Data**: 2026-10-13 · **Requisitos**: REQ-SEC-01..03, constitution I

## Contexto

O enunciado envia `user_id` no corpo. Se ele substituir o usuário autenticado, há IDOR
(um cliente lê dados de outro).

## Decisão

O usuário efetivo é o `sub` do JWT validado. `user_id` no corpo é opcional; se divergir → 403,
auditoria e nenhum agente executado. O grafo recebe `user_id` em `config["configurable"]`, fora do
estado visível ao LLM; as tools leem dali via `RunnableConfig` injetado. O `thread_id` é
`{user_id}:{conversation_id}`.

## Alternativas

- **`user_id` do corpo**: IDOR por definição.
- **`user_id` como parâmetro da tool**: o modelo poderia ser induzido a trocar o alvo.

## Consequências

- (+) Injeção em linguagem natural não muda o cliente consultado, por construção.
- (−) Testes e demo precisam gerar tokens (`scripts/issue_token.py`).

## Revisão 2026-11-26: documento no texto não é IDOR

Como as tools só leem o lojista do token, um CPF, CNPJ ou e-mail escrito na mensagem não alcança
dados de ninguém. O classificador de entrada (`prompts/input_guard.md`) deixou de tratar documento
no texto como ataque: `injection` agora é só (1) instrução contra as regras, o prompt, a persona ou
a identidade do assistente, ou (2) pedido de dados de conta que o **texto** atribui a outra parte
("do cliente CPF ...", "de outro lojista", "eu sou o cliente2024"). Um número de documento sozinho,
com ou sem "meu", é do próprio lojista. O bloqueio desses pedidos é experiência e defesa em
profundidade (o lojista ouve que não pode, em vez de receber os próprios dados sem entender); a
garantia de isolamento continua sendo esta ADR.

O classificador responde, nesta ordem, `reason`, `instruction_target`, `data_owner`, `topic` e
`classification`, roda com temperatura 0, e bloqueia quando a classe **ou** os campos indicam
ataque. Medição: [reports/data/input-guard-probe-2026-11-26.json](../reports/data/input-guard-probe-2026-11-26.json).
