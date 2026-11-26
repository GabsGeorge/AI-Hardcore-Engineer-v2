/** EventSource simulado para os testes: guarda as instâncias abertas e deixa o teste emitir
 * eventos nomeados (com `lastEventId`) e erros com o `readyState` que o navegador usaria. */
export class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readonly url: string;
  readonly withCredentials: boolean;
  readyState = FakeEventSource.CONNECTING;
  onerror: ((event: Event) => void) | null = null;
  private listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>();

  constructor(url: string, init?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = Boolean(init?.withCredentials);
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, listener: (event: MessageEvent<string>) => void): void {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), listener]);
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED;
  }

  get closed(): boolean {
    return this.readyState === FakeEventSource.CLOSED;
  }

  emit(name: string, data: unknown, id = ""): void {
    this.readyState = FakeEventSource.OPEN;
    const event = new MessageEvent<string>(name, {
      data: JSON.stringify(data),
      lastEventId: id,
    });
    for (const listener of this.listeners.get(name) ?? []) listener(event);
  }

  fail(readyState: number): void {
    this.readyState = readyState;
    this.onerror?.(new Event("error"));
  }

  static reset(): void {
    FakeEventSource.instances = [];
  }

  static last(): FakeEventSource {
    const source = FakeEventSource.instances.at(-1);
    if (!source) throw new Error("nenhum EventSource aberto");
    return source;
  }
}
