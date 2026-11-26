import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import i18n from "i18next";
import { MemoryRouter } from "react-router-dom";
import { afterAll, describe, expect, it, vi } from "vitest";
import { Spinner, ToastProvider } from "@/design";
import { AccountsPage } from "./AccountsPage";

// QA-R3-015..019 follow-up: role and status were printed raw ("active", "operator") in the
// Portuguese UI, and the Spinner's accessible name was fixed in Portuguese.

const getMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, api: { ...original.api, get: (...a: unknown[]) => getMock(...a) } };
});

const ACCOUNTS = [
  {
    id: "acc_1",
    email: "op@example.com",
    display_name: "Operadora Um",
    role: "operator",
    status: "pending",
    linked_merchant_id: null,
  },
  {
    id: "acc_2",
    email: "loja@example.com",
    display_name: "Loja Dois",
    role: "merchant",
    status: "active",
    linked_merchant_id: "cliente1988",
  },
];

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter>
          <AccountsPage />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("AccountsPage: role and status are translated", () => {
  afterAll(async () => {
    await i18n.changeLanguage("pt");
  });

  it("shows Portuguese labels in the pt UI, never the raw API values", async () => {
    await i18n.changeLanguage("pt");
    getMock.mockResolvedValue(ACCOUNTS);
    renderPage();
    const first = (await screen.findByText("Operadora Um")).closest("li") as HTMLElement;
    expect(within(first).getByText("Operador")).toBeInTheDocument();
    expect(within(first).getByText("Pendente")).toBeInTheDocument();
    const second = screen.getByText("Loja Dois").closest("li") as HTMLElement;
    expect(within(second).getByText("Lojista")).toBeInTheDocument();
    expect(within(second).getByText("Ativa")).toBeInTheDocument();
    const list = screen.getByRole("list");
    expect(list.textContent).not.toMatch(/\b(operator|merchant|pending|active)\b/);
    expect(screen.getByRole("option", { name: "Bloqueada" })).toBeInTheDocument();
  });

  it("shows English labels in the en UI", async () => {
    await i18n.changeLanguage("en");
    getMock.mockResolvedValue(ACCOUNTS);
    renderPage();
    const first = (await screen.findByText("Operadora Um")).closest("li") as HTMLElement;
    expect(within(first).getByText("Operator")).toBeInTheDocument();
    expect(within(first).getByText("Pending")).toBeInTheDocument();
  });
});

describe("Spinner accessible name follows the UI language", () => {
  it.each([
    ["pt", "Carregando…"],
    ["en", "Loading…"],
  ])("%s", async (lang, name) => {
    await i18n.changeLanguage(lang);
    render(<Spinner />);
    expect(screen.getByRole("img", { name })).toBeInTheDocument();
  });
});
