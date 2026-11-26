import { describe, expect, it } from "vitest";
import { color } from "./tokens";

// QA-R3-006..011: axe found ~100 `color-contrast` violations on /queue, all the "SLA estourado"
// badge (warning text on its own 10% tint gave 4.39:1). DESIGN.md §a11y asks for ≥ 4.5:1 for
// `label` text on every surface; this test checks the token pairs the UI actually renders.

function channels(hex: string): number[] {
  const h = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

function luminance(hex: string): number {
  const weights = [0.2126, 0.7152, 0.0722];
  return channels(hex).reduce((sum, v, i) => {
    const c = v / 255;
    const linear = c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    return sum + (weights[i] ?? 0) * linear;
  }, 0);
}

function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** `bg-state-x/10` painted over a surface: 10% of the colour, 90% of what is under it. */
function tint(fg: string, under: string, alpha = 0.1): string {
  const f = channels(fg);
  const u = channels(under);
  return `#${f
    .map((v, i) => Math.round(alpha * v + (1 - alpha) * (u[i] ?? 0)))
    .map((v) => v.toString(16).padStart(2, "0"))
    .join("")}`;
}

const SURFACES = { "surface-100": color.surface[100], "surface-200": color.surface[200] };
const MIN = 4.5;

describe("design tokens: text contrast ≥ 4.5:1 (DESIGN.md, axe color-contrast)", () => {
  for (const [surfaceName, surface] of Object.entries(SURFACES)) {
    it.each([
      ["success", color.state.success],
      ["warning", color.state.warning],
      ["danger", color.state.danger],
    ])(`StatusBadge %s on its tint over ${surfaceName}`, (_, fg) => {
      expect(contrast(fg, tint(fg, surface))).toBeGreaterThanOrEqual(MIN);
    });

    it.each([
      ["ink-400", color.ink[400]],
      ["ink-500", color.ink[500]],
      ["ink-700", color.ink[700]],
    ])(`%s text on ${surfaceName}`, (_, fg) => {
      expect(contrast(fg, surface)).toBeGreaterThanOrEqual(MIN);
    });
  }

  it("StatusBadge neutral (ink-700 on surface-200)", () => {
    expect(contrast(color.ink[700], color.surface[200])).toBeGreaterThanOrEqual(MIN);
  });

  it("StatusBadge brand (brand-pressed on brand-soft)", () => {
    expect(contrast(color.brand.pressed, color.brand.soft)).toBeGreaterThanOrEqual(MIN);
  });

  it("the old warning shade really failed, so this test would have caught QA-R3-006", () => {
    expect(contrast("#B45309", tint("#B45309", color.surface[100]))).toBeLessThan(MIN);
  });
});
