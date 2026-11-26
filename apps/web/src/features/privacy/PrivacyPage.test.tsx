import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/design";
import { ApiError } from "@/lib/api";
import { PrivacyPage } from "./PrivacyPage";

const deleteMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, api: { ...original.api, delete: (...a: unknown[]) => deleteMock(...a) } };
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/privacy"]}>
          <Routes>
            <Route path="/privacy" element={<PrivacyPage />} />
            <Route path="/login" element={<p>tela de login</p>} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("PrivacyPage (FR-016, FR-016a, QA-016)", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("pt");
  });
  beforeEach(() => {
    deleteMock.mockReset();
  });

  it("apaga as conversas só depois de digitar EXCLUIR e mostra o resumo", async () => {
    deleteMock.mockResolvedValue({
      deleted: { conversations: 3, feedback: 1, audit_events_pseudonymized: 4 },
    });
    const user = userEvent.setup();
    renderPage();
    const button = screen.getByRole("button", { name: "Apagar conversas" });
    expect(button.hasAttribute("disabled")).toBe(true);
    const [convsConfirm] = screen.getAllByLabelText(/Digite EXCLUIR para confirmar/);
    await user.type(convsConfirm!, "EXCLUIR");
    await user.click(button);
    expect(deleteMock).toHaveBeenCalledWith("/v1/me/data");
    expect(await screen.findByText(/3 conversas/)).toBeTruthy();
  });

  it("exclui a conta com senha e confirmação e volta para o login", async () => {
    deleteMock.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByLabelText(/Senha atual/), "minha-senha-atual-123");
    const confirms = screen.getAllByLabelText(/Digite EXCLUIR para confirmar/);
    await user.type(confirms[1]!, "EXCLUIR");
    await user.click(screen.getByRole("button", { name: "Excluir conta" }));
    expect(deleteMock).toHaveBeenCalledWith("/v1/me/account", {
      password: "minha-senha-atual-123",
      confirmation: "EXCLUIR",
    });
    expect(await screen.findByText("tela de login")).toBeTruthy();
  });

  it("senha errada fica marcada no campo e a conta não sai", async () => {
    deleteMock.mockRejectedValue(new ApiError("credentials_invalid", "Senha inválida.", 401, null));
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByLabelText(/Senha atual/), "errada-errada-123");
    const confirms = screen.getAllByLabelText(/Digite EXCLUIR para confirmar/);
    await user.type(confirms[1]!, "EXCLUIR");
    await user.click(screen.getByRole("button", { name: "Excluir conta" }));
    expect(await screen.findByText("Senha incorreta. Tente novamente.")).toBeTruthy();
    expect(screen.queryByText("tela de login")).toBeNull();
  });
});
