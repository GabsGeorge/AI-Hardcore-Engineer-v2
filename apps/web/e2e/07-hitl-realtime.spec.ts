import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Browser, type Page } from "@playwright/test";

/**
 * HITL em tempo real com a stack real (FR-010, T065): web, api, Postgres, Redis, grafo, fila do
 * operador e o canal SSE `/v1/conversations/{id}/events`. Só o LLM é roteirizado
 * (`tests/e2e_llm.py`, montado pelo docker-compose.test.yml): sem chave nem internet no CI.
 * As contas vêm de `tests/e2e_server.py`.
 */

// Senha das contas criadas por tests/e2e_server.py: vem do ambiente (o CI gera uma por execução).
const PASSWORD = process.env.E2E_PASSWORD ?? "";
if (PASSWORD.length < 12)
  throw new Error("E2E_PASSWORD (>= 12 caracteres) precisa estar no ambiente");
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

async function seriousA11yViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  return results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
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
  // C3: axe também no chat (com o banner de handoff aberto), não só no login.
  expect(await seriousA11yViolations(merchant)).toEqual([]);
  const ticketId = UUID.exec((await banner.textContent()) ?? "")?.[0];
  expect(ticketId, "o banner mostra o número do chamado").toBeTruthy();

  // O operador abre a fila e responde o chamado do lojista.
  const operator = await signIn(browser, OPERATOR);
  await operator.goto("/queue");
  await expect(operator.getByRole("heading", { name: /fila de atendimento/i })).toBeVisible();
  expect(await seriousA11yViolations(operator)).toEqual([]); // C3: axe na fila do operador
  await operator.goto(`/queue/${ticketId}`);
  // Marca única por execução SEM sequência longa de dígitos: o guardrail de saída mascara PII
  // na resposta (src/app/guardrails/pii.py), e ~10% dos `Date.now()` (13 dígitos) passam no
  // Luhn e viravam "[CARTAO]", então o texto esperado nunca aparecia. Era a falha intermitente
  // deste teste (CI 45924616525, 46070852196, 46073927534). Em base 36 são ~8 caracteres.
  const reply = `Estorno aprovado (e2e ${Date.now().toString(36)}).`;
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
