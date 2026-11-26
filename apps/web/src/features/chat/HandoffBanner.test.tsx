import { render, screen } from "@testing-library/react";
import i18n from "i18next";
import { beforeAll, describe, expect, it } from "vitest";
import { HandoffBanner } from "./HandoffBanner";

describe("HandoffBanner: os 4 estados (C3)", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("pt");
  });

  it("none: não mostra nada", () => {
    const { container } = render(
      <HandoffBanner handoff={{ status: "none", ticket_id: null }} slaBreached={false} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("awaiting_human: aguardando, com o número do chamado", () => {
    render(
      <HandoffBanner
        handoff={{ status: "awaiting_human", ticket_id: "T-7" }}
        slaBreached={false}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/aguardando atendente/i);
    expect(screen.getByRole("status")).toHaveTextContent("T-7");
  });

  it("awaiting_human com SLA estourado: aviso de demora", () => {
    render(<HandoffBanner handoff={{ status: "awaiting_human", ticket_id: "T-7" }} slaBreached />);
    expect(screen.getByRole("status")).toHaveTextContent(/está demorando/i);
  });

  it("resolved: respondido", () => {
    render(
      <HandoffBanner handoff={{ status: "resolved", ticket_id: "T-7" }} slaBreached={false} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/respondido/i);
  });

  it("expired: encerrado, como alerta", () => {
    render(<HandoffBanner handoff={{ status: "expired", ticket_id: "T-7" }} slaBreached={false} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/atendimento encerrado/i);
  });
});
