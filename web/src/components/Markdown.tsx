import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";

// Tabloları yatay kaydırılabilir bir sarmalayıcı içine alır (geniş YÖK Atlas
// tabloları taşmasın diye). Linkler yeni sekmede açılır.
export function Markdown({
  children,
  className = "",
}: {
  children: string;
  className?: string;
}) {
  return (
    <div className={`markdown text-[15px] ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={{
          table({ node, ...props }) {
            void node;
            return (
              <div className="table-wrap">
                <table {...props} />
              </div>
            );
          },
          a({ node, ...props }) {
            void node;
            return <a {...props} target="_blank" rel="noopener noreferrer" />;
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
