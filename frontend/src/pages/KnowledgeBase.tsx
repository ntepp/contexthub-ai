import { useEffect, useState } from 'react'
import { FolderOpen, Plus, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getCollections, createCollection } from '@/lib/api'
import { UploadZone } from '@/components/knowledge-base/UploadZone'
import { TaskStatus } from '@/components/knowledge-base/TaskStatus'

interface Collection { id: string; name: string; description?: string; document_count: number }
interface UploadJob { taskId: string; filename: string }

export function KnowledgeBase() {
  const [collections, setCollections] = useState<Collection[]>([])
  const [activeCollection, setActiveCollection] = useState('default')
  const [jobs, setJobs] = useState<UploadJob[]>([])
  const [newColName, setNewColName] = useState('')
  const [creating, setCreating] = useState(false)

  const loadCollections = () =>
    getCollections().then(setCollections).catch(() => {})

  useEffect(() => { loadCollections() }, [])

  const handleCreate = async () => {
    if (!newColName.trim()) return
    try {
      await createCollection(newColName.trim())
      setNewColName('')
      setCreating(false)
      loadCollections()
    } catch {
      alert('Collection already exists or backend error.')
    }
  }

  return (
    <div className="flex h-full">
      {/* Collections sidebar */}
      <div className="w-56 border-r border-border bg-surface flex flex-col shrink-0">
        <div className="flex items-center justify-between px-4 py-4 border-b border-border">
          <h2 className="text-xs font-semibold text-muted-fg uppercase tracking-wider">Collections</h2>
          <button
            onClick={() => setCreating((c) => !c)}
            className="text-muted-fg hover:text-primary transition-colors"
          >
            {creating ? <X size={14} /> : <Plus size={14} />}
          </button>
        </div>

        {creating && (
          <div className="p-3 border-b border-border">
            <input
              autoFocus
              value={newColName}
              onChange={(e) => setNewColName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              placeholder="Collection name…"
              className="w-full bg-background border border-border rounded-lg px-3 py-1.5 text-sm text-primary placeholder-muted-fg outline-none focus:border-accent"
            />
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-2">
          {['default', ...collections.map((c) => c.name)].map((name) => (
            <button
              key={name}
              onClick={() => setActiveCollection(name)}
              className={cn(
                'w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-left transition-colors',
                activeCollection === name
                  ? 'bg-accent/10 text-accent'
                  : 'text-secondary hover:text-primary hover:bg-muted/30',
              )}
            >
              <FolderOpen size={14} className="shrink-0" />
              <span className="truncate">{name}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-2xl mx-auto px-8 py-10 space-y-8">
          {/* Header */}
          <div>
            <h1 className="text-2xl font-semibold text-primary">Knowledge Base</h1>
            <p className="text-sm text-muted-fg mt-1">
              Upload documents to{' '}
              <span className="text-accent font-medium">{activeCollection}</span>
            </p>
          </div>

          {/* Upload zone */}
          <UploadZone
            collection={activeCollection}
            onUpload={(taskId, filename) =>
              setJobs((prev) => [{ taskId, filename }, ...prev])
            }
          />

          {/* Task list */}
          {jobs.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-xs font-semibold text-muted-fg uppercase tracking-wider">
                Indexing queue
              </h3>
              <div className="space-y-2">
                {jobs.map((job) => (
                  <TaskStatus key={job.taskId} taskId={job.taskId} filename={job.filename} />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
