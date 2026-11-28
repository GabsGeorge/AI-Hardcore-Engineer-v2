import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/design";
import { ApiError } from "@/lib/api";
import { QueuePage } from "./QueuePage";
import { TicketPage } from "./TicketPage";
import type { HandoffRow } from "./types";

// Auditoria do front-end (2026-11-28): quando a consulta da fila falhava, a tela caía no mesmo
// caminho da lista vazia e dizia "Nenhum chamado aguardando atendimento". Para o operador isso é
// uma afirmação falsa: havia chamados, a tela é que não conseguiu carregar.

const getMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, api: { ...original.api, get: (...a: unknown[]) => getMock(...a) } };
});

const ROW: HandoffRow = {
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

const serverError = () => new ApiError("http_500", "Internal Server Error", 500, null);

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/queue" element={<QueuePage />} />
            <Route path="/queue/:ticket_id" element={<TicketPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
  return client;
}

describe("QueuePage: falha ao carregar não é fila vazia", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("pt");
  });
  beforeEach(() => {
    getMock.mockReset();
  });

  it("mostra o erro com a ação de tentar de novo, e nunca 'nenhum chamado'", async () => {
    getMock.mockRejectedValue(serverError());
    renderAt("/queue");

    const title = await screen.findByText("Não foi possível carregar");
    expect(title.closest('[role="alert"]')).not.toBeNull();
    expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeInTheDocument();
    expect(screen.queryByText("Nada por aqui")).not.toBeInTheDocument();
    expect(screen.queryByText(/nenhum chamado aguardando/i)).not.toBeInTheDocument();
  });

  it("tentar de novo busca outra vez e mostra a fila", async () => {
    getMock.mockRejectedValueOnce(serverError()).mockResolvedValue({ items: [ROW] });
    renderAt("/queue");

    await userEvent.click(await screen.findByRole("button", { name: "Tentar de novo" }));

    expect(await screen.findByText(ROW.summary)).toBeInTheDocument();
    expect(screen.queryByText("Não foi possível carregar")).not.toBeInTheDocument();
    expect(getMock).toHaveBeenCalledTimes(2);
  });

  it("uma fila realmente vazia continua dizendo que está vazia, sem aviso de erro", async () => {
    getMock.mockResolvedValue({ items: [] });
    renderAt("/queue");

    expect(await screen.findByText("Nada por aqui")).toBeInTheDocument();
    expect(screen.queryByText("Não foi possível carregar")).not.toBeInTheDocument();
    expect(screen.queryByText("Não foi possível atualizar")).not.toBeInTheDocument();
  });

  it("se a atualização falha, mantém os chamados na tela e avisa que podem estar antigos", async () => {
    getMock.mockResolvedValueOnce({ items: [ROW] }).mockRejectedValue(serverError());
    const client = renderAt("/queue");
    expect(await screen.findByText(ROW.summary)).toBeInTheDocument();

    // O mesmo que o intervalo de 30 s faz: busca de novo com a lista já na tela.
    await act(async () => {
      await client.refetchQueries({ queryKey: ["handoffs"] });
    });

    const notice = await screen.findByText("Não foi possível atualizar");
    expect(notice.closest('[role="status"]')).not.toBeNull();
    expect(screen.getByText(/pode estar desatualizado/i)).toBeInTheDocument();
    expect(screen.getByText(ROW.summary)).toBeInTheDocument();
    expect(screen.queryByText("Nada por aqui")).not.toBeInTheDocument();
  });
});

describe("TicketPage: falha ao carregar não é 'chamado fora da fila'", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("pt");
  });
  beforeEach(() => {
    getMock.mockReset();
  });

  it("mostra o erro de carregamento quando a consulta falha", async () => {
    getMock.mockRejectedValue(serverError());
    renderAt(`/queue/${ROW.id}`);

    expect(await screen.findByText("Não foi possível carregar")).toBeInTheDocument();
    expect(screen.queryByText(/não está mais na fila/i)).not.toBeInTheDocument();
  });

  it("depois de tentar de novo com sucesso, abre o chamado", async () => {
    getMock.mockRejectedValueOnce(serverError()).mockResolvedValue({ items: [ROW] });
    renderAt(`/queue/${ROW.id}`);

    await userEvent.click(await screen.findByRole("button", { name: "Tentar de novo" }));

    expect(await screen.findByText(ROW.summary)).toBeInTheDocument();
    expect(screen.getByText("Loja Demo")).toBeInTheDocument();
  });

  it("com a fila carregada e sem o chamado nela, diz que ele não está mais na fila", async () => {
    getMock.mockResolvedValue({ items: [] });
    renderAt(`/queue/${ROW.id}`);

    expect(await screen.findByText(/não está mais na fila/i)).toBeInTheDocument();
    expect(screen.queryByText("Não foi possível carregar")).not.toBeInTheDocument();
  });
});
