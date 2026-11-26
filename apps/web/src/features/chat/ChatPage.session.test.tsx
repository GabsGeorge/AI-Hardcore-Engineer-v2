import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPage, MAX_MESSAGE_CHARS } from "./ChatPage";

const streamPostMock = vi.fn();
vi.mock("@/lib/sse", () => ({
  streamPost: (...args: unknown[]) => streamPostMock(...args),
}));

function LoginProbe() {
  const location = useLocation();
  return <p data-testid="login">{location.search}</p>;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/chat"]}>
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/login" element={<LoginProbe />} />
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

describe("ChatPage: sessão, vínculo e limites (QA-040, QA-021, QA-057)", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("pt");
  });
  beforeEach(() => {
    streamPostMock.mockReset();
  });

  it("sessão expirada leva ao login com volta para o chat", async () => {
    streamPostMock.mockImplementation(async () => {
      throw new Error("sse_http_401");
    });
    await ask("oi");
    const probe = await screen.findByTestId("login");
    expect(probe.textContent).toContain("expired=1");
    expect(probe.textContent).toContain(encodeURIComponent("/chat"));
  });

  it("conta sem estabelecimento recebe aviso claro em vez do erro genérico", async () => {
    streamPostMock.mockImplementation(async () => {
      throw new Error("sse_http_409");
    });
    await ask("oi");
    expect(await screen.findByText(/não está vinculada a um estabelecimento/i)).toBeTruthy();
    expect(screen.queryByText(/Tente de novo em instantes/i)).toBeNull();
  });

  it("o campo aceita no máximo o limite do backend", () => {
    renderPage();
    const field = screen.getByLabelText(/mensagem|message/i);
    expect(field.getAttribute("maxlength")).toBe(String(MAX_MESSAGE_CHARS));
    expect(MAX_MESSAGE_CHARS).toBe(4000);
  });

  it("devolve o foco ao campo quando a resposta termina", async () => {
    streamPostMock.mockImplementation(
      async (_p: string, _b: unknown, h: { onEvent?: (n: string, d: unknown) => void }) => {
        // A resposta leva um instante, como na rede: o campo fica desabilitado nesse meio-tempo.
        await new Promise((resolve) => setTimeout(resolve, 20));
        h.onEvent?.("done", {
          response: "ok",
          conversation_id: "c1",
          route: [],
          sources: [],
          tools_used: [],
          confidence: 1,
          handoff: { status: "none", ticket_id: null },
          guardrails: { input: "pass", output: "pass" },
          trace_id: "t1",
        });
      },
    );
    await ask("oi");
    await screen.findByText("ok");
    expect(document.activeElement).toBe(screen.getByLabelText(/mensagem|message/i));
  });
});
