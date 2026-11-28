import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router-dom";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { isChunkLoadError, RouteError } from "./RouteError";
import { routes } from "./routes";

// Auditoria do front-end (2026-11-28): o roteador não tinha `errorElement`. Uma página que
// quebrasse ao renderizar, ou cujo arquivo não carregasse depois de um deploy, caía na tela de
// erro de desenvolvedor do React Router ("Unexpected Application Error!"), em inglês e sem saída.

const getMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, api: { ...original.api, get: (...a: unknown[]) => getMock(...a) } };
});

// Duas páginas reais que quebram ao renderizar: uma dentro da área autenticada, outra fora.
vi.mock("@/features/handoffs/QueuePage", () => ({
  QueuePage: () => {
    throw new Error("falha de renderização na fila");
  },
}));
vi.mock("@/features/auth/ForgotPage", () => ({
  ForgotPage: () => {
    throw new Error("falha de renderização fora do layout");
  },
}));

function renderRoutes(config: RouteObject[], path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(config, { initialEntries: [path] });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("RouteError: uma página que falha mostra a tela de erro do produto", () => {
  // O React registra no console todo erro de renderização capturado; é o esperado aqui.
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});

  beforeAll(async () => {
    await i18n.changeLanguage("pt");
  });
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockResolvedValue({ id: "op_1", role: "operator", display_name: "Operadora Um" });
  });
  afterAll(() => {
    consoleError.mockRestore();
    consoleWarn.mockRestore();
  });

  it("dentro da área autenticada, mostra o erro e mantém o cabeçalho e a navegação", async () => {
    renderRoutes(routes, "/queue");

    const title = await screen.findByRole("heading", { name: "Algo deu errado nesta página" });
    expect(title.closest('[role="alert"]')).not.toBeNull();
    expect(screen.getByRole("button", { name: "Recarregar a página" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Voltar ao início" })).toHaveAttribute("href", "/");
    // O layout continua de pé: o operador ainda vê o menu e o botão de sair.
    expect(screen.getAllByRole("link", { name: "Fila" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Sair" })).toBeInTheDocument();
    // Nem a tela do React Router, nem o texto do erro.
    expect(screen.queryByText(/unexpected application error/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/falha de renderização/i)).not.toBeInTheDocument();
  });

  it("fora da área autenticada, a tela de erro ocupa a página inteira", async () => {
    renderRoutes(routes, "/forgot");

    const title = await screen.findByRole("heading", { name: "Algo deu errado nesta página" });
    expect(title.closest("main")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Sair" })).not.toBeInTheDocument();
    expect(screen.queryByText(/unexpected application error/i)).not.toBeInTheDocument();
  });

  it("quando o arquivo da página não carrega, explica que há versão nova e recarrega", async () => {
    const reload = vi.fn();
    renderRoutes(
      [
        {
          path: "/",
          errorElement: <RouteError fullPage reload={reload} />,
          lazy: () =>
            Promise.reject(
              new TypeError(
                "Failed to fetch dynamically imported module: http://localhost/assets/QueuePage-a1b2c3.js",
              ),
            ),
        },
      ],
      "/",
    );

    expect(await screen.findByRole("heading", { name: "Há uma versão nova" })).toBeInTheDocument();
    expect(screen.getByText(/foi atualizado enquanto esta aba estava aberta/i)).toBeInTheDocument();
    expect(screen.queryByText(/dynamically imported/i)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Recarregar a página" }));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("a tela de erro acompanha o idioma", async () => {
    await i18n.changeLanguage("en");
    try {
      renderRoutes(routes, "/forgot");
      expect(
        await screen.findByRole("heading", { name: "Something went wrong on this page" }),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Reload the page" })).toBeInTheDocument();
    } finally {
      await i18n.changeLanguage("pt");
    }
  });
});

describe("isChunkLoadError: reconhece a falha de carregar o arquivo de uma página", () => {
  it.each([
    ["Chrome", "Failed to fetch dynamically imported module: https://app/assets/ChatPage-1a2b.js"],
    ["Firefox", "error loading dynamically imported module: https://app/assets/ChatPage-1a2b.js"],
    ["Safari", "Importing a module script failed."],
  ])("%s", (_browser, message) => {
    expect(isChunkLoadError(new TypeError(message))).toBe(true);
  });

  it("um erro comum de renderização não é tratado como versão nova", () => {
    expect(isChunkLoadError(new TypeError("Cannot read properties of undefined"))).toBe(false);
    expect(isChunkLoadError("Failed to fetch dynamically imported module")).toBe(false);
    expect(isChunkLoadError(null)).toBe(false);
  });
});
