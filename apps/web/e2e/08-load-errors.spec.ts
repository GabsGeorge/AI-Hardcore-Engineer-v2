import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Falhas de carregamento no navegador, com as respostas do servidor simuladas no próprio
 * navegador (`page.route`), como em 05-chat.spec.ts: não dá para pedir à API real que falhe.
 *
 * O que se testa é o que a pessoa vê quando algo não carrega. Antes desta correção, a fila do
 * operador dizia "nenhum chamado aguardando" quando a consulta falhava, o painel do
 * administrador mostrava três zeros, e uma página cujo arquivo não carregasse caía na tela de
 * erro de desenvolvedor do React Router.
 */

const TICKET = {
  id: "T-1042",
  conversation_id: "11111111-1111-4111-8111-111111111111",
  reason: "human_request",
  status: "awaiting_human",
  priority: "high",
  summary: "Lojista pede revisão de uma cobrança em duplicidade.",
  context: { merchant: { id: "cliente1988", name: "Loja Demo" } },
  pending_action: null,
  created_at: "2026-11-28T12:00:00+00:00",
  sla_due_at: "2099-01-01T00:00:00+00:00",
  expires_at: "2099-01-02T00:00:00+00:00",
};

const QUEUE_API = /\/v1\/handoffs\?/;
const OVERVIEW_API = /\/v1\/admin\/queue$/;
const QUEUE_PAGE_FILE = /\/assets\/QueuePage-[^/]+\.js$/;

const json = (status: number, body: unknown) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

async function signedInAs(page: Page, role: "operator" | "admin") {
  await page.route("**/v1/auth/me", (route) =>
    route.fulfill(json(200, { id: "acc_e2e", role, display_name: "Conta de Teste" })),
  );
}

async function expectNoSeriousA11yIssue(page: Page) {
  const axe = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => v.id)).toEqual([]);
}

test.describe("Falhas de carregamento — o que a pessoa vê", () => {
  test("fila do operador: falha não é 'fila vazia', e tentar de novo recupera", async ({
    page,
  }) => {
    let failing = true;
    await signedInAs(page, "operator");
    await page.route(QUEUE_API, (route) =>
      route.fulfill(failing ? json(500, {}) : json(200, { items: [TICKET] })),
    );
    await page.goto("/queue");

    await expect(page.getByText("Não foi possível carregar")).toBeVisible();
    await expect(page.getByText("Nada por aqui")).toHaveCount(0);
    await expect(page.getByText(/nenhum chamado aguardando/i)).toHaveCount(0);
    await expectNoSeriousA11yIssue(page);

    failing = false;
    await page.getByRole("button", { name: "Tentar de novo" }).click();

    await expect(page.getByText(TICKET.summary)).toBeVisible();
    await expect(page.getByText("Não foi possível carregar")).toHaveCount(0);
  });

  test("painel do administrador: sem resposta não mostra zeros", async ({ page }) => {
    await signedInAs(page, "admin");
    await page.route(OVERVIEW_API, (route) => route.fulfill(json(500, {})));
    await page.goto("/admin/queue");

    await expect(page.getByRole("heading", { name: "Fila do dia" })).toBeVisible();
    await expect(page.getByText("Não foi possível carregar")).toBeVisible();
    await expect(page.getByText("Aguardando atendimento")).toHaveCount(0);
    await expect(page.getByText("SLA estourado hoje")).toHaveCount(0);
    await expectNoSeriousA11yIssue(page);
  });

  test("sessão que acaba com a fila aberta leva ao login com o aviso, não a um erro sem saída", async ({
    page,
  }) => {
    await signedInAs(page, "operator");
    await page.route(QUEUE_API, (route) =>
      route.fulfill(json(401, { error: { code: "unauthorized", message: "Sessão inválida." } })),
    );
    await page.goto("/queue");

    await expect(page).toHaveURL(/\/login\?next=%2Fqueue&expired=1$/);
    await expect(page.getByText(/sua sessão expirou/i)).toBeVisible();
    await expect(page.getByText("Não foi possível carregar")).toHaveCount(0);
  });

  test("arquivo da página que não carrega: avisa que há versão nova, e recarregar resolve", async ({
    page,
  }) => {
    // Depois de um deploy, o arquivo antigo da página deixa de existir no servidor (404).
    await signedInAs(page, "operator");
    await page.route(QUEUE_API, (route) => route.fulfill(json(200, { items: [TICKET] })));
    await page.route(QUEUE_PAGE_FILE, (route) => route.fulfill({ status: 404, body: "" }));
    await page.goto("/queue");

    await expect(page.getByRole("heading", { name: "Há uma versão nova" })).toBeVisible();
    await expect(page.getByText(/unexpected application error/i)).toHaveCount(0);
    // O cabeçalho e o menu continuam na tela.
    await expect(page.getByRole("button", { name: "Sair" })).toBeVisible();
    await expectNoSeriousA11yIssue(page);

    await page.unroute(QUEUE_PAGE_FILE);
    await page.getByRole("button", { name: "Recarregar a página" }).click();

    await expect(page.getByText(TICKET.summary)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Há uma versão nova" })).toHaveCount(0);
  });
});
