import { expect, test, type Browser, type Page } from "@playwright/test";

/**
 * Chat de ponta a ponta com a stack real (T054, FR-010/FR-011): login pela tela, envio da
 * pergunta, resposta do backend (grafo, guardrails, tools contra o Postgres, SSE do POST) e a
 * conversa de volta depois de recarregar. Só o LLM é roteirizado (`tests/e2e_llm.py`): sem chave
 * nem internet no CI. O lojista do E2E está vinculado ao `cliente1988` do seed, então a pergunta
 * de recebimentos vai ao especialista de suporte, que lê a agenda de verdade; a resposta na tela
 * traz o que a tool devolveu.
 */

// Senha das contas criadas por tests/e2e_server.py: vem do ambiente (o CI gera uma por execução).
const PASSWORD = process.env.E2E_PASSWORD ?? "";
if (PASSWORD.length < 12)
  throw new Error("E2E_PASSWORD (>= 12 caracteres) precisa estar no ambiente");
const MERCHANT = "e2e-lojista@getnet.example.com";

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

async function ask(page: Page, text: string): Promise<void> {
  await page.getByLabel(/^mensagem/i).fill(text);
  await page.getByRole("button", { name: /^enviar$/i }).click();
}

// O design system mantém um role="alert" invisível e sempre presente (anúncios para leitores de
// tela); um aviso de verdade é o alerta que tem texto.
const visibleAlert = (page: Page) => page.getByRole("alert").filter({ hasText: /./ });
const ANSWER =
  /Encontrei \d+ lançamentos na sua agenda de recebimentos, somando R\$ [\d.]+,\d{2}\./;

test("lojista entra, pergunta dos recebimentos e vê a resposta com os dados da conta", async ({
  browser,
}) => {
  const page = await signIn(browser, MERCHANT);
  await page.goto("/chat");

  const question = `Quando caem meus recebimentos? (e2e ${Date.now().toString(36)})`;
  await ask(page, question);

  const log = page.getByRole("log");
  await expect(log.getByText(question)).toBeVisible();
  const answer = log.getByText(ANSWER);
  await expect(answer).toBeVisible({ timeout: 30_000 });
  // A rota do turno aparece com a resposta: o especialista de suporte atendeu.
  await expect(log.getByText("support", { exact: true })).toBeVisible();
  await expect(page.getByLabel(/^mensagem/i)).toBeEnabled();
  await expect(visibleAlert(page)).toHaveCount(0);
  const answered = await answer.textContent();
  expect(answered).not.toMatch(/Encontrei 0 lançamentos/); // o seed tem agenda para o lojista

  // A conversa ficou gravada no servidor: o histórico a lista (pela pergunta, única nesta
  // execução) e, reaberta, ela traz a pergunta e a mesma resposta.
  await page.getByRole("link", { name: /^histórico$/i }).click();
  await page.getByRole("link").filter({ hasText: question }).click();
  await expect(page).toHaveURL(/\/chat\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("log").getByText(question)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("log").getByText(answered ?? "")).toBeVisible();
});

test("pergunta sem base na documentação vai para um atendente, sem inventar resposta", async ({
  browser,
}) => {
  const page = await signIn(browser, MERCHANT);
  await page.goto("/chat");

  // A base de conhecimento do E2E é vazia: o especialista não acha fonte e o turno escala.
  await ask(page, `Como funciona a antecipação de recebíveis? (e2e ${Date.now().toString(36)})`);

  await expect(page.getByRole("status").filter({ hasText: /aguardando atendente/i })).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByRole("log").getByText(/encaminhei sua conversa/i)).toBeVisible();
  await expect(page.getByLabel(/^mensagem/i)).toBeDisabled();
  await expect(visibleAlert(page)).toHaveCount(0);
});
