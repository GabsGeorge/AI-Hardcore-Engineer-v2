import { expect, test, type Browser, type Page } from "@playwright/test";

/**
 * HITL em tempo real com a stack real (FR-010, T065): web, api, Postgres, Redis, grafo, fila do
 * operador e o canal SSE `/v1/conversations/{id}/events`. Só o LLM é roteirizado
 * (`tests/e2e_llm.py`, montado pelo docker-compose.test.yml): sem chave nem internet no CI.
 * As contas vêm de `tests/e2e_server.py`.
 */

const PASSWORD = "e2e-senha-de-teste-123456";
const MERCHANT = "e2e-lojista@getnet.example.com";
const OPERATOR = "e2e-operador@getnet.example.com";
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

async function signIn(browser: Browser, email: string): Promise<Page> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/login");
  await page.getByLabel(/^e-mail/i).fill(email);
  await page.getByLabel(/^senha/i).fill(PASSWORD);
  await page.getByRole("button", { name: /^entrar$/i }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  return page;
}

test("resposta do operador aparece no chat do lojista em até 5 s, sem recarregar", async ({
  browser,
}) => {
  const merchant = await signIn(browser, MERCHANT);
  await merchant.goto("/chat");
  let navigations = 0;
  merchant.on("framenavigated", (frame) => {
    if (frame === merchant.mainFrame()) navigations += 1;
  });

  await merchant.getByLabel(/^mensagem/i).fill("Quero falar com um atendente humano");
  await merchant.getByRole("button", { name: /^enviar$/i }).click();

  const banner = merchant.getByRole("status").filter({ hasText: /aguardando atendente/i });
  await expect(banner).toBeVisible({ timeout: 30_000 });
  await expect(merchant.getByLabel(/^mensagem/i)).toBeDisabled();
  const ticketId = UUID.exec((await banner.textContent()) ?? "")?.[0];
  expect(ticketId, "o banner mostra o número do chamado").toBeTruthy();

  // O operador abre a fila e responde o chamado do lojista.
  const operator = await signIn(browser, OPERATOR);
  await operator.goto("/queue");
  await expect(operator.getByRole("heading", { name: /fila de atendimento/i })).toBeVisible();
  await operator.goto(`/queue/${ticketId}`);
  const reply = `Estorno aprovado (e2e ${Date.now()}).`;
  await operator.getByLabel(/resposta ao lojista/i).fill(reply);
  await operator.getByRole("button", { name: /enviar resposta/i }).click();
  const sentAt = Date.now();

  // Sem recarregar: a resposta chega pelo canal SSE em até 5 s.
  await expect(merchant.getByRole("log").getByText(reply)).toBeVisible({ timeout: 5_000 });
  expect(Date.now() - sentAt).toBeLessThanOrEqual(5_000);
  await expect(merchant.getByRole("status").filter({ hasText: /^respondido/i })).toBeVisible();
  await expect(merchant.getByLabel(/^mensagem/i)).toBeEnabled();
  expect(navigations, "a página do lojista não recarregou nem navegou").toBe(0);
});
