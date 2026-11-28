import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPage } from "./ChatPage";

type Handlers = {
  onEvent?: (name: string, data: unknown) => void;
  onError?: (err: Error) => void;
  onDone?: () => void;
};

const streamPostMock = vi.fn();
vi.mock("@/lib/sse", () => ({
  streamPost: (...args: unknown[]) => streamPostMock(...args),
}));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/chat"]}>
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/chat/:id" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function ask(text: string) {
  const user = userEvent.setup();
  renderPage();
  await user.type(screen.getByLabelText(/mensagem|message/i), text);
  await user.click(screen.getByRole("button", { name: /enviar|send/i }));
}

const FINAL = {
  response: "A taxa do débito acima de R$ 80 mil é 0,85%.",
  conversation_id: "c1",
  route: ["supervisor", "knowledge"],
  sources: [
    {
      title: "Maquininhas Getnet",
      url: "https://site.getnet.com.br/todas-as-maquininhas/#offers-card",
      kind: "kb",
    },
  ],
  tools_used: [],
  confidence: 0.9,
  handoff: { status: "none", ticket_id: null },
  guardrails: { input: "pass", output: "pass" },
  trace_id: "t1",
};

/** Um fluxo que para no meio e só continua quando o teste manda (`release`). */
function gated(steps: (h: Handlers) => void, rest: (h: Handlers) => void) {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  streamPostMock.mockImplementation(async (_path: string, _body: unknown, h: Handlers) => {
    steps(h);
    await gate;
    rest(h);
  });
  return () => release();
}

describe("ChatPage: resposta aparece enquanto é gerada (eventos token)", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("pt");
  });
  beforeEach(() => {
    streamPostMock.mockReset();
  });

  it("mostra o rascunho com o aviso de verificação e troca pela resposta final no done", async () => {
    const release = gated(
      (h) => {
        h.onEvent?.("agent_step", { node: "knowledge" });
        h.onEvent?.("token", { text: "A taxa do débito " });
        h.onEvent?.("token", { text: "acima de R$ 80 mil " });
      },
      (h) => h.onEvent?.("done", FINAL),
    );
    await ask("qual a taxa do débito?");

    const draft = await screen.findByTestId("draft");
    expect(draft).toHaveTextContent("A taxa do débito acima de R$ 80 mil");
    expect(draft).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText(/verificando a resposta/i)).toBeInTheDocument();
    expect(screen.queryByText(/pensando/i)).not.toBeInTheDocument();

    release();
    expect(await screen.findByText(/0,85%/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("draft")).not.toBeInTheDocument());
    expect(screen.queryByText(/verificando a resposta/i)).not.toBeInTheDocument();
  });

  it("quando o verificador troca a resposta, o rascunho some e fica só a resposta final", async () => {
    const blocked = {
      ...FINAL,
      response: "Não encontrei base suficiente nas fontes consultadas.",
      sources: [],
      guardrails: { input: "pass", output: "blocked" },
    };
    const release = gated(
      (h) => h.onEvent?.("token", { text: "A taxa do Pix é de 0,10% por transação " }),
      (h) => h.onEvent?.("done", blocked),
    );
    await ask("qual a taxa do Pix?");
    expect(await screen.findByTestId("draft")).toHaveTextContent("0,10%");

    release();
    expect(await screen.findByText(/não encontrei base suficiente/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/0,10%/)).not.toBeInTheDocument());
  });

  it("token_reset limpa o rascunho e o texto novo começa do zero", async () => {
    const release = gated(
      (h) => {
        h.onEvent?.("token", { text: "Primeira tentativa de resposta " });
        h.onEvent?.("token_reset", {});
        h.onEvent?.("token", { text: "Segunda resposta " });
      },
      (h) => h.onEvent?.("done", FINAL),
    );
    await ask("qual a taxa do débito?");
    const draft = await screen.findByTestId("draft");
    expect(draft).toHaveTextContent("Segunda resposta");
    expect(draft).not.toHaveTextContent("Primeira tentativa");
    release();
    expect(await screen.findByText(/0,85%/)).toBeInTheDocument();
  });

  it("se o fluxo cai no meio, o rascunho não fica na tela como se fosse resposta", async () => {
    const release = gated(
      (h) => h.onEvent?.("token", { text: "Texto parcial ainda não verificado " }),
      (h) => h.onDone?.(),
    );
    await ask("qual a taxa do débito?");
    expect(await screen.findByTestId("draft")).toBeInTheDocument();

    release();
    expect(await screen.findByRole("alert")).toHaveTextContent(/interrompida/i);
    expect(screen.queryByText(/texto parcial/i)).not.toBeInTheDocument();
  });
});
