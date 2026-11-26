import { render, screen } from "@testing-library/react";
import i18n from "i18next";
import { beforeAll, describe, expect, it } from "vitest";
import { homeFor } from "@/app/session";
import { merchantLabel, TicketContext } from "./TicketContext";

describe("TicketContext (QA-022)", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("pt");
  });

  it("mostra o lojista e as últimas mensagens como texto, sem o JSON da rota", () => {
    render(
      <TicketContext
        context={{
          merchant: { id: "cliente1988", name: "Mercadinho Sol Nascente" },
          recent_messages: [
            { role: "merchant", content: "Quero falar com um atendente <b>agora</b>" },
            { role: "assistant", content: "Vou transferir você." },
          ],
          route: ["input_guardrail", "supervisor", "escalation"],
        }}
      />,
    );
    expect(screen.getByText("Mercadinho Sol Nascente")).toBeTruthy();
    expect(screen.getByText("(cliente1988)")).toBeTruthy();
    // Entrada do lojista é texto: a tag aparece literal, não vira HTML.
    expect(screen.getByText("Quero falar com um atendente <b>agora</b>")).toBeTruthy();
    expect(screen.queryByText(/"route"/)).toBeNull();
  });

  it("chamado antigo (só a rota) não quebra", () => {
    render(<TicketContext context={{ route: ["escalation"] }} />);
    expect(screen.getByText("Lojista não identificado")).toBeTruthy();
    expect(merchantLabel({ route: [] })).toBe("");
  });
});

describe("homeFor (QA-064, QA-066)", () => {
  it("cada papel começa na sua tela", () => {
    expect(homeFor("merchant")).toBe("/chat");
    expect(homeFor("operator")).toBe("/queue");
    expect(homeFor("admin")).toBe("/admin");
  });
});
