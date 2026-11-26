import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Browser, type Page } from "@playwright/test";

/**
 * Privacidade (FR-016, T080) e página inicial por papel (QA-064/066) com a stack real. As contas
 * vêm de `tests/e2e_server.py`; só o LLM é roteirizado. A exclusão da conta fica nos testes de
 * integração (`tests/integration/test_me_delete_account.py`) e no Vitest: aqui apagaria a conta
 * compartilhada pelos outros specs.
 */

const PASSWORD = "e2e-senha-de-teste-123456";
const MERCHANT = "e2e-lojista@getnet.example.com";
const OPERATOR = "e2e-operador@getnet.example.com";

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

test("lojista apaga as conversas pela tela de Privacidade e o histórico fica vazio", async ({
  browser,
}) => {
  const page = await signIn(browser, MERCHANT);
  await expect(page).toHaveURL(/\/chat$/);
  await page.goto("/privacy");
  await expect(page.getByRole("heading", { name: "Privacidade", level: 1 })).toBeVisible();
  await expect(page.getByText("Em construção")).toHaveCount(0);

  const axe = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => v.id)).toEqual([]);

  const button = page.getByRole("button", { name: "Apagar conversas" });
  await expect(button).toBeDisabled();
  await page.getByLabel("Digite EXCLUIR para confirmar").first().fill("EXCLUIR");
  await button.click();
  await expect(page.getByText("Resumo do que foi apagado")).toBeVisible();

  await page.goto("/history");
  await expect(page.locator("a[href^='/chat/']")).toHaveCount(0);
});

test("operador entra direto na fila depois do login", async ({ browser }) => {
  const page = await signIn(browser, OPERATOR);
  await expect(page).toHaveURL(/\/queue$/);
  await expect(page.getByText(/acesso negado/i)).toHaveCount(0);
});
