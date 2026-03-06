import { useEffect, useRef, useState } from 'react'
import { Send, StopCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { askQuestion, getCollections } from '@/lib/api'
import { MessageBubble, type Message } from '@/components/chat/MessageBubble'
import { ModelSelector } from '@/components/chat/ModelSelector'

interface Collection { id: string; name: string }

export function Chat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [model, setModel] = useState('')
  const [collection, setCollection] = useState('default')
  const [collections, setCollections] = useState<Collection[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    getCollections().then(setCollections).catch(() => {})
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const send = async () => {
    const q = input.trim()
    if (!q || loading) return
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: q }])
    setLoading(true)
    try {
      const res = await askQuestion(q, collection)
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: res.answer, sources: res.sources },
      ])
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: 'Error contacting backend. Is the API running?' },
      ])
    } finally {
      setLoading(false)
    }
  }

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

          {/* Collection selector */}
          <select
            value={collection}
            onChange={(e) => setCollection(e.target.value)}
            className="bg-surface border border-border text-secondary text-sm rounded-lg px-3 py-1.5 outline-none focus:border-accent hover:border-muted transition-colors"
          >
            <option value="default">default</option>
            {collections.map((c) => (
              <option key={c.id} value={c.name}>{c.name}</option>
            ))}
          </select>
        </div>

        {messages.length > 0 && (
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
          messages.map((m, i) => <MessageBubble key={i} {...m} />)
        )}

        {loading && (
          <div className="flex gap-3 animate-fade-in">
            <div className="flex items-center justify-center w-8 h-8 rounded-full bg-accent/10 text-accent shrink-0">
              <span className="text-xs">✦</span>
            </div>
            <div className="bg-surface border border-border rounded-2xl rounded-tl-sm px-4 py-3">
              <div className="flex gap-1.5 items-center h-4">
                {[0, 1, 2].map((i) => (
                  <div
                    key={i}
                    className="w-1.5 h-1.5 rounded-full bg-accent/60 animate-bounce"
                    style={{ animationDelay: `${i * 150}ms` }}
                  />
                ))}
              </div>
            </div>
          </div>
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
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question… (Shift+Enter for new line)"
            rows={1}
            className="flex-1 bg-transparent text-sm text-primary placeholder-muted-fg resize-none outline-none max-h-40 leading-relaxed"
            style={{ height: 'auto' }}
            onInput={(e) => {
              const t = e.currentTarget
              t.style.height = 'auto'
              t.style.height = `${t.scrollHeight}px`
            }}
          />
          <button
            onClick={loading ? undefined : send}
            disabled={!input.trim() && !loading}
            className={cn(
              'flex items-center justify-center w-8 h-8 rounded-xl transition-all shrink-0',
              input.trim() && !loading
                ? 'bg-accent text-white hover:bg-accent/80'
                : 'bg-muted/30 text-muted-fg cursor-not-allowed',
            )}
          >
            {loading ? <StopCircle size={14} /> : <Send size={14} />}
          </button>
        </div>
        <p className="text-center text-[10px] text-muted-fg mt-2 opacity-50">
          Answers sourced from your indexed documents
        </p>
      </div>
    </div>
  )
}
