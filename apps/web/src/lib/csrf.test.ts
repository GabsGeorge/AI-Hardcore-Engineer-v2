import { afterEach, describe, expect, it } from "vitest";
import { readCsrfCookie } from "./csrf";

describe("readCsrfCookie", () => {
  afterEach(() => {
    document.cookie = "csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
    document.cookie = "xcsrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  it("returns null without the cookie", () => {
    expect(readCsrfCookie()).toBeNull();
  });

  it("reads and decodes the csrf cookie", () => {
    document.cookie = "csrf=a%2Bb; path=/";
    expect(readCsrfCookie()).toBe("a+b");
  });

  it("ignores a cookie whose name only ends in csrf", () => {
    document.cookie = "xcsrf=evil; path=/";
    expect(readCsrfCookie()).toBeNull();
  });
});
