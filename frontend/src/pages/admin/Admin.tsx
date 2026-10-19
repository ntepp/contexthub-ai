import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/ui/Notice'
import { Access } from './Access'
import { Audit } from './Audit'
import { System } from './System'
import { Users } from './Users'

const TABS = [
  ['utilisateurs', 'Utilisateurs'],
  ['acces', 'Accès'],
  ['journal', "Journal d'audit"],
  ['systeme', 'Système'],
] as const

export function Admin() {
  return (
    <div className="h-full overflow-y-auto px-8 py-8">
      <PageHeader title="Administration" />
      <nav className="-mt-2 mb-6 flex gap-1 border-b border-rule" aria-label="Sections d'administration">
        {TABS.map(([path, label]) => (
          <NavLink
            key={path}
            to={`/admin/${path}`}
            className={({ isActive }) =>
              cn(
                '-mb-px border-b-2 px-3 py-2 text-sm',
                isActive ? 'border-ledger font-medium text-ink' : 'border-transparent text-slate hover:text-ink',
              )
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<Navigate to="utilisateurs" replace />} />
        <Route path="utilisateurs" element={<Users />} />
        <Route path="acces" element={<Access />} />
        <Route path="journal" element={<Audit />} />
        <Route path="systeme" element={<System />} />
      </Routes>
    </div>
  )
}
