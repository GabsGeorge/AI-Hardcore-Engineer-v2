import { act, render, screen, waitFor, within } from "@testing-library/react";
import i18n from "i18next";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveRegion } from "@/design";
import { FakeEventSource } from "@/test/fakeEventSource";
import { ChatPage } from "./ChatPage";

// Hidratação de /chat/:id: cada conversa volta com o handoff pedido pelo teste.
const conversations: Record<string, { status: string; ticket_id: string | null }> = {};
vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(async (path: string) => {
      const id = path.split("/").pop()!;
      return {
        conversation_id: id,
        handoff: conversations[id] ?? { status: "none", ticket_id: null },
        messages: [
          { role: "user", content: "Quero falar com um atendente" },
          { role: "assistant", content: "Resposta antiga do assistente" },
        ],
      };
    }),
  },
}));

let goTo: (path: string) => void = () => {};
function Navigator() {
  goTo = useNavigate();
  return null;
}

function renderAt(path: string) {
  return render(
    <LiveRegion>
      <MemoryRouter initialEntries={[path]}>
        <Navigator />
        <Routes>
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/chat/:id" element={<ChatPage />} />
          <Route path="/historico" element={<p>Histórico</p>} />
        </Routes>
      </MemoryRouter>
    </LiveRegion>,
  );
}

const composer = () => screen.getByLabelText(/^mensagem/i);

async function openAwaiting(id = "conv-1") {
  conversations[id] = { status: "awaiting_human", ticket_id: "T-1042" };
  renderAt(`/chat/${id}`);
  await screen.findByText(/aguardando atendente/i);
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  return FakeEventSource.last();
}

describe("ChatPage: resposta do atendente em tempo real (FR-010, T065)", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("pt");
  });
  beforeEach(() => {
    FakeEventSource.reset();
    vi.stubGlobal("EventSource", FakeEventSource);
    for (const key of Object.keys(conversations)) delete conversations[key];
  });
  afterEach(() => vi.unstubAllGlobals());

  it("ao reabrir pelo histórico uma conversa em awaiting_human, começa a escutar", async () => {
    const source = await openAwaiting("conv-9");
    expect(source.url).toBe("/v1/conversations/conv-9/events");
    expect(composer()).toBeDisabled();
  });

  it("não escuta quando a conversa não está esperando atendente", async () => {
    conversations["conv-2"] = { status: "resolved", ticket_id: "T-1" };
    renderAt("/chat/conv-2");
    await screen.findByText(/respondido/i);
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("final_message: mostra a resposta, anuncia, marca resolvido, libera o campo e fecha", async () => {
    const source = await openAwaiting();
    act(() => source.emit("final_message", { content: "Estorno aprovado." }, "final_message:2"));

    expect(await screen.findByRole("log")).toHaveTextContent("Estorno aprovado.");
    expect(screen.getByText(/^respondido$/i)).toBeInTheDocument();
    await waitFor(() => expect(composer()).toBeEnabled());
    expect(source.closed).toBe(true);
    // A LiveRegion (aria-live) recebe o texto no próximo frame.
    await waitFor(() =>
      expect(document.querySelector('[aria-live="polite"]')).toHaveTextContent("Estorno aprovado."),
    );
  });

  it("final_message igual à última resposta já carregada não duplica a bolha", async () => {
    // A página carregou entre a gravação da resposta e o `resolved`: o GET já trouxe a
    // resposta, e o canal a entrega de novo antes do status (docs/limitations.md §6).
    const source = await openAwaiting();
    act(() =>
      source.emit("final_message", { content: "Resposta antiga do assistente" }, "final_message:1"),
    );
    expect(await screen.findByText(/^respondido$/i)).toBeInTheDocument();
    // Só a lista de mensagens: o anúncio para leitor de tela (aria-live) repete o texto fora
    // dela um frame depois, e contar na página inteira dava 1 ou 2 conforme o momento.
    const log = within(screen.getByRole("log"));
    expect(log.getAllByText("Resposta antiga do assistente")).toHaveLength(1);
    await waitFor(() => expect(composer()).toBeEnabled());
  });

  it("handoff_update: atualiza o estado do handoff", async () => {
    const source = await openAwaiting();
    act(() =>
      source.emit("handoff_update", { status: "resolved", ticket_id: "T-1042" }, "hu:resolved"),
    );
    expect(await screen.findByText(/^respondido$/i)).toBeInTheDocument();
    expect(composer()).toBeEnabled();
    expect(source.closed).toBe(true);
  });

  it("sla_breached: liga o aviso de demora e mantém a escuta", async () => {
    const source = await openAwaiting();
    act(() => source.emit("sla_breached", { ticket_id: "T-1042" }, "sla:T-1042"));
    expect(await screen.findByText(/está demorando/i)).toBeInTheDocument();
    expect(composer()).toBeDisabled();
    expect(source.closed).toBe(false);
  });

  it("handoff_expired: marca expirado, libera o campo e fecha", async () => {
    const source = await openAwaiting();
    act(() => source.emit("handoff_expired", { ticket_id: "T-1042" }, "exp:T-1042"));
    expect(await screen.findByText(/atendimento encerrado/i)).toBeInTheDocument();
    expect(composer()).toBeEnabled();
    expect(source.closed).toBe(true);
  });

  it("fecha ao sair da página", async () => {
    const source = await openAwaiting();
    act(() => goTo("/historico"));
    await screen.findByText("Histórico");
    expect(source.closed).toBe(true);
  });

  it("fecha ao trocar de conversa e escuta a nova se ela também espera atendente", async () => {
    const first = await openAwaiting("conv-1");
    conversations["conv-3"] = { status: "awaiting_human", ticket_id: "T-2000" };
    act(() => goTo("/chat/conv-3"));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
    expect(first.closed).toBe(true);
    expect(FakeEventSource.last().url).toBe("/v1/conversations/conv-3/events");
  });
});
