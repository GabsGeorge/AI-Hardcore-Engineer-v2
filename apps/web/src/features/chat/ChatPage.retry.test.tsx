import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPage } from "./ChatPage";

const streamPostMock = vi.fn();
vi.mock("@/lib/sse", () => ({
  streamPost: (...args: unknown[]) => streamPostMock(...args),
}));

// O SseHttpError real tem a mensagem `sse_http_429` e carrega `retryAfterSeconds`.
const rateLimited = (retryAfterSeconds: number | null) =>
  Object.assign(new Error("sse_http_429"), { retryAfterSeconds });

async function ask() {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={["/chat"]}>
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await user.type(screen.getByLabelText(/^mensagem/i), "Qual a taxa?");
  await user.click(screen.getByRole("button", { name: /enviar/i }));
}

describe("ChatPage: aviso de limite usa o Retry-After real (A4)", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("pt");
  });
  beforeEach(() => {
    streamPostMock.mockReset();
  });

  it("mostra os segundos que o servidor mandou", async () => {
    streamPostMock.mockImplementation(async () => {
      throw rateLimited(17);
    });
    await ask();
    expect(await screen.findByText(/aguarde 17 s/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^mensagem/i)).toBeDisabled();
  });

  it("sem Retry-After usa 60 s como padrão", async () => {
    streamPostMock.mockImplementation(async () => {
      throw rateLimited(null);
    });
    await ask();
    expect(await screen.findByText(/aguarde 60 s/i)).toBeInTheDocument();
  });
});
