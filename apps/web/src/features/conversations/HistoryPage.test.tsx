import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { HistoryPage } from "./HistoryPage";

// Auditoria do front-end (2026-11-28): se a consulta do histórico falhava, o lojista via "Nada
// por aqui ainda. Comece uma conversa", como se nunca tivesse conversado.

const getMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, api: { ...original.api, get: (...a: unknown[]) => getMock(...a) } };
});

const serverError = () => new ApiError("http_500", "Internal Server Error", 500, null);

const conversation = (id: string, preview: string) => ({
  id,
  started_at: "2026-11-27T10:00:00+00:00",
  last_activity_at: "2026-11-27T10:05:00+00:00",
  handoff_status: "none",
  last_message_preview: preview,
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <HistoryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("HistoryPage: falha ao carregar não é histórico vazio", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("pt");
  });
  beforeEach(() => {
    getMock.mockReset();
  });

  it("mostra o erro com a ação de tentar de novo, e nunca 'nada por aqui'", async () => {
    getMock.mockRejectedValue(serverError());
    renderPage();

    const title = await screen.findByText("Não foi possível carregar");
    expect(title.closest('[role="alert"]')).not.toBeNull();
    expect(screen.queryByText("Nada por aqui ainda")).not.toBeInTheDocument();

    getMock.mockResolvedValue({
      items: [conversation("c1", "Qual a taxa do débito?")],
      next_cursor: null,
    });
    await userEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));
    expect(await screen.findByText("Qual a taxa do débito?")).toBeInTheDocument();
  });

  it("um histórico realmente vazio continua convidando a começar uma conversa", async () => {
    getMock.mockResolvedValue({ items: [], next_cursor: null });
    renderPage();

    expect(await screen.findByText("Nada por aqui ainda")).toBeInTheDocument();
    expect(screen.queryByText("Não foi possível carregar")).not.toBeInTheDocument();
  });

  it("se 'carregar mais' falha, mantém o que já estava na tela e deixa tentar de novo", async () => {
    getMock
      .mockResolvedValueOnce({
        items: [conversation("c1", "Primeira conversa")],
        next_cursor: "cursor-2",
      })
      .mockRejectedValueOnce(serverError())
      .mockResolvedValue({ items: [conversation("c2", "Segunda conversa")], next_cursor: null });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Carregar mais" }));

    expect(await screen.findByText("Não foi possível carregar")).toBeInTheDocument();
    expect(screen.getByText("Primeira conversa")).toBeInTheDocument();
    // O aviso é o de "não carregou", não o de "o que está na tela pode estar antigo".
    expect(screen.queryByText("Não foi possível atualizar")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Tentar de novo" }));

    expect(await screen.findByText("Segunda conversa")).toBeInTheDocument();
    expect(screen.getByText("Primeira conversa")).toBeInTheDocument();
    expect(screen.queryByText("Não foi possível carregar")).not.toBeInTheDocument();
    expect(String(getMock.mock.calls[2]?.[0])).toContain("cursor=cursor-2");
  });
});
