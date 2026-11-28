import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/design";
import { ApiError } from "@/lib/api";
import { AccountsPage } from "./AccountsPage";
import { AuditPage } from "./AuditPage";
import { MerchantLinkPage } from "./MerchantLinkPage";
import { QueueOverviewPage } from "./QueueOverviewPage";

// Auditoria do front-end (2026-11-28): nas telas do administrador, uma consulta que falhava
// aparecia como resultado vazio. O painel da fila mostrava três zeros ("fila limpa"), a
// auditoria mostrava "Nenhum evento" e a lista de contas, "Nenhuma conta".

const getMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, api: { ...original.api, get: (...a: unknown[]) => getMock(...a) } };
});

const serverError = () => new ApiError("http_500", "Internal Server Error", 500, null);

function renderPage(page: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter>{page}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
  return client;
}

beforeAll(async () => {
  await i18n.changeLanguage("pt");
});
beforeEach(() => {
  getMock.mockReset();
});

describe("QueueOverviewPage: sem resposta não há números", () => {
  it("na falha mostra o erro e nenhum indicador, em vez de três zeros", async () => {
    getMock.mockRejectedValue(serverError());
    renderPage(<QueueOverviewPage />);

    const title = await screen.findByText("Não foi possível carregar");
    expect(title.closest('[role="alert"]')).not.toBeNull();
    expect(screen.queryByText("Aguardando atendimento")).not.toBeInTheDocument();
    expect(screen.queryByText("SLA estourado hoje")).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    // O título e o caminho para a fila continuam na tela.
    expect(screen.getByRole("heading", { name: "Fila do dia" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ir para a fila" })).toBeInTheDocument();
  });

  it("tentar de novo traz os números", async () => {
    getMock
      .mockRejectedValueOnce(serverError())
      .mockResolvedValue({ awaiting_human: 7, sla_breached_today: 3, expired_today: 2 });
    renderPage(<QueueOverviewPage />);

    await userEvent.click(await screen.findByRole("button", { name: "Tentar de novo" }));

    expect(await screen.findByText("7")).toBeInTheDocument();
    expect(screen.queryByText("Não foi possível carregar")).not.toBeInTheDocument();
  });

  it("se a atualização falha, mantém os números e avisa que podem estar antigos", async () => {
    getMock
      .mockResolvedValueOnce({ awaiting_human: 7, sla_breached_today: 3, expired_today: 2 })
      .mockRejectedValue(serverError());
    const client = renderPage(<QueueOverviewPage />);
    expect(await screen.findByText("7")).toBeInTheDocument();

    await act(async () => {
      await client.refetchQueries({ queryKey: ["admin", "queue-overview"] });
    });

    const notice = await screen.findByText("Não foi possível atualizar");
    expect(notice.closest('[role="status"]')).not.toBeNull();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("zeros de verdade continuam aparecendo como zeros", async () => {
    getMock.mockResolvedValue({ awaiting_human: 0, sla_breached_today: 0, expired_today: 0 });
    renderPage(<QueueOverviewPage />);

    expect(await screen.findByText("Aguardando atendimento")).toBeInTheDocument();
    expect(screen.getAllByText("0")).toHaveLength(3);
    expect(screen.queryByText("Não foi possível carregar")).not.toBeInTheDocument();
  });
});

describe("AuditPage: falha ao carregar não é 'nenhum evento'", () => {
  it("mostra o erro, e não a auditoria vazia", async () => {
    getMock.mockRejectedValue(serverError());
    renderPage(<AuditPage />);

    expect(await screen.findByText("Não foi possível carregar")).toBeInTheDocument();
    expect(screen.queryByText("Nenhum evento")).not.toBeInTheDocument();
  });

  it("uma auditoria sem eventos continua dizendo que não há eventos", async () => {
    getMock.mockResolvedValue([]);
    renderPage(<AuditPage />);

    expect(await screen.findByText("Nenhum evento")).toBeInTheDocument();
    expect(screen.queryByText("Não foi possível carregar")).not.toBeInTheDocument();
  });
});

describe("AccountsPage: falha ao carregar não é 'nenhuma conta'", () => {
  it("mostra o erro, e não a lista vazia", async () => {
    getMock.mockRejectedValue(serverError());
    renderPage(<AccountsPage />);

    expect(await screen.findByText("Não foi possível carregar")).toBeInTheDocument();
    expect(screen.queryByText("Nenhuma conta")).not.toBeInTheDocument();
  });
});

describe("MerchantLinkPage: falha ao carregar as contas é avisada", () => {
  it("mostra o erro acima do formulário e busca de novo ao tentar", async () => {
    getMock.mockRejectedValueOnce(serverError()).mockResolvedValue([
      {
        id: "acc_2",
        email: "loja@example.com",
        display_name: "Loja Dois",
        role: "merchant",
        status: "active",
        linked_merchant_id: null,
      },
    ]);
    renderPage(<MerchantLinkPage />);

    await userEvent.click(await screen.findByRole("button", { name: "Tentar de novo" }));

    expect(await screen.findByRole("option", { name: /Loja Dois/ })).toBeInTheDocument();
    expect(screen.queryByText("Não foi possível carregar")).not.toBeInTheDocument();
  });
});
