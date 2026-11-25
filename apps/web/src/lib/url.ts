/** True só para URL absoluta `http:`/`https:` com host. `javascript:`, `data:`, `vbscript:`,
 * relativa ou malformada → false: o texto vindo do agente ou de uma fonte não vira link. */
export function isHttpUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url.trim());
    return (parsed.protocol === "http:" || parsed.protocol === "https:") && parsed.host !== "";
  } catch {
    return false;
  }
}
