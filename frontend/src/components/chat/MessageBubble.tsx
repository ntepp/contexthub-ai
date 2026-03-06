import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { User, Zap } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: { source: string; page: number | null }[]
}

export function MessageBubble({ role, content, sources }: Message) {
  const isUser = role === 'user'

  return (
    <div className={cn('flex gap-3 animate-fade-in', isUser && 'flex-row-reverse')}>
      {/* Avatar */}
      <div
        className={cn(
          'flex items-center justify-center w-8 h-8 rounded-full shrink-0 text-xs',
          isUser ? 'bg-muted text-secondary' : 'bg-accent/10 text-accent',
        )}
      >
        {isUser ? <User size={14} /> : <Zap size={14} />}
      </div>

      {/* Bubble */}
      <div className={cn('max-w-[75%] space-y-2', isUser && 'items-end flex flex-col')}>
        <div
          className={cn(
            'rounded-2xl px-4 py-3 text-sm leading-relaxed',
            isUser
              ? 'bg-accent text-white rounded-tr-sm'
              : 'bg-surface border border-border text-primary rounded-tl-sm',
          )}
        >
          {isUser ? (
            content
          ) : (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                code: ({ className, children }) => {
                  const isBlock = className?.includes('language-')
                  return isBlock ? (
                    <pre className="mt-2 overflow-x-auto rounded-lg bg-background p-3 text-xs font-mono">
                      <code>{children}</code>
                    </pre>
                  ) : (
                    <code className="rounded bg-background px-1.5 py-0.5 text-xs font-mono text-accent">
                      {children}
                    </code>
                  )
                },
                table: ({ children }) => (
                  <div className="overflow-x-auto mt-2">
                    <table className="text-xs border-collapse w-full">{children}</table>
                  </div>
                ),
                th: ({ children }) => (
                  <th className="border border-border px-2 py-1 bg-muted/30 text-left font-medium">
                    {children}
                  </th>
                ),
                td: ({ children }) => (
                  <td className="border border-border px-2 py-1">{children}</td>
                ),
              }}
            >
              {content}
            </ReactMarkdown>
          )}
        </div>

        {/* Sources */}
        {sources && sources.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {sources.map((s, i) => (
              <span
                key={i}
                className="text-[10px] px-2 py-0.5 rounded-full bg-muted/40 text-muted-fg border border-border"
              >
                {s.source.split('/').pop()}
                {s.page != null && ` · p.${s.page}`}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
