import { useRef, useState } from 'react'
import { Upload, FileText } from 'lucide-react'
import { cn } from '@/lib/utils'
import { uploadPDF } from '@/lib/api'

interface Props {
  collection: string
  onUpload: (taskId: string, filename: string) => void
}

export function UploadZone({ collection, onUpload }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    const file = files[0]
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      alert('Only PDF files are supported.')
      return
    }
    setUploading(true)
    try {
      const res = await uploadPDF(file, collection)
      onUpload(res.task_id, file.name)
    } catch {
      alert('Upload failed. Is the backend running?')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => { e.preventDefault(); setDragging(false); handleFiles(e.dataTransfer.files) }}
      onClick={() => inputRef.current?.click()}
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed',
        'p-10 cursor-pointer transition-all duration-200 select-none',
        dragging ? 'border-accent bg-accent/5' : 'border-border hover:border-muted hover:bg-muted/10',
        uploading && 'opacity-50 pointer-events-none',
      )}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".pdf"
        className="hidden"
        onChange={(e) => handleFiles(e.target.files)}
      />
      <div className="flex items-center justify-center w-12 h-12 rounded-full bg-accent/10">
        {uploading ? (
          <Upload size={20} className="text-accent animate-bounce" />
        ) : (
          <FileText size={20} className="text-accent" />
        )}
      </div>
      <div className="text-center">
        <p className="text-sm font-medium text-primary">
          {uploading ? 'Uploading…' : 'Drop a PDF here or click to browse'}
        </p>
        <p className="text-xs text-muted-fg mt-1">PDF files only · max 50 MB</p>
      </div>
    </div>
  )
}
