import { useEffect, useRef, useState, useCallback } from 'react'
import { Send, Square } from 'lucide-react'
import { cn } from '@/lib/utils'
import { streamAnswer, getCollections } from '@/lib/api'
import { MessageBubble, type Message } from '@/components/chat/MessageBubble'
import { ModelSelector } from '@/components/chat/ModelSelector'

interface Collection { id: string; name: string }

export function Chat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [model, setModel] = useState('')
  const [collection, setCollection] = useState('default')
  const [collections, setCollections] = useState<Collection[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    getCollections().then(setCollections).catch(() => {})
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const send = useCallback(async () => {
    const q = input.trim()
    if (!q || streaming) return

    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: q }])

    // Add an empty assistant message that we'll fill token by token
    setMessages((prev) => [...prev, { role: 'assistant', content: '' }])
    setStreaming(true)

    const abort = new AbortController()
    abortRef.current = abort

    await streamAnswer(q, collection, {
      signal: abort.signal,
      onToken: (token) => {
        setMessages((prev) => {
          const updated = [...prev]
          const last = updated[updated.length - 1]
          if (last?.role === 'assistant') {
            updated[updated.length - 1] = { ...last, content: last.content + token }
          }
          return updated
        })
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
      },
      onSources: (sources) => {
        setMessages((prev) => {
          const updated = [...prev]
          const last = updated[updated.length - 1]
          if (last?.role === 'assistant') {
            updated[updated.length - 1] = { ...last, sources }
          }
          return updated
        })
      },
      onDone: () => setStreaming(false),
      onError: () => {
        setMessages((prev) => {
          const updated = [...prev]
          const last = updated[updated.length - 1]
          if (last?.role === 'assistant' && !last.content) {
            updated[updated.length - 1] = {
              ...last,
              content: 'Error contacting backend. Is the API running?',
            }
          }
          return updated
        })
        setStreaming(false)
      },
    })
  }, [input, streaming, collection])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Top bar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-border bg-surface shrink-0">
        <div className="flex items-center gap-3">
          <ModelSelector value={model} onChange={setModel} />
          <select
            value={collection}
            onChange={(e) => setCollection(e.target.value)}
            className="bg-surface border border-border text-secondary text-sm rounded-lg px-3 py-1.5 outline-none focus:border-accent hover:border-muted transition-colors"
          >
            {[...new Set(['default', ...collections.map((c) => c.name)])].map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
        </div>

        {messages.length > 0 && !streaming && (
          <button
            onClick={() => setMessages([])}
            className="text-xs text-muted-fg hover:text-secondary transition-colors"
          >
            Clear chat
          </button>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center gap-3 opacity-40">
            <div className="text-4xl">✦</div>
            <p className="text-sm text-muted-fg">Ask anything about your documents</p>
          </div>
        ) : (
          messages.map((m, i) => {
            const isStreamingLast = streaming && i === messages.length - 1 && m.role === 'assistant'
            return (
              <div key={i} className={cn(isStreamingLast && 'relative')}>
                <MessageBubble {...m} />
                {isStreamingLast && m.content && (
                  <span className="inline-block w-0.5 h-4 bg-accent ml-0.5 animate-pulse align-middle" />
                )}
              </div>
            )
          })
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-6 pb-6 shrink-0">
        <div
          className={cn(
            'flex items-end gap-3 rounded-2xl border border-border bg-surface p-3 transition-colors',
            'focus-within:border-accent/50',
          )}
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question… (Shift+Enter for new line)"
            rows={1}
            disabled={streaming}
            className="flex-1 bg-transparent text-sm text-primary placeholder-muted-fg resize-none outline-none max-h-40 leading-relaxed disabled:opacity-50"
            style={{ height: 'auto' }}
            onInput={(e) => {
              const t = e.currentTarget
              t.style.height = 'auto'
              t.style.height = `${t.scrollHeight}px`
            }}
          />
          <button
            onClick={streaming ? stop : send}
            disabled={!streaming && !input.trim()}
            className={cn(
              'flex items-center justify-center w-8 h-8 rounded-xl transition-all shrink-0',
              streaming
                ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                : input.trim()
                ? 'bg-accent text-white hover:bg-accent/80'
                : 'bg-muted/30 text-muted-fg cursor-not-allowed',
            )}
          >
            {streaming ? <Square size={12} fill="currentColor" /> : <Send size={14} />}
          </button>
        </div>
        <p className="text-center text-[10px] text-muted-fg mt-2 opacity-50">
          Answers sourced from your indexed documents
        </p>
      </div>
    </div>
  )
}
