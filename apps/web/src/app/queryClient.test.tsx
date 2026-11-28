import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import i18n from "i18next";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { QueuePage } from "@/features/handoffs/QueuePage";
import { ApiError } from "@/lib/api";
import { AuthGuard } from "./guards";
import { createQueryClient } from "./queryClient";

// A fila do operador busca de novo a cada 30 s. Quando a sessão acaba com a aba aberta, a busca
// volta 401: "não foi possível carregar, tente de novo" seria um beco sem saída, porque tentar de
// novo falha para sempre. O certo é voltar ao login e retornar à mesma página depois.

const getMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, api: { ...original.api, get: (...a: unknown[]) => getMock(...a) } };
});

const OPERATOR = { id: "op_1", role: "operator", display_name: "Operadora Um" };
const unauthorized = () => new ApiError("unauthorized", "Unauthorized", 401, null);

function Where() {
  const location = useLocation();
  return <div data-testid="where">{`${location.pathname}${location.search}`}</div>;
}

function renderQueue() {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/queue"]}>
        <Routes>
          <Route path="/login" element={<Where />} />
          <Route element={<AuthGuard />}>
            <Route path="/queue" element={<QueuePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("createQueryClient: 401 em uma consulta encerra a sessão na tela", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("pt");
  });
  beforeEach(() => {
    getMock.mockReset();
  });

  it("volta ao login com o aviso de sessão expirada e o caminho para retornar", async () => {
    getMock.mockImplementation((path: string) =>
      path === "/v1/auth/me" ? Promise.resolve(OPERATOR) : Promise.reject(unauthorized()),
    );
    renderQueue();

    expect(await screen.findByTestId("where")).toHaveTextContent("/login?next=%2Fqueue&expired=1");
    expect(screen.queryByText("Não foi possível carregar")).not.toBeInTheDocument();
  });

  it("um erro que não é 401 não derruba a sessão: fica na página, com o erro de carregamento", async () => {
    getMock.mockImplementation((path: string) =>
      path === "/v1/auth/me"
        ? Promise.resolve(OPERATOR)
        : Promise.reject(new ApiError("http_500", "Internal Server Error", 500, null)),
    );
    renderQueue();

    expect(await screen.findByText("Não foi possível carregar")).toBeInTheDocument();
    expect(screen.queryByTestId("where")).not.toBeInTheDocument();
  });

  it("quem nunca entrou vai ao login sem o aviso de sessão expirada", async () => {
    getMock.mockImplementation(() => Promise.reject(unauthorized()));
    renderQueue();

    const where = await screen.findByTestId("where");
    expect(where).toHaveTextContent("/login?next=%2Fqueue");
    expect(where).not.toHaveTextContent("expired");
  });
});
