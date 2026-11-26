import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { isRateable, MessageFeedback } from "./MessageFeedback";

const postMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, api: { ...original.api, post: (...a: unknown[]) => postMock(...a) } };
});

const TRACE = "0123456789abcdef0123456789abcdef";

describe("MessageFeedback (FR-015)", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("pt");
  });
  beforeEach(() => {
    postMock.mockReset();
  });

  it("envia a avaliação com o trace da resposta e agradece", async () => {
    postMock.mockResolvedValue({ status: "accepted" });
    const user = userEvent.setup();
    render(<MessageFeedback traceId={TRACE} />);
    await user.click(screen.getByRole("button", { name: /Não ajudou/ }));
    expect(postMock).toHaveBeenCalledWith("/v1/feedback", { trace_id: TRACE, rating: -1 });
    expect(await screen.findByText("Obrigado pela avaliação.")).toBeTruthy();
  });

  it("não aparece em resposta sem trace real (atendente, histórico antigo)", () => {
    const { container } = render(<MessageFeedback traceId="f-123" />);
    expect(container.textContent).toBe("");
    expect(isRateable("h-abc-1")).toBe(false);
    expect(isRateable(TRACE)).toBe(true);
  });
});
