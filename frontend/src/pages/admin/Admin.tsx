import { useEffect, useRef } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/ui/Notice'
import { Access } from './Access'
import { Audit } from './Audit'
import { Connections } from './Connections'
import { Models } from './Models'
import { System } from './System'
import { Usage } from './Usage'
import { Users } from './Users'

const TABS = [
  ['utilisateurs', 'Utilisateurs'],
  ['acces', 'Accès'],
  ['connecteurs', 'Connecteurs'],
  ['journal', "Journal d'audit"],
  ['modeles', 'Modèles'],
  ['consommation', 'Consommation'],
  ['systeme', 'Système'],
] as const

export function Admin() {
  const { pathname } = useLocation()
  const tabs = useRef<HTMLElement>(null)
  // Below lg the tabs scroll sideways: keep the current one in view.
  useEffect(() => {
    tabs.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [pathname])

  return (
    <div className="h-full overflow-y-auto px-8 py-8 max-md:px-4 max-md:py-5">
      <PageHeader title="Administration" />
      <nav
        ref={tabs}
        className="-mt-2 mb-6 flex gap-1 border-b border-rule max-lg:-mx-8 max-lg:overflow-x-auto max-lg:border-b-0 max-lg:px-8 max-lg:shadow-[inset_0_-1px_0_theme(colors.rule)] max-lg:[scrollbar-width:none] max-md:-mx-4 max-md:px-4"
        aria-label="Sections d'administration"
      >
        {TABS.map(([path, label]) => (
          <NavLink
            key={path}
            to={`/admin/${path}`}
            className={({ isActive }) =>
              cn(
                '-mb-px shrink-0 whitespace-nowrap border-b-2 px-3 py-2 text-sm max-lg:mb-0 max-md:py-3',
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
        <Route path="connecteurs" element={<Connections />} />
        <Route path="journal" element={<Audit />} />
        <Route path="modeles" element={<Models />} />
        <Route path="consommation" element={<Usage />} />
        <Route path="systeme" element={<System />} />
      </Routes>
    </div>
  )
}
