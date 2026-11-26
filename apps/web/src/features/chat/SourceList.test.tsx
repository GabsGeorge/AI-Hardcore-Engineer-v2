import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SourceList } from "./SourceList";

describe("SourceList: só http/https vira link (A3)", () => {
  it("javascript: e data: aparecem como texto, https como link", () => {
    const { container } = render(
      <SourceList
        sources={[
          { title: "Ok", url: "https://site.getnet.com.br/", kind: "kb" },
          { title: "Script", url: "javascript:alert(document.cookie)", kind: "web" },
          { title: "Dados", url: "data:text/html,<script>1</script>", kind: "web" },
        ]}
      />,
    );
    expect(container.querySelectorAll("a")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Ok" })).toHaveAttribute(
      "href",
      "https://site.getnet.com.br/",
    );
    expect(screen.getByText("Script").closest("a")).toBeNull();
    expect(screen.getByText("Dados").closest("a")).toBeNull();
  });
});
