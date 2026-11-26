import { describe, expect, it } from "vitest";
import { isHttpUrl } from "./url";

describe("isHttpUrl", () => {
  it.each([
    ["https://site.getnet.com.br/", true],
    ["http://example.com", true],
    ["javascript:alert(1)", false],
    [" JavaScript:alert(1)", false],
    ["data:text/html,<b>x</b>", false],
    ["vbscript:msgbox(1)", false],
    ["/relativa", false],
    ["", false],
    [undefined, false],
  ])("%s → %s", (url, expected) => {
    expect(isHttpUrl(url)).toBe(expected);
  });
});
